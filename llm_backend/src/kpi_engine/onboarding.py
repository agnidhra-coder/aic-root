"""Turning a file into a company's contract: propose, decide, write.

The deterministic half of onboarding. Everything here runs without a model, and
the no-LLM path is not a degraded version of this module -- it *is* this module,
with the LLM's extra bindings simply absent. `kpi_agent.kpi_plan` adds proposals
on top; it never replaces anything decided here.

Three phases, in order:

1. **Propose.** `deterministic_plan` reads the staged file's profile, binds what
   the catalogue's synonyms recognise outright, and reports the rest -- including
   every KPI it could *not* compute and why, because a shopping list is more
   useful than a silence.
2. **Decide.** `merge_confirmation` applies the user's verdicts to a stored draft.
   Every override is re-validated against the file's header; a decision is a
   different proposal, never an exemption from checking.
3. **Write.** `synthesise_*` build the contract, source spec and DAG in memory,
   and `write_company_configs` commits all four files or none of them.

The DAG is split deliberately. Its nodes and `deterministic` edges are a
mechanical consequence of the confirmed contract -- a measure feeds the KPI whose
expression names it, and nothing about that is a judgement call. Only `causal`
edges and lever ownership are ever proposed, and `synthesise_graph` accepts them
as an argument so this module stays free of the model that produced them.
"""

from __future__ import annotations

import datetime as dt
import logging
import shutil
import warnings
from pathlib import Path
from typing import Iterable

import pandas as pd

from kpi_engine.catalogue import (
    bind_by_synonym,
    bind_variant,
    bindable_variants,
    load_catalog,
    match_column,
    select_variant,
)
from kpi_engine.causal.dag import CausalGraph
from kpi_engine.config_io import read_json, write_json, write_yaml
from kpi_engine.contracts.catalogue import SeedCatalog
from kpi_engine.contracts.configs import (
    CausalEdge,
    CausalGraphSpec,
    CausalNode,
    Guards,
    KpiContract,
    KpiDef,
    SourceSpec,
)
from kpi_engine.contracts.onboarding import (
    BoundMeasure,
    ColumnReport,
    ConfirmedPlan,
    KpiPlan,
    KpiProposal,
    PlanConfirmation,
    UnavailableKpi,
)
from kpi_engine.contracts.payloads import DataProfile
from kpi_engine.contracts.tenancy import AgentDefaults, CompanySpec
from kpi_engine.tenancy import (
    COMPANY_FILENAME,
    CompanyPaths,
    forget_company,
    open_company,
)

log = logging.getLogger("kpi_agent.onboard")

# Headers that name an observation date outright, checked before any parsing.
_DATE_NAMES = frozenset(
    {
        "date", "day", "orderdate", "transactiondate", "postingdate",
        "reportdate", "period", "periodstart", "week", "weekstart",
        "month", "monthstart", "timestamp", "datetime",
    }
)

# A dimension worth slicing by: enough distinct values to compare, few enough that
# each slice still holds rows. Beyond this an "entity" is really an identifier.
_MIN_DISTINCT = 2
_MAX_DISTINCT = 50
_MAX_DISTINCT_FRACTION = 0.2

# Days per period, for turning a coverage span into a period count. Approximate
# for month by design -- the question is "is this off by a factor", not "by one".
_PERIOD_DAYS = {"day": 1.0, "week": 7.0, "month": 30.44}


class OnboardingError(Exception):
    """A plan could not be proposed, merged, or written."""


class UnknownPlan(OnboardingError):
    """This company has no drafted plan at all."""


class StalePlan(OnboardingError):
    """A draft exists, but the caller is confirming a different one.

    Distinct from `UnknownPlan` because the two are different mistakes and want
    different answers: nothing to confirm is a missing resource, while confirming
    a superseded plan is a conflict -- someone re-proposed in between, and the
    decisions in hand were made against KPIs that may no longer be on offer.
    """


# --------------------------------------------------------------------------- inspecting


def read_header(path: Path, *, rows: int = 2000) -> tuple[list[str], pd.DataFrame]:
    """The staged file's header and a head, for date sniffing."""
    head = pd.read_csv(path, nrows=rows)
    return [str(c) for c in head.columns], head


def infer_date_column(header: list[str], head: pd.DataFrame) -> str | None:
    """The column holding the observation date.

    Names first, because a column called `Date` is a date whatever its dtype;
    then parseability, because a file may call it `Posting Period`. A source with
    no date column cannot become a panel at all, so returning None is a hard
    problem the caller must surface rather than paper over.
    """
    for column in header:
        if str(column).lower().replace(" ", "").replace("_", "") in _DATE_NAMES:
            return column
    for column in header:
        series = head[column].dropna()
        if series.empty or pd.api.types.is_numeric_dtype(series):
            continue
        # Sniffing is deliberately format-agnostic: the whole question is whether
        # this column reads as dates at all, so pandas warning that it could not
        # infer one format is the expected case rather than a problem.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            parsed = pd.to_datetime(series, errors="coerce")
        if parsed.notna().mean() >= 0.95:
            return column
    return None


def infer_entity_columns(
    header: list[str], head: pd.DataFrame, date_column: str | None
) -> list[str]:
    """Dimensions the panel could be sliced by.

    Low-cardinality, non-numeric, not the date. A column with one value cannot
    separate anything and a column with a value per row is an identifier, not a
    dimension -- both would produce slices the detector can say nothing about.
    """
    out: list[str] = []
    n = max(len(head), 1)
    for column in header:
        if column == date_column:
            continue
        series = head[column].dropna()
        if series.empty or pd.api.types.is_numeric_dtype(series):
            continue
        distinct = series.nunique()
        if _MIN_DISTINCT <= distinct <= _MAX_DISTINCT and distinct < n * _MAX_DISTINCT_FRACTION:
            out.append(column)
    return out


def infer_schema(staged: Path) -> tuple[list[str], pd.DataFrame, str | None, list[str]]:
    """The file's header, a head, and which columns are the date and dimensions.

    Run *before* profiling, not after, because profiling itself needs the answer:
    `sources.base._postprocess` parses the spec's `date_column`, and a blank
    template declares `Date` while the file may well call it `Txn Day`. Profiling
    against the template's guess raises `KeyError` on a file that is perfectly
    valid -- which is the ordering problem onboarding exists to solve, showing up
    one layer further down.
    """
    header, head = read_header(staged)
    date_column = infer_date_column(header, head)
    return header, head, date_column, infer_entity_columns(header, head, date_column)


def numeric_columns(profile: DataProfile) -> set[str]:
    return {c.name for c in profile.columns if _is_numeric_dtype(c.dtype)}


def _is_numeric_dtype(dtype: str) -> bool:
    return any(t in dtype.lower() for t in ("int", "float", "decimal"))


def choose_grain(
    coverage_days: int | None, min_train_periods: int | None, *, prefer: str = "week"
) -> tuple[str, str | None]:
    """The coarsest grain the history can still train a baseline on.

    `min_train_periods` counts PERIODS, not days, so a coarse grain over a short
    file never reaches it: the detector emits no expectation, finds nothing, and
    the report says the period was quiet. That is a wrong answer wearing the shape
    of a right one, which is why the arithmetic is done here rather than left to
    whoever picks a default. Returns the grain and an explanation when it moved.
    """
    if not coverage_days or not min_train_periods:
        return prefer, None
    if coverage_days / _PERIOD_DAYS[prefer] >= min_train_periods:
        return prefer, None
    candidate = prefer
    while candidate != "day":
        candidate = "day" if candidate == "week" else "week"
        if coverage_days / _PERIOD_DAYS[candidate] >= min_train_periods:
            return candidate, (
                f"Grain set to {candidate} rather than {prefer}: the file spans "
                f"{coverage_days} days, only about "
                f"{coverage_days / _PERIOD_DAYS[prefer]:.0f} {prefer} periods "
                f"against the detector's {min_train_periods}-period training "
                f"requirement."
            )
        if candidate == "day":
            break
    return "day", (
        f"Even at day grain the file holds about {coverage_days} periods against "
        f"the detector's {min_train_periods}-period training requirement. Expect "
        f"few or no events until more history arrives."
    )


def tune_guards(seed: Guards, profile: DataProfile, entity_keys: list[str]) -> Guards:
    """Soften a support guard the file cannot meet, and say nothing else.

    The catalogue's `min_rows_per_cell: 3` is right for a dense extract and blanks
    the panel of a sparse one. The coverage figures already say how many rows a
    slice actually holds, so this is arithmetic over the profile rather than a
    guess -- and it only ever *lowers* the bar, never raises it past what the
    catalogue asked for.
    """
    for cov in profile.coverage:
        if list(cov.keys) == list(entity_keys):
            floor = max(1, min(seed.min_rows_per_cell, int(cov.mean_rows_per_cell)))
            if floor != seed.min_rows_per_cell:
                return seed.model_copy(update={"min_rows_per_cell": floor})
            break
    return seed


# --------------------------------------------------------------------------- proposing


def deterministic_plan(
    paths: CompanyPaths,
    source_id: str,
    *,
    plan_id: str,
    staged: Path,
    profile: DataProfile,
    catalog: SeedCatalog | None = None,
    min_train_periods: int | None = None,
    frame: pd.DataFrame | None = None,
) -> KpiPlan:
    """Everything the catalogue recognises in this file, with no model involved.

    This is the whole `--no-llm` answer and the seed the model is shown. On a
    well-named extract it binds most of the vocabulary; what it binds, it binds by
    exact name, so it is never wrong about a column even when it is silent about
    one.
    """
    catalog = catalog or load_catalog()
    header, head, date_column, entity_columns = infer_schema(staged)
    problems: list[str] = []

    if date_column is None:
        problems.append(
            "No column in this file reads as an observation date. Every KPI is a "
            "series over time, so one must be named before a plan can be confirmed."
        )

    span = coverage_days(frame if frame is not None else head, date_column)
    grain, grain_note = choose_grain(span, min_train_periods)
    if grain_note:
        problems.append(grain_note)

    numeric = numeric_columns(profile)
    proposed: list[KpiProposal] = []
    unavailable: list[UnavailableKpi] = []

    for seed in catalog.kpis:
        hit = select_variant(seed, header)
        alternatives = [v for v in bindable_variants(seed, header)]
        if hit is None:
            unavailable.append(_unavailable(seed, header, alternatives))
            continue
        variant, bound = hit
        # A non-numeric measure column cannot be aggregated at all, so it blocks
        # the recommendation. Redundancy does not: an exact identity bars a column
        # from acting as an *independent driver* in the DAG, which is a statement
        # about attribution, not about whether the KPI can be computed. Saying so
        # is useful; refusing the KPI over it would be wrong.
        blocking = [
            f"{column!r} is not numeric in this file, so it cannot be aggregated"
            for column in bound.values()
            if column not in numeric
        ]
        caveats = blocking + [
            f"{column!r} is barred as an independent driver: "
            f"{profile.redundant_columns[column]}"
            for column in bound.values()
            if column in profile.redundant_columns
        ]
        proposed.append(
            KpiProposal(
                name=seed.name,
                variant_id=variant.variant_id,
                category=seed.category,
                unit=seed.unit,
                direction=seed.direction,
                description=seed.description,
                expression=variant.expression,
                doc_formula=seed.doc_formula,
                drivers=list(seed.drivers),
                primary_metric=seed.primary_metric,
                measures=[
                    BoundMeasure(
                        alias=m.alias,
                        column=bound[m.alias],
                        agg=m.agg,
                        bound_by="synonym",
                    )
                    for m in variant.measures
                ],
                materiality=seed.materiality,
                guards=tune_guards(seed.guards, profile, entity_columns[:1]),
                computable=True,
                aggregation_safe=variant.aggregation_safe,
                recommended=variant.aggregation_safe and not blocking,
                caveats=caveats,
                alternatives=[v for v in alternatives if v != variant.variant_id],
            )
        )

    plan = KpiPlan(
        plan_id=plan_id,
        company_id=paths.slug,
        source_id=source_id,
        catalog_id=catalog.catalog_id,
        generated_at=dt.datetime.now(),
        generated_by="deterministic",
        staged_csv=_relative(paths, staged),
        header=header,
        n_rows=profile.description.n_rows,
        date_column=date_column,
        entity_columns=entity_columns,
        time_grain=grain,  # type: ignore[arg-type]
        proposed=proposed,
        unavailable=unavailable,
        problems=problems,
    )
    return with_column_report(plan, profile)


def _unavailable(seed, header: list[str], alternatives: list[str]) -> UnavailableKpi:
    """Why a KPI could not be computed, in terms of what was looked for."""
    variant = seed.variants[0]
    bound = bind_by_synonym(variant, header)
    missing = [a for a in variant.aliases if a not in bound]
    if alternatives:
        reason = (
            f"Only variant(s) {alternatives} bind, and they do not survive "
            "aggregation to a reporting grain. Available on request."
        )
    else:
        reason = (
            f"No column names {', '.join(missing)}."
            if missing
            else "No variant of this KPI binds against this file."
        )
    return UnavailableKpi(
        name=seed.name,
        doc_formula=seed.doc_formula,
        missing_aliases=missing,
        tried_synonyms=sorted(
            {s for a in missing for s in variant.measure(a).synonyms}
        ),
        reason=reason,
    )


def with_column_report(plan: KpiPlan, profile: DataProfile) -> KpiPlan:
    """What became of every column, so nothing is silently ignored."""
    by_name = {c.name: c for c in profile.columns}
    used: dict[str, list[str]] = {}
    for proposal in plan.proposed:
        for measure in proposal.measures:
            if measure.column:
                used.setdefault(measure.column, []).append(proposal.name)

    reports: list[ColumnReport] = []
    for column in plan.header:
        stats = by_name.get(column)
        if column == plan.date_column:
            role = "date"
        elif column in plan.entity_columns:
            role = "entity"
        elif column in used:
            role = "measure"
        elif column in profile.redundant_columns:
            role = "redundant"
        else:
            role = "unused"
        reports.append(
            ColumnReport(
                name=column,
                dtype=stats.dtype if stats else "unknown",
                numeric=_is_numeric_dtype(stats.dtype) if stats else False,
                nulls=stats.nulls if stats else 0,
                role=role,  # type: ignore[arg-type]
                used_by=sorted(used.get(column, [])),
                redundant_because=profile.redundant_columns.get(column, ""),
            )
        )
    return plan.model_copy(update={"columns": reports})


def coverage_days(frame: pd.DataFrame, date_column: str | None) -> int | None:
    """How many days the file spans.

    Computed here rather than read from the profile because `DataProfile` records
    the latest date and not the earliest -- it exists to answer "is this stale",
    not "is there enough history to train on". The span is what decides the grain,
    so it is worth the one pass.
    """
    if not date_column or date_column not in frame.columns:
        return None
    dates = pd.to_datetime(frame[date_column], errors="coerce").dropna()
    if dates.empty:
        return None
    return int((dates.max() - dates.min()).days)


# --------------------------------------------------------------------------- deciding


def save_draft(paths: CompanyPaths, plan: KpiPlan) -> Path:
    return write_json(plan, paths.draft_plan_path())


def load_draft(paths: CompanyPaths) -> KpiPlan:
    path = paths.draft_plan_path()
    if not path.exists():
        raise UnknownPlan(
            f"Company '{paths.slug}' has no drafted KPI plan. "
            f"POST /companies/{paths.slug}/kpi-plan with a CSV first."
        )
    return KpiPlan.model_validate(read_json(path))


def merge_confirmation(
    plan: KpiPlan, confirmation: PlanConfirmation, *, catalog: SeedCatalog | None = None
) -> tuple[KpiPlan, list[str]]:
    """Apply the user's verdicts to a draft, re-validating every override.

    A decision is a different proposal, not an exemption from checking -- the same
    rule `validate_intent` applies to a CLI override. An override naming a column
    the file does not carry is dropped with a problem, never silently accepted.
    """
    if confirmation.plan_id != plan.plan_id:
        raise StalePlan(
            f"Plan id {confirmation.plan_id!r} is not the current draft "
            f"({plan.plan_id!r}). Re-read the draft and confirm against it, rather "
            "than merging a stale decision onto a plan that has since changed."
        )
    catalog = catalog or load_catalog()
    problems: list[str] = []
    verdicts = {d.name: d for d in confirmation.decisions}

    unknown = sorted(set(verdicts) - {p.name for p in plan.proposed})
    if unknown:
        problems.append(
            f"Decision(s) for KPI(s) this plan never proposed, ignored: {unknown}."
        )

    kept: list[KpiProposal] = []
    for proposal in plan.proposed:
        decision = verdicts.get(proposal.name)
        if decision is None:
            # Silence keeps the recommendation, which is what makes an empty
            # `decisions` list mean "take what you proposed" rather than "take
            # nothing".
            if proposal.recommended:
                kept.append(proposal)
            continue
        if decision.verdict == "reject":
            continue
        merged, issues = _apply_decision(proposal, decision, plan.header, catalog)
        problems.extend(issues)
        if merged is not None:
            kept.append(merged)

    date_column = confirmation.date_column or plan.date_column
    if date_column is not None and date_column not in plan.header:
        problems.append(
            f"date_column {date_column!r} is not in this file; keeping "
            f"{plan.date_column!r}."
        )
        date_column = plan.date_column

    entity_columns = (
        plan.entity_columns
        if confirmation.entity_columns is None
        else list(confirmation.entity_columns)
    )
    absent = [c for c in entity_columns if c not in plan.header]
    if absent:
        problems.append(f"Entity column(s) not in this file, dropped: {absent}.")
        entity_columns = [c for c in entity_columns if c in plan.header]

    merged_plan = plan.model_copy(
        update={
            "proposed": kept,
            "date_column": date_column,
            "entity_columns": entity_columns,
            "time_grain": confirmation.time_grain or plan.time_grain,
            "problems": [*plan.problems, *problems],
        }
    )
    return merged_plan, problems


def _apply_decision(
    proposal: KpiProposal, decision, header: list[str], catalog: SeedCatalog
) -> tuple[KpiProposal | None, list[str]]:
    """One accepted KPI, with any variant switch and binding overrides applied."""
    problems: list[str] = []
    seed = catalog.kpi(proposal.name)

    variant_id = decision.variant_id or proposal.variant_id
    try:
        variant = seed.variant(variant_id)
    except KeyError as exc:
        problems.append(f"{exc}. Keeping {proposal.variant_id!r}.")
        variant = seed.variant(proposal.variant_id)

    columns = {m.alias: m.column for m in proposal.measures if m.column}
    sources = {m.alias: m.bound_by for m in proposal.measures}
    if variant.variant_id != proposal.variant_id:
        # A different variant has different aliases, so start from what its own
        # synonyms recognise rather than carrying the old bindings across.
        columns = bind_by_synonym(variant, header)
        sources = dict.fromkeys(columns, "synonym")

    for alias, column in (decision.bindings or {}).items():
        if alias not in variant.aliases:
            problems.append(
                f"KPI '{proposal.name}' variant '{variant.variant_id}' has no alias "
                f"{alias!r}; binding ignored."
            )
            continue
        if column not in header:
            problems.append(
                f"KPI '{proposal.name}': column {column!r} is not in this file; "
                f"binding for {alias!r} ignored."
            )
            continue
        columns[alias] = column
        sources[alias] = "user"

    missing = [a for a in variant.aliases if a not in columns]
    if missing:
        problems.append(
            f"KPI '{proposal.name}' was accepted but has no column for "
            f"{missing}; it cannot be computed and was dropped."
        )
        return None, problems

    return (
        proposal.model_copy(
            update={
                "variant_id": variant.variant_id,
                "expression": variant.expression,
                "aggregation_safe": variant.aggregation_safe,
                "measures": [
                    BoundMeasure(
                        alias=m.alias,
                        column=columns[m.alias],
                        agg=m.agg,
                        bound_by=sources.get(m.alias, "user"),  # type: ignore[arg-type]
                    )
                    for m in variant.measures
                ],
                "materiality": decision.materiality or proposal.materiality,
                "guards": decision.guards or proposal.guards,
                "computable": True,
            }
        ),
        problems,
    )


# --------------------------------------------------------------------------- synthesis


def synthesise_contract(
    plan: KpiPlan, *, contract_id: str, catalog: SeedCatalog | None = None
) -> KpiContract:
    """The tenant's KPI contract, built from the catalogue and the confirmed bindings.

    Every field except the columns comes from the catalogue, so two tenants that
    accept Gross Profit Margin are measuring the same quantity.
    """
    catalog = catalog or load_catalog()
    kpis: list[KpiDef] = []
    for proposal in plan.proposed:
        seed = catalog.kpi(proposal.name)
        variant = seed.variant(proposal.variant_id)
        definition = bind_variant(
            seed, variant, {m.alias: m.column for m in proposal.measures if m.column}
        )
        kpis.append(
            definition.model_copy(
                update={
                    "materiality": proposal.materiality,
                    "guards": proposal.guards,
                }
            )
        )
    if not kpis:
        raise OnboardingError(
            "No KPI survived confirmation, so there is nothing to compute. A "
            "company with an empty contract would answer every question with "
            "silence, which is worse than refusing to be configured."
        )
    return KpiContract(
        contract_id=contract_id,
        source_id=plan.source_id,
        time_grain=plan.time_grain,
        entity_keys=[],  # the panel is sliced per run; the contract stays total-level
        kpis=kpis,
    )


def synthesise_source_spec(base: SourceSpec, plan: KpiPlan) -> SourceSpec:
    """The source spec, with the date and dimensions this file actually has.

    `path` is deliberately left as the base declares it -- company-relative, and
    where `attach_source_data` will put the file. Rewriting it here would put an
    absolute path into a config that must stay relocatable.
    """
    if plan.date_column is None:
        raise OnboardingError(
            "This plan has no date column, so no source spec can be written. "
            "A KPI is a series over time; without a date there is no series."
        )
    return base.model_copy(
        update={
            "date_column": plan.date_column,
            "entity_columns": list(plan.entity_columns),
        }
    )


def synthesise_graph(
    contract: KpiContract,
    *,
    graph_id: str,
    catalog: SeedCatalog | None = None,
    causal_edges: Iterable[CausalEdge] = (),
    levers: dict[str, tuple[bool, str | None]] | None = None,
) -> tuple[CausalGraphSpec, list[str]]:
    """The governed DAG for a confirmed contract.

    Nodes and `deterministic` edges are derived, not proposed: a measure feeds the
    KPI whose expression names it, and that follows from the arithmetic rather
    than from anyone's opinion. A measure node is named by its column, so two KPIs
    built on `Total Revenue` share one node and an attribution about revenue
    reaches both.

    `forbidden_edges` is seeded with the reverse of every deterministic edge. A
    KPI cannot cause its own inputs, and saying so explicitly means a bad proposal
    is refused by name -- "you declared an edge you forbade" -- rather than
    surfacing later as an anonymous cycle.

    Returns the spec and the problems from rejecting proposed edges.
    """
    catalog = catalog or load_catalog()
    levers = levers or {}
    problems: list[str] = []

    kpi_names = [k.name for k in contract.kpis]
    nodes: list[CausalNode] = [
        CausalNode(kind="kpi", name=k.name, description=k.description)
        for k in contract.kpis
    ]

    columns: dict[str, str] = {}
    for kpi in contract.kpis:
        for alias, measure in kpi.measures.items():
            columns.setdefault(measure.column, alias)

    hints = _lever_hints(catalog)
    for column in sorted(columns):
        controllable, owner = levers.get(column, hints.get(column, (False, None)))
        nodes.append(
            CausalNode(
                kind="measure",
                name=column,
                column=column,
                controllable=controllable,
                owner=owner,
                description="",
            )
        )

    deterministic = [
        CausalEdge(source=m.column, target=kpi.name, relation="deterministic")
        for kpi in contract.kpis
        for m in kpi.measures.values()
    ]
    forbidden: list[tuple[str, str]] = [(e.target, e.source) for e in deterministic]

    known = {n.name for n in nodes}
    declared = {(e.source, e.target) for e in deterministic}
    accepted: list[CausalEdge] = []
    for edge in causal_edges:
        problem = _reject_edge(edge, known, kpi_names, declared, forbidden)
        if problem:
            problems.append(problem)
            continue
        accepted.append(edge)
        declared.add((edge.source, edge.target))
        forbidden.append((edge.target, edge.source))

    spec = CausalGraphSpec(
        graph_id=graph_id,
        nodes=nodes,
        edges=[*deterministic, *accepted],
        forbidden_edges=forbidden,
    )
    spec, cycle_problems = _break_cycles(spec, len(deterministic))
    return spec, [*problems, *cycle_problems]


def _reject_edge(
    edge: CausalEdge,
    known: set[str],
    kpi_names: list[str],
    declared: set[tuple[str, str]],
    forbidden: list[tuple[str, str]],
) -> str | None:
    """Why a proposed causal edge may not be added, or None."""
    missing = {edge.source, edge.target} - known
    if missing:
        return f"Edge {edge.source} -> {edge.target} names unknown node(s) {sorted(missing)}; dropped."
    if edge.source == edge.target:
        return f"Edge {edge.source} -> itself is not a mechanism; dropped."
    if edge.target in kpi_names:
        # A KPI's parents are fixed by its expression, and the algebraic layer
        # already attributes a movement to them exactly. A causal edge into the
        # KPI would have the router estimate what algebra already knows.
        return (
            f"Edge {edge.source} -> {edge.target} points at a KPI, whose parents are "
            "fixed by its formula; dropped so estimation cannot double-count exact "
            "algebra."
        )
    if (edge.source, edge.target) in declared:
        return f"Edge {edge.source} -> {edge.target} is already declared; dropped."
    if (edge.source, edge.target) in set(forbidden):
        return f"Edge {edge.source} -> {edge.target} reverses a declared edge; dropped."
    return None


def _break_cycles(spec: CausalGraphSpec, keep: int) -> tuple[CausalGraphSpec, list[str]]:
    """Drop proposed edges until the graph is acyclic, never a derived one.

    `keep` is the count of deterministic edges at the head of the list; they are
    a consequence of the contract's arithmetic and cannot themselves form a cycle,
    so only what follows them is ever removed.
    """
    import networkx as nx

    problems: list[str] = []
    edges = list(spec.edges)
    for _ in range(20):
        graph = nx.DiGraph()
        graph.add_nodes_from(n.name for n in spec.nodes)
        graph.add_edges_from((e.source, e.target) for e in edges)
        if nx.is_directed_acyclic_graph(graph):
            break
        cycle = nx.find_cycle(graph)
        for source, target in reversed(cycle):
            index = next(
                (
                    i
                    for i, e in enumerate(edges)
                    if i >= keep and e.source == source and e.target == target
                ),
                None,
            )
            if index is not None:
                problems.append(
                    f"Edge {source} -> {target} closes a cycle {cycle}; dropped."
                )
                edges.pop(index)
                break
        else:
            problems.append(
                f"A cycle {cycle} remains among derived edges and cannot be broken."
            )
            break
    return spec.model_copy(update={"edges": edges}), problems


def _lever_hints(catalog: SeedCatalog) -> dict[str, tuple[bool, str | None]]:
    """Ownership the catalogue already knows, keyed by synonym.

    Domain knowledge written once in git, which is what gives the no-LLM DAG
    levers with named owners rather than an attribution nobody can act on.
    """
    hints: dict[str, tuple[bool, str | None]] = {}
    for seed in catalog.kpis:
        for variant in seed.variants:
            for measure in variant.measures:
                if not measure.controllable_hint:
                    continue
                for synonym in measure.synonyms:
                    hints.setdefault(synonym, (True, measure.owner_hint))
    return hints


def resolve_levers(
    catalog: SeedCatalog, columns: Iterable[str]
) -> dict[str, tuple[bool, str | None]]:
    """Lever hints matched onto the columns a tenant actually has."""
    hints = _lever_hints(catalog)
    out: dict[str, tuple[bool, str | None]] = {}
    for column in columns:
        hit = match_column([column], list(hints))
        if hit is not None:
            out[column] = hints[hit]
    return out


def check_header(contract: KpiContract, spec: SourceSpec, header: list[str]) -> list[str]:
    """Every column the new contract needs, checked before anything is written."""
    needed = {spec.date_column, *spec.entity_columns}
    for kpi in contract.kpis:
        needed.update(m.column for m in kpi.measures.values())
    missing = sorted(c for c in needed if c not in header)
    return (
        [
            f"The synthesised contract names {len(missing)} column(s) the file does "
            f"not carry: {missing}. This is a binder bug, not a user error."
        ]
        if missing
        else []
    )


# --------------------------------------------------------------------------- writing


def write_company_configs(
    paths: CompanyPaths,
    *,
    contract: KpiContract,
    source_spec: SourceSpec,
    graph: CausalGraphSpec,
    agent: AgentDefaults,
    plan_id: str,
) -> tuple[CompanyPaths, list[str], Path]:
    """Commit a confirmed plan: all four files, or none of them.

    Everything is validated in memory before a byte is written -- including
    constructing the `CausalGraph`, which is what rejects a forbidden edge or a
    cycle. That ordering is what makes "the DAG failed after the contract was
    written" almost unreachable; the archive-and-restore below covers the I/O
    failure that remains.

    `company.yaml` is rewritten last and unconditionally, and
    `forget_company` runs in a `finally`. `open_company` memoises on
    `company.yaml`'s mtime alone, so rewriting a semantics file without both would
    serve a stale `CompanyPaths` carrying a stale parsed contract -- the tenant
    would keep answering from the KPIs it had before the user changed them.
    """
    CausalGraph(graph).validate()  # raises on a forbidden edge or a cycle

    binding = paths.spec.binding(contract.source_id)
    targets = {
        "source": paths.resolve(binding.source),
        "contract": paths.resolve(binding.contract),
        "graph": paths.resolve(paths.spec.configs.graph),
        "company": paths.root / COMPANY_FILENAME,
    }
    spec = paths.spec.model_copy(update={"agent": agent})
    spec = CompanySpec.model_validate(spec.model_dump(mode="json"))

    archive = paths.superseded_dir(plan_id)
    archive.mkdir(parents=True, exist_ok=True)
    for name, path in targets.items():
        if path.exists():
            shutil.copy2(path, archive / f"{name}-{path.name}")

    try:
        write_yaml(source_spec, targets["source"])
        write_yaml(contract, targets["contract"])
        write_yaml(graph, targets["graph"])
        write_yaml(spec, targets["company"])  # last: it is the memoisation key
    except BaseException:
        _restore(archive, targets)
        raise
    finally:
        forget_company(paths.slug)

    reopened = open_company(paths.slug)
    problems = reopened.validate()
    if problems:
        _restore(archive, targets)
        forget_company(paths.slug)
        raise OnboardingError(
            "The configuration written for "
            f"'{paths.slug}' does not validate and has been rolled back:\n  - "
            + "\n  - ".join(problems)
        )
    return reopened, [], archive


def _restore(archive: Path, targets: dict[str, Path]) -> None:
    for name, path in targets.items():
        saved = archive / f"{name}-{path.name}"
        if saved.exists():
            shutil.copy2(saved, path)


def save_confirmed(paths: CompanyPaths, confirmed: ConfirmedPlan) -> Path:
    return write_json(confirmed, paths.confirmed_plan_path())


def _relative(paths: CompanyPaths, path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(paths.root.resolve()))
    except ValueError:
        return str(path)


def default_agent_defaults(plan: KpiPlan, current: AgentDefaults) -> AgentDefaults:
    """The company's new defaults: the confirmed grain, and one slicing dimension.

    One entity key rather than all of them. A controlled comparison needs untreated
    slices to compare against, and slicing by every dimension at once leaves the
    estimator no control group and each cell too few rows to model.
    """
    return current.model_copy(
        update={
            "time_grain": plan.time_grain,
            "entity_keys": plan.entity_columns[:1],
        }
    )
