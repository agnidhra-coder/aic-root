"""The KPI seed catalogue: the whole vocabulary a tenant may be offered.

`templates/reference/kpi_list.csv` is the human document, and its `Formula`
column is prose over display names -- `Store Entrances OR Website Sessions` is a
disjunction, `Purchase Value x Purchase Freq x Lifespan` uses a multiplication
sign `ast.parse` rejects outright, and `Beg. Inventory + Purchases - End.
Inventory` is a stock-flow identity that does not survive aggregation to a grain.
None of it can be handed to `semantics/expressions.py`, whose module docstring
already says a formula string is untrusted input.

So the arithmetic is transcribed once, here, by hand, and checked into git. That
is what lets the model's job shrink to the one thing it is actually good at:
deciding which column of *this* tenant's file holds the quantity an alias names.
The model never writes an expression, never invents a KPI, and never chooses a
threshold. `validate_expression` would catch a malformed string and nothing else
-- `revenue / revenue` parses perfectly well -- so "the model may not author a
formula" has to be a structural guarantee rather than a validation rule.

Three fields carry most of the weight:

- `variants` -- an ordered list of `(measures, expression)` pairs. The binder
  takes the first one every alias of which binds to a real column. That turns the
  document's `OR` into ordered fallback, and lets a KPI prefer a directly
  supplied column over an identity it would otherwise have to reconstruct.
- `aggregation_safe` -- `build_panel` aggregates measures to the grain and *then*
  evaluates the expression, which is what forces ratio-of-sums. A stock-flow
  identity summed over seven days is arithmetic nonsense, so such a variant is
  kept in the vocabulary, shown to the user, and never selected automatically.
- `synonyms` -- likely headers, most likely first, matched exactly under
  `normalise`. An exact match needs no model, which is what makes the `--no-llm`
  path a real answer rather than a stub.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from kpi_engine.contracts.configs import (
    AggFunc,
    Direction,
    Guards,
    KpiDef,
    Materiality,
    MeasureDef,
    Strict,
)

# What a measure *is*, which decides whether summing it to a grain is meaningful.
# `flow` accumulates over a period (revenue, units sold); `stock` is a level read
# at an instant (inventory on hand); `attribute` is a property that does not
# accumulate at all (square footage); `rate` is already a ratio.
MeasureKind = Literal["flow", "stock", "attribute", "rate"]


class SeedMeasure(Strict):
    """One alias of one catalogue KPI, and how to recognise its column."""

    alias: str = Field(
        pattern=r"^[a-z][a-z0-9_]*$",
        description="A Python identifier: it appears verbatim in the expression.",
    )
    agg: AggFunc = "sum"
    kind: MeasureKind = "flow"
    synonyms: list[str] = Field(
        default_factory=list,
        description="Likely column headers, most likely first. Matched exactly "
        "under `catalogue.normalise`, never fuzzily -- the honest claim is 'I "
        "recognised this header', not 'I guessed'.",
    )
    description: str = ""
    controllable_hint: bool = Field(
        default=False,
        description="Whether a named team can set this directly within a period. "
        "Domain knowledge, the same class as `forbidden_edges`, which is why it is "
        "written here rather than asked of a model.",
    )
    owner_hint: str | None = Field(
        default=None, description="A team, never a person."
    )


class SeedVariant(Strict):
    """One way of computing a KPI, given the columns a file happens to carry."""

    variant_id: str
    expression: str
    measures: list[SeedMeasure] = Field(min_length=1)
    aggregation_safe: bool = Field(
        default=True,
        description="False when the arithmetic does not survive being summed to a "
        "grain -- a stock-flow identity, or a rate of rates. Such a variant is "
        "offered and explained, never chosen automatically.",
    )
    note: str = ""

    @model_validator(mode="after")
    def _is_a_real_kpi_definition(self) -> SeedVariant:
        """Build the real thing with placeholder columns.

        This runs `validate_expression` *and* `KpiDef._expression_uses_known_measures`,
        so a broken seed row fails when the catalogue loads rather than at some
        tenant's first onboarding -- the same guarantee `load_template` gives a
        company template.
        """
        aliases = [m.alias for m in self.measures]
        dupes = sorted({a for a in aliases if aliases.count(a) > 1})
        if dupes:
            raise ValueError(f"Variant {self.variant_id!r} repeats aliases: {dupes}")
        KpiDef(
            name=self.variant_id,
            measures={m.alias: MeasureDef(column=m.alias, agg=m.agg) for m in self.measures},
            expression=self.expression,
        )
        return self

    @property
    def aliases(self) -> list[str]:
        return [m.alias for m in self.measures]

    def measure(self, alias: str) -> SeedMeasure:
        for m in self.measures:
            if m.alias == alias:
                return m
        raise KeyError(f"Variant '{self.variant_id}' declares no alias '{alias}'")


class SeedKpi(Strict):
    """One row of the KPI document, in a form the engine can act on."""

    name: str = Field(description="Verbatim from the document. The catalogue's key.")
    category: str = Field(
        description="Normalised to the contract vocabulary ('Customer & Marketing'), "
        "not the document's abbreviation ('Customer & Mktg')."
    )
    unit: Literal["currency", "ratio", "percent", "count", "days"]
    direction: Direction
    description: str = Field(default="", description="From 'What It Judges'.")
    drivers: list[str] = Field(
        default_factory=list,
        description="Split from 'Key Drivers'. Free text, as in KpiDef; the "
        "machine-usable form is the DAG.",
    )
    primary_metric: str = Field(
        default="",
        description="From 'Primary Metric Affected'. Has no counterpart in KpiDef "
        "and deliberately never reaches one -- it groups the plan for a reader and "
        "seeds the causal call, nothing more.",
    )
    doc_formula: str = Field(
        default="",
        description="The document's own formula, verbatim, for display beside the "
        "binding so a reviewer can check the transcription.",
    )
    materiality: Materiality = Field(default_factory=Materiality)
    guards: Guards = Field(default_factory=Guards)
    variants: list[SeedVariant] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_variant_ids(self) -> SeedKpi:
        ids = [v.variant_id for v in self.variants]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"KPI {self.name!r} repeats variant ids: {dupes}")
        return self

    def variant(self, variant_id: str) -> SeedVariant:
        for v in self.variants:
            if v.variant_id == variant_id:
                return v
        raise KeyError(
            f"KPI '{self.name}' has no variant '{variant_id}'. "
            f"Known: {[v.variant_id for v in self.variants]}"
        )


class SeedCatalog(Strict):
    """`templates/reference/kpi_catalog.yaml` -- the entire KPI vocabulary.

    Product-level reference, not a company config: it is never copied into a
    tenant and never edited per tenant. A tenant's `KpiContract` is *derived*
    from it, which is what keeps every tenant's Gross Profit Margin the same
    quantity.
    """

    catalog_id: str
    source_doc: str = "templates/reference/kpi_list.csv"
    kpis: list[SeedKpi] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_names(self) -> SeedCatalog:
        names = [k.name for k in self.kpis]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"Catalogue repeats KPI names: {dupes}")
        return self

    def kpi(self, name: str) -> SeedKpi:
        for k in self.kpis:
            if k.name == name:
                return k
        raise KeyError(f"No KPI '{name}' in catalogue '{self.catalog_id}'")

    @property
    def names(self) -> list[str]:
        return [k.name for k in self.kpis]
