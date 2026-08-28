"""Deriving a company's KPIs from its own extract.

The whole of this file runs without a model. That is the point being pinned as
much as the behaviour: exact column-name matching is the floor under onboarding,
and if it ever stops producing a runnable contract on its own, the `--no-llm`
path has quietly become a stub.

What the model adds is tested where the model is stubbed, in `test_agent.py`;
what it is *not allowed* to add -- a KPI outside the catalogue, an edge into a
KPI node, a binding to a column that is not there -- is tested here, against the
validators rather than against a model.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from kpi_engine.catalogue import (
    UnbindableVariant,
    bind_by_synonym,
    bind_variant,
    load_catalog,
    match_column,
    normalise,
    select_variant,
)
from kpi_engine.contracts.catalogue import SeedKpi, SeedVariant
from kpi_engine.contracts.configs import CausalEdge, KpiContract
from kpi_engine.contracts.onboarding import PlanConfirmation, PlanDecision
from kpi_engine.onboarding import (
    OnboardingError,
    choose_grain,
    infer_date_column,
    infer_entity_columns,
    merge_confirmation,
    synthesise_contract,
    synthesise_graph,
)

CATALOG_DOC = Path("templates/reference/kpi_list.csv")


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


# --------------------------------------------------------------------------- #
# The catalogue itself
# --------------------------------------------------------------------------- #


def test_every_row_of_the_kpi_document_has_a_seed_entry(catalog):
    """The CSV is the human document and the YAML is the machine one.

    They are transcribed by hand, so nothing keeps them in step except this. A
    KPI in the document with no seed entry can never be offered to a tenant, and
    a seed entry with no document row has no reviewed formula behind it.
    """
    documented = set(pd.read_csv(CATALOG_DOC)["Key Performance Indicator (KPI)"])
    seeded = set(catalog.names)
    assert documented == seeded, (
        f"only in the document: {sorted(documented - seeded)}; "
        f"only in the catalogue: {sorted(seeded - documented)}"
    )


def test_every_seed_variant_parses_as_a_real_kpi_definition(catalog):
    """A broken formula must fail when the catalogue loads, not at a tenant's
    first onboarding. `SeedVariant` builds a real `KpiDef` to check, which is what
    runs the restricted AST evaluator over every expression in the file."""
    for seed in catalog.kpis:
        for variant in seed.variants:
            definition = bind_variant(
                seed, variant, {a: f"col_{a}" for a in variant.aliases}
            )
            assert set(definition.measures) == set(variant.aliases)


def test_a_seed_variant_naming_an_alias_it_does_not_declare_is_rejected_at_load():
    with pytest.raises(ValueError, match="undefined measures"):
        SeedVariant(
            variant_id="broken",
            expression="revenue / visitors",
            measures=[{"alias": "revenue"}],
        )


def test_a_seed_expression_the_ast_evaluator_forbids_is_rejected_at_load():
    """Formula strings are untrusted input. An attribute access is not arithmetic."""
    with pytest.raises(ValueError):
        SeedVariant(
            variant_id="hostile",
            expression="revenue.__class__",
            measures=[{"alias": "revenue"}],
        )


def test_the_catalogue_never_offers_a_kpi_whose_category_the_contract_cannot_carry(catalog):
    """`category` is free text on `KpiDef`, but it is what groups a report. The
    document abbreviates ('Customer & Mktg'); the contract spells it out."""
    assert "Customer & Mktg" not in {k.category for k in catalog.kpis}
    assert "Customer & Marketing" in {k.category for k in catalog.kpis}


# --------------------------------------------------------------------------- #
# Matching, without a model
# --------------------------------------------------------------------------- #


def test_an_exact_normalised_synonym_match_is_found_without_a_model():
    assert match_column(["Total Revenue"], ["total_revenue", "Date"]) == "total_revenue"
    assert match_column(["COGS"], ["c.o.g.s.", "Date"]) == "c.o.g.s."
    assert normalise("Cost Of Ads") == normalise("cost_of_ads") == "costofads"


def test_a_synonym_that_only_nearly_matches_does_not_bind():
    """Exact or nothing. An approximate match cannot honestly claim to have
    recognised a header, and a wrong binding corrupts every number that KPI ever
    produces -- silently, because the arithmetic still evaluates."""
    assert match_column(["Total Revenue"], ["Total Revenues"]) is None
    assert match_column(["Units Sold"], ["Units Solds", "Unit Sold"]) is None


def test_the_catalogues_synonym_order_decides_when_several_columns_match(catalog):
    """`Net Sales == Total Revenue == Retail Sales` is an exact identity in the
    demo extract, so a file may carry all three. Which one binds must be the
    catalogue's choice, never the file's column order."""
    seed = catalog.kpi("Gross Profit Margin")
    variant = seed.variant("from_revenue_and_cogs")
    bound = bind_by_synonym(variant, ["Retail Sales", "Net Sales", "Total Revenue", "COGS"])
    assert bound["total_revenue"] == "Total Revenue"


def test_the_first_fully_bindable_variant_is_the_one_chosen(catalog):
    """The document's 'Store Entrances OR Website Sessions' is a choice of column.
    Ordered fallback makes it deterministic rather than a judgement call."""
    seed = catalog.kpi("Foot & Digital Traffic")
    variant, _ = select_variant(seed, ["Store Entrances", "Website Sessions"])
    assert variant.variant_id == "both_channels"
    variant, _ = select_variant(seed, ["Website Sessions"])
    assert variant.variant_id == "digital_only"


def test_a_variant_that_does_not_survive_time_aggregation_is_never_chosen_automatically(catalog):
    """`build_panel` aggregates measures to the grain and *then* evaluates, so a
    stock-flow identity summed over a week counts the same goods repeatedly. Such
    a variant stays in the vocabulary and stays opt-in."""
    seed = catalog.kpi("Cost of Goods Sold (COGS)")
    header = ["Beginning Inventory", "Purchases", "Ending Inventory"]
    assert select_variant(seed, header) is None
    variant, bound = select_variant(seed, header, allow_unsafe=True)
    assert variant.variant_id == "stock_flow"
    assert not variant.aggregation_safe


def test_a_directly_supplied_column_is_preferred_over_reconstructing_it(catalog):
    seed = catalog.kpi("Cost of Goods Sold (COGS)")
    variant, _ = select_variant(
        seed, ["COGS", "Beginning Inventory", "Purchases", "Ending Inventory"]
    )
    assert variant.variant_id == "direct"


def test_two_aliases_of_one_kpi_may_never_bind_the_same_column(catalog):
    """`revenue - revenue` is always zero and always wrong."""
    seed = catalog.kpi("Gross Profit")
    variant = seed.variant("from_revenue_and_cogs")
    with pytest.raises(UnbindableVariant, match="degenerate"):
        bind_variant(seed, variant, {"total_revenue": "Revenue", "cogs": "Revenue"})


def test_binding_a_variant_with_a_missing_alias_raises_rather_than_inventing_a_column(catalog):
    seed = catalog.kpi("Gross Profit Margin")
    variant = seed.variant("from_revenue_and_cogs")
    with pytest.raises(UnbindableVariant, match="no column"):
        bind_variant(seed, variant, {"total_revenue": "Total Revenue"})


# --------------------------------------------------------------------------- #
# Reading the file
# --------------------------------------------------------------------------- #


def test_a_date_column_is_found_by_name_before_it_is_parsed():
    head = pd.DataFrame({"Date": ["2026-01-01"], "Note": ["2026-01-02"]})
    assert infer_date_column(["Date", "Note"], head) == "Date"


def test_a_date_column_with_an_unusual_name_is_found_by_parsing_it():
    head = pd.DataFrame({"Region": ["West"] * 5, "Posting Period": ["2026-01-0%d" % i for i in range(1, 6)]})
    assert infer_date_column(["Region", "Posting Period"], head) == "Posting Period"


def test_an_identifier_column_is_not_offered_as_a_dimension():
    """A column with a different value on every row cannot separate anything; a
    column with one value cannot either. Neither is a dimension."""
    head = pd.DataFrame(
        {
            "Date": ["2026-01-01"] * 100,
            "Region": ["West", "East"] * 50,
            "Order Id": [f"o{i}" for i in range(100)],
            "Currency": ["GBP"] * 100,
        }
    )
    entities = infer_entity_columns(list(head.columns), head, "Date")
    assert entities == ["Region"]


def test_a_grain_the_baseline_could_never_train_on_is_lowered():
    """`min_train_periods` counts PERIODS. A coarse grain over a short file never
    reaches it, so the detector emits no expectation, finds nothing, and the
    report says the period was quiet -- a wrong answer shaped like a right one."""
    grain, note = choose_grain(120, 56, prefer="week")
    assert grain == "day"
    assert "training requirement" in note


def test_a_grain_the_history_supports_is_left_alone():
    grain, note = choose_grain(730, 56, prefer="week")
    assert grain == "week"
    assert note is None


# --------------------------------------------------------------------------- #
# Synthesis
# --------------------------------------------------------------------------- #


def _contract(catalog, names=("Gross Profit Margin", "Conversion Rate")) -> KpiContract:
    from kpi_engine.contracts.onboarding import BoundMeasure, KpiPlan, KpiProposal

    proposals = []
    for name in names:
        seed = catalog.kpi(name)
        variant = seed.variants[0]
        proposals.append(
            KpiProposal(
                name=seed.name, variant_id=variant.variant_id, category=seed.category,
                unit=seed.unit, direction=seed.direction, expression=variant.expression,
                measures=[
                    BoundMeasure(alias=m.alias, column=m.synonyms[0], agg=m.agg,
                                 bound_by="synonym")
                    for m in variant.measures
                ],
                materiality=seed.materiality, guards=seed.guards,
                computable=True, recommended=True,
            )
        )
    plan = KpiPlan(
        plan_id="t", company_id="testco", source_id="s", catalog_id=catalog.catalog_id,
        generated_at=dt.datetime.now(), staged_csv="x.csv",
        header=[m.column for p in proposals for m in p.measures],
        proposed=proposals,
    )
    return synthesise_contract(plan, contract_id="t_v1", catalog=catalog)


def test_every_kpi_node_has_exactly_its_measures_as_deterministic_parents(catalog):
    contract = _contract(catalog)
    spec, _ = synthesise_graph(contract, graph_id="t_v1", catalog=catalog)
    for kpi in contract.kpis:
        parents = {
            e.source for e in spec.edges
            if e.target == kpi.name and e.relation == "deterministic"
        }
        assert parents == {m.column for m in kpi.measures.values()}


def test_two_kpis_sharing_a_column_share_one_measure_node(catalog):
    """A node per column, not per (kpi, measure) pair -- otherwise an attribution
    about revenue would reach only one of the KPIs built on it."""
    contract = _contract(catalog, ("Gross Profit Margin", "Gross Profit"))
    spec, _ = synthesise_graph(contract, graph_id="t_v1", catalog=catalog)
    names = [n.name for n in spec.nodes if n.kind == "measure"]
    assert len(names) == len(set(names))
    assert "Total Revenue" in names


def test_the_synthesised_graph_forbids_every_reverse_of_a_deterministic_edge(catalog):
    """A KPI cannot cause its own inputs. Saying so explicitly means a bad
    proposal is refused by name rather than surfacing as an anonymous cycle."""
    contract = _contract(catalog)
    spec, _ = synthesise_graph(contract, graph_id="t_v1", catalog=catalog)
    forbidden = {tuple(e) for e in spec.forbidden_edges}
    for edge in spec.edges:
        if edge.relation == "deterministic":
            assert (edge.target, edge.source) in forbidden


def test_a_proposed_causal_edge_into_a_kpi_node_is_dropped(catalog):
    """The algebraic layer attributes a KPI's movement to its measures exactly.
    An estimated edge into the KPI would re-derive that and double-count it."""
    contract = _contract(catalog)
    spec, problems = synthesise_graph(
        contract, graph_id="t_v1", catalog=catalog,
        causal_edges=[CausalEdge(source="COGS", target="Gross Profit Margin")],
    )
    assert not [e for e in spec.edges if e.relation == "causal"]
    assert any("points at a KPI" in p for p in problems)


def test_a_proposed_edge_that_reverses_a_deterministic_one_is_refused(catalog):
    contract = _contract(catalog)
    spec, problems = synthesise_graph(
        contract, graph_id="t_v1", catalog=catalog,
        causal_edges=[CausalEdge(source="Total Revenue", target="COGS"),
                      CausalEdge(source="COGS", target="Total Revenue")],
    )
    causal = [(e.source, e.target) for e in spec.edges if e.relation == "causal"]
    assert causal == [("Total Revenue", "COGS")]
    assert any("reverses a declared edge" in p for p in problems)


def test_a_proposed_edge_that_closes_a_cycle_is_dropped_and_the_rest_survive(catalog):
    contract = _contract(catalog, ("Gross Profit Margin", "Conversion Rate"))
    spec, problems = synthesise_graph(
        contract, graph_id="t_v1", catalog=catalog,
        causal_edges=[
            CausalEdge(source="COGS", target="Number of Sales"),
            CausalEdge(source="Number of Sales", target="Total Visitors"),
            CausalEdge(source="Total Visitors", target="COGS"),
        ],
    )
    from kpi_engine.causal.dag import CausalGraph

    CausalGraph(spec).validate()  # must not raise
    assert len([e for e in spec.edges if e.relation == "causal"]) == 2


def test_a_graph_with_no_causal_edges_is_still_one_the_engine_can_load(catalog):
    """The floor under the no-LLM path: deterministic skeleton only. Attribution
    degrades to the algebraic layer; it does not fail."""
    from kpi_engine.causal.dag import CausalGraph

    contract = _contract(catalog)
    spec, problems = synthesise_graph(contract, graph_id="t_v1", catalog=catalog)
    assert not [e for e in spec.edges if e.relation == "causal"]
    assert not problems
    CausalGraph(spec).validate()


def test_the_no_llm_graph_still_names_an_owner_for_each_lever(catalog):
    """Ownership is domain knowledge written once in the catalogue, so a
    deterministic run still produces levers someone can act on."""
    from kpi_engine.onboarding import resolve_levers

    contract = _contract(catalog)
    columns = {m.column for k in contract.kpis for m in k.measures.values()}
    spec, _ = synthesise_graph(
        contract, graph_id="t_v1", catalog=catalog,
        levers=resolve_levers(catalog, columns),
    )
    owners = {n.name: n.owner for n in spec.nodes if n.controllable}
    assert owners.get("COGS") == "Supply Chain"


# --------------------------------------------------------------------------- #
# Deciding
# --------------------------------------------------------------------------- #


def _plan(catalog):
    from kpi_engine.contracts.onboarding import BoundMeasure, KpiPlan, KpiProposal

    seed = catalog.kpi("Gross Profit Margin")
    variant = seed.variant("from_revenue_and_cogs")
    return KpiPlan(
        plan_id="p1", company_id="testco", source_id="s", catalog_id=catalog.catalog_id,
        generated_at=dt.datetime.now(), staged_csv="x.csv",
        header=["Date", "Region", "Total Revenue", "COGS", "Net Sales"],
        date_column="Date", entity_columns=["Region"],
        proposed=[
            KpiProposal(
                name=seed.name, variant_id=variant.variant_id, category=seed.category,
                unit=seed.unit, direction=seed.direction, expression=variant.expression,
                measures=[
                    BoundMeasure(alias="total_revenue", column="Total Revenue", bound_by="synonym"),
                    BoundMeasure(alias="cogs", column="COGS", bound_by="synonym"),
                ],
                materiality=seed.materiality, guards=seed.guards,
                computable=True, recommended=True,
            )
        ],
    )


def test_silence_keeps_the_recommendation_rather_than_taking_nothing(catalog):
    """An empty `decisions` list means 'no corrections', not 'accept nothing'."""
    merged, _ = merge_confirmation(_plan(catalog), PlanConfirmation(plan_id="p1"))
    assert [p.name for p in merged.proposed] == ["Gross Profit Margin"]


def test_a_rejected_kpi_does_not_reach_the_contract(catalog):
    merged, _ = merge_confirmation(
        _plan(catalog),
        PlanConfirmation(
            plan_id="p1",
            decisions=[PlanDecision(name="Gross Profit Margin", verdict="reject")],
        ),
    )
    assert merged.proposed == []


def test_a_plan_confirmed_with_every_kpi_rejected_is_refused_rather_than_written(catalog):
    """A company with an empty contract answers every question with silence,
    which is worse than refusing to be configured."""
    merged, _ = merge_confirmation(
        _plan(catalog),
        PlanConfirmation(
            plan_id="p1",
            decisions=[PlanDecision(name="Gross Profit Margin", verdict="reject")],
        ),
    )
    with pytest.raises(OnboardingError, match="nothing to compute"):
        synthesise_contract(merged, contract_id="t_v1", catalog=catalog)


def test_a_user_override_rebinds_a_measure_and_is_recorded_as_theirs(catalog):
    merged, problems = merge_confirmation(
        _plan(catalog),
        PlanConfirmation(
            plan_id="p1",
            decisions=[
                PlanDecision(
                    name="Gross Profit Margin",
                    bindings={"total_revenue": "Net Sales"},
                )
            ],
        ),
    )
    measures = {m.alias: m for m in merged.proposed[0].measures}
    assert measures["total_revenue"].column == "Net Sales"
    assert measures["total_revenue"].bound_by == "user"
    assert not problems


def test_an_override_naming_a_column_the_file_lacks_is_dropped_and_named(catalog):
    """A decision is a different proposal, not an exemption from checking."""
    merged, problems = merge_confirmation(
        _plan(catalog),
        PlanConfirmation(
            plan_id="p1",
            decisions=[
                PlanDecision(name="Gross Profit Margin", bindings={"cogs": "Imaginary"})
            ],
        ),
    )
    assert merged.proposed[0].measures[1].column == "COGS"
    assert any("Imaginary" in p for p in problems)


def test_a_stale_plan_id_is_refused_rather_than_merged_onto_the_current_draft(catalog):
    with pytest.raises(Exception, match="not the current draft"):
        merge_confirmation(_plan(catalog), PlanConfirmation(plan_id="p0"))


def test_a_decision_for_a_kpi_the_plan_never_proposed_is_reported_not_invented(catalog):
    merged, problems = merge_confirmation(
        _plan(catalog),
        PlanConfirmation(plan_id="p1", decisions=[PlanDecision(name="ROAS")]),
    )
    assert [p.name for p in merged.proposed] == ["Gross Profit Margin"]
    assert any("never proposed" in p for p in problems)
