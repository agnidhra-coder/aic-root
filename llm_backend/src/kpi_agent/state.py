"""The state that flows through the graph."""

from __future__ import annotations

from typing import Any, TypedDict

from kpi_agent.models import (
    AnalysisIntent,
    CrossSourceLink,
    GroundedContext,
    Narrative,
    VerificationResult,
)


class AgentState(TypedDict, total=False):
    # --- inputs -------------------------------------------------------------
    question: str
    persona: str | None
    run_id: str
    config: dict[str, Any]

    # --- deterministic ingestion -------------------------------------------
    sources: list[Any]          # (SourceSpec, KpiContract, DataProfile, DataFrame)
    catalog: dict[str, Any]
    # What the grain check needs: the span the data covers and the number of
    # periods the detector's baseline must see before it emits an expectation.
    coverage_days: int | None
    min_train_periods: int | None

    # --- planning -----------------------------------------------------------
    intent: AnalysisIntent | None
    intent_problems: list[str]
    clarification: str | None

    # --- pipeline -----------------------------------------------------------
    results: dict[str, Any]     # source_id -> PipelineResult
    links: list[CrossSourceLink]

    # --- narration ----------------------------------------------------------
    context: GroundedContext | None
    narrative: Narrative | None
    verification: VerificationResult | None
    repair_attempts: int
    used_fallback: bool
    fallback_reason: str

    # --- output -------------------------------------------------------------
    report_markdown: str
    report_path: str
    telemetry: dict[str, Any]
    errors: list[str]
