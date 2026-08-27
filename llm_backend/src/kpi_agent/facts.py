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

from kpi_agent.models import CrossSourceLink, Fact, GroundedContext


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
    return f"{value:,.4g}".rstrip()


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
) -> GroundedContext:
    builder = FactBuilder()
    include = set(persona_spec.get("include_facts", []))
    withheld = set(persona_spec.get("restricted_entitlements", []))

    events: list[dict[str, Any]] = []
    abstentions: list[dict[str, Any]] = []

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
        abstentions=abstentions,
        freshness=[f.model_dump(mode="json") for f in freshness],
        data_caveats=_caveats(series_profiles or [], abstentions),
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
                        f"{contribution.driver}: {contribution.contribution:+,.4g}"
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
                            f", 95% CI {contribution.ci_low:+,.4g} to {contribution.ci_high:+,.4g}"
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
_CAP_PRIORITY = ["link", "movement", "confidence", "contribution", "method",
                 "freshness", "quality"]


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


def _notable_profiles(profiles: list[SeriesProfile], limit: int = 12) -> list[SeriesProfile]:
    """Only the profiles a reader would want flagged: the unusable and the thin.

    A clean series is not a caveat. Including every profile would bury the two that
    actually qualify the answer under sixty that do not.
    """
    notable = [p for p in profiles if not p.usable or p.quality.low_support_share > 0.25]
    return notable[:limit]


def _caveats(profiles: list[SeriesProfile], abstentions: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    unusable = [p for p in profiles if not p.usable]
    if unusable:
        out.append(
            f"{len(unusable)} of {len(profiles)} series were not usable "
            f"({unusable[0].unusable_reason}); they are excluded from the findings."
        )
    if abstentions:
        out.append(
            f"{len(abstentions)} event(s) produced no explanation: the evidence was "
            "insufficient and the engine abstained rather than guess."
        )
    return out


def _slice(entity: dict[str, str]) -> str:
    return ", ".join(f"{k}={v}" for k, v in entity.items()) if entity else "the total"
