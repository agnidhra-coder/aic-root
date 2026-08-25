"""Output payloads. Every stage emits one of these as JSON.

The chain is deliberate: Flag -> EventWindow -> Attribution -> EvidenceBundle.
The EvidenceBundle is the *only* object a narration layer should ever receive,
so anything an LLM needs to say must be a field here, computed deterministically.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from kpi_engine import SCHEMA_VERSION

AnomalyType = Literal[
    "Point Deviation",
    "Sustained Trend Drift",
    "Structural Break",
    "Multivariate Divergence",
]
CausalMethod = Literal["algebraic_lmdi", "mix_decomposition", "did", "its", "dml", "none"]


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = SCHEMA_VERSION


# --------------------------------------------------------------------------- source/profile


class Freshness(Payload):
    source_id: str
    max_date: date | None
    as_of: date
    lag_days: int | None
    refresh_cadence: str
    is_stale: bool


class ColumnProfile(Payload):
    name: str
    dtype: str
    non_null: int
    nulls: int
    zeros: int
    min: float | None = None
    max: float | None = None
    mean: float | None = None


class IdentityFinding(Payload):
    """A column that is exactly reproducible from others.

    Anything listed here is barred from acting as an independent driver: a
    regression on a perfectly collinear column produces an arbitrary split of
    the effect, not an attribution.
    """

    target: str
    components: list[str]
    coefficients: list[float]
    max_abs_residual: float
    kind: Literal["duplicate", "linear_identity"]


class GrainCoverage(Payload):
    keys: list[str]
    n_slices: int
    filled_cells: int
    possible_cells: int
    mean_rows_per_cell: float
    sufficient: bool


class SourceDescription(Payload):
    source_id: str
    path: str
    n_rows: int
    columns: list[str]
    dtypes: dict[str, str]


class DataProfile(Payload):
    source_id: str
    generated_at: datetime
    description: SourceDescription
    freshness: Freshness
    columns: list[ColumnProfile]
    duplicate_groups: list[list[str]]
    identities: list[IdentityFinding]
    identity_groups: list[list[str]] = Field(
        default_factory=list,
        description="Sets of columns tied by one exact linear constraint. Each such set "
        "carries k-1 degrees of freedom, so exactly one member is dropped.",
    )
    redundant_columns: dict[str, str] = Field(
        default_factory=dict,
        description="column -> why it is barred as an independent driver.",
    )
    coverage: list[GrainCoverage]

    def blocked_driver_columns(self) -> set[str]:
        """Columns that must not be used as independent causal drivers."""
        return set(self.redundant_columns)


# --------------------------------------------------------------------------- detection


class Lineage(Payload):
    source_id: str
    source_path: str
    contract_id: str
    kpi: str
    columns: list[str]
    time_grain: str
    entity_filter: dict[str, str] = Field(default_factory=dict)


# --------------------------------------------------------------------------- eda / series profile
#
# Descriptive, never inferential. Detection answers "is this point unusual?";
# these answer the prior question "what is this series doing at all?" -- the
# baseline context a narration layer needs to place an event against. Nothing
# downstream branches on these, which is what keeps the stage unable to regress
# detection.

TrendDirection = Literal["rising", "falling", "flat", "indeterminate"]


class TrendSummary(Payload):
    """Direction and rate of the underlying level, robust to outliers.

    Theil-Sen rather than OLS on purpose: OLS slope is dragged by exactly the
    spikes detection exists to flag, so a single anomaly would be reported as a
    trend. Significance is Mann-Kendall (rank-based, no normality assumption).
    """

    direction: TrendDirection
    slope_per_period: float = Field(description="Theil-Sen slope in KPI units per period.")
    slope_pct_per_period: float | None = Field(
        default=None, description="Slope as % of the median level -- the number a human quotes."
    )
    trend_strength: float = Field(description="Hyndman-Wang trend strength, 0-1, from STL.")
    r_squared: float
    p_value: float = Field(description="Mann-Kendall; above alpha the direction is not called.")
    total_change_pct: float | None = Field(
        default=None, description="Fitted first-to-last change, in percent."
    )
    n_periods: int
    method: str = "theil_sen + mann_kendall"


class SeasonalitySummary(Payload):
    """Whether a repeating cycle exists -- and says so honestly when it does not."""

    detected: bool
    period: int | None = None
    strength: float = Field(description="Hyndman-Wang seasonal strength, 0-1.")
    peak_label: str | None = Field(default=None, description="Where in the cycle the peak sits.")
    trough_label: str | None = None
    reason: str = Field(description="Why seasonality was or was not called.")


class DataQualitySummary(Payload):
    """How far this series can be trusted. Tells a narrator when to stay quiet."""

    n_periods: int
    n_missing: int
    coverage: float = Field(description="Non-null periods / periods spanned.")
    longest_gap_periods: int
    mean_support: float = Field(description="Mean raw rows backing each cell.")
    min_support: int
    low_support_share: float = Field(description="Share of cells below the contract's guard.")
    sufficient_history: bool
    volatility_cv: float | None = Field(
        default=None, description="Robust coefficient of variation: MAD-sigma / |median|."
    )
    outlier_share: float = Field(
        default=0.0, description="Share beyond modified-z 3.5. Descriptive only, not a detection."
    )


class SeriesSegment(Payload):
    """A homogeneous stretch between structural breaks -- a 'phase' to narrate."""

    index: int
    start: date
    end: date
    n_periods: int
    mean: float
    slope_per_period: float
    direction: TrendDirection
    pct_change_vs_previous: float | None = None


class SeriesProfile(Payload):
    """Everything descriptive known about one KPI on one entity slice."""

    kpi: str
    entity: dict[str, str] = Field(default_factory=dict)
    period_start: date
    period_end: date
    time_grain: str
    trend: TrendSummary
    seasonality: SeasonalitySummary
    quality: DataQualitySummary
    segments: list[SeriesSegment] = Field(default_factory=list)
    headline: str = Field(
        description="Deterministic template sentence. A check on the narrator, not a substitute."
    )
    usable: bool = Field(description="False when history or support is too thin to characterise.")
    unusable_reason: str | None = None
    lineage: Lineage


class Flag(Payload):
    """One period flagged by one detector on one series."""

    kpi: str
    entity: dict[str, str] = Field(default_factory=dict)
    period: date
    detector: Literal["point", "changepoint_pelt", "changepoint_cusum", "multivariate"]
    anomaly_type: AnomalyType
    observed: float | None
    expected: float | None
    residual: float | None
    score: float = Field(description="Detector-native score (|z|, Mahalanobis D, CUSUM stat).")
    threshold: float
    support: int = Field(description="Raw rows backing this cell; drives abstention.")
    direction: Literal["up", "down", "unknown"] = "unknown"


class ObservedDeviation(Payload):
    kpi: str
    expected: float | None
    actual: float | None
    abs_delta: float | None
    pct_delta: float | None
    material: bool


class EventWindow(Payload):
    """Contiguous flags clustered into one reportable event."""

    event_id: str
    anomaly_types: list[AnomalyType]
    detectors: list[str]
    window_start: date
    window_end: date
    entity: dict[str, str] = Field(default_factory=dict)
    primary_kpis_affected: list[str]
    observed_deviations: list[ObservedDeviation]
    candidate_covariates: list[str]
    excluded_covariates: dict[str, str] = Field(
        default_factory=dict, description="covariate -> reason it was barred (collinearity, etc)."
    )
    peak_score: float
    n_flags: int
    min_support: int
    lineage: Lineage


# --------------------------------------------------------------------------- causal


class Contribution(Payload):
    driver: str
    contribution: float = Field(description="In the KPI's own units unless stated otherwise.")
    share: float = Field(description="Signed fraction of the total explained movement.")
    method: CausalMethod
    exact: bool = Field(description="True when derived algebraically rather than estimated.")
    std_error: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    controllable: bool = False
    owner: str | None = None


class MethodChoice(Payload):
    """Why one causal method was used and the others were not — evidence in its own right."""

    chosen: CausalMethod
    reason: str
    considered: dict[str, str] = Field(
        default_factory=dict, description="method -> why it was rejected."
    )
    preconditions_checked: dict[str, bool] = Field(default_factory=dict)


class Attribution(Payload):
    event_id: str
    kpi: str
    total_delta: float | None
    explained_delta: float | None
    residual_delta: float | None
    method_choice: MethodChoice
    contributions: list[Contribution]
    diagnostics: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- evidence


class AbstainPayload(Payload):
    """Emitted instead of a finding when evidence is insufficient or contradictory."""

    event_id: str | None
    kpi: str
    reason_code: Literal[
        "insufficient_support",
        "insufficient_history",
        "failed_parallel_trends",
        "contradictory_methods",
        "below_confidence_threshold",
        "no_valid_covariates",
    ]
    message: str
    missing_evidence: list[str] = Field(default_factory=list)
    what_would_resolve_it: list[str] = Field(default_factory=list)


class ConfidenceBreakdown(Payload):
    score: float = Field(ge=0.0, le=1.0)
    signal_to_noise: float | None = None
    support_factor: float | None = None
    history_factor: float | None = None
    estimator_precision: float | None = None
    method_agreement: float | None = None
    formula: str


class EvidenceBundle(Payload):
    """The single object a narration layer consumes. Nothing else is passed on.

    Every number here was produced by deterministic code or a named estimator;
    no field is intended to be filled by a language model.
    """

    event_id: str
    generated_at: datetime
    event: EventWindow
    attributions: list[Attribution]
    confidence: ConfidenceBreakdown
    abstained: bool
    abstention: AbstainPayload | None = None
    freshness: Freshness
    lineage: Lineage
    deterministic_methods: list[str] = Field(
        default_factory=list, description="Non-LLM methods that produced these numbers."
    )
    llm_methods: list[str] = Field(
        default_factory=list, description="Empty in v1; reserved for the narration stage."
    )


# --------------------------------------------------------------------------- telemetry


class StageTelemetry(Payload):
    stage: str
    started_at: datetime
    duration_ms: float
    rows_in: int | None = None
    rows_out: int | None = None
    notes: dict[str, Any] = Field(default_factory=dict)


class RunManifest(Payload):
    run_id: str
    started_at: datetime
    finished_at: datetime | None = None
    dataset: str
    configs: dict[str, str] = Field(default_factory=dict)
    stages: list[StageTelemetry] = Field(default_factory=list)
    total_duration_ms: float = 0.0
    counts: dict[str, int] = Field(default_factory=dict)
    llm_calls: int = 0
    llm_tokens_in: int = 0
    llm_tokens_out: int = 0
    llm_cost_usd: float | None = Field(
        default=0.0,
        description="None when the model's rates are not known. Distinct from 0.0, "
        "which would read as a run that cost nothing.",
    )
    llm_model: str | None = None
