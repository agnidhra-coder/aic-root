"""The state that flows through the graph."""

from __future__ import annotations

from typing import Any, TypedDict

from kpi_agent.models import (
    AnalysisIntent,
    ContextAlignment,
    CrossSourceLink,
    ExogenousFactor,
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
    # Derived, not asked of the model: an empty KPI list already means "every
    # KPI" everywhere downstream, so this reads the plan rather than adding a
    # field the planner could get wrong.
    survey: bool

    # --- pipeline -----------------------------------------------------------
    results: dict[str, Any]     # source_id -> PipelineResult
    links: list[CrossSourceLink]
    # What the user asserted, placed against the events -- and what could not be
    # placed, which is equally part of the answer.
    context_alignments: list[ContextAlignment]
    unaligned_factors: list[ExogenousFactor]

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
