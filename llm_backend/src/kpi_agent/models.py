"""Typed contracts for everything crossing the LLM boundary.

Two of these are what the model is *constrained to produce* (`AnalysisIntent`,
`Narrative`); the rest are what the deterministic side produces and the model is
allowed to read. Keeping them in one file makes the boundary legible: anything
here is either an input the model may see or an output it may emit, and nothing
else reaches it.

`extra="forbid"` throughout. A model that invents a field should fail validation
rather than have the field quietly ignored -- an ignored field is a silent
divergence between what the model thought it said and what the engine acted on.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Persona = Literal["analyst", "exec", "ops"]
TimeGrain = Literal["day", "week", "month"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- #
# LLM output #1: the analysis plan
# --------------------------------------------------------------------------- #


class AnalysisIntent(Strict):
    """A user question, resolved against the catalog into a runnable configuration.

    This is the only place a natural-language request is allowed to influence what
    the engine computes, and every field is checked against the catalog afterwards
    by `intent.validate_intent`. The model proposes; the validator decides.
    """

    kpis: list[str] = Field(
        default_factory=list,
        description="KPI names to analyse, exactly as spelled in the catalog. "
        "Empty means every KPI in the named sources.",
    )
    entity_keys: list[str] = Field(
        default_factory=list,
        description="Dimension columns to slice by, from the catalog's entity columns. "
        "Empty means total level. Prefer the fewest keys that answer the question -- "
        "deeper slices have thinner support.",
    )
    time_grain: TimeGrain = Field(
        description="Aggregation grain. Choose the coarsest grain that still resolves "
        "the question; the catalog states which grains have sufficient coverage."
    )
    date_start: str | None = Field(
        default=None, description="ISO date, or null for the full history."
    )
    date_end: str | None = Field(default=None, description="ISO date, or null.")
    sources: list[str] = Field(
        default_factory=list,
        description="Source ids to run, from the catalog. Include the supply-chain "
        "source when the question could plausibly have a supply-side cause.",
    )
    persona: Persona = Field(
        description="Who is asking, which sets the depth and framing of the answer."
    )
    question_restated: str = Field(
        description="The question as you understood it, in one sentence."
    )
    reasoning: str = Field(
        default="",
        description="Why this configuration answers the question. One or two sentences.",
    )
    clarification_needed: str | None = Field(
        default=None,
        description="Set this ONLY if the question cannot be resolved against the "
        "catalog -- an unknown KPI, an ambiguous entity, a period outside coverage. "
        "State the single question that would resolve it. Null otherwise.",
    )


# --------------------------------------------------------------------------- #
# Deterministic input to the narrator
# --------------------------------------------------------------------------- #


class Fact(Strict):
    """One number the narrator is permitted to state, with where it came from."""

    id: str
    label: str
    value: float | None
    display: str
    unit: str | None = None
    kind: str
    kpi: str | None = None
    entity: dict[str, str] = Field(default_factory=dict)
    source_id: str | None = None
    method: str | None = None
    exact: bool | None = None
    controllable: bool | None = None
    owner: str | None = None
    lineage: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None


class CrossSourceLink(Strict):
    """A sales-side event and a supply-side event the DAG permits connecting.

    `dag_path` is the licence. Without a declared path from the supply-side node to
    a driver of the affected KPI there is no link, however well the two windows
    happen to align -- which is the difference between reading a graph and mining
    a coincidence.
    """

    sales_event_id: str
    scm_event_id: str
    shared_entity: dict[str, str]
    sales_window: tuple[str, str]
    scm_window: tuple[str, str]
    overlap_days: int
    lag_days: int
    scm_kpis_moved: list[str]
    sales_kpis_moved: list[str]
    dag_path: list[str]
    dag_edge: tuple[str, str]
    relation: str
    note: str


class GroundedContext(Strict):
    """Everything the narrator sees. Nothing else is in its context window."""

    question: str
    question_restated: str
    persona: Persona
    run_id: str
    time_grain: str
    entity_keys: list[str]
    period_start: str | None = None
    period_end: str | None = None
    facts: list[Fact] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    links: list[CrossSourceLink] = Field(default_factory=list)
    abstentions: list[dict[str, Any]] = Field(default_factory=list)
    freshness: list[dict[str, Any]] = Field(default_factory=list)
    data_caveats: list[str] = Field(default_factory=list)
    levers: list[dict[str, Any]] = Field(default_factory=list)
    persona_brief: str = ""

    def fact(self, fact_id: str) -> Fact | None:
        return next((f for f in self.facts if f.id == fact_id), None)


# --------------------------------------------------------------------------- #
# LLM output #2: the narrative
# --------------------------------------------------------------------------- #


class Claim(Strict):
    text: str = Field(description="One sentence. Any number in it must be quoted "
                                  "from a fact's `display` value.")
    evidence_ids: list[str] = Field(
        description="Fact ids supporting this sentence. At least one, always."
    )


class Action(Strict):
    driver: str = Field(description="The driver that moved, named exactly as the fact does.")
    lever: str = Field(description="The controllable node that can act on it.")
    action: str = Field(description="What to actually do. Concrete and bounded.")
    owner: str = Field(description="The owner the evidence names for that lever.")
    expected_impact: str = Field(
        description="What should change if this works, referencing a quantified fact."
    )
    confidence: float = Field(description="0-1, taken from the event's confidence score.")
    monitoring: str = Field(description="Which KPI to watch, at which grain, over what period.")
    evidence_ids: list[str] = Field(default_factory=list)


class Narrative(Strict):
    headline: str = Field(description="One line a busy reader could act on.")
    what_happened: list[Claim] = Field(default_factory=list)
    why: list[Claim] = Field(
        default_factory=list,
        description="Attributed causes only. If a KPI's evidence abstained, it does "
        "not get a 'why' -- put it in `abstained_from` instead.",
    )
    needs_attention: list[Claim] = Field(
        default_factory=list,
        description="What a human should look at now, most urgent first.",
    )
    actions: list[Action] = Field(default_factory=list)
    uncertainty: str = Field(
        default="",
        description="What this analysis cannot tell you, and why. Be specific about "
        "which method was used and what it assumes.",
    )
    abstained_from: list[str] = Field(
        default_factory=list,
        description="Questions the evidence was insufficient to answer, and what "
        "would resolve each.",
    )


# --------------------------------------------------------------------------- #
# Deterministic verification
# --------------------------------------------------------------------------- #


class Violation(Strict):
    code: Literal[
        "ungrounded_number",
        "unknown_evidence_id",
        "missing_evidence",
        "unknown_driver",
        "unknown_owner",
        "uncontrollable_lever",
        "causal_claim_on_abstention",
        "direction_contradicts_evidence",
        "confidence_not_grounded",
    ]
    where: str
    detail: str


class VerificationResult(Strict):
    passed: bool
    violations: list[Violation] = Field(default_factory=list)
    numbers_checked: int = 0
    claims_checked: int = 0

    def as_feedback(self) -> str:
        lines = [
            f"- [{v.code}] {v.where}: {v.detail}" for v in self.violations
        ]
        return "\n".join(lines)
