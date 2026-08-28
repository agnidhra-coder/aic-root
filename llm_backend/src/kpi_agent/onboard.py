"""Driving onboarding: propose a plan, then commit a confirmed one.

Two generators, each yielding `(event_name, payload)` as a stage lands. They sit
in the same relation to `kpi_engine.onboarding` that `graph.py` does to
`pipeline.py`: the engine module holds every decision and every write, and this
one only sequences them, reaches for the model where a model is wanted, and
narrates what happened.

There is no LangGraph here. The question flow branches -- clarify, no findings,
narrate, repair, fall back -- and a state graph earns its keep. Onboarding is a
straight line with one optional model call in it, and expressing that as a graph
would add a compiled state machine to look at rather than a function to read.

Both generators are also the CLI's implementation and the API's, so a plan
proposed at the terminal and one proposed over HTTP cannot differ -- the same
guarantee `stream_agent` gives `/ask`.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any, Iterator

import pandas as pd

from kpi_engine.catalogue import load_catalog
from kpi_engine.config_io import write_json
from kpi_engine.contracts.configs import CausalEdge
from kpi_engine.contracts.onboarding import ConfirmedPlan, KpiPlan, PlanConfirmation
from kpi_engine.onboarding import (
    check_header,
    default_agent_defaults,
    deterministic_plan,
    infer_schema,
    load_draft,
    merge_confirmation,
    resolve_levers,
    save_confirmed,
    save_draft,
    synthesise_contract,
    synthesise_graph,
    synthesise_source_spec,
    with_column_report,
    write_company_configs,
)
from kpi_engine.profiling import profile_source
from kpi_engine.provisioning import attach_source_data, stage_source_data
from kpi_engine.sources import build_source
from kpi_engine.tenancy import CompanyPaths

from kpi_agent import kpi_plan as planner
from kpi_agent.llm import ENGINE, LlmUnavailable, Usage

log = logging.getLogger("kpi_agent.onboard")

Event = tuple[str, dict[str, Any]]


def new_plan_id(now: dt.datetime | None = None) -> str:
    return f"kpiplan-{(now or dt.datetime.now()):%Y%m%d-%H%M%S}"


def new_run_id(now: dt.datetime | None = None) -> str:
    return f"warmup-{(now or dt.datetime.now()):%Y%m%d-%H%M%S}"


# --------------------------------------------------------------------------- propose


def propose(
    paths: CompanyPaths,
    source_id: str,
    *,
    content: bytes | None = None,
    csv_path: str | None = None,
    llm: Any = None,
    usage: Usage | None = None,
    plan_id: str | None = None,
) -> Iterator[Event]:
    """Stage an upload, profile it, and propose which KPIs it can support."""
    usage = usage or Usage()
    plan_id = plan_id or new_plan_id()
    catalog = load_catalog()

    log.info("%s staging the upload for %s/%s", ENGINE, paths.slug, source_id)
    staged, warnings, n_rows = stage_source_data(
        paths, source_id, content=content, csv_path=csv_path
    )
    frame = pd.read_csv(staged)
    yield "staged", {
        "source_id": source_id,
        "rows": n_rows,
        "columns": len(frame.columns),
        "header": [str(c) for c in frame.columns],
        "warnings": warnings,
    }

    # The schema is sniffed before profiling, not after, because profiling needs
    # it: the adapter parses the spec's `date_column`, and the template's guess
    # (`Date`) is exactly what a tenant with its own naming will not have.
    _header, _head, date_column, entity_columns = infer_schema(staged)
    if date_column is None:
        log.info("%s no column reads as a date; the plan will say so", ENGINE)

    # Profiling is minutes on a wide file -- `find_linear_identities` searches
    # signed combinations of up to three columns -- so it is worth saying so
    # before it starts rather than after.
    log.info("%s profiling %d columns; this is the slow step", ENGINE, len(frame.columns))
    spec = paths.source_spec(source_id).model_copy(
        update={
            "path": str(staged),
            "date_column": date_column or paths.source_spec(source_id).date_column,
            "entity_columns": entity_columns,
        }
    )
    profile = profile_source(build_source(spec, base_dir=paths.root), frame)
    # Cached now, for two reasons. It is the same bytes the confirm step will
    # accept, so re-deriving it there would repeat the minutes this just took --
    # and without it `validate_causal` has no redundancy list, and a column bound
    # by an exact accounting identity would be free to become an independent
    # driver. `attach_source_data` rewrites it afterwards with the final path.
    write_json(profile, paths.profile_path(source_id))
    yield "profile", {
        "n_rows": profile.description.n_rows,
        "redundant_columns": dict(profile.redundant_columns),
        "duplicate_groups": [list(g) for g in profile.duplicate_groups],
        "coverage": [
            {"keys": c.keys, "mean_rows_per_cell": round(c.mean_rows_per_cell, 2),
             "sufficient": c.sufficient}
            for c in profile.coverage
        ],
    }

    plan = deterministic_plan(
        paths, source_id,
        plan_id=plan_id, staged=staged, profile=profile, catalog=catalog,
        min_train_periods=paths.detection().baseline.min_train_periods,
        frame=frame,
    )
    log.info("%s exact name matching bound %d of %d catalogue KPIs",
             ENGINE, len(plan.proposed), len(catalog.kpis))
    yield "plan", {"stage": "matched", "plan": plan.model_dump(mode="json")}

    if llm is not None:
        plan = yield from _extend_with_model(plan, llm, usage, catalog, profile)

    save_draft(paths, plan)
    yield "plan", {"stage": "resolved", "plan": plan.model_dump(mode="json")}
    yield "drafted", {
        "plan_id": plan.plan_id,
        "proposed": [p.name for p in plan.proposed],
        "recommended": plan.recommended,
        "unavailable": [u.name for u in plan.unavailable],
        "problems": plan.problems,
        "llm_tokens": {"in": usage.tokens_in, "out": usage.tokens_out,
                       "calls": usage.calls},
    }


def _extend_with_model(
    plan: KpiPlan, llm: Any, usage: Usage, catalog, profile
) -> Iterator[Event]:
    """Ask the model to bind what exact matching could not. Degrade, never abort."""
    prebound = {
        p.name: {m.alias: m.column for m in p.measures if m.column}
        for p in plan.proposed
    }
    try:
        proposal = planner.propose_bindings(
            llm, usage,
            catalog_view=planner.catalog_view(catalog, plan.header),
            file_view=planner.file_view(profile, plan),
            already_bound=planner.prebound_view(catalog, plan.header),
        )
    except LlmUnavailable as exc:
        log.info("%s no model available (%s); keeping the exact matches", ENGINE, exc)
        return plan.model_copy(
            update={"problems": [*plan.problems, f"The model was unavailable: {exc}"]}
        )

    bound, problems = planner.validate_bindings(
        proposal, catalog=catalog, header=plan.header,
        profile=profile, prebound=prebound,
    )
    date_column, entities = planner.default_entity_columns(
        proposal, plan.header, plan.entity_columns
    )
    added = sorted(set(bound) - set(prebound))
    if added:
        log.info("%s the model bound %d further KPI(s): %s", ENGINE, len(added), added)

    plan = _rebuild(plan, bound, catalog, profile)
    plan = plan.model_copy(
        update={
            "generated_by": "llm",
            "model": getattr(llm, "model", None),
            "date_column": date_column or plan.date_column,
            "entity_columns": entities,
            "problems": [*plan.problems, *problems],
        }
    )
    yield "bindings", {
        "added": added,
        "unmatched_columns": [c for c in proposal.unmatched_columns if c in plan.header],
        "problems": problems,
    }
    return plan


def _rebuild(plan: KpiPlan, bound: dict[str, dict[str, str]], catalog, profile) -> KpiPlan:
    """Re-derive the proposals from a merged binding set.

    Everything except the columns is taken from the catalogue again rather than
    patched onto the old proposal, so a KPI the model added is described exactly
    the way one exact matching found would be.
    """
    from kpi_engine.catalogue import bindable_variants
    from kpi_engine.contracts.onboarding import BoundMeasure, KpiProposal
    from kpi_engine.onboarding import tune_guards

    numeric = {c.name for c in profile.columns
               if any(t in c.dtype.lower() for t in ("int", "float", "decimal"))}
    existing = {p.name: p for p in plan.proposed}
    proposed: list[KpiProposal] = []

    for name, columns in bound.items():
        seed = catalog.kpi(name)
        variant = next(
            (v for v in seed.variants if set(v.aliases) == set(columns)), None
        )
        if variant is None:
            continue
        was = existing.get(name)
        sources = {m.alias: m.bound_by for m in was.measures} if was else {}
        blocking = [
            f"{c!r} is not numeric in this file, so it cannot be aggregated"
            for c in columns.values() if c not in numeric
        ]
        caveats = blocking + [
            f"{c!r} is barred as an independent driver: {profile.redundant_columns[c]}"
            for c in columns.values() if c in profile.redundant_columns
        ]
        proposed.append(
            KpiProposal(
                name=seed.name, variant_id=variant.variant_id, category=seed.category,
                unit=seed.unit, direction=seed.direction, description=seed.description,
                expression=variant.expression, doc_formula=seed.doc_formula,
                drivers=list(seed.drivers), primary_metric=seed.primary_metric,
                measures=[
                    BoundMeasure(alias=m.alias, column=columns[m.alias], agg=m.agg,
                                 bound_by=sources.get(m.alias, "llm"))
                    for m in variant.measures
                ],
                materiality=seed.materiality,
                guards=tune_guards(seed.guards, profile, plan.entity_columns[:1]),
                computable=True,
                aggregation_safe=variant.aggregation_safe,
                recommended=variant.aggregation_safe and not blocking,
                caveats=caveats,
                alternatives=[
                    v for v in bindable_variants(seed, plan.header)
                    if v != variant.variant_id
                ],
            )
        )

    proposed.sort(key=lambda p: p.name)
    kept = {p.name for p in proposed}
    return with_column_report(
        plan.model_copy(
            update={
                "proposed": proposed,
                "unavailable": [u for u in plan.unavailable if u.name not in kept],
            }
        ),
        profile,
    )


# --------------------------------------------------------------------------- confirm


def prepare(
    paths: CompanyPaths, confirmation: PlanConfirmation
) -> tuple[KpiPlan, Any, Any, Any, list[str]]:
    """Merge a decision and synthesise everything, touching no file.

    Split out from `commit` so the API can run it inside the request and return a
    422 with the problem list, rather than opening a 200 that later carries an
    error event. Everything here is pure and takes milliseconds.
    """
    plan = load_draft(paths)
    merged, problems = merge_confirmation(plan, confirmation)
    catalog = load_catalog()

    contract_id = confirmation.contract_id or f"{paths.slug.replace('-', '_')}_auto_v1"
    contract = synthesise_contract(merged, contract_id=contract_id, catalog=catalog)
    source_spec = synthesise_source_spec(
        paths.base_source_spec(merged.source_id), merged
    )
    columns = {m.column for k in contract.kpis for m in k.measures.values()}
    graph, graph_problems = synthesise_graph(
        contract, graph_id=contract_id, catalog=catalog,
        levers=resolve_levers(catalog, columns),
    )
    problems += graph_problems
    problems += check_header(contract, source_spec, merged.header)
    return merged, contract, source_spec, graph, problems


def commit(
    paths: CompanyPaths,
    confirmation: PlanConfirmation,
    *,
    prepared: tuple[KpiPlan, Any, Any, Any, list[str]] | None = None,
    llm: Any = None,
    usage: Usage | None = None,
    run_id: str | None = None,
) -> Iterator[Event]:
    """Write the confirmed configuration, attach the data, and warm the pipeline."""
    usage = usage or Usage()
    run_id = run_id or new_run_id()
    merged, contract, source_spec, graph, problems = prepared or prepare(
        paths, confirmation
    )
    profile = None

    if llm is not None:
        graph, problems, profile = yield from _extend_graph(
            paths, merged, contract, graph, llm, usage, problems
        )

    log.info("%s writing %s's configuration: %d KPI(s)",
             ENGINE, paths.slug, len(contract.kpis))
    updated, _, archive = write_company_configs(
        paths,
        contract=contract, source_spec=source_spec, graph=graph,
        agent=default_agent_defaults(merged, paths.spec.agent),
        plan_id=merged.plan_id,
    )
    binding = updated.spec.binding(merged.source_id)
    yield "configs", {
        "contract_id": contract.contract_id,
        "graph_id": graph.graph_id,
        "kpis": [k.name for k in contract.kpis],
        "entity_columns": list(source_spec.entity_columns),
        "date_column": source_spec.date_column,
        "time_grain": contract.time_grain,
        "edges": {
            "deterministic": sum(1 for e in graph.edges if e.relation == "deterministic"),
            "causal": sum(1 for e in graph.edges if e.relation == "causal"),
        },
        "levers": [{"node": n.name, "owner": n.owner} for n in graph.nodes if n.controllable],
        "written": [binding.source, binding.contract, updated.spec.configs.graph, "company.yaml"],
        "superseded": str(archive.name),
        "problems": problems,
    }

    # The ordinary attach, header gate and all. It now checks the file against the
    # contract we just synthesised from it, so a binder bug fails here rather than
    # becoming a panel full of NaN.
    staged = updated.staging_path(merged.source_id)
    log.info("%s accepting the staged file against the new contract", ENGINE)
    updated, warnings = attach_source_data(updated, merged.source_id, csv_path=staged)
    yield "data", {
        "source_id": merged.source_id,
        "rows": merged.n_rows,
        "ready": updated.data_ready,
        "warnings": warnings,
    }

    confirmed = ConfirmedPlan(
        plan_id=merged.plan_id, company_id=updated.slug, source_id=merged.source_id,
        confirmed_at=dt.datetime.now(), draft=merged, confirmation=confirmation,
        accepted=[p.name for p in merged.proposed],
        rejected=[d.name for d in confirmation.decisions if d.verdict == "reject"],
        contract_id=contract.contract_id, graph_id=graph.graph_id,
        written=[binding.source, binding.contract, updated.spec.configs.graph, "company.yaml"],
        superseded=str(archive.relative_to(updated.root)),
        warm_up_run_id=run_id if confirmation.warm_up else None,
        problems=problems,
    )
    save_confirmed(updated, confirmed)
    staged.unlink(missing_ok=True)

    if confirmation.warm_up:
        yield from _warm_up(updated, run_id)


def _extend_graph(paths, merged, contract, graph, llm, usage, problems):
    """Ask the model for causal mechanisms. A refusal costs levers, never the run."""
    from kpi_engine.config_io import read_json
    from kpi_engine.contracts.payloads import DataProfile

    profile_path = paths.profile_path(merged.source_id)
    profile = (
        DataProfile.model_validate(read_json(profile_path))
        if profile_path.exists() else None
    )
    nodes = [
        {"name": n.name, "kind": n.kind, "column": n.column}
        for n in graph.nodes if n.kind == "measure"
    ]
    kpis = [
        {"name": k.name, "expression": k.expression,
         "measures": {a: m.column for a, m in k.measures.items()}}
        for k in contract.kpis
    ]
    hints = {k.name: list(k.drivers) for k in contract.kpis}
    try:
        proposal = planner.propose_causal(
            llm, usage, nodes=nodes, kpis=kpis, driver_hints=hints
        )
    except LlmUnavailable as exc:
        log.info("%s no model for the causal step (%s); deterministic edges only",
                 ENGINE, exc)
        yield "graph", {"edges_added": 0, "problems": [f"Model unavailable: {exc}"]}
        return graph, problems, profile

    if profile is None:
        # Without a profile there is no redundancy list to check against, so the
        # only checks left are the structural ones `synthesise_graph` runs anyway.
        edges = [
            CausalEdge(source=e.source, target=e.target, relation="causal", note=e.note)
            for e in proposal.edges
        ]
        levers, extra = {}, ["No cached profile; collinear drivers were not screened."]
    else:
        edges, levers, extra = planner.validate_causal(
            proposal, contract=contract, profile=profile
        )

    catalog = load_catalog()
    columns = {m.column for k in contract.kpis for m in k.measures.values()}
    merged_levers = {**resolve_levers(catalog, columns), **levers}
    rebuilt, graph_problems = synthesise_graph(
        contract, graph_id=graph.graph_id, catalog=catalog,
        causal_edges=edges, levers=merged_levers,
    )
    kept = sum(1 for e in rebuilt.edges if e.relation == "causal")
    log.info("%s the model proposed %d causal edge(s); %d kept",
             ENGINE, len(proposal.edges), kept)
    yield "graph", {
        "edges_proposed": len(proposal.edges),
        "edges_added": kept,
        "levers": [{"node": n.name, "owner": n.owner}
                   for n in rebuilt.nodes if n.controllable],
        "problems": extra + graph_problems,
    }
    return rebuilt, [*problems, *extra, *graph_problems], profile


def _warm_up(paths: CompanyPaths, run_id: str) -> Iterator[Event]:
    """Run the pipeline once so the first question does not pay for it.

    A failure here does **not** undo the configuration. The contract passed
    validation and the file passed the header gate, so the data genuinely matches
    what the user chose; a thin history or an unlucky threshold is not a reason to
    revert a tenant to KPIs it never asked for. It is reported and the company
    stays ready.
    """
    from kpi_engine.pipeline import run_pipeline

    detection = paths.detection()
    eda = paths.eda()
    graph = paths.graph()
    started = time.monotonic()

    for binding in paths.spec.sources:
        source_id = binding.source_id
        try:
            spec = paths.source_spec(source_id)
            contract = paths.contract(source_id)
        except Exception as exc:  # noqa: BLE001 - a second source may be unconfigured
            log.info("%s %s is not configured; skipped", ENGINE, source_id)
            yield "engine", {"stage": "warm_up", "source_id": source_id,
                             "skipped": str(exc)}
            continue

        log.info("%s warming up %s at %s grain", ENGINE, source_id, contract.time_grain)
        try:
            result = run_pipeline(
                spec, contract, detection,
                paths=paths, run_id=f"{run_id}/{source_id}",
                graph=graph, eda=eda,
                entity_keys=list(paths.spec.agent.entity_keys),
                time_grain=paths.spec.agent.time_grain,
                top_events=paths.spec.agent.top_events,
            )
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            log.info("%s warm-up of %s failed: %s", ENGINE, source_id, exc)
            yield "engine", {"stage": "warm_up", "source_id": source_id,
                             "error": f"{type(exc).__name__}: {exc}"}
            continue

        yield "engine", {
            "stage": "warm_up",
            "source_id": source_id,
            "flags": len(result.flags),
            "events": len(result.events),
            "bundles": len(result.bundles),
            "seconds": round(time.monotonic() - started, 1),
        }
