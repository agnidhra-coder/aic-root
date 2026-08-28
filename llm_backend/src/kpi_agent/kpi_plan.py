"""Binding a tenant's columns to the KPI vocabulary, and proposing its mechanisms.

Two model calls, both shaped exactly like `intent.plan_intent`: a frozen system
prompt, structured output via `method="json_schema"`, and a *separate*
deterministic resolver that decides whether what came back may be used. The model
proposes; ordinary Python decides. No model is ever consulted about whether the
model was right.

What is different here is how little the model is allowed to say. It does not
write expressions -- those are transcribed into `templates/reference/kpi_catalog.yaml`
and checked into git, because `validate_expression` can only confirm that a
formula string parses, and `revenue / revenue` parses perfectly well. It does not
choose units, categories, directions or thresholds. It does not name a KPI outside
the catalogue. It picks which column of *this* file holds the quantity an alias
names, and which mechanisms connect those columns -- two genuinely ambiguous
questions that a name-matcher cannot answer and a person would otherwise have to.

The deterministic matcher runs **first**, in both paths. Its bindings go to the
model as already settled, and `validate_bindings` gives them precedence when the
two disagree. That ordering is what stops a model overturning a column match that
was certain, and it is why `--no-llm` is a real answer rather than a stub: the
model extends the floor, it never replaces it.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from kpi_engine.catalogue import bind_by_synonym, normalise
from kpi_engine.contracts.catalogue import SeedCatalog
from kpi_engine.contracts.configs import CausalEdge, KpiContract
from kpi_engine.contracts.onboarding import KpiPlan
from kpi_engine.contracts.payloads import DataProfile

from kpi_agent.llm import MODEL, LlmUnavailable, Usage, unavailable
from kpi_agent.models import CausalProposal, KpiBindingProposal

log = logging.getLogger("kpi_agent.onboard")

BINDING_SYSTEM = """\
You map the columns of one company's data extract onto a fixed catalogue of KPIs.

You do not compute anything, you do not write formulas, and you do not decide what \
a KPI means. Every KPI's arithmetic, unit, direction and thresholds are already \
fixed in the catalogue. Your only job is to say which column of THIS file holds the \
quantity each measure alias names.

Rules:
- Every KPI name and every alias you emit must appear verbatim in the CATALOGUE. \
Every column you emit must appear verbatim in the FILE header. Do not invent, \
translate, pluralise, abbreviate or correct spelling.
- Bind an alias only when the column genuinely holds that quantity. Leaving an alias \
unbound costs one KPI; binding the wrong column corrupts every number that KPI ever \
produces, silently, because the arithmetic will still evaluate. When unsure, do not \
bind.
- Two aliases of the same KPI must never take the same column. An expression like \
`revenue - revenue` is always zero and always wrong.
- Bindings listed as ALREADY BOUND were matched by exact name and are settled. Do \
not restate them and do not contradict them.
- A measure must be a quantity that can be added up over a period. An identifier, a \
status, a name or a free-text note is never a measure, whatever it is called.
- Many catalogue KPIs will not be computable from this file. That is the expected \
outcome and not a failure. Say nothing about them; the engine reports them itself.
- `date_column` is the column holding the observation date -- when the row happened, \
not when it was loaded or exported.
- `entity_columns` are dimensions worth comparing across: region, channel, category, \
supplier. A column with one value cannot separate anything, and a column with a \
different value on every row is an identifier, not a dimension. Neither belongs here.
"""

CAUSAL_SYSTEM = """\
You declare the causal mechanisms between the measured quantities of one company.

The graph you contribute to governs what may later be offered as an explanation: a \
movement with no declared path to a driver is reported as unexplained rather than \
guessed at. An edge you invent becomes a claim the system will make on your behalf.

Rules:
- Propose edges only between nodes listed in NODES, named verbatim.
- Never point an edge at a KPI node. A KPI's parents are fixed by its formula and \
are already known exactly; adding a causal edge there would have an estimator \
re-derive what algebra computes exactly, and double-count it.
- Direction is a mechanism, not a correlation. Ad spend causes traffic; traffic does \
not cause ad spend. If both directions seem arguable, omit the edge -- a missing \
edge costs one explanation, a reversed edge produces a confident wrong one.
- Do not propose an edge that merely restates arithmetic. Cost feeding a total that \
is defined as a sum of costs is not a mechanism.
- The DRIVER HINTS come from the KPI documentation. They are business concepts, not \
column names. Map one onto a node or drop it; never emit a name that is not in NODES.
- Mark a node controllable only when a named team can set it directly within a \
period. Ad spend is a lever; visitor counts are context. An owner is a team \
("Procurement", "Growth Marketing"), never a person.
- Proposing nothing is a valid answer. The engine already has every deterministic \
edge it needs.
"""


# --------------------------------------------------------------------------- binding


def propose_bindings(
    llm: Any,
    usage: Usage,
    *,
    catalog_view: dict[str, Any],
    file_view: dict[str, Any],
    already_bound: list[dict[str, str]],
) -> KpiBindingProposal:
    """Model call: the catalogue and the file's schema in, column bindings out.

    The catalogue goes in the middle message and the file last, so the stable
    prefix stays stable across calls and implicit caching can hit -- the same
    ordering `plan_intent` uses. `include_raw=True` keeps both the token counts
    and a validation failure reachable.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    structured = llm.with_structured_output(
        KpiBindingProposal, method="json_schema", include_raw=True
    )
    log.info("%s matching %d columns to the KPI catalogue",
             MODEL, len(file_view.get("columns", [])))
    started = time.monotonic()
    try:
        result = structured.invoke([
            SystemMessage(BINDING_SYSTEM),
            HumanMessage("CATALOGUE\n" + json.dumps(catalog_view, indent=2, default=str)),
            HumanMessage(
                "FILE\n" + json.dumps(file_view, indent=2, default=str)
                + "\n\nALREADY BOUND (exact name matches; settled)\n"
                + json.dumps(already_bound, indent=2)
            ),
        ])
    except Exception as exc:  # noqa: BLE001 - transport, API and timeout alike
        raise unavailable(exc, started) from exc
    log.info("%s bindings returned in %.1fs", MODEL, time.monotonic() - started)

    usage.record("KpiBindingProposal", getattr(result["raw"], "usage_metadata", None))
    proposal = result["parsed"]
    if proposal is None:
        raise LlmUnavailable(
            "The binding proposal did not validate against KpiBindingProposal: "
            f"{result.get('parsing_error') or 'no content returned'}."
        )
    return proposal


def validate_bindings(
    proposal: KpiBindingProposal,
    *,
    catalog: SeedCatalog,
    header: list[str],
    profile: DataProfile,
    prebound: dict[str, dict[str, str]],
) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Decide which proposed bindings may be used. Resolve, do not merely reject.

    Returns `kpi -> {alias: column}` and the problems. A binding that fails is
    dropped and named; the rest survive, because a proposal naming three real
    columns and one imaginary one should bind the three.

    An exact synonym match outranks the model. Where the two disagree the match
    wins and the disagreement is recorded, so a reviewer sees it and can override
    it deliberately at confirm rather than discovering it in a number later.
    """
    problems: list[str] = []
    numeric = {c.name for c in profile.columns
               if any(t in c.dtype.lower() for t in ("int", "float", "decimal"))}
    by_norm = {normalise(c): c for c in header}
    out: dict[str, dict[str, str]] = {k: dict(v) for k, v in prebound.items()}

    for binding in proposal.bindings:
        try:
            seed = catalog.kpi(binding.kpi)
        except KeyError:
            problems.append(
                f"Binding names KPI {binding.kpi!r}, which is not in the catalogue; "
                "dropped. The catalogue is the whole vocabulary."
            )
            continue

        aliases = {a for v in seed.variants for a in v.aliases}
        if binding.alias not in aliases:
            problems.append(
                f"KPI '{seed.name}' has no alias {binding.alias!r}; dropped. "
                f"Known: {sorted(aliases)}."
            )
            continue

        column = by_norm.get(normalise(binding.column))
        if column is None:
            problems.append(
                f"KPI '{seed.name}' alias {binding.alias!r} names column "
                f"{binding.column!r}, which this file does not carry; dropped."
            )
            continue
        if column not in numeric:
            problems.append(
                f"KPI '{seed.name}' alias {binding.alias!r} names {column!r}, which "
                "is not numeric and cannot be aggregated; dropped."
            )
            continue

        settled = out.get(seed.name, {}).get(binding.alias)
        if settled is not None:
            if settled != column:
                problems.append(
                    f"KPI '{seed.name}' alias {binding.alias!r} was matched by name to "
                    f"{settled!r}; the model proposed {column!r}. Keeping the exact "
                    "match -- override it at confirm if it is wrong."
                )
            continue
        out.setdefault(seed.name, {})[binding.alias] = column

    # A column bound twice within one KPI makes its expression degenerate.
    for name, columns in list(out.items()):
        chosen = list(columns.values())
        dupes = sorted({c for c in chosen if chosen.count(c) > 1})
        if dupes:
            problems.append(
                f"KPI '{name}' would bind {dupes} to more than one alias, which makes "
                "its expression degenerate; the KPI was dropped."
            )
            out.pop(name)
    return out, problems


def default_entity_columns(
    proposal: KpiBindingProposal | None, header: list[str], fallback: list[str]
) -> tuple[str | None, list[str]]:
    """The model's date and entity choices, kept only where the file agrees."""
    if proposal is None:
        return None, fallback
    date_column = proposal.date_column if proposal.date_column in header else None
    entities = [c for c in proposal.entity_columns if c in header]
    return date_column, entities or fallback


# --------------------------------------------------------------------------- causal


def propose_causal(
    llm: Any,
    usage: Usage,
    *,
    nodes: list[dict[str, Any]],
    kpis: list[dict[str, Any]],
    driver_hints: dict[str, list[str]],
) -> CausalProposal:
    """Model call: this tenant's measure nodes in, causal mechanisms out."""
    from langchain_core.messages import HumanMessage, SystemMessage

    structured = llm.with_structured_output(
        CausalProposal, method="json_schema", include_raw=True
    )
    log.info("%s proposing causal structure over %d nodes", MODEL, len(nodes))
    started = time.monotonic()
    try:
        result = structured.invoke([
            SystemMessage(CAUSAL_SYSTEM),
            HumanMessage(
                "KPIS (their parents are fixed; never point an edge at one)\n"
                + json.dumps(kpis, indent=2, default=str)
                + "\n\nDRIVER HINTS (concepts from the documentation)\n"
                + json.dumps(driver_hints, indent=2, default=str)
            ),
            HumanMessage("NODES\n" + json.dumps(nodes, indent=2, default=str)),
        ])
    except Exception as exc:  # noqa: BLE001
        raise unavailable(exc, started) from exc
    log.info("%s causal structure returned in %.1fs", MODEL, time.monotonic() - started)

    usage.record("CausalProposal", getattr(result["raw"], "usage_metadata", None))
    proposal = result["parsed"]
    if proposal is None:
        raise LlmUnavailable(
            "The causal proposal did not validate against CausalProposal: "
            f"{result.get('parsing_error') or 'no content returned'}."
        )
    return proposal


def validate_causal(
    proposal: CausalProposal,
    *,
    contract: KpiContract,
    profile: DataProfile,
) -> tuple[list[CausalEdge], dict[str, tuple[bool, str | None]], list[str]]:
    """Filter proposed edges and levers to what may reach the graph.

    Only the checks that need the *profile* live here; membership, direction and
    acyclicity are `onboarding.synthesise_graph`'s job, because they are
    properties of the graph rather than of the model's reply. The one check that
    belongs here is redundancy: a column bound by an exact accounting identity
    cannot act as an independent driver, and a regression on it yields an
    arbitrary split of the effect rather than an attribution.
    """
    problems: list[str] = []
    columns = {m.column for k in contract.kpis for m in k.measures.values()}
    blocked = profile.blocked_driver_columns()

    edges: list[CausalEdge] = []
    for edge in proposal.edges:
        if edge.source in blocked:
            problems.append(
                f"Edge {edge.source} -> {edge.target} dropped: {edge.source!r} is "
                f"barred as an independent driver ({profile.redundant_columns[edge.source]})."
            )
            continue
        edges.append(
            CausalEdge(source=edge.source, target=edge.target,
                       relation="causal", note=edge.note)
        )

    levers: dict[str, tuple[bool, str | None]] = {}
    for lever in proposal.levers:
        if lever.node not in columns:
            problems.append(
                f"Lever names {lever.node!r}, which is not a measure of this "
                "contract; ignored."
            )
            continue
        levers[lever.node] = (lever.controllable, lever.owner)
    return edges, levers, problems


# --------------------------------------------------------------------------- views


def catalog_view(catalog: SeedCatalog, header: list[str]) -> dict[str, Any]:
    """What the model may know about the vocabulary.

    Every KPI and every variant, including the ones that will not bind -- the
    model needs to see what it is choosing between. `expression` is shown so it
    can tell which alias means what, and is never something it may change.
    """
    return {
        "catalog_id": catalog.catalog_id,
        "note": (
            "The whole KPI vocabulary. You may bind columns to these and nothing "
            "else. Expressions are fixed and are shown only so you can tell what "
            "each alias means."
        ),
        "kpis": [
            {
                "name": seed.name,
                "category": seed.category,
                "unit": seed.unit,
                "judges": seed.description,
                "documented_formula": seed.doc_formula,
                "variants": [
                    {
                        "variant_id": v.variant_id,
                        "expression": v.expression,
                        "aggregation_safe": v.aggregation_safe,
                        "measures": [
                            {
                                "alias": m.alias,
                                "means": m.description,
                                "kind": m.kind,
                                "example_headers": m.synonyms[:4],
                            }
                            for m in v.measures
                        ],
                    }
                    for v in seed.variants
                ],
            }
            for seed in catalog.kpis
        ],
    }


def file_view(profile: DataProfile, plan: KpiPlan) -> dict[str, Any]:
    """What the model may know about the data: its schema, never its rows.

    The same rule `kpi_agent/catalog.py` follows for the question planner. A
    binding is a decision about what a column *means*, and that is answerable
    from the schema; showing values here would let the choice be tuned to make a
    particular number appear.
    """
    stats = {c.name: c for c in profile.columns}
    return {
        "n_rows": profile.description.n_rows,
        "columns": [
            {
                "name": c,
                "dtype": stats[c].dtype if c in stats else "unknown",
                "nulls": stats[c].nulls if c in stats else 0,
                "redundant_because": profile.redundant_columns.get(c, ""),
            }
            for c in plan.header
        ],
        "already_chosen": {
            "date_column": plan.date_column,
            "entity_columns": plan.entity_columns,
        },
    }


def prebound_view(catalog: SeedCatalog, header: list[str]) -> list[dict[str, str]]:
    """The exact-match bindings, in the shape the model is asked to produce.

    Shown so it extends a partial answer rather than starting from nothing, and
    so it can see which columns are already spoken for.
    """
    out: list[dict[str, str]] = []
    for seed in catalog.kpis:
        for variant in seed.variants:
            bound = bind_by_synonym(variant, header)
            if len(bound) != len(variant.measures):
                continue
            out.extend(
                {"kpi": seed.name, "alias": alias, "column": column}
                for alias, column in bound.items()
            )
            break
    return out
