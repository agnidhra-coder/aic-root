"""`AgentState` -> typed events, one per stage, as soon as that stage has data.

This is the only module that serialises anything. It matters that it stays that
way: `AgentState` carries a live `ChatGoogleGenerativeAI` and a `Usage` in its
config, raw DataFrames in `sources`, and `PipelineResult` dataclasses in
`results`. None of those may reach the wire -- not because they would fail to
encode, but because a payload assembled by dumping state is a payload nobody
decided the shape of. Every event below is built by naming its fields.

The events mirror the six panels `render_console` prints, in the order the graph
produces them, so the terminal view and a UI built on this stream are two
renderings of one run rather than two accounts of it.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Iterator
from typing import Any

from kpi_agent import render
from kpi_agent.graph import stream_agent
from kpi_agent.llm import DEFAULT_MODEL

from kpi_engine.tenancy import CompanyPaths
from kpi_api.models import AskRequest, build_config

# A run ends in one of three terminal nodes, all of them honest outcomes.
_TERMINAL = {"report": "report", "clarify": "clarification", "no_findings": "no_findings"}

Event = tuple[str, dict[str, Any]]


def ask_events(
    req: AskRequest,
    llm: Any = None,
    *,
    paths: CompanyPaths,
    run_id: str | None = None,
) -> Iterator[Event]:
    """Run the agent, yielding `(event_name, payload)` as each stage completes.

    The final `done` event names the outcome. `error` is yielded rather than
    raised, because a stream that has already delivered a plan and an engine
    summary should end by saying what went wrong, not by closing silently.
    """
    started = time.monotonic()
    config = build_config(paths, req)
    # Settled here rather than left to the graph, so the very first event can
    # name the directory the run will write to and a client can poll for it.
    run_id = run_id or req.run_id or f"ask-{dt.datetime.now():%Y%m%d-%H%M%S}"

    yield "started", {
        "run_id": run_id,
        "company": paths.slug,
        "question": req.question,
        "persona": req.persona,
        "model": None if req.no_llm else (req.model or DEFAULT_MODEL),
        "no_llm": req.no_llm,
        "time_grain": req.time_grain,
        "entity_keys": req.entity_keys,
    }

    ctx: dict[str, Any] = {"narrate_attempts": 0, "verify_attempts": 0}
    outcome = "incomplete"
    final_run_id = run_id

    try:
        for node, state in stream_agent(
            req.question, company=paths, llm=llm, persona=req.persona,
            run_id=run_id, config=config,
        ):
            final_run_id = state.get("run_id", run_id)
            yield from _events_for(node, state, ctx)
            if node in _TERMINAL:
                outcome = _TERMINAL[node]
    except Exception as exc:  # noqa: BLE001 - reported to the client, not swallowed
        yield "error", {"type": type(exc).__name__, "message": str(exc)}
        outcome = "error"

    yield "done", {
        "run_id": final_run_id,
        "outcome": outcome,
        "duration_ms": round((time.monotonic() - started) * 1000),
    }


def _events_for(node: str, state: dict, ctx: dict) -> Iterator[Event]:
    if node == "ingest_sources":
        yield "ingest", _ingest(state)

    elif node == "plan_intent":
        yield "plan", _plan(state, stage="proposed")

    elif node == "validate_intent":
        # A rejected plan produces no resolved intent; the `clarify` node that
        # follows carries the answer, so there is nothing to report here yet.
        if not state.get("clarification"):
            yield "plan", _plan(state, stage="resolved")

    elif node == "run_pipelines":
        yield "engine", _engine(state, stage="pipelines")

    elif node == "link_sources":
        yield "engine", _engine(state, stage="links")

    elif node == "assemble_facts":
        yield "evidence", _evidence(state)

    elif node in ("narrate", "use_fallback"):
        ctx["narrate_attempts"] += 1
        yield "narrative", _narrative(state, ctx["narrate_attempts"])
        if node == "use_fallback":
            # The template is verified too -- it is generated from the fact table
            # rather than from a model, so it should pass, and if it ever does not
            # that is a bug worth surfacing rather than one to hide.
            ctx["verify_attempts"] += 1
            yield "verification", _verification(state, ctx["verify_attempts"])

    elif node == "verify":
        ctx["verify_attempts"] += 1
        yield "verification", _verification(state, ctx["verify_attempts"])

    elif node == "clarify":
        yield "clarification", {
            "message": state.get("clarification")
            or "I need more detail to answer that.",
            "problems": state.get("intent_problems", []),
            "report_markdown": state.get("report_markdown", ""),
        }

    elif node == "no_findings":
        yield "no_findings", _no_findings(state)

    elif node == "report":
        yield "telemetry", dict(state.get("telemetry") or {})
        yield "report", _report(state)


# --------------------------------------------------------------------------- #
# Projections. Each one names its fields; none of them dumps state.
# --------------------------------------------------------------------------- #


def _ingest(state: dict) -> dict:
    sources = []
    for spec, _contract, _profile, df in state.get("sources", []):
        sources.append({
            "source_id": spec.source_id,
            "rows": int(len(df)),
            "columns": int(len(df.columns)),
        })
    return {
        "sources": sources,
        "coverage_days": state.get("coverage_days"),
        "min_train_periods": state.get("min_train_periods"),
    }


def _plan(state: dict, *, stage: str) -> dict:
    intent = state.get("intent")
    return {
        "stage": stage,
        "intent": intent.model_dump(mode="json") if intent else None,
        # Adjustments the validator made and recorded -- a grain stepped down, an
        # override the data could not support. Never a silent substitution.
        "problems": state.get("intent_problems", []),
    }


def _engine(state: dict, *, stage: str) -> dict:
    return {
        "stage": stage,
        "per_source": render.engine_summary(state.get("results")),
        "links": [link.model_dump(mode="json") for link in state.get("links", [])],
        "errors": state.get("errors", []),
    }


def _evidence(state: dict) -> dict:
    """The fact table, in full.

    Every fact goes out, not only the cited ones: this event is emitted before a
    narrative exists, so there is nothing yet to have cited anything. A client
    that wants the console's cited-only view filters on the `evidence_ids` the
    `narrative` event carries.
    """
    context = state.get("context")
    if context is None:
        return {}
    dumped = context.model_dump(mode="json")
    return {
        "facts": dumped["facts"],
        "events": dumped["events"],
        "links": dumped["links"],
        "abstentions": dumped["abstentions"],
        "abstention_summary": render.abstention_summary(context),
        "freshness": dumped["freshness"],
        "data_caveats": dumped["data_caveats"],
        "levers": dumped["levers"],
        "persona_brief": dumped["persona_brief"],
        "question_restated": dumped["question_restated"],
        "persona": dumped["persona"],
        "time_grain": dumped["time_grain"],
        "entity_keys": dumped["entity_keys"],
        "period_start": dumped["period_start"],
        "period_end": dumped["period_end"],
    }


def _narrative(state: dict, attempt: int) -> dict:
    narrative = state.get("narrative")
    return {
        "attempt": attempt,
        "narrative": narrative.model_dump(mode="json") if narrative else None,
        "used_fallback": bool(state.get("used_fallback")),
        "fallback_reason": state.get("fallback_reason", ""),
    }


def _verification(state: dict, attempt: int) -> dict:
    verification = state.get("verification")
    payload = verification.model_dump(mode="json") if verification else {}
    return {"attempt": attempt, **payload}


def _no_findings(state: dict) -> dict:
    """Say precisely what was searched.

    A bare "nothing found" is unfalsifiable: the reader cannot tell a genuinely
    quiet quarter from a planner that narrowed the window to a week with no data.
    So the configuration that produced the empty result is part of the answer
    here, exactly as it is in the written report.
    """
    intent = state.get("intent")
    period = "the full history"
    if intent and (intent.date_start or intent.date_end):
        period = f"{intent.date_start or 'the start'} to {intent.date_end or 'the end'}"
    return {
        "searched": {
            "time_grain": intent.time_grain if intent else None,
            "entity_keys": list(intent.entity_keys) if intent else [],
            "period": period,
            "sources": list((state.get("results") or {}).keys()),
        },
        "intent": intent.model_dump(mode="json") if intent else None,
        "per_source": render.engine_summary(state.get("results")),
        "errors": state.get("errors", []),
        "report_markdown": state.get("report_markdown", ""),
    }


def _report(state: dict) -> dict:
    path = state.get("report_path", "")
    return {
        "report_markdown": state.get("report_markdown", ""),
        "report_path": path,
        "report_json_path": path[: -len(".md")] + ".json" if path.endswith(".md") else "",
    }


def collapse(events: list[Event]) -> dict[str, Any]:
    """Fold a stream into one object, for the non-streaming endpoint.

    Built from the same events rather than from the state, so the two endpoints
    cannot disagree about what a run produced. Events that can repeat -- the
    repair loop's narrative and verification, the plan's proposed-then-resolved,
    the engine's two stages -- collapse to the last one, which is the final word;
    `log` accumulates.
    """
    out: dict[str, Any] = {"log": []}
    for name, payload in events:
        if name == "log":
            out["log"].append(payload)
        else:
            out[name] = payload
    return out
