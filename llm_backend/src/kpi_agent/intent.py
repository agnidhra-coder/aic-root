"""Turn a question into a configuration -- then check the configuration.

The model proposes an `AnalysisIntent`; `validate_intent` decides whether it is
runnable. That split matters. Structured output guarantees the *shape* of what
comes back, not its truth: nothing stops a model returning a well-formed intent
naming a KPI that does not exist, a dimension the source does not carry, or a
grain the data cannot support. The validator is what turns a plausible plan into
a permitted one, and it is ordinary Python -- no model is consulted about whether
the model was right.

A rejected intent is not an error. It routes to a clarifying question, which is
the behaviour the problem statement asks for when a request cannot be resolved.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
from typing import Any

from kpi_engine.contracts.configs import KpiContract, SourceSpec
from kpi_engine.contracts.payloads import DataProfile

from kpi_agent.llm import MODEL, LlmUnavailable, Usage, unavailable
from kpi_agent.models import AnalysisIntent, ExogenousFactor

log = logging.getLogger(__name__)

SYSTEM = """\
You plan analyses for a deterministic KPI engine. You do not analyse anything \
yourself and you never see data rows -- you choose which KPIs, which slices, \
which grain and which sources the engine should compute, and the engine does the rest.

Rules:
- Every KPI name, entity column and source id you emit must appear verbatim in the \
catalog. Do not invent, translate, pluralise or abbreviate them.
- `kpis` names the metrics whose MOVEMENT is to be explained. It may only contain \
KPI names from the catalog. A driver column -- cost of goods, an expense line, a \
unit count -- is part of the ANSWER, not the question: the engine will attribute \
the movement to it on its own. If the question asks about a driver ("why did COGS \
spike"), choose the KPIs that driver feeds instead, and let the attribution name it.
- Grain is bounded from below by history, not by taste. The detector trains its \
baseline on a fixed number of PERIODS before it will emit an expectation, so a \
coarse grain over a short span leaves it nothing to learn from and it will \
correctly report that nothing happened. The catalog gives each source's date range \
and the minimum period count; pick the coarsest grain that still clears it. When \
the arithmetic is close, prefer `week` -- it is the grain this data is dense enough \
to model at.
- Prefer the fewest entity keys that answer the question, and prefer a dimension on \
which only SOME slices moved. A controlled comparison needs untreated slices to \
compare against: if a disruption is attributable to one supplier, slicing by \
supplier gives the estimator a control group, while slicing by region marks every \
region as affected and leaves it none. Total level (no entity keys) is right for a \
movement that is system-wide.
- If the question could plausibly have a supply-side cause -- cost of goods, margin, \
inventory, availability, fulfilment -- include the supply-chain source as well as the \
sales source. Cross-source explanation is the point of having both.
- If the question names a period, set date_start and date_end. If it names a relative \
period ("last quarter", "this month"), resolve it against the catalog's date range, \
which is the data's own clock and may differ from today's date.
- Set clarification_needed ONLY when the request genuinely cannot be resolved against \
the catalog -- an unknown metric, an ambiguous entity, a period outside coverage. Not \
naming a KPI is never a reason to ask for clarification.
- A request may give you nothing to narrow with, in three ways that are the same \
instruction written differently: it is EMPTY or blank; it is vague but answerable \
("what needs attention?", "how are we doing?"); or it states context and asks nothing \
("we ran a billboard campaign in the West last week"). Treat all three identically for \
`kpis` -- leave it EMPTY rather than listing every KPI in the catalog. Empty is a \
positive instruction meaning "all of them", and it is what puts the run in survey mode, \
where the descriptive findings are reported alongside whatever the detector flags. \
Write `question_restated` as the review you decided to run; do not echo an empty string \
back, and do not restate a bare assertion as though it were a question.
- The request may assert something the data does not contain: weather, a strike, a \
competitor's promotion, a public holiday, a campaign nobody logged. Record each as one \
`exogenous` entry. Transcribe what was said and invent nothing around it -- give dates \
only if dates were given, and name an entity only when it is a catalog entity column \
and one of its values. A vague mention ("the weather was bad") is still worth recording \
with null dates. This is a hypothesis for the reader to weigh, not a finding: it must \
not change which KPIs, which grain, which slice or which period you choose, and the \
engine will never treat it as a cause.
- Those last two rules are independent, and the third case above is where that shows: \
a request that only states context produces an EMPTY `kpis` list AND a populated \
`exogenous` list in the same answer. Recording what the user said is never a substitute \
for sweeping the KPIs, and sweeping them is never a reason to drop what they said.
"""


def plan_intent(
    llm: Any,
    usage: Usage,
    question: str,
    catalog: dict[str, Any],
    persona: str | None = None,
) -> AnalysisIntent:
    """Model call #1: the question and the catalog in, a proposed configuration out.

    The catalog goes in the middle message and the question last, so the stable
    prefix stays stable across the two calls of a run and Gemini's implicit caching
    can hit. `include_raw=True` is what makes the token counts and a validation
    failure both reachable -- without it a bad reply arrives as an exception with
    the usage already lost.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    persona_note = (
        f"\nThe caller has already declared their persona as '{persona}'. "
        f"Use it; do not infer a different one."
        if persona else
        "\nInfer the persona from how the question is phrased."
    )
    structured = llm.with_structured_output(
        AnalysisIntent, method="json_schema", include_raw=True
    )

    log.info("%s planning the analysis with %s", MODEL, getattr(llm, "model", "model"))
    started = time.monotonic()
    try:
        result = structured.invoke([
            SystemMessage(SYSTEM),
            HumanMessage("CATALOG\n" + json.dumps(catalog, indent=2, default=str)),
            HumanMessage(f"Question: {question}{persona_note}"),
        ])
    except Exception as exc:  # noqa: BLE001 - transport, API and timeout alike
        raise unavailable(exc, started) from exc
    log.info("%s plan returned in %.1fs", MODEL, time.monotonic() - started)

    usage.record("AnalysisIntent", getattr(result["raw"], "usage_metadata", None))
    intent = result["parsed"]
    if intent is None:
        raise LlmUnavailable(
            f"The plan did not validate against AnalysisIntent: "
            f"{result.get('parsing_error') or 'no content returned'}."
        )
    if persona:
        intent = intent.model_copy(update={"persona": persona})
    return intent


def default_intent(
    question: str,
    persona: str,
    sources: list[tuple[SourceSpec, KpiContract, DataProfile]],
    time_grain: str = "week",
    entity_keys: list[str] | None = None,
) -> AnalysisIntent:
    """The plan used when no model is available.

    Deliberately broad -- every KPI in every source, one sensible slice -- because
    without a model to narrow the question, narrowing it ourselves would be a guess
    dressed as a plan. A wide sweep at a sane grain is honest about knowing less.

    This is also where an absent question lands, and the two are the same plan for
    the same reason: nothing here can narrow the analysis, so the analysis is not
    narrowed. Only the restatement differs -- echoing an empty string back leaves
    the reader with a blank line where the run's own account of itself should be.
    """
    return AnalysisIntent(
        kpis=[],
        entity_keys=entity_keys if entity_keys is not None else ["Region"],
        time_grain=time_grain,
        sources=[spec.source_id for spec, _, _ in sources],
        persona=persona,  # type: ignore[arg-type]
        question_restated=question.strip() or (
            "No question was asked, so this is a broad sweep of every KPI for "
            "whatever needs attention."
        ),
        reasoning=(
            "No model available; running a broad sweep at the default grain. Any "
            "context stated in the question was not parsed, because parsing it is "
            "the one thing here that needs a model."
        ),
    )


def validate_intent(
    intent: AnalysisIntent,
    sources: list[tuple[SourceSpec, KpiContract, DataProfile]],
    *,
    min_train_periods: int | None = None,
    coverage_days: int | None = None,
    overrides: dict[str, Any] | None = None,
) -> tuple[AnalysisIntent | None, list[str]]:
    """Check the plan against the catalog. Returns (resolved intent, problems).

    Resolution is not merely validation: unknown fields are *dropped* where dropping
    them still leaves a runnable analysis, and only rejected where it does not. An
    intent naming three real KPIs and one imaginary one should run the three, not
    stop to ask about the fourth.

    `min_train_periods` is the detector's baseline training requirement, counted in
    periods, and `coverage_days` the span the data covers. Together they are what
    makes the grain check possible: see `_resolve_grain`.
    `overrides` carries explicit CLI choices, which win over the model's proposal --
    a demo has to be reproducible, and the planner is not deterministic.
    """
    problems: list[str] = []
    overrides = overrides or {}
    by_id = {spec.source_id: (spec, contract, profile) for spec, contract, profile in sources}

    # An explicit CLI choice replaces the model's, and is then validated on the same
    # terms -- an override is a different proposal, not an exemption from checking.
    forced = {k: v for k, v in overrides.items() if v is not None}
    if forced:
        intent = intent.model_copy(update=forced)
        for key, value in forced.items():
            problems.append(f"{key} forced to {value!r} by the caller.")

    requested_sources = intent.sources or list(by_id)
    known_sources = [s for s in requested_sources if s in by_id]
    unknown_sources = [s for s in requested_sources if s not in by_id]
    if unknown_sources:
        problems.append(
            f"Unknown source id(s): {', '.join(unknown_sources)}. "
            f"Available: {', '.join(by_id)}."
        )
    if not known_sources:
        return None, problems or ["No usable source was named."]

    # Entitlement is checked before existence, and the order matters: a restricted
    # column is not absent, it is withheld. Falling through to the "no such
    # dimension" branch would tell the caller something untrue about the data, and
    # a refusal that quietly omits the withheld part is worse than one that says so.
    restricted = {
        c for sid in known_sources for c in by_id[sid][1].restricted_columns
    }
    named = {*intent.kpis, *intent.entity_keys}
    hit = named & restricted
    if hit:
        return None, problems + [
            f"The request names restricted column(s): {', '.join(sorted(hit))}. "
            "These are withheld by the KPI contract's entitlement rules."
        ]

    # KPIs -- resolved per source, since the two contracts have disjoint KPI sets.
    all_kpis = {
        k.name for sid in known_sources for k in by_id[sid][1].kpis
    }
    kept_kpis = [k for k in intent.kpis if k in all_kpis]
    dropped_kpis = [k for k in intent.kpis if k not in all_kpis]
    if dropped_kpis:
        problems.append(
            f"Unknown KPI(s), ignored: {', '.join(dropped_kpis)}. "
            f"Available: {', '.join(sorted(all_kpis))}."
        )
    if intent.kpis and not kept_kpis:
        return None, problems + [
            "None of the requested KPIs exist in the catalog, so there is nothing to compute."
        ]

    # Entity keys must exist in at least one named source; a key that exists in only
    # one is fine, because each source is run against its own spec.
    all_entity_cols = {c for sid in known_sources for c in by_id[sid][0].entity_columns}
    kept_keys = [k for k in intent.entity_keys if k in all_entity_cols]
    dropped_keys = [k for k in intent.entity_keys if k not in all_entity_cols]
    if dropped_keys:
        problems.append(
            f"Unknown entity column(s), ignored: {', '.join(dropped_keys)}. "
            f"Available: {', '.join(sorted(all_entity_cols))}."
        )
    if intent.entity_keys and not kept_keys:
        return None, problems + [
            "None of the requested dimensions exist in the data. "
            f"Available: {', '.join(sorted(all_entity_cols))}."
        ]

    # Dates must land inside coverage, or there is nothing to read.
    start, end, date_problems = _resolve_dates(intent, [by_id[s][2] for s in known_sources])
    problems.extend(date_problems)
    if date_problems and start is None and end is None and (intent.date_start or intent.date_end):
        return None, problems

    # Grain is checked last, because it is the only field whose validity depends on
    # which sources survived the checks above.
    grain = _resolve_grain(
        intent.time_grain,
        coverage_days,
        min_train_periods,
        problems,
        forced="time_grain" in forced,
    )

    # The user's own context, checked on the same terms as everything else and
    # never allowed to narrow the analysis. A stated window is not a report
    # window: filtering to the heatwave would hide the alternative explanations
    # the reader needs in order to judge the heatwave.
    factors = _resolve_exogenous(intent.exogenous, all_entity_cols, all_kpis, problems)

    resolved = intent.model_copy(
        update={
            "kpis": kept_kpis,
            "entity_keys": kept_keys,
            "sources": known_sources,
            "time_grain": grain,
            "date_start": str(start) if start else None,
            "date_end": str(end) if end else None,
            "exogenous": factors,
        }
    )
    return resolved, problems


def _resolve_exogenous(
    factors: list[ExogenousFactor],
    entity_columns: set[str],
    kpi_names: set[str],
    problems: list[str],
) -> list[ExogenousFactor]:
    """Drop what does not resolve, keep what does, and never reject the run over it.

    A factor is the user's word, not a request, so an unresolvable part of one is
    a reason to narrow the claim rather than to stop and ask. The one thing that
    must not happen is silence: every clearance is recorded, because a factor the
    reader believes was taken into account and was not is worse than one plainly
    refused.

    `entity_value` is deliberately *not* checked here -- nothing in a
    `DataProfile` carries a column's distinct values, and inventing a second
    source of truth for them would be worse than the alternative. A value the
    data does not hold simply aligns to no event, and is reported as unaligned.
    """
    resolved: list[ExogenousFactor] = []
    for factor in factors:
        if not factor.label.strip() and not factor.detail.strip():
            continue

        update: dict[str, object] = {}

        if factor.entity_key and factor.entity_key not in entity_columns:
            problems.append(
                f"Context '{factor.label}' was scoped to '{factor.entity_key}', which "
                f"is not a dimension in the data; it is kept, unscoped. "
                f"Available: {', '.join(sorted(entity_columns))}."
            )
            update["entity_key"] = None
            update["entity_value"] = None

        dropped_kpis = [k for k in factor.affects_kpis if k not in kpi_names]
        if dropped_kpis:
            problems.append(
                f"Context '{factor.label}' named unknown KPI(s), ignored: "
                f"{', '.join(dropped_kpis)}."
            )
            update["affects_kpis"] = [k for k in factor.affects_kpis if k in kpi_names]

        for field in ("date_start", "date_end"):
            raw = getattr(factor, field)
            if raw and _parse_date(raw, f"Context '{factor.label}' {field}", problems) is None:
                update[field] = None

        resolved.append(factor.model_copy(update=update) if update else factor)
    return resolved


# Days per period, for turning a coverage span into a period count. Approximate for
# month by design: the check is "is this off by a factor", not "is this off by one".
_PERIOD_DAYS = {"day": 1, "week": 7, "month": 30.44}
_FINER = {"month": "week", "week": "day"}


def _resolve_grain(
    grain: str,
    coverage_days: int | None,
    min_train_periods: int | None,
    problems: list[str],
    forced: bool = False,
) -> str:
    """Downgrade a grain the data cannot support, and say so.

    `detection.baseline.min_train_periods` is counted in PERIODS, not days, and is
    never reached when the grain is coarse enough that the whole history holds fewer
    of them. The detector then emits no expectation, finds nothing, and the report
    says the quarter was quiet -- a wrong answer wearing the shape of a right one.
    Rather than let that happen silently, step down to the finest grain that clears
    the bar and record why.

    A caller who asked for the grain explicitly gets a warning instead of a
    downgrade: overriding the planner is the point of the flag, and silently
    ignoring the override would be the same class of lie.
    """
    if not min_train_periods or not coverage_days:
        return grain

    days = coverage_days
    candidate = grain
    while True:
        periods = days / _PERIOD_DAYS[candidate]
        if periods >= min_train_periods:
            break
        finer = _FINER.get(candidate)
        if finer is None:
            break
        if forced:
            problems.append(
                f"At {candidate} grain the data holds about {periods:.0f} periods, "
                f"below the detector's {min_train_periods}-period training "
                f"requirement. It was requested explicitly, so it stands -- expect "
                f"few or no events."
            )
            return candidate
        candidate = finer

    if candidate != grain:
        problems.append(
            f"Grain lowered from {grain} to {candidate}: the data spans {days} days, "
            f"which is only about {days / _PERIOD_DAYS[grain]:.0f} {grain} periods "
            f"against the detector's {min_train_periods}-period training "
            f"requirement, so no baseline would ever be emitted."
        )
    return candidate


def _resolve_dates(
    intent: AnalysisIntent, profiles: list[DataProfile]
) -> tuple[dt.date | None, dt.date | None, list[str]]:
    maxes = [p.freshness.max_date for p in profiles if p.freshness.max_date]
    coverage_end = max(maxes) if maxes else None

    problems: list[str] = []
    start = _parse_date(intent.date_start, "date_start", problems)
    end = _parse_date(intent.date_end, "date_end", problems)

    if start and end and start > end:
        problems.append(f"date_start ({start}) is after date_end ({end}).")
        return None, None, problems
    if coverage_end and start and start > coverage_end:
        problems.append(
            f"The requested period starts at {start}, after the data ends "
            f"({coverage_end}). There is nothing to analyse there."
        )
        return None, None, problems
    return start, end, problems


def _parse_date(value: str | None, label: str, problems: list[str]) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        problems.append(f"{label} '{value}' is not an ISO date; ignored.")
        return None
