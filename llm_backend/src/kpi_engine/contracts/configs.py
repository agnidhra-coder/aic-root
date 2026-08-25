"""Config models. Every YAML file under `configs/` deserialises into one of these.

Design rule: nothing in the engine reads a raw dict. A config that does not
validate against these models never reaches a computation, which is what makes
LLM-authored configs safe to accept later.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AggFunc = Literal["sum", "mean", "min", "max", "median", "last", "first", "count"]
TimeGrain = Literal["day", "week", "month"]
Direction = Literal["higher_is_better", "lower_is_better", "neutral"]


class Strict(BaseModel):
    """Reject unknown keys everywhere: a typo in a config is an error, not a silent default."""

    model_config = ConfigDict(extra="forbid", frozen=False)


# --------------------------------------------------------------------------- source


class SourceSpec(Strict):
    """Binding to one physical dataset. `type` selects the adapter in sources.registry."""

    source_id: str
    type: Literal["csv", "excel"]
    path: str
    sheet: str | None = Field(default=None, description="Excel only.")
    date_column: str = "Date"
    entity_columns: list[str] = Field(
        default_factory=list,
        description="Dimension columns available for slicing (Region, Channel, ...).",
    )
    refresh_cadence: Literal["daily", "weekly", "monthly", "unknown"] = "daily"
    as_of: date | None = Field(
        default=None,
        description="Treat this as 'now' when computing freshness; None means use file mtime.",
    )
    dtype_overrides: dict[str, str] = Field(default_factory=dict)


# --------------------------------------------------------------------------- semantics


class MeasureDef(Strict):
    """One additive base quantity pulled from a source column.

    Measures are aggregated *before* the KPI expression runs, which is what
    forces ratio-of-sums semantics rather than mean-of-ratios.
    """

    column: str
    agg: AggFunc = "sum"


class Materiality(Strict):
    """A movement must clear both bars to be worth reporting: statistical and business."""

    min_abs_pct: float = Field(default=5.0, ge=0.0)
    min_business_impact: float = Field(
        default=0.0, ge=0.0, description="Absolute impact in the KPI's unit; 0 disables."
    )


class Guards(Strict):
    denominator_zero: Literal["nan", "zero", "error"] = "nan"
    min_rows_per_cell: int = Field(default=5, ge=1)
    min_history_periods: int = Field(default=28, ge=2)
    clip_quantiles: tuple[float, float] | None = None


class KpiDef(Strict):
    name: str
    category: str = "Uncategorised"
    measures: dict[str, MeasureDef]
    expression: str = Field(
        description="Arithmetic over measure aliases only. Parsed by the restricted "
        "AST evaluator in semantics.expressions; `eval` is never used."
    )
    unit: Literal["currency", "ratio", "percent", "count", "days"] = "ratio"
    direction: Direction = "neutral"
    description: str = ""
    drivers: list[str] = Field(
        default_factory=list,
        description="Business drivers named in the KPI documentation. Free text; the "
        "machine-usable version lives in the causal DAG.",
    )
    materiality: Materiality = Field(default_factory=Materiality)
    guards: Guards = Field(default_factory=Guards)

    @model_validator(mode="after")
    def _expression_uses_known_measures(self) -> KpiDef:
        from kpi_engine.semantics.expressions import referenced_names

        unknown = referenced_names(self.expression) - set(self.measures)
        if unknown:
            raise ValueError(
                f"KPI '{self.name}' expression references undefined measures: {sorted(unknown)}"
            )
        return self


class KpiContract(Strict):
    """The semantic contract: definitions, grain, access restrictions."""

    contract_id: str
    source_id: str
    time_grain: TimeGrain = "day"
    entity_keys: list[str] = Field(
        default_factory=list,
        description="Dimensions the panel is sliced by. Empty means total-only.",
    )
    kpis: list[KpiDef]
    restricted_columns: list[str] = Field(
        default_factory=list,
        description="Columns withheld from personas without entitlement. Recorded in "
        "lineage so redaction is auditable.",
    )

    @model_validator(mode="after")
    def _unique_kpi_names(self) -> KpiContract:
        names = [k.name for k in self.kpis]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"Duplicate KPI names in contract: {sorted(dupes)}")
        return self

    def kpi(self, name: str) -> KpiDef:
        for k in self.kpis:
            if k.name == name:
                return k
        raise KeyError(f"KPI '{name}' not in contract '{self.contract_id}'")


# --------------------------------------------------------------------------- causal


class CausalNode(Strict):
    """A node in the governed DAG.

    `controllable` and `owner` are what later turn a statistical driver into an
    actionable lever with decision rights attached.
    """

    name: str
    kind: Literal["measure", "kpi", "external"] = "measure"
    column: str | None = Field(default=None, description="Source column, when kind == measure.")
    controllable: bool = False
    owner: str | None = None
    description: str = ""


class CausalEdge(Strict):
    source: str
    target: str
    relation: Literal["deterministic", "causal"] = "causal"
    note: str = ""


class CausalGraphSpec(Strict):
    graph_id: str
    nodes: list[CausalNode]
    edges: list[CausalEdge]
    forbidden_edges: list[tuple[str, str]] = Field(
        default_factory=list,
        description="Semantically impossible directions, e.g. (Net Margin, Cost Of Ads). "
        "Rejected even if a discovery routine proposes them.",
    )

    @model_validator(mode="after")
    def _edges_reference_known_nodes(self) -> CausalGraphSpec:
        known = {n.name for n in self.nodes}
        for e in self.edges:
            missing = {e.source, e.target} - known
            if missing:
                raise ValueError(f"Edge {e.source}->{e.target} references unknown nodes: {missing}")
        return self


# --------------------------------------------------------------------------- detection


class DecompositionSpec(Strict):
    enabled: bool = True
    period: int = Field(default=7, ge=2, description="Seasonal period in time-grain steps.")
    robust: bool = True
    min_periods_required: int = Field(
        default=3, ge=2, description="Need this many full cycles before STL is trusted."
    )


class BaselineSpec(Strict):
    model: Literal["seasonal_naive", "ridge_lags"] = "ridge_lags"
    lags: list[int] = Field(default_factory=lambda: [1, 7, 14])
    rolling_windows: list[int] = Field(default_factory=lambda: [7, 28])
    min_train_periods: int = Field(default=28, ge=4)


class PointSpec(Strict):
    enabled: bool = True
    z_threshold: float = Field(default=3.5, gt=0)
    mad_floor: float = Field(
        default=1e-9, gt=0, description="Guards against zero MAD on constant stretches."
    )


class ChangePointSpec(Strict):
    enabled: bool = True
    method: Literal["pelt", "cusum", "both"] = "both"
    pelt_model: Literal["rbf", "l2", "l1"] = "rbf"
    pelt_penalty: float = Field(default=10.0, gt=0)
    min_segment: int = Field(default=14, ge=2)
    cusum_k: float = Field(default=0.5, gt=0, description="Slack in robust sigma units.")
    cusum_h: float = Field(default=5.0, gt=0, description="Decision interval in robust sigma units.")


class MultivariateSpec(Strict):
    enabled: bool = True
    alpha: float = Field(default=0.001, gt=0, lt=1, description="Chi-square tail probability.")
    shrinkage: float = Field(default=0.1, ge=0.0, le=1.0)
    min_train_periods: int = Field(default=60, ge=10)


class WindowingSpec(Strict):
    merge_gap: int = Field(
        default=2,
        ge=0,
        description="Flags this many *periods* apart join one event window. Converted to "
        "days using the contract's time grain, so the value means the same thing "
        "whether the panel is daily or weekly.",
    )
    min_window_len: int = Field(default=1, ge=1)
    min_flags: int = Field(
        default=2,
        ge=1,
        description="Corroboration bar. A lone flag on a heavy-tailed ratio is usually "
        "noise; requiring a second flag (or a second detector) is a far more "
        "stable filter than chasing the z-threshold upward.",
    )
    min_detectors: int = Field(
        default=1,
        ge=1,
        description="Alternative corroboration route: agreement across detector families.",
    )
    require_material: bool = Field(
        default=True,
        description="Drop events where no KPI cleared its materiality bar. Statistical "
        "significance without business impact is not a finding.",
    )


class DetectionSpec(Strict):
    profile_id: str = "default"
    decomposition: DecompositionSpec = Field(default_factory=DecompositionSpec)
    baseline: BaselineSpec = Field(default_factory=BaselineSpec)
    point: PointSpec = Field(default_factory=PointSpec)
    changepoint: ChangePointSpec = Field(default_factory=ChangePointSpec)
    multivariate: MultivariateSpec = Field(default_factory=MultivariateSpec)
    windowing: WindowingSpec = Field(default_factory=WindowingSpec)
    confidence_threshold: float = Field(
        default=0.70, ge=0.0, le=1.0, description="Below this the engine abstains."
    )


# --------------------------------------------------------------------------- eda


class TrendSpec(Strict):
    alpha: float = Field(
        default=0.05, gt=0, lt=1, description="Mann-Kendall significance for calling a direction."
    )
    min_periods: int = Field(
        default=8, ge=4, description="Below this a slope is not meaningfully estimable."
    )
    flat_slope_pct: float = Field(
        default=0.5,
        ge=0,
        description="A per-period rate at or above this |%/period| is material on its own.",
    )
    min_total_change_pct: float = Field(
        default=5.0,
        ge=0,
        description="A trend is also material if it moves the level this much end to end, "
        "however slowly. Matches the Materiality.min_abs_pct convention. Both bars "
        "exist because neither alone works: a rate test calls a 0.4%/period drift "
        "flat when it compounds to +52% over two years, and a cumulative test alone "
        "would flag a barely-moving series measured over a long enough window.",
    )


class SegmentSpec(Strict):
    enabled: bool = True
    pelt_penalty: float = Field(
        default=12.0, gt=0, description="Matches configs/detection/default.yaml for consistency."
    )
    min_segment: int = Field(default=4, ge=2)
    max_segments: int = Field(
        default=6, ge=1, description="Keeps the narration surface small and readable."
    )


class EdaSpec(Strict):
    profile_id: str = "default"
    decomposition: DecompositionSpec = Field(default_factory=DecompositionSpec)
    trend: TrendSpec = Field(default_factory=TrendSpec)
    segments: SegmentSpec = Field(default_factory=SegmentSpec)
    min_history: int = Field(
        default=12, ge=2, description="Fewer periods than this and the profile is marked unusable."
    )
    seasonality_floor: float = Field(
        default=0.45,
        ge=0.0,
        le=1.0,
        description="Below this strength, report no seasonality rather than subtract noise. "
        "Calibrated above the p99 of STL seasonal strength on pure noise; see "
        "configs/eda/default.yaml for the measured null distribution.",
    )
    outlier_z: float = Field(
        default=3.5, gt=0, description="Modified-z cutoff for the descriptive outlier share."
    )


# --------------------------------------------------------------------------- scenarios


class ColumnOp(Strict):
    op: Literal["multiply", "add", "set"]
    value: float


class EventWindowSpec(Strict):
    start: date
    end: date

    @model_validator(mode="after")
    def _ordered(self) -> EventWindowSpec:
        if self.end < self.start:
            raise ValueError(f"Event window end {self.end} precedes start {self.start}")
        return self


class InjectedEvent(Strict):
    """One synthetic movement stamped onto the base data, with its truth recorded."""

    event_id: str
    description: str = ""
    target_columns: dict[str, ColumnOp]
    filters: dict[str, list[str]] = Field(
        default_factory=dict, description="Dimension value filters; empty means all rows."
    )
    window: EventWindowSpec
    shape: Literal["step", "ramp", "spike", "decay"] = "step"
    propagate: bool = Field(
        default=True,
        description="Re-derive dependent columns through the accounting identities so the "
        "mutated dataset stays internally consistent.",
    )
    affected_kpis: list[str] = Field(default_factory=list)
    true_drivers: list[str] = Field(default_factory=list)


class ScenarioSpec(Strict):
    scenario_id: str
    description: str = ""
    base_source_id: str
    seed: int = 42
    events: list[InjectedEvent]


ConfigModel = Annotated[
    SourceSpec | KpiContract | CausalGraphSpec | DetectionSpec | ScenarioSpec,
    Field(discriminator=None),
]
