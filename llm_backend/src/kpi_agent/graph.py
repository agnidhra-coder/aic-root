"""The LangGraph state machine.

Read the edges and the design reads back: two model calls, at the two ends, with
every computation between them deterministic. The model chooses *what to analyse*
and *how to say it*; everything that produces a number sits in between, untouched
by it.

    ingest -> plan -> validate -+-> clarify (END)
                                +-> run -+-> no_findings (END)
                                         +-> link -> context -> facts -> narrate -> verify
    verify -+-> narrate   (one repair pass)
            +-> fallback  (deterministic template)
            +-> report (END)

The three terminal states other than `report` are all honest outcomes rather than
errors: a question that cannot be resolved gets a clarifying question, a slice with
nothing in it gets told so, and a narrative that cannot be verified gets replaced
by one that can.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Iterator
from typing import Any

from langgraph.graph import END, StateGraph

from kpi_engine.config_io import write_json
from kpi_engine.pipeline import PipelineResult, load_or_build_profile, run_pipeline
from kpi_engine.sources import build_source
from kpi_engine.tenancy import CompanyPaths, company_config, open_company

from kpi_agent import facts as facts_mod
from kpi_agent.catalog import build_catalog
from kpi_agent import intent as intent_mod
from kpi_agent import exogenous as exogenous_mod
from kpi_agent import linking, narrate as narrate_mod, render, verify as verify_mod
from kpi_agent.llm import ENGINE, MODEL, LlmUnavailable, Usage
from kpi_agent.models import GroundedContext
from kpi_agent.state import AgentState

MAX_REPAIR_ATTEMPTS = 1

log = logging.getLogger("kpi_agent.graph")


# --------------------------------------------------------------------------- #
# Nodes
# --------------------------------------------------------------------------- #


def ingest_sources(state: AgentState) -> dict[str, Any]:
    paths: CompanyPaths = state["config"]["paths"]
    wanted = state["config"].get("sources")
    started = time.monotonic()
    loaded = []
    for binding in paths.bindings:
        if wanted and binding.source_id not in wanted:
            continue
        # `source_spec` has already folded in the binding's dataset override and
        # made the path absolute, so nothing here has to know where the company
        # folder is.
        spec = paths.source_spec(binding.source_id)
        contract = paths.contract(binding.source_id)
        source = build_source(spec, base_dir=paths.root)
        df = source.load()
        profile = load_or_build_profile(paths, source, df, spec.source_id)
        loaded.append((spec, contract, profile, df))
    if not loaded:
        raise ValueError(
            f"Company '{paths.slug}' has no sources matching {sorted(wanted or [])}. "
            f"Declared: {paths.spec.source_ids}"
        )

    graph = paths.graph()
    detection = paths.detection()
    min_train = detection.baseline.min_train_periods

    for spec, _contract, _profile, df in loaded:
        log.info("%s ingested %s: %s rows, %s columns",
                 ENGINE, spec.source_id, f"{len(df):,}", len(df.columns))
    log.info("%s ingest complete in %.2fs", ENGINE, time.monotonic() - started)

    return {
        "sources": loaded,
        "catalog": build_catalog(loaded, graph, min_train_periods=min_train),
        "coverage_days": _coverage_days(loaded),
        "min_train_periods": min_train,
        "errors": [],
        "repair_attempts": 0,
        "used_fallback": False,
    }


def _coverage_days(loaded: list[tuple[Any, Any, Any, Any]]) -> int | None:
    """The widest span any source covers, in days.

    Widest rather than narrowest: a grain is unusable only when *no* source can
    support it. A short secondary file should not drag the whole analysis to a
    finer grain than the primary needs.
    """
    import pandas as pd

    spans = []
    for spec, _contract, _profile, df in loaded:
        if spec.date_column not in df.columns:
            continue
        dates = pd.to_datetime(df[spec.date_column], errors="coerce").dropna()
        if len(dates):
            spans.append((dates.max() - dates.min()).days)
    return max(spans) if spans else None


def plan_intent(state: AgentState) -> dict[str, Any]:
    cfg = state["config"]
    llm = cfg.get("llm")
    trimmed = [(s, c, p) for s, c, p, _ in state["sources"]]

    if llm is None:
        log.info("%s no model: planning a broad sweep at the default grain", ENGINE)
        return {
            "intent": intent_mod.default_intent(
                state["question"], state.get("persona") or "analyst", trimmed,
                # `or`, not `.get(..., "week")`: both callers set this key
                # explicitly, and `None` there means "the caller said nothing",
                # which a two-argument `.get` does not treat as absent. Passing
                # that `None` through fails `AnalysisIntent`'s grain literal and
                # takes down the whole no-model path.
                time_grain=cfg.get("time_grain") or "week",
                entity_keys=cfg.get("entity_keys"),
            ),
            "fallback_reason": "no model available (--no-llm)",
        }

    try:
        planned = intent_mod.plan_intent(
            llm, cfg["usage"], state["question"], state["catalog"], state.get("persona")
        )
        log.info("%s proposed: %s at %s grain%s, sources %s",
                 MODEL, ", ".join(planned.kpis) or "every KPI", planned.time_grain,
                 " by " + ", ".join(planned.entity_keys) if planned.entity_keys else " at total level",
                 ", ".join(planned.sources) or "all")
        return {"intent": planned}
    except LlmUnavailable as exc:
        # Losing the planner costs the tailored configuration, not the analysis.
        # A broad sweep at a sane grain still answers "what needs attention".
        return {
            "intent": intent_mod.default_intent(
                state["question"], state.get("persona") or "analyst", trimmed,
                time_grain=cfg.get("time_grain") or "week",
                entity_keys=cfg.get("entity_keys"),
            ),
            "errors": [*state.get("errors", []), f"planner unavailable: {exc}"],
            "fallback_reason": f"planner unavailable: {exc}",
        }


def validate_intent(state: AgentState) -> dict[str, Any]:
    cfg = state["config"]
    trimmed = [(s, c, p) for s, c, p, _ in state["sources"]]
    proposed = state["intent"]

    if proposed is not None and proposed.clarification_needed:
        log.info("%s the planner asked for clarification instead of a plan", MODEL)
        return {
            "clarification": proposed.clarification_needed,
            "intent_problems": [],
            "survey": not proposed.kpis,
        }

    resolved, problems = intent_mod.validate_intent(
        proposed, trimmed,
        min_train_periods=state.get("min_train_periods"),
        coverage_days=state.get("coverage_days"),
        # An explicit CLI choice outranks the planner's. Without this a demo is
        # only as reproducible as the model's mood.
        overrides={
            "time_grain": cfg.get("time_grain"),
            "entity_keys": cfg.get("entity_keys"),
        },
    )
    if resolved is None:
        log.info("%s plan rejected: %s", ENGINE, " ".join(problems))
        return {
            "clarification": (
                "I could not resolve that request against the data. "
                + " ".join(problems)
            ),
            "intent_problems": problems,
            "survey": not (proposed.kpis if proposed else []),
        }
    for problem in problems:
        log.info("%s %s", ENGINE, problem)
    log.info("%s resolved: %s at %s grain%s",
             ENGINE, ", ".join(resolved.kpis) or "every KPI", resolved.time_grain,
             " by " + ", ".join(resolved.entity_keys) if resolved.entity_keys else " at total level")
    # Survey mode is read off the resolved plan rather than asked of the model.
    # An empty KPI list already means "every KPI" in `models.py`, `intent.py` and
    # `run_pipelines` alike, so a question that named none is a sweep by
    # construction. Deriving it keeps one fact in one place.
    #
    # Covering every available KPI counts too, and has to. Asked "how are we
    # doing?", the planner is as likely to enumerate the catalogue as to leave
    # the field empty -- those are the same instruction written two ways, and
    # reading only the empty one makes the mode depend on the model's phrasing.
    # That is precisely the fragility deriving it was meant to remove: the run
    # that found this returned "no material movement" over a series whose margin
    # had fallen 76% end to end.
    available = {
        k.name
        for spec, contract, _ in trimmed
        if spec.source_id in resolved.sources
        for k in contract.kpis
    }
    survey = not resolved.kpis or bool(available and set(resolved.kpis) >= available)
    if survey:
        log.info("%s survey mode: every declared KPI, with descriptive findings "
                 "reported alongside whatever the detector flags", ENGINE)
    return {
        "intent": resolved,
        "intent_problems": problems,
        "clarification": None,
        "survey": survey,
    }


def run_pipelines(state: AgentState) -> dict[str, Any]:
    cfg = state["config"]
    paths: CompanyPaths = cfg["paths"]
    resolved = state["intent"]
    detection = paths.detection()
    eda = paths.eda()
    graph = paths.graph()

    # A requested period scopes the *answer*, not the data the baseline learns from.
    # Restricting the load instead leaves the detector with a handful of periods
    # against a 28-period history requirement -- it then correctly finds nothing,
    # which reads as "a quiet quarter" when it actually means "not enough history
    # was loaded to tell".
    report_window = None
    if resolved.date_start or resolved.date_end:
        start = dt.date.fromisoformat(resolved.date_start) if resolved.date_start else dt.date.min
        end = dt.date.fromisoformat(resolved.date_end) if resolved.date_end else dt.date.max
        report_window = (start, end)

    results: dict[str, PipelineResult] = {}
    errors = list(state.get("errors", []))

    if report_window:
        log.info("%s report window %s to %s (detection still runs on the full history)",
                 ENGINE, report_window[0], report_window[1])

    for spec, contract, _profile, _df in state["sources"]:
        if resolved.sources and spec.source_id not in resolved.sources:
            continue

        # Each source keeps its own native grain and its own dimensions. Forcing the
        # weekly supply file to a requested daily grain would fabricate resolution it
        # does not have; forcing `Supplier` onto the sales file would fail outright.
        grain = resolved.time_grain if spec.source_id == paths.primary_source_id else contract.time_grain
        keys = [k for k in resolved.entity_keys if k in spec.entity_columns]
        kpis = [k for k in resolved.kpis if k in {d.name for d in contract.kpis}] or None

        started = time.monotonic()
        log.info("%s %s: computing KPIs at %s grain%s",
                 ENGINE, spec.source_id, grain,
                 " by " + ", ".join(keys) if keys else " at total level")
        try:
            result = run_pipeline(
                spec, contract, detection,
                paths=paths,
                run_id=f"{state['run_id']}/{spec.source_id}",
                graph=graph, eda=eda,
                kpis=kpis, entity_keys=keys, time_grain=grain,
                report_window=report_window,
                top_events=cfg.get("top_events", 5),
            )
        except Exception as exc:  # noqa: BLE001 - one source failing must not lose the other
            log.info("%s %s failed: %s", ENGINE, spec.source_id, exc)
            errors.append(f"{spec.source_id}: {exc}")
        else:
            results[spec.source_id] = result
            log.info("%s %s: %d flags -> %d events -> %d evidence bundles (%.2fs)",
                     ENGINE, spec.source_id, len(result.flags), len(result.events),
                     len(result.bundles), time.monotonic() - started)
            if not result.events:
                log.info("%s %s produced no events; nothing from this source can be "
                         "explained", ENGINE, spec.source_id)

    return {"results": results, "errors": errors}


def link_sources(state: AgentState) -> dict[str, Any]:
    paths: CompanyPaths = state["config"]["paths"]
    graph = paths.graph()
    results: dict[str, PipelineResult] = state["results"]

    primary = results.get(paths.primary_source_id)
    secondary = [r for sid, r in results.items() if sid != paths.primary_source_id]
    if primary is None or not secondary:
        log.info("%s no cross-source link possible: only one source produced events",
                 ENGINE)
        return {"links": []}

    links = []
    for other in secondary:
        links.extend(
            linking.link_events(
                primary.events, other.events,
                primary.contract, other.contract, graph,
                sales_bundles=primary.bundles,
            )
        )
    log.info("%s %d cross-source link(s), each licensed by a declared DAG edge",
             ENGINE, len(links))
    return {"links": links}


def align_context(state: AgentState) -> dict[str, Any]:
    """Place whatever the user asserted against whatever the engine detected.

    Deliberately its own node rather than a step inside `assemble_facts`. What it
    produces is evidence of a different kind -- unmeasured, unlicensed, the user's
    word -- and giving it a seam of its own means a reader of the graph can see
    that it never touches attribution, and a client watching the stream is told
    what happened to their hypothesis before the narrative is written.
    """
    resolved = state.get("intent")
    factors = list(resolved.exogenous) if resolved else []
    if not factors:
        return {"context_alignments": [], "unaligned_factors": []}

    alignments, unaligned = exogenous_mod.align_factors(factors, state.get("results") or {})
    log.info("%s %d context factor(s) supplied: %d aligned to a detected event, "
             "%d could not be placed", ENGINE, len(factors), len(alignments), len(unaligned))
    return {"context_alignments": alignments, "unaligned_factors": unaligned}


def assemble_facts(state: AgentState) -> dict[str, Any]:
    cfg = state["config"]
    paths: CompanyPaths = cfg["paths"]
    resolved = state["intent"]
    graph = paths.graph()
    personas = paths.personas()
    persona_spec = personas.get(resolved.persona, personas["analyst"])

    results: dict[str, PipelineResult] = state["results"]
    primary_id = paths.primary_source_id
    primary = results.get(primary_id)

    sales_bundles = primary.bundles if primary else []
    secondary_bundles = [
        b for sid, r in results.items() if sid != primary_id for b in r.bundles
    ]
    series = [p for r in results.values() for p in r.series_profiles]

    context = facts_mod.build_context(
        question=state["question"],
        intent=resolved,
        persona_spec=persona_spec,
        run_id=state["run_id"],
        sales_bundles=sales_bundles,
        scm_bundles=secondary_bundles,
        links=state.get("links", []),
        alignments=state.get("context_alignments", []),
        unaligned_factors=state.get("unaligned_factors", []),
        survey=bool(state.get("survey")),
        graph=graph,
        freshness=[r.freshness for r in results.values()],
        series_profiles=series,
        period=(resolved.date_start, resolved.date_end),
    )
    log.info("%s fact table: %d facts for persona '%s', %d abstention(s)",
             ENGINE, len(context.facts), resolved.persona, len(context.abstentions))
    return {"context": context}


def narrate(state: AgentState) -> dict[str, Any]:
    cfg = state["config"]
    llm = cfg.get("llm")
    context = state["context"]
    verification = state.get("verification")

    if llm is None:
        log.info("%s no model: rendering the deterministic template", ENGINE)
        return {
            "narrative": narrate_mod.fallback_narrative(
                context, "no model available (--no-llm)"
            ),
            "used_fallback": True,
            "fallback_reason": "no model available (--no-llm)",
        }

    feedback = verification.as_feedback() if verification and not verification.passed else None
    try:
        story = narrate_mod.narrate(
            llm, cfg["usage"], context,
            repair_feedback=feedback, previous=state.get("narrative"),
        )
        return {"narrative": story}
    except LlmUnavailable as exc:
        log.info("%s narrator unavailable (%s); falling back to the template",
                 ENGINE, exc)
        return {
            "narrative": narrate_mod.fallback_narrative(context, str(exc)),
            "used_fallback": True,
            "fallback_reason": str(exc),
            "errors": [*state.get("errors", []), f"narrator unavailable: {exc}"],
        }


def verify_narrative(state: AgentState) -> dict[str, Any]:
    paths: CompanyPaths = state["config"]["paths"]
    graph = paths.graph()
    result = verify_mod.verify(state["narrative"], state["context"], graph)
    if result.passed:
        log.info("%s verified: %d numeric claims and %d statements all resolved "
                 "against the fact table",
                 ENGINE, result.numbers_checked, result.claims_checked)
    else:
        log.info("%s verification failed: %s", ENGINE,
                 "; ".join(f"{v.code} at {v.where}" for v in result.violations))
    return {
        "verification": result,
        "repair_attempts": state.get("repair_attempts", 0) + (0 if result.passed else 1),
    }


def use_fallback(state: AgentState) -> dict[str, Any]:
    verification = state.get("verification")
    reason = (
        f"the model's narrative failed verification twice "
        f"({len(verification.violations)} violations)"
        if verification else "verification failed"
    )
    log.info("%s %s; rendering the deterministic template instead", ENGINE, reason)
    story = narrate_mod.fallback_narrative(state["context"], reason)
    graph = state["config"]["paths"].graph()
    return {
        "narrative": story,
        "used_fallback": True,
        "fallback_reason": reason,
        # The template output is verified too. It is generated from the table rather
        # than from a model, so it should pass -- and if it ever does not, that is a
        # bug in the template worth surfacing, not one to hide behind a bypass.
        "verification": verify_mod.verify(story, state["context"], graph),
    }


def clarify(state: AgentState) -> dict[str, Any]:
    question = state.get("clarification") or "I need more detail to answer that."
    body = (
        f"# I need one clarification\n\n"
        f"**You asked** — {render.asked(state['question'])}\n\n"
        f"{question}\n\n"
        "Nothing was computed, because running the wrong analysis and reporting it "
        "confidently is worse than asking.\n"
    )
    _write_terse_report(state, body, "clarification")
    return {"report_markdown": body}


def no_findings(state: AgentState) -> dict[str, Any]:
    """Nothing cleared the thresholds. Say precisely what was searched.

    A bare "nothing found" is unfalsifiable and unactionable: the reader cannot
    tell a genuinely quiet quarter from a planner that narrowed the window to a
    week with no data. So the configuration that produced the empty result is part
    of the answer, and it is written to disk alongside it.
    """
    resolved = state.get("intent")
    slice_ = ", ".join(resolved.entity_keys) if resolved and resolved.entity_keys else "total level"
    period = "the full history"
    if resolved and (resolved.date_start or resolved.date_end):
        period = f"{resolved.date_start or 'the start'} to {resolved.date_end or 'the end'}"

    results = state.get("results") or {}
    body = (
        f"# No material movement found\n\n"
        f"**You asked** — {render.asked(state['question'])}\n\n"
        f"**Searched** — {resolved.time_grain if resolved else 'default'} grain, "
        f"{slice_}, over {period}, across "
        f"{', '.join(results) or 'no source'}.\n\n"
        f"No event cleared both the statistical threshold and the KPI contract's "
        f"materiality floor.\n\n"
        "That is a finding, not a failure: the thresholds are calibrated against "
        "injected ground truth, and reporting tail noise as an event would be worse "
        "than reporting nothing.\n"
    )

    if results:
        body += "\n| Source | KPIs | Periods | Flags | Events | Explained | Trending |\n"
        body += "|---|---|---|---|---|---|---|\n"
        for source_id, result in results.items():
            body += (
                f"| {source_id} | {len(result.panel.kpi_names())} | "
                f"{result.panel.values['period'].nunique()} | {len(result.flags)} | "
                f"{len(result.events)} | {len(result.bundles)} | "
                f"{facts_mod.count_trending(result.series_profiles)} |\n"
            )
        body += (
            "\nFlags without events means movements were detected but none carried "
            "enough corroboration to become an event; events without explanations "
            "means attribution was attempted and abstained. Trending counts series "
            "whose underlying level is moving without any single period being "
            "anomalous -- zero there means the period really was quiet, rather "
            "than merely uneventful.\n"
        )

    if resolved:
        body += (
            f"\nIf that is not the question you meant, the analysis was configured as:\n\n"
            f"```json\n{resolved.model_dump_json(indent=2)}\n```\n"
        )

    errors = state.get("errors", [])
    if errors:
        body += "\nProblems encountered during the run:\n\n" + "\n".join(f"- {e}" for e in errors) + "\n"

    _write_terse_report(state, body, "no_findings")
    return {"report_markdown": body}


def _write_terse_report(state: AgentState, markdown: str, outcome: str) -> None:
    """Persist the short-circuit outcomes too.

    These paths produce no evidence bundle, but they are exactly the runs someone
    will want to inspect afterwards -- "why did it ask me that", "why did it find
    nothing" -- and an answer that exists only in terminal scrollback cannot be.
    """
    paths: CompanyPaths = state["config"]["paths"]
    out = paths.run_dir(state["run_id"])
    (out / "agent_report.md").write_text(markdown)
    intent = state.get("intent")
    write_json(
        {
            "run_id": state["run_id"],
            "company": paths.slug,
            "outcome": outcome,
            "question": state["question"],
            "intent": intent.model_dump(mode="json") if intent else None,
            "intent_problems": state.get("intent_problems", []),
            "clarification": state.get("clarification"),
            "errors": state.get("errors", []),
        },
        out / "agent_report.json",
    )


def report(state: AgentState) -> dict[str, Any]:
    cfg = state["config"]
    paths: CompanyPaths = cfg["paths"]
    context: GroundedContext = state["context"]
    results: dict[str, PipelineResult] = state["results"]

    deterministic_ms = sum(
        r.manifest.total_duration_ms for r in results.values() if r.manifest
    )
    deterministic_stages = sum(
        len(r.manifest.stages) for r in results.values() if r.manifest
    )
    usage: Usage = cfg["usage"]
    telemetry = {
        "deterministic_stages": deterministic_stages,
        "deterministic_ms": deterministic_ms,
        "llm_calls": usage.calls,
        "llm_tokens_in": usage.tokens_in,
        "llm_tokens_out": usage.tokens_out,
        "llm_cost_usd": usage.cost_usd,
        "model": getattr(cfg.get("llm"), "model", None),
        "llm_per_call": usage.per_call,
        "used_fallback": state.get("used_fallback", False),
        "fallback_reason": state.get("fallback_reason", ""),
    }

    markdown = render.render_markdown(
        state["narrative"], context, state["verification"], telemetry
    )

    out = paths.run_dir(state["run_id"])
    (out / "agent_report.md").write_text(markdown)
    write_json(
        {
            "run_id": state["run_id"],
            # An artefact that does not say whose data it describes is a hazard
            # the moment two companies' reports sit in the same place.
            "company": paths.slug,
            "question": state["question"],
            "intent": state["intent"].model_dump(mode="json") if state["intent"] else None,
            "intent_problems": state.get("intent_problems", []),
            "narrative": state["narrative"].model_dump(mode="json"),
            "verification": state["verification"].model_dump(mode="json"),
            "context": context.model_dump(mode="json"),
            "telemetry": telemetry,
            "errors": state.get("errors", []),
        },
        out / "agent_report.json",
    )

    # The manifest's LLM fields have been zero since the engine was written. Now that
    # both halves are measured, the split is a fact rather than a claim.
    for result in results.values():
        if result.manifest is None:
            continue
        cost = telemetry["llm_cost_usd"]
        updated = result.manifest.model_copy(update={
            "llm_calls": telemetry["llm_calls"],
            "llm_tokens_in": telemetry["llm_tokens_in"],
            "llm_tokens_out": telemetry["llm_tokens_out"],
            # None rather than 0.0 for an unpriced model: zero would read as free.
            "llm_cost_usd": round(cost, 6) if cost is not None else None,
            "llm_model": telemetry.get("model"),
        })
        write_json(updated, result.out_dir / "run_manifest.json")

    log.info("%s report written -> %s", ENGINE, out / "agent_report.md")
    return {
        "report_markdown": markdown,
        "report_path": str(out / "agent_report.md"),
        "telemetry": telemetry,
    }


# --------------------------------------------------------------------------- #
# Conditional edges
# --------------------------------------------------------------------------- #


def _after_validate(state: AgentState) -> str:
    return "clarify" if state.get("clarification") else "run_pipelines"


def _after_run(state: AgentState) -> str:
    """Where an empty detection goes.

    A survey with nothing detected is not the same outcome as a survey with
    nothing to say. `eda/` has already characterised every series in the run --
    trend, seasonality, phases -- and until now that was computed, written to
    disk, and shown to nobody. When the question named no KPI and the detector
    flagged nothing material, those descriptions *are* the answer, so the run
    continues down the ordinary path. `link_sources` and `align_context` are
    both no-ops with no bundles, which is why the path can be reused rather
    than branched around.

    `no_findings` still exists, for the case it was written for: a slice where
    nothing moved and nothing is trending either.
    """
    results = state.get("results") or {}
    if any(r.bundles for r in results.values()):
        return "link_sources"
    if state.get("survey") and facts_mod.has_notable_trends(results):
        log.info("%s no event cleared the thresholds, but the survey has "
                 "descriptive findings to report", ENGINE)
        return "link_sources"
    return "no_findings"


def _after_verify(state: AgentState) -> str:
    verification = state.get("verification")
    if verification and verification.passed:
        return "report"
    if state.get("used_fallback"):
        # The fallback's own output failed verification. Report it anyway, with the
        # violations printed: a visible template bug beats a silent retry loop.
        return "report"
    if state.get("repair_attempts", 0) > MAX_REPAIR_ATTEMPTS:
        return "use_fallback"
    return "narrate"


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #


def build_graph():
    g = StateGraph(AgentState)

    g.add_node("ingest_sources", ingest_sources)
    g.add_node("plan_intent", plan_intent)
    g.add_node("validate_intent", validate_intent)
    g.add_node("run_pipelines", run_pipelines)
    g.add_node("link_sources", link_sources)
    g.add_node("align_context", align_context)
    g.add_node("assemble_facts", assemble_facts)
    g.add_node("narrate", narrate)
    g.add_node("verify", verify_narrative)
    g.add_node("use_fallback", use_fallback)
    g.add_node("report", report)
    g.add_node("clarify", clarify)
    g.add_node("no_findings", no_findings)

    g.set_entry_point("ingest_sources")
    g.add_edge("ingest_sources", "plan_intent")
    g.add_edge("plan_intent", "validate_intent")
    g.add_conditional_edges("validate_intent", _after_validate,
                            {"clarify": "clarify", "run_pipelines": "run_pipelines"})
    g.add_conditional_edges("run_pipelines", _after_run,
                            {"link_sources": "link_sources", "no_findings": "no_findings"})
    g.add_edge("link_sources", "align_context")
    g.add_edge("align_context", "assemble_facts")
    g.add_edge("assemble_facts", "narrate")
    g.add_edge("narrate", "verify")
    g.add_conditional_edges("verify", _after_verify,
                            {"narrate": "narrate", "use_fallback": "use_fallback",
                             "report": "report"})
    g.add_edge("use_fallback", "report")
    g.add_edge("report", END)
    g.add_edge("clarify", END)
    g.add_edge("no_findings", END)

    return g.compile()


def stream_agent(
    question: str,
    *,
    company: str | CompanyPaths,
    llm: Any = None,
    persona: str | None = None,
    run_id: str | None = None,
    config: dict[str, Any] | None = None,
) -> Iterator[tuple[str, AgentState]]:
    """Yield `(node_name, state-so-far)` as each node completes.

    The state of the final yield is exactly what `run_agent` returns: this is the
    one execution path, and `run_agent` is a drain of it. A caller that wants to
    watch a run happen -- an HTTP stream, a progress bar -- gets the same objects
    the CLI renders at the end, only earlier. Nothing here decides what a stage
    *means*; that projection belongs to whoever is watching.

    `AgentState` declares no reducers, so every channel is LangGraph's `LastValue`
    and accumulating the deltas with `dict.update` reproduces `invoke`'s final
    state exactly. `test_streaming_the_graph_reproduces_what_invoke_returns` pins
    that rather than trusting it.

    `company` is required and has no default. There is no module-level config to
    fall back on: a run configured from whichever tenant happened to be wired in
    would report one company's numbers under another's name.
    """
    paths = company if isinstance(company, CompanyPaths) else open_company(company)
    cfg = {**company_config(paths), **(config or {})}
    cfg["paths"] = paths
    cfg["company"] = paths.slug
    cfg["llm"] = llm
    cfg["usage"] = Usage()
    run_id = run_id or f"ask-{dt.datetime.now():%Y%m%d-%H%M%S}"

    state: AgentState = {
        "question": question,
        "persona": persona,
        "run_id": run_id,
        "config": cfg,
        "errors": [],
        "repair_attempts": 0,
        "used_fallback": False,
    }

    app = build_graph()
    for chunk in app.stream(dict(state), {"recursion_limit": 40},
                            stream_mode="updates"):
        for node, delta in chunk.items():
            # An interrupt or a subgraph chunk carries no mapping to merge; only
            # a node's own update does.
            if isinstance(delta, dict):
                state.update(delta)
            yield node, state


def run_agent(
    question: str,
    *,
    company: str | CompanyPaths,
    llm: Any = None,
    persona: str | None = None,
    run_id: str | None = None,
    config: dict[str, Any] | None = None,
) -> AgentState:
    """Run the graph to completion. `llm=None` is the deterministic path -- no key,
    no call.

    The model object and the run's token accounting travel together in the config;
    the two nodes that call a model reach for them there and call LangChain
    directly. Nothing in between knows a model exists.
    """
    final: AgentState = {}
    for _node, final in stream_agent(
        question, company=company, llm=llm, persona=persona, run_id=run_id, config=config
    ):
        pass
    return final
