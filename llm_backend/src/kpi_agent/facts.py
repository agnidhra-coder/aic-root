"""Flatten evidence bundles into a numbered fact table.

This is the mechanism behind the grounding guarantee, and it is deliberately dull:
every number the narrator is allowed to write is extracted here, given an id, and
paired with its lineage. The narrator receives the table and nothing else -- no
panel, no raw frame, no bundle it could re-read differently.

The pairing is what makes verification possible. Because each fact carries both a
`value` (for numeric comparison) and a `display` (the exact string to quote), the
verifier downstream can check a claim mechanically rather than by asking a second
model whether the first one was honest.

Persona affects only *which* facts are included and in what order. It never changes
a value, and entitlement filtering happens here -- by dropping facts, not by
instructing the narrator to be discreet.
"""

from __future__ import annotations

from typing import Any

from kpi_engine.causal.dag import CausalGraph
from kpi_engine.contracts.payloads import EvidenceBundle, Freshness, SeriesProfile

from kpi_agent.models import (
    ContextAlignment,
    CrossSourceLink,
    ExogenousFactor,
    Fact,
    GroundedContext,
)


class FactBuilder:
    """Issues F1, F2, ... and remembers what each one meant."""

    def __init__(self) -> None:
        self.facts: list[Fact] = []
        self._n = 0

    def add(self, **kwargs: Any) -> Fact:
        self._n += 1
        fact = Fact(id=f"F{self._n}", **kwargs)
        self.facts.append(fact)
        return fact


def _fmt_number(value: float, *, sign: bool = False) -> str:
    """Comma-grouped, fixed-point rendering of `value` -- never scientific notation.

    `f"{value:,.4g}"` (the natural choice for "a few significant figures") switches
    to `3.885e+06` once the exponent leaves `.4g`'s fixed-point range, which reads
    as a typo to a business audience. Magnitude decides the decimal count instead:
    whole units once the number is large enough that a fraction is not meaningful.
    """
    sign_spec = "+" if sign else ""
    if abs(value) >= 1000:
        return f"{value:{sign_spec},.0f}"
    return f"{value:{sign_spec},.4g}"


def _fmt(value: float | None, unit: str | None) -> str:
    if value is None:
        return "n/a"
    if unit == "percent":
        return f"{value:.2f}%"
    if unit == "pct_delta":
        return f"{value:+.1f}%"
    if unit == "currency":
        return f"{value:,.2f}"
    if unit == "days":
        return f"{value:.1f} days"
    if unit == "ratio" or unit == "score":
        return f"{value:.3f}"
    if unit == "count":
        return f"{int(round(value)):,}"
    return _fmt_number(value)


def build_context(
    *,
    question: str,
    intent,
    persona_spec: dict[str, Any],
    run_id: str,
    sales_bundles: list[EvidenceBundle],
    scm_bundles: list[EvidenceBundle],
    links: list[CrossSourceLink],
    graph: CausalGraph,
    freshness: list[Freshness],
    series_profiles: list[SeriesProfile] | None = None,
    period: tuple[str | None, str | None] = (None, None),
    alignments: list[ContextAlignment] | None = None,
    unaligned_factors: list[ExogenousFactor] | None = None,
    survey: bool = False,
) -> GroundedContext:
    builder = FactBuilder()
    include = set(persona_spec.get("include_facts", []))
    withheld = set(persona_spec.get("restricted_entitlements", []))

    events: list[dict[str, Any]] = []
    abstentions: list[dict[str, Any]] = []

    alignments = alignments or []
    unaligned_factors = unaligned_factors or []

    # The user's own context leads the table. It is what they actually asked
    # about, and it is the one kind of fact whose absence from the answer would
    # read as the question having been ignored.
    if "context" in include:
        for alignment in alignments:
            builder.add(
                label=f"Context stated by the user: {alignment.factor_label}",
                value=float(alignment.overlap_days),
                display=(
                    f"{alignment.factor_label}, "
                    + (
                        f"overlapping {alignment.event_id} by "
                        f"{alignment.overlap_days} day(s)"
                        if alignment.overlap_days
                        else f"{alignment.lag_days} day(s) from {alignment.event_id}"
                    )
                ),
                unit=None,
                kind="context",
                kpi=alignment.kpis_moved[0] if alignment.kpis_moved else None,
                source_id=alignment.source_id,
                method="user_asserted",
                # `None`, not `False`. The exact/estimated distinction is about
                # how a computed quantity was arrived at, and this one was not
                # computed at all -- rendering it as "estimated" would credit the
                # engine with having modelled the user's sentence.
                exact=None,
                lineage={
                    "event_id": alignment.event_id,
                    "entity_match": alignment.entity_match,
                    "direction_agrees": alignment.direction_agrees,
                },
                note=alignment.note,
            )
        for factor in unaligned_factors:
            builder.add(
                label=f"Context stated by the user: {factor.label}",
                value=None,
                # Named, not just described. The label is what the user will
                # recognise as their own point; a bare restatement of the detail
                # reads like the report volunteered it.
                display=(
                    f"{factor.label}: {factor.detail}" if factor.detail else factor.label
                ),
                unit=None,
                kind="context",
                method="user_asserted",
                exact=None,
                note=(
                    "Stated by the user. Nothing in the detected windows lines up "
                    "with it"
                    + (
                        ", because no date was given to line up."
                        if not (factor.date_start or factor.date_end)
                        else "."
                    )
                    + " The engine did not measure it and cannot weigh it."
                ),
            )

    for bundle in [*sales_bundles, *scm_bundles]:
        events.append(
            _event_entry(bundle, builder, include, withheld, graph)
        )
        if bundle.abstained and bundle.abstention:
            abstentions.append(
                {
                    "event_id": bundle.event_id,
                    "kpi": bundle.abstention.kpi,
                    "reason_code": bundle.abstention.reason_code,
                    "message": bundle.abstention.message,
                    "missing_evidence": bundle.abstention.missing_evidence,
                    "what_would_resolve_it": bundle.abstention.what_would_resolve_it,
                }
            )

    # Descriptions belong in a survey, or where there is nothing else to say.
    # Asked "why did CAC rise in the West", a reader does not want to hear that
    # Fill Rate has been drifting -- that is a digression dressed as thoroughness.
    # Asked "how are we doing", or asked anything at all in a period where no
    # event cleared the thresholds, it is the answer. `_CAP_PRIORITY` then keeps
    # trends below every attributed fact, so they can fill a budget but never
    # displace evidence.
    describe = survey or not (sales_bundles or scm_bundles)
    if "trend" in include and series_profiles and describe:
        for profile, shape in _notable_trends(series_profiles):
            builder.add(**_trend_fact(profile, shape))

    if "quality" in include and series_profiles:
        for profile in _notable_profiles(series_profiles):
            builder.add(
                label=f"{profile.kpi} data quality, {_slice(profile.entity)}",
                value=profile.quality.coverage,
                display=f"{profile.quality.coverage:.0%} period coverage",
                unit=None,
                kind="quality",
                kpi=profile.kpi,
                entity=profile.entity,
                source_id=profile.lineage.source_id,
                lineage=profile.lineage.model_dump(mode="json"),
                note=profile.unusable_reason or profile.headline,
            )

    if "freshness" in include:
        for fresh in freshness:
            builder.add(
                label=f"{fresh.source_id} data freshness",
                value=float(fresh.lag_days) if fresh.lag_days is not None else None,
                display=(
                    f"{fresh.lag_days} days behind as of {fresh.as_of}"
                    if fresh.lag_days is not None else f"as of {fresh.as_of}"
                ),
                unit=None,
                kind="freshness",
                source_id=fresh.source_id,
                note="stale" if fresh.is_stale else "within cadence",
            )

    link_facts: list[CrossSourceLink] = []
    if "link" in include:
        for link in links:
            builder.add(
                label=(
                    f"Cross-source link: {link.scm_event_id} -> {link.sales_event_id} "
                    f"via {' -> '.join(link.dag_path)}"
                ),
                value=float(link.lag_days),
                display=f"{link.lag_days} day lag, {link.overlap_days} day overlap",
                unit=None,
                kind="link",
                kpi=link.sales_kpis_moved[0] if link.sales_kpis_moved else None,
                entity=link.shared_entity,
                method=link.relation,
                lineage={"dag_path": link.dag_path, "dag_edge": list(link.dag_edge)},
                note=link.note,
            )
            link_facts.append(link)

    levers: list[dict[str, Any]] = []
    if "lever" in include:
        for node in graph.spec.nodes:
            if node.controllable and node.column not in withheld:
                levers.append(
                    {"lever": node.name, "owner": node.owner,
                     "description": node.description, "column": node.column}
                )

    facts = _cap(builder.facts, persona_spec.get("max_facts", 100))
    kept = {f.id for f in facts}
    # Trim event references to facts that survived the persona cap, or the narrator
    # would be pointed at ids it cannot see.
    for entry in events:
        entry["fact_ids"] = [i for i in entry.get("fact_ids", []) if i in kept]

    return GroundedContext(
        question=question,
        question_restated=getattr(intent, "question_restated", question),
        persona=getattr(intent, "persona", "analyst"),
        run_id=run_id,
        time_grain=getattr(intent, "time_grain", "week"),
        entity_keys=list(getattr(intent, "entity_keys", []) or []),
        period_start=period[0],
        period_end=period[1],
        facts=facts,
        events=events,
        links=link_facts,
        # Everything the user asserted, aligned or not. The narrator has to be
        # able to answer a hypothesis it could not place, and it cannot do that
        # from a list that quietly omits the ones that did not fit.
        exogenous=[f.model_dump(mode="json") for f in getattr(intent, "exogenous", [])],
        alignments=list(alignments),
        abstentions=abstentions,
        freshness=[f.model_dump(mode="json") for f in freshness],
        # Counted after the cap, not before it. A caveat about four trend calls
        # in a table that shows none is a footnote pointing at nothing -- and the
        # cap does drop them, deliberately, when a run has attributed events to
        # spend its budget on instead.
        data_caveats=_caveats(
            series_profiles or [],
            abstentions,
            trend_calls=sum(
                1 for f in facts
                if f.kind == "trend" and f.method == "theil_sen + mann_kendall"
            ),
        ),
        levers=levers,
        persona_brief=persona_spec.get("brief", "").strip(),
    )


def _event_entry(
    bundle: EvidenceBundle,
    builder: FactBuilder,
    include: set[str],
    withheld: set[str],
    graph: CausalGraph,
) -> dict[str, Any]:
    event = bundle.event
    ids: list[str] = []
    source_id = bundle.lineage.source_id if bundle.lineage else None

    if "movement" in include:
        for dev in event.observed_deviations:
            if not dev.material:
                continue
            fact = builder.add(
                label=f"{dev.kpi} moved in {_slice(event.entity)}",
                value=dev.pct_delta,
                display=(
                    f"{dev.kpi} {dev.pct_delta:+.1f}% "
                    f"({_fmt(dev.expected, None)} expected, {_fmt(dev.actual, None)} actual)"
                    if dev.pct_delta is not None else f"{dev.kpi} moved"
                ),
                unit="pct_delta",
                kind="movement",
                kpi=dev.kpi,
                entity=event.entity,
                source_id=source_id,
                lineage=event.lineage.model_dump(mode="json") if event.lineage else {},
                note=(
                    f"{event.window_start} to {event.window_end}; "
                    f"{', '.join(event.anomaly_types)}; "
                    f"detected by {', '.join(event.detectors)}"
                ),
                expected=dev.expected,
                actual=dev.actual,
            )
            ids.append(fact.id)

    if "contribution" in include:
        for attribution in bundle.attributions:
            for contribution in attribution.contributions:
                column = graph.column_for(contribution.driver) or contribution.driver
                if column in withheld or contribution.driver in withheld:
                    continue
                fact = builder.add(
                    label=f"{contribution.driver} contribution to {attribution.kpi}",
                    value=contribution.contribution,
                    display=(
                        f"{contribution.driver}: {_fmt_number(contribution.contribution, sign=True)}"
                        # A share is nan when the total delta is zero -- the
                        # contribution is real but its proportion is undefined, and
                        # printing "nan%" in a business report is worse than
                        # printing nothing.
                        + (
                            f" ({contribution.share:+.1%} of the move)"
                            if contribution.share is not None
                            and contribution.share == contribution.share else ""
                        )
                        + (
                            f", 95% CI {_fmt_number(contribution.ci_low, sign=True)} to "
                            f"{_fmt_number(contribution.ci_high, sign=True)}"
                            if contribution.ci_low is not None
                            and contribution.ci_high is not None else ""
                        )
                    ),
                    unit=None,
                    kind="contribution",
                    kpi=attribution.kpi,
                    entity=event.entity,
                    source_id=source_id,
                    method=contribution.method,
                    exact=contribution.exact,
                    controllable=contribution.controllable,
                    owner=contribution.owner,
                    lineage={"event_id": bundle.event_id, "kpi": attribution.kpi},
                    note=(
                        "exact algebra -- follows from the KPI's definition"
                        if contribution.exact else
                        f"estimated by {contribution.method}"
                    ),
                    # Same NaN check `display` above uses, so the two agree on
                    # exactly when a share exists: `x == x` is false only for NaN.
                    share=(
                        contribution.share
                        if contribution.share is not None
                        and contribution.share == contribution.share else None
                    ),
                )
                ids.append(fact.id)

            if "method" in include:
                fact = builder.add(
                    label=f"Method chosen for {attribution.kpi}",
                    value=None,
                    display=attribution.method_choice.chosen,
                    unit=None,
                    kind="method",
                    kpi=attribution.kpi,
                    entity=event.entity,
                    source_id=source_id,
                    method=attribution.method_choice.chosen,
                    lineage={
                        "considered": attribution.method_choice.considered,
                        "preconditions": attribution.method_choice.preconditions_checked,
                    },
                    note=attribution.method_choice.reason,
                )
                ids.append(fact.id)

    if "confidence" in include:
        fact = builder.add(
            label=f"Confidence in the explanation of {bundle.event_id}",
            value=bundle.confidence.score,
            display=f"{bundle.confidence.score:.2f}",
            unit="score",
            kind="confidence",
            entity=event.entity,
            source_id=source_id,
            lineage={"formula": bundle.confidence.formula},
            note=(
                f"signal-to-noise {bundle.confidence.signal_to_noise:.2f}, "
                f"support {bundle.confidence.support_factor:.2f}, "
                f"history {bundle.confidence.history_factor:.2f}"
            ),
        )
        ids.append(fact.id)

    return {
        "event_id": bundle.event_id,
        "source_id": source_id,
        "window": [str(event.window_start), str(event.window_end)],
        "entity": event.entity,
        "anomaly_types": list(event.anomaly_types),
        "detectors": list(event.detectors),
        "kpis_moved": [d.kpi for d in event.observed_deviations if d.material],
        "abstained": bundle.abstained,
        "confidence": bundle.confidence.score,
        "deterministic_methods": list(bundle.deterministic_methods),
        "fact_ids": ids,
    }


# What a persona's cap should spend its budget on, most valuable first. Truncating
# in construction order instead means a run with forty contributions exhausts the
# budget before a single cross-source link is reached -- and the link is the one
# fact that answers "is supply to blame", which is the question a second source
# exists to make answerable.
_CAP_PRIORITY = ["context", "link", "movement", "confidence", "contribution",
                 "method", "trend", "freshness", "quality"]


def _cap(facts: list[Fact], limit: int) -> list[Fact]:
    """Trim to `limit` by kind priority, then restore issue order.

    Restoring the original order matters: ids are assigned at creation, so a table
    sorted any other way would present F41 above F7 and read as though facts were
    missing.
    """
    if len(facts) <= limit:
        return facts

    kept: list[Fact] = []
    remaining = limit
    by_kind: dict[str, list[Fact]] = {}
    for fact in facts:
        by_kind.setdefault(fact.kind, []).append(fact)

    for kind in _CAP_PRIORITY + [k for k in by_kind if k not in _CAP_PRIORITY]:
        if remaining <= 0:
            break
        take = by_kind.get(kind, [])[:remaining]
        kept.extend(take)
        remaining -= len(take)

    order = {fact.id: i for i, fact in enumerate(facts)}
    return sorted(kept, key=lambda f: order[f.id])


# How many of the eda stage's descriptions are worth putting in front of a
# reader. The persona's own `max_facts` and `_CAP_PRIORITY` trim further; this
# bound exists so that a survey over forty series does not arrive as forty
# sentences saying much the same thing.
TREND_LIMIT = 12

# Shapes a `SeriesProfile` can contribute, and how each is labelled. One fact
# *kind* covers all three -- adding three kinds would mean threading three
# entries through seven persona files for a distinction the reader does not
# make. `method` is what separates them.
TrendShape = str  # "trend" | "seasonality" | "phase"


def has_notable_trends(results: dict) -> bool:
    """Whether any source produced a description worth reporting on its own.

    Read by `graph._after_run` to decide whether a survey that detected nothing
    still has an answer. Cheap: it stops at the first hit.
    """
    for result in results.values():
        for profile in getattr(result, "series_profiles", []) or []:
            if _trend_shapes(profile):
                return True
    return False


def count_trending(profiles: list[SeriesProfile] | None) -> int:
    """Series whose underlying level is called as moving. For the no-findings table."""
    return sum(
        1 for p in (profiles or [])
        if p.usable and p.trend.direction in {"rising", "falling"}
    )


def _trend_shapes(profile: SeriesProfile) -> list[TrendShape]:
    """Which descriptions this series has earned, if any.

    No threshold is invented here, which is the point. `eda/trend.py`'s
    `call_direction` already names a direction only when Mann-Kendall clears the
    configured alpha *and* the movement clears `flat_slope_pct` or
    `min_total_change_pct`; a named direction therefore already means
    "significant and material", and second-guessing it with a bar of our own
    would put two materiality standards in the codebase.
    """
    if not profile.usable:
        return []
    shapes: list[TrendShape] = []
    if profile.trend.direction in {"rising", "falling"}:
        shapes.append("trend")
    if profile.seasonality.detected:
        shapes.append("seasonality")
    if _last_phase(profile) is not None:
        shapes.append("phase")
    return shapes


def _last_phase(profile: SeriesProfile):
    """The most recent segment that broke from the one before it.

    Only the last: a reader wants to know what the series is doing *now*, and
    reciting every phase of a two-year history is what `series_profiles.json`
    is for.
    """
    for segment in reversed(profile.segments):
        if segment.pct_change_vs_previous is None:
            continue
        # The same bar `eda` uses for a trend to count as material end-to-end.
        if abs(segment.pct_change_vs_previous) >= 5.0:
            return segment
    return None


def _notable_trends(
    profiles: list[SeriesProfile], limit: int = TREND_LIMIT
) -> list[tuple[SeriesProfile, TrendShape]]:
    """The descriptions worth stating, strongest first.

    Ranked by the size of the end-to-end move rather than by significance: a
    p-value orders series by how sure we are, and a reader scanning a survey
    wants them ordered by how much happened.
    """
    ranked = sorted(
        (p for p in profiles if _trend_shapes(p)),
        key=lambda p: abs(p.trend.total_change_pct or 0.0),
        reverse=True,
    )
    out: list[tuple[SeriesProfile, TrendShape]] = []
    for profile in ranked:
        for shape in _trend_shapes(profile):
            if len(out) >= limit:
                return out
            out.append((profile, shape))
    return out


def _trend_fact(profile: SeriesProfile, shape: TrendShape) -> dict[str, Any]:
    """One `trend` fact, whichever shape it is.

    Every number a narrator might reach for -- the p-value, the period count, the
    slope -- goes into `note`, because `verify._fact_values` reads `note` as well
    as `display`. A description whose supporting statistics are not quotable is a
    description the narrator has to either omit or invent.
    """
    where = _slice(profile.entity)
    # Rounded to the precision the `display` string states. A fact whose `value`
    # carries fifteen significant figures and whose display carries three invites
    # a narrator to quote "-76.80413627059869%" -- which is grounded, passes
    # verification, and reads as though the engine measured a series to a
    # femtometre. The two halves of a fact should agree about how much is known.
    common = {
        "kind": "trend",
        "kpi": profile.kpi,
        "entity": profile.entity,
        "source_id": profile.lineage.source_id,
        # Descriptive, so neither exact algebra nor an estimate of an effect.
        "exact": False,
    }

    if shape == "seasonality":
        season = profile.seasonality
        return {
            **common,
            "label": f"{profile.kpi} seasonality, {where}",
            "value": round(season.strength, 2),
            "display": (
                f"{profile.kpi} has a repeating {season.period}-period cycle, "
                f"strength {season.strength:.2f}"
            ),
            "unit": None,
            "method": "stl_seasonality",
            "lineage": profile.lineage.model_dump(mode="json"),
            "note": (
                f"Peaks at {season.peak_label}, troughs at {season.trough_label}. "
                f"{season.reason} Descriptive only: a cycle is not a movement to "
                f"explain."
            ),
        }

    if shape == "phase":
        segment = _last_phase(profile)
        return {
            **common,
            "label": f"{profile.kpi} latest phase, {where}",
            "value": round(segment.pct_change_vs_previous, 1),
            "display": (
                f"{profile.kpi} shifted {segment.pct_change_vs_previous:+.1f}% into "
                f"the phase running {segment.start} to {segment.end}"
            ),
            "unit": "pct_delta",
            "method": "pelt_segment",
            "lineage": profile.lineage.model_dump(mode="json"),
            "note": (
                f"Phase mean {_fmt_number(segment.mean)} over {segment.n_periods} periods, "
                f"{segment.direction}. A structural break in the level, not an "
                f"anomaly: no detector flagged it."
            ),
        }

    trend = profile.trend
    return {
        **common,
        "label": f"{profile.kpi} trend, {where}",
        "value": round(trend.total_change_pct, 1)
                 if trend.total_change_pct is not None else None,
        "display": (
            f"{profile.kpi} is {trend.direction}, {trend.total_change_pct:+.1f}% "
            f"end to end"
            + (
                f" ({trend.slope_pct_per_period:+.2f}% per period)"
                if trend.slope_pct_per_period is not None else ""
            )
        ),
        "unit": "pct_delta",
        "method": trend.method,
        "lineage": profile.lineage.model_dump(mode="json"),
        "note": (
            f"{profile.headline} Mann-Kendall p={trend.p_value:.3f} over "
            f"{trend.n_periods} periods. Descriptive: the series has been moving, "
            f"which is not the same as a period having been anomalous."
        ),
    }


def _notable_profiles(profiles: list[SeriesProfile], limit: int = 12) -> list[SeriesProfile]:
    """Only the profiles a reader would want flagged: the unusable and the thin.

    A clean series is not a caveat. Including every profile would bury the two that
    actually qualify the answer under sixty that do not.
    """
    notable = [p for p in profiles if not p.usable or p.quality.low_support_share > 0.25]
    return notable[:limit]


def _caveats(
    profiles: list[SeriesProfile],
    abstentions: list[dict[str, Any]],
    trend_calls: int = 0,
) -> list[str]:
    out: list[str] = []
    unusable = [p for p in profiles if not p.usable]
    if unusable:
        out.append(
            f"{len(unusable)} of {len(profiles)} series were not usable "
            f"({unusable[0].unusable_reason}); they are excluded from the findings."
        )
    if trend_calls:
        # Not optional decoration. `configs/eda/default.yaml` records the measured
        # false-positive rate and states plainly that the stage applies no FDR
        # correction, deliberately -- correcting would make one series' verdict
        # depend on which others happened to be in the run. The consequence is
        # that a survey over many series expects a spurious call or two, and
        # reporting trends without saying so is how a descriptive stage starts
        # manufacturing findings.
        out.append(
            f"{trend_calls} of {len(profiles)} series were called trending at "
            f"alpha 0.05, where about 4% of pure-noise series would be. No "
            f"multiple-testing correction is applied, by design. Read a single "
            f"trend call as a hypothesis, not a finding."
        )
    if abstentions:
        out.append(
            f"{len(abstentions)} event(s) produced no explanation: the evidence was "
            "insufficient and the engine abstained rather than guess."
        )
    return out


def _slice(entity: dict[str, str]) -> str:
    return ", ".join(f"{k}={v}" for k, v in entity.items()) if entity else "the total"
