"""The KPI plan: what is proposed for a tenant, what was decided, and what it wrote.

A company is created before it has data, and until now it was also created before
anyone had looked at that data -- its KPIs were whatever template it was seeded
from. These models carry the handshake that fixes that: an agent proposes a
mapping from the catalogue's KPIs onto this file's columns, a human edits and
confirms it, and the confirmed plan becomes the tenant's real contract and DAG.

Two files persist under the company:

- `configs/_draft/kpi_plan.json` -> `KpiPlan`. What was proposed. Rewritten by
  each new proposal, read by each confirmation.
- `configs/_confirmed/kpi_plan.json` -> `ConfirmedPlan`. What was decided, and
  what it produced. This is the provenance record, and it exists because
  `write_yaml` uses `safe_dump`: the generated `kpis.yaml` and `dag.yaml` carry no
  comments and cannot say where a binding came from. This can, per alias.

`KpiPlan` names paths (`staged_csv`) and so does `ConfirmedPlan` (`written`,
`superseded`). That is not the thing the "no field may name a path" invariant
forbids -- that rule is about *request* bodies on an unauthenticated API, where a
caller-supplied path string is an arbitrary file read. These are server-authored
records of what the server itself did, in the same way `SourceBinding.dataset`
is. `PlanConfirmation`, which *is* caller-supplied, names no path at all.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from kpi_engine import SCHEMA_VERSION
from kpi_engine.contracts.configs import (
    AggFunc,
    Direction,
    Guards,
    Materiality,
    Strict,
    TimeGrain,
)
from kpi_engine.contracts.tenancy import CompanySlug

# A plan id becomes a directory name under `configs/_superseded/`, so it is
# constrained here rather than checked at the point of use -- the same reasoning
# as `AskRequest.run_id`.
PLAN_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"

# Where a binding came from. Precedence runs left to right: an exact synonym match
# is settled and a model may not overturn it, but a human may overturn either.
BindingSource = Literal["synonym", "llm", "user", "unbound"]


class BoundMeasure(Strict):
    """One alias of one KPI, and the column it was bound to."""

    alias: str
    column: str | None = Field(
        default=None, description="None means nothing in this file names it."
    )
    agg: AggFunc = "sum"
    bound_by: BindingSource = "unbound"
    note: str = ""


class KpiProposal(Strict):
    """One catalogue KPI, as it would be computed from this file.

    Everything except `measures` is copied from the catalogue. The proposal is a
    *binding*, not a definition: the arithmetic was fixed in git long before this
    tenant existed.
    """

    name: str
    variant_id: str
    category: str
    unit: Literal["currency", "ratio", "percent", "count", "days"]
    direction: Direction
    description: str = ""
    expression: str
    doc_formula: str = ""
    drivers: list[str] = Field(default_factory=list)
    primary_metric: str = ""
    measures: list[BoundMeasure]
    materiality: Materiality
    guards: Guards
    computable: bool = Field(description="Every alias has a column.")
    aggregation_safe: bool = True
    recommended: bool = Field(
        description="Computable and aggregation-safe. What `--accept-all` takes."
    )
    caveats: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(
        default_factory=list,
        description="Other variant ids this file could satisfy, offered as a choice.",
    )

    @property
    def columns(self) -> list[str]:
        return [m.column for m in self.measures if m.column]


class UnavailableKpi(Strict):
    """A catalogue KPI this file cannot compute, and why.

    Reported rather than omitted: a KPI that is silently absent looks like an
    oversight, where one that says which column it wanted is a shopping list.
    """

    name: str
    doc_formula: str = ""
    missing_aliases: list[str] = Field(default_factory=list)
    tried_synonyms: list[str] = Field(default_factory=list)
    reason: str = ""


class ColumnReport(Strict):
    """One column of the uploaded file, and what became of it."""

    name: str
    dtype: str
    numeric: bool
    nulls: int = 0
    distinct: int | None = None
    role: Literal["date", "entity", "measure", "unused", "redundant"] = "unused"
    used_by: list[str] = Field(default_factory=list)
    redundant_because: str = ""


class KpiPlan(Strict):
    """`configs/_draft/kpi_plan.json` -- what the agent proposes, before anyone decides."""

    schema_version: str = SCHEMA_VERSION
    plan_id: str = Field(pattern=PLAN_ID_PATTERN)
    company_id: CompanySlug
    source_id: str
    catalog_id: str
    generated_at: datetime
    generated_by: Literal["llm", "deterministic"] = "deterministic"
    model: str | None = None
    staged_csv: str = Field(description="Company-relative path to the staged upload.")
    header: list[str] = Field(default_factory=list)
    n_rows: int = 0
    date_column: str | None = None
    entity_columns: list[str] = Field(default_factory=list)
    time_grain: TimeGrain = "week"
    proposed: list[KpiProposal] = Field(default_factory=list)
    unavailable: list[UnavailableKpi] = Field(default_factory=list)
    columns: list[ColumnReport] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)

    def kpi(self, name: str) -> KpiProposal:
        for p in self.proposed:
            if p.name == name:
                return p
        raise KeyError(
            f"Plan '{self.plan_id}' proposes no KPI '{name}'. "
            f"Proposed: {[p.name for p in self.proposed]}"
        )

    @property
    def recommended(self) -> list[str]:
        return [p.name for p in self.proposed if p.recommended]


class PlanDecision(Strict):
    """One verdict on one proposed KPI. A KPI not named here keeps its proposal."""

    name: str
    verdict: Literal["accept", "reject"] = "accept"
    variant_id: str | None = Field(
        default=None, description="Switch to a different variant of the same KPI."
    )
    bindings: dict[str, str] | None = Field(
        default=None,
        description="alias -> column, overriding what was proposed. Validated "
        "against the file's header like everything else.",
    )
    materiality: Materiality | None = None
    guards: Guards | None = None


class PlanConfirmation(Strict):
    """The user's half of the handshake. Names no filesystem path, deliberately."""

    plan_id: str = Field(pattern=PLAN_ID_PATTERN)
    decisions: list[PlanDecision] = Field(default_factory=list)
    date_column: str | None = None
    entity_columns: list[str] | None = Field(
        default=None,
        description="None means keep the plan's; [] means total level and is a "
        "real instruction, not an omission.",
    )
    time_grain: TimeGrain | None = None
    contract_id: str | None = None
    warm_up: bool = True


class ConfirmedPlan(Strict):
    """`configs/_confirmed/kpi_plan.json` -- the decision, and what it produced.

    Embeds the whole draft rather than referencing it, so the provenance survives
    the next proposal overwriting `_draft/`.
    """

    schema_version: str = SCHEMA_VERSION
    plan_id: str = Field(pattern=PLAN_ID_PATTERN)
    company_id: CompanySlug
    source_id: str
    confirmed_at: datetime
    draft: KpiPlan
    confirmation: PlanConfirmation
    accepted: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)
    contract_id: str
    graph_id: str
    written: list[str] = Field(
        default_factory=list, description="Company-relative paths this plan wrote."
    )
    superseded: str = Field(
        default="", description="Company-relative archive of what it replaced."
    )
    warm_up_run_id: str | None = None
    problems: list[str] = Field(default_factory=list)
