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


class ExogenousFactor(Strict):
    """Something the USER says happened, which the data does not contain.

    Weather, a strike, a competitor's promotion, a public holiday. It is
    *transcribed*, never measured: the model's job here is to record what was
    said without inventing specifics around it, and nothing downstream is
    permitted to turn one of these into a quantity. An `EvidenceBundle` is what
    the engine found; this is what the user believes, and the two must stay
    visibly different all the way to the page.

    Flat string fields rather than a nested `dict[str, str]` for the entity, for
    the same reason `MeasureBinding` is flat: nested maps compile to
    `additionalProperties`, which these APIs handle least reliably.
    """

    label: str = Field(description="A short name for it. 'heatwave', 'rail strike'.")
    detail: str = Field(
        description="What the user said, in one sentence. Transcribe it -- do not "
        "add a magnitude, a date or a mechanism they did not give you."
    )
    date_start: str | None = Field(
        default=None,
        description="ISO date, only if they gave one. Null if they were vague.",
    )
    date_end: str | None = Field(default=None, description="ISO date, or null.")
    entity_key: str | None = Field(
        default=None,
        description="A catalog entity column, if they scoped it to one. Null otherwise.",
    )
    entity_value: str | None = Field(
        default=None, description="A value of that column, verbatim from the catalog."
    )
    affects_kpis: list[str] = Field(
        default_factory=list,
        description="Catalog KPI names they said it touched. Empty means they did "
        "not say, which is not the same as 'all'.",
    )
    expected_direction: Literal["increase", "decrease", "unknown"] = Field(
        default="unknown",
        description="Which way they think it pushed the KPI. 'unknown' unless they said.",
    )


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
    exogenous: list[ExogenousFactor] = Field(
        default_factory=list,
        description="Context the question asserts that the data does not contain. "
        "Recording one changes nothing about the configuration you choose: it is a "
        "hypothesis for the reader to weigh, and the engine never treats it as a cause.",
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


class ContextAlignment(Strict):
    """A user-asserted factor whose window happens to sit on a detected event.

    Deliberately *not* a `CrossSourceLink`. A link is licensed by a declared path
    through the causal graph; this is date and entity arithmetic and nothing else,
    so it is a coincidence in time that a reader may find worth knowing. It never
    acquires a licence, never carries a share, and never becomes a cause -- the
    note says so, the fact built from it says so, and `verify.py` enforces it.
    """

    factor_label: str
    event_id: str
    source_id: str | None = None
    overlap_days: int
    lag_days: int
    entity_match: Literal["exact", "unscoped"]
    kpis_moved: list[str] = Field(default_factory=list)
    direction_agrees: bool | None = Field(
        default=None,
        description="Whether the user's expected direction matches the observed "
        "sign. None when either side did not say.",
    )
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
    # What the user asserted, resolved. Present even where nothing aligned, so a
    # hypothesis the evidence cannot place is answered rather than ignored.
    exogenous: list[dict[str, Any]] = Field(default_factory=list)
    alignments: list[ContextAlignment] = Field(default_factory=list)
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
    confidence: float | None = Field(
        default=None,
        description="0-1, taken from the event's confidence score. Null when the "
        "recommendation rests on a descriptive finding rather than a measured "
        "explanation -- say so in the text rather than inventing a number.",
    )
    monitoring: str = Field(description="Which KPI to watch, at which grain, over what period.")
    evidence_ids: list[str] = Field(default_factory=list)


class GeneralRecommendation(Strict):
    """A suggestion drawn from general business/industry knowledge, not this
    tenant's measured evidence -- there is no external knowledge base to ground
    it against, so it is kept structurally separate from `Action` rather than
    dressed up as one. Never cited as evidence, never carries a number, never a
    cause.
    """

    related_kpi: str = Field(
        description="A KPI named in the facts that this recommendation is about."
    )
    action: str = Field(
        description="What to consider doing. Concrete framing, but explicitly a "
        "general practice, not a measured finding."
    )
    rationale: str = Field(
        description="Why this is generally good practice for this kind of KPI "
        "movement -- domain reasoning, not data."
    )


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
    general_recommendations: list[GeneralRecommendation] = Field(
        default_factory=list,
        description="2-3 extra suggestions from general business knowledge, "
        "offered because this tenant has no causal lever measured for everything "
        "that moved. Never grounded in the fact table -- no numbers, no "
        "evidence_ids.",
    )
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
        "unlicensed_context_cause",
        "direction_contradicts_evidence",
        "confidence_not_grounded",
        "unknown_kpi",
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


# --------------------------------------------------------------------------- #
# LLM output #3: which columns hold which KPI's measures
# --------------------------------------------------------------------------- #


class MeasureBinding(Strict):
    """One alias of one catalogue KPI, bound to one column of this tenant's file.

    A flat list of these, rather than `dict[str, dict[str, str]]`. Nested maps
    compile to `additionalProperties`, which is the JSON-schema construct these
    APIs handle least reliably; a list of small objects with named string fields
    is what survives the round trip intact.
    """

    kpi: str = Field(description="Catalogue KPI name, verbatim. Never invented.")
    alias: str = Field(description="Measure alias, verbatim from the catalogue entry.")
    column: str = Field(description="Column header, verbatim from the file.")
    confidence: Literal["exact", "likely", "guess"] = Field(
        default="likely",
        description="How sure you are the column holds that quantity.",
    )


class KpiBindingProposal(Strict):
    """Which catalogue KPIs this file can compute, and from which columns.

    The model chooses column bindings and nothing else. Expressions, units,
    directions, categories and thresholds all come from the catalogue, which is
    checked into git -- so a wrong binding costs one KPI, and there is no way for
    a wrong *formula* to exist at all.
    """

    date_column: str = Field(description="The column holding the observation date.")
    entity_columns: list[str] = Field(
        default_factory=list,
        description="Low-cardinality dimensions worth slicing by (Region, Channel).",
    )
    bindings: list[MeasureBinding] = Field(default_factory=list)
    unmatched_columns: list[str] = Field(
        default_factory=list,
        description="Columns you could not place. Reported to the user, not an error.",
    )
    reasoning: str = ""


# --------------------------------------------------------------------------- #
# LLM output #4: the causal mechanisms between this tenant's measures
# --------------------------------------------------------------------------- #


class ProposedEdge(Strict):
    """A behavioural mechanism between two measure nodes.

    Deliberately no `relation` field. Deterministic edges follow from the
    confirmed contract's arithmetic and are derived, not proposed; everything here
    is `causal` by construction.
    """

    source: str = Field(description="The node that moves first, verbatim from NODES.")
    target: str = Field(description="The node it moves, verbatim from NODES.")
    note: str = Field(default="", description="The mechanism, in one clause.")


class ProposedLever(Strict):
    """A node a named team can actually set."""

    node: str
    controllable: bool
    owner: str | None = Field(default=None, description="A team, never a person.")


class CausalProposal(Strict):
    edges: list[ProposedEdge] = Field(default_factory=list)
    levers: list[ProposedLever] = Field(default_factory=list)
    reasoning: str = ""
