"""The agent's grounding guarantee, tested where it is actually enforced.

None of these need an API key. The model object is a parameter everywhere, so a stub
returning canned objects drives the whole graph -- which is the point of the
boundary: if the model half were not substitutable, it could not be verified either.

What is pinned here is the *refusal* behaviour rather than the happy path. A
narrator that produces a good report is not evidence of anything; a verifier that
rejects a fabricated number is.
"""

from __future__ import annotations

import datetime as dt
import time

import pandas as pd
import pytest

from kpi_engine.causal.dag import CausalGraph
from kpi_engine.config_io import (
    load_contract,
    load_detection,
    load_graph,
    load_source,
    project_root,
)
from kpi_engine.contracts.payloads import (
    EventWindow,
    Lineage,
    ObservedDeviation,
)
from kpi_engine.scenarios.scm_generator import generate_scm_panel

from kpi_agent import build_graph
from kpi_agent.llm import Usage
from kpi_agent.intent import validate_intent
from kpi_agent.linking import link_events
from kpi_agent.models import (
    Action,
    AnalysisIntent,
    Claim,
    Fact,
    GroundedContext,
    Narrative,
    VerificationResult,
)
from kpi_agent.verify import verify

ROOT = project_root()


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def graph():
    return CausalGraph(load_graph(ROOT / "configs/causal/retail_dag.yaml"))


@pytest.fixture
def context():
    """A minimal but realistic fact table: one movement, one exact contribution."""
    return GroundedContext(
        question="why did CAC rise in the West?",
        question_restated="Why did CAC rise in the West?",
        persona="analyst",
        run_id="test",
        time_grain="week",
        entity_keys=["Region"],
        facts=[
            Fact(id="F1", label="CAC moved", value=34.2, display="CAC +34.2%",
                 unit="pct_delta", kind="movement", kpi="CAC", entity={"Region": "West"}),
            Fact(id="F2", label="Cost Of Ads contribution to CAC", value=4.87,
                 display="Cost Of Ads: +4.87 (+49.5% of the move)", kind="contribution",
                 kpi="CAC", entity={"Region": "West"}, method="algebraic_lmdi",
                 exact=True, controllable=True, owner="Performance Marketing"),
            Fact(id="F3", label="Confidence", value=0.82, display="0.82", unit="score",
                 kind="confidence", entity={"Region": "West"}),
        ],
        events=[{"event_id": "EV-1", "confidence": 0.82, "fact_ids": ["F1", "F2", "F3"]}],
        levers=[{"lever": "Cost Of Ads", "owner": "Performance Marketing",
                 "description": "Paid media spend.", "column": "Cost Of Ads"}],
    )


def _narrative(**overrides) -> Narrative:
    base = dict(
        headline="CAC rose in the West.",
        what_happened=[Claim(text="CAC +34.2% in the West.", evidence_ids=["F1"])],
        why=[Claim(text="Cost Of Ads: +4.87 (+49.5% of the move).", evidence_ids=["F2"])],
        needs_attention=[],
        actions=[],
        uncertainty="",
        abstained_from=[],
    )
    base.update(overrides)
    return Narrative(**base)


class StubLlm:
    """A chat model that returns queued objects, one per call, in order.

    Shaped like what the nodes actually call: `with_structured_output(...)` returns
    something with `.invoke(messages)`, and the reply is the `include_raw=True`
    mapping. Deliberately not configurable by prompt -- a stub that inspected the
    prompt would be testing the prompt rather than the graph, and the graph's
    routing is what these tests are about.
    """

    model = "stub"

    def __init__(self, *responses):
        self._queue = list(responses)
        self.calls: list[dict] = []

    def with_structured_output(self, schema, **kwargs):
        return _StubChain(self, schema, kwargs)


class _StubChain:
    def __init__(self, stub, schema, kwargs):
        self._stub, self._schema, self._kwargs = stub, schema, kwargs

    def invoke(self, messages):
        self._stub.calls.append({"schema": self._schema.__name__, "messages": messages,
                                 **self._kwargs})
        for i, item in enumerate(self._stub._queue):
            if isinstance(item, self._schema):
                return {"raw": _StubMessage(), "parsed": self._stub._queue.pop(i),
                        "parsing_error": None}
        raise AssertionError(f"StubLlm has no queued {self._schema.__name__}")


class _StubMessage:
    usage_metadata = {"input_tokens": 100, "output_tokens": 50,
                      "input_token_details": {"cache_read": 0}}


# --------------------------------------------------------------------------- #
# The verifier: every one of these must be rejected
# --------------------------------------------------------------------------- #


def test_verifier_accepts_a_fully_grounded_narrative(context, graph):
    result = verify(_narrative(), context, graph)
    assert result.passed, result.violations
    assert result.numbers_checked > 0


def test_verifier_rejects_a_number_that_is_in_no_fact(context, graph):
    """The failure mode the whole design exists to prevent.

    41.5 is plausible, adjacent to real numbers, and completely invented. Nothing
    about the sentence looks wrong; only the table can tell.
    """
    bad = _narrative(
        what_happened=[Claim(text="CAC rose 41.5% in the West.", evidence_ids=["F1"])]
    )
    result = verify(bad, context, graph)
    assert not result.passed
    assert any(v.code == "ungrounded_number" for v in result.violations)


def test_verifier_tolerates_rounding_but_not_a_different_number(context, graph):
    ok = verify(
        _narrative(what_happened=[Claim(text="CAC rose 34.2%.", evidence_ids=["F1"])]),
        context, graph,
    )
    assert ok.passed

    not_ok = verify(
        _narrative(what_happened=[Claim(text="CAC rose 43.2%.", evidence_ids=["F1"])]),
        context, graph,
    )
    assert not not_ok.passed


def test_verifier_rejects_an_unresolvable_evidence_id(context, graph):
    bad = _narrative(
        what_happened=[Claim(text="CAC rose.", evidence_ids=["F99"])]
    )
    result = verify(bad, context, graph)
    assert any(v.code == "unknown_evidence_id" for v in result.violations)


def test_verifier_rejects_an_uncited_claim(context, graph):
    bad = _narrative(what_happened=[Claim(text="CAC rose.", evidence_ids=[])])
    result = verify(bad, context, graph)
    assert any(v.code == "missing_evidence" for v in result.violations)


def test_verifier_rejects_an_action_on_a_node_nobody_controls(context, graph):
    """`New Customers` is an outcome. Recommending someone "set" it is not an action."""
    bad = _narrative(actions=[Action(
        driver="New Customers", lever="New Customers",
        action="Increase new customers.", owner="Growth Marketing",
        expected_impact="CAC falls.", confidence=0.82,
        monitoring="Watch CAC weekly.", evidence_ids=["F2"],
    )])
    result = verify(bad, context, graph)
    assert any(v.code == "uncontrollable_lever" for v in result.violations)


def test_verifier_rejects_an_action_assigned_to_the_wrong_owner(context, graph):
    bad = _narrative(actions=[Action(
        driver="Cost Of Ads", lever="Cost Of Ads",
        action="Cut paid search bids.", owner="Finance",
        expected_impact="CAC falls.", confidence=0.82,
        monitoring="Watch CAC weekly.", evidence_ids=["F2"],
    )])
    result = verify(bad, context, graph)
    assert any(v.code == "unknown_owner" for v in result.violations)


def test_verifier_rejects_an_invented_confidence(context, graph):
    """A confidence number must be one the engine computed, not one that reads well."""
    bad = _narrative(actions=[Action(
        driver="Cost Of Ads", lever="Cost Of Ads",
        action="Cut paid search bids.", owner="Performance Marketing",
        expected_impact="CAC falls.", confidence=0.95,
        monitoring="Watch CAC weekly.", evidence_ids=["F2"],
    )])
    result = verify(bad, context, graph)
    assert any(v.code == "confidence_not_grounded" for v in result.violations)


def test_verifier_rejects_a_cause_asserted_over_an_abstention(context, graph):
    """Abstention is an output. Explaining around it is the thing not to do."""
    context.abstentions = [{
        "event_id": "EV-1", "kpi": "CAC", "reason_code": "insufficient_history",
        "message": "Only 17 periods precede this window.",
        "missing_evidence": [], "what_would_resolve_it": [],
    }]
    bad = _narrative(why=[Claim(
        text="CAC rose because of Cost Of Ads.", evidence_ids=["F2"],
    )])
    result = verify(bad, context, graph)
    assert any(v.code == "causal_claim_on_abstention" for v in result.violations)


def test_verifier_rejects_a_sentence_pointing_the_wrong_way(context, graph):
    bad = _narrative(what_happened=[Claim(
        text="CAC fell sharply in the West.", evidence_ids=["F1"],
    )])
    result = verify(bad, context, graph)
    assert any(v.code == "direction_contradicts_evidence" for v in result.violations)


def test_verifier_ignores_structural_small_integers(context, graph):
    """"across 5 regions" must not be treated as a quoted measurement."""
    ok = _narrative(what_happened=[Claim(
        text="CAC +34.2%, seen in 2 detectors across 5 regions.", evidence_ids=["F1"],
    )])
    assert verify(ok, context, graph).passed


# --------------------------------------------------------------------------- #
# Intent validation
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def source_triples():
    from kpi_engine.pipeline import load_or_build_profile
    from kpi_engine.sources import build_source

    out = []
    for src, con in [
        ("configs/sources/retail_csv.yaml", "configs/semantics/retail_kpis.yaml"),
        ("configs/sources/scm_csv.yaml", "configs/semantics/scm_kpis.yaml"),
    ]:
        spec = load_source(ROOT / src)
        contract = load_contract(ROOT / con)
        source = build_source(spec, base_dir=ROOT)
        df = source.load()
        out.append((spec, contract, load_or_build_profile(source, df, spec.source_id)))
    return out


def _intent(**kw) -> AnalysisIntent:
    base = dict(kpis=[], entity_keys=[], time_grain="week", sources=[],
                persona="analyst", question_restated="q")
    base.update(kw)
    return AnalysisIntent(**base)


def test_an_unknown_kpi_is_dropped_when_a_real_one_remains(source_triples):
    resolved, problems = validate_intent(
        _intent(kpis=["CAC", "Customer Delight Index"]), source_triples
    )
    assert resolved is not None
    assert resolved.kpis == ["CAC"]
    assert any("Customer Delight Index" in p for p in problems)


def test_an_intent_naming_only_imaginary_kpis_is_refused(source_triples):
    resolved, problems = validate_intent(_intent(kpis=["Vibes"]), source_triples)
    assert resolved is None
    assert problems


def test_an_unknown_dimension_is_refused_when_it_is_the_only_one(source_triples):
    resolved, _ = validate_intent(_intent(entity_keys=["Franchise"]), source_triples)
    assert resolved is None


def test_a_restricted_column_is_refused_rather_than_silently_dropped(source_triples):
    """Entitlement must produce a refusal, not an answer with a quiet hole in it."""
    resolved, problems = validate_intent(
        _intent(entity_keys=["Purchase Price Per Unit"]), source_triples
    )
    assert resolved is None
    assert any("restricted" in p.lower() for p in problems)


def test_a_period_after_the_data_ends_is_refused(source_triples):
    resolved, problems = validate_intent(
        _intent(date_start="2099-01-01"), source_triples
    )
    assert resolved is None
    assert any("after the data ends" in p for p in problems)


def test_an_unknown_source_is_dropped_but_a_known_one_still_runs(source_triples):
    resolved, problems = validate_intent(
        _intent(sources=["retail_daily", "crm_cloud"]), source_triples
    )
    assert resolved is not None
    assert resolved.sources == ["retail_daily"]
    assert any("crm_cloud" in p for p in problems)


# --------------------------------------------------------------------------- #
# Cross-source linking
# --------------------------------------------------------------------------- #


def _event(event_id, kpi, entity, start, end, source_id="retail_daily") -> EventWindow:
    return EventWindow(
        event_id=event_id,
        anomaly_types=["Structural Break"],
        detectors=["changepoint_pelt"],
        window_start=dt.date.fromisoformat(start),
        window_end=dt.date.fromisoformat(end),
        entity=entity,
        primary_kpis_affected=[kpi],
        observed_deviations=[ObservedDeviation(
            kpi=kpi, expected=10.0, actual=8.0, abs_delta=-2.0,
            pct_delta=-20.0, material=True,
        )],
        candidate_covariates=[],
        peak_score=5.0,
        n_flags=3,
        min_support=10,
        lineage=Lineage(
            source_id=source_id, source_path="x.csv", contract_id="c",
            kpi=kpi, columns=[], time_grain="week", entity_filter=entity,
        ),
    )


@pytest.fixture(scope="module")
def contracts():
    return (
        load_contract(ROOT / "configs/semantics/retail_kpis.yaml"),
        load_contract(ROOT / "configs/semantics/scm_kpis.yaml"),
    )


def test_a_declared_path_produces_a_link(graph, contracts):
    sales_c, scm_c = contracts
    links = link_events(
        [_event("EV-S", "Inventory Turnover", {"Region": "West"}, "2026-08-10", "2026-09-07")],
        [_event("EV-C", "Fill Rate", {"Region": "West"}, "2026-08-10", "2026-09-07", "scm_weekly")],
        sales_c, scm_c, graph,
    )
    assert links
    assert links[0].dag_path[-1] == "Inventory Turnover"
    assert links[0].overlap_days > 0


def test_no_declared_path_means_no_link(graph, contracts):
    """The load-bearing test.

    The windows overlap exactly and the region matches, so every temporal heuristic
    says "connected". CAC has no path from any supply-chain node, so there is no
    link -- which is the difference between reading the graph and mining coincidence.
    """
    sales_c, scm_c = contracts
    links = link_events(
        [_event("EV-S", "CAC", {"Region": "West"}, "2026-08-10", "2026-09-07")],
        [_event("EV-C", "Fill Rate", {"Region": "West"}, "2026-08-10", "2026-09-07", "scm_weekly")],
        sales_c, scm_c, graph,
    )
    assert links == []


def test_events_in_different_regions_are_never_linked(graph, contracts):
    sales_c, scm_c = contracts
    links = link_events(
        [_event("EV-S", "Inventory Turnover", {"Region": "West"}, "2026-08-10", "2026-09-07")],
        [_event("EV-C", "Fill Rate", {"Region": "North"}, "2026-08-10", "2026-09-07", "scm_weekly")],
        sales_c, scm_c, graph,
    )
    assert links == []


def test_a_gap_beyond_the_lag_tolerance_breaks_the_link(graph, contracts):
    sales_c, scm_c = contracts
    links = link_events(
        [_event("EV-S", "Inventory Turnover", {"Region": "West"}, "2026-12-01", "2026-12-20")],
        [_event("EV-C", "Fill Rate", {"Region": "West"}, "2026-01-05", "2026-01-25", "scm_weekly")],
        sales_c, scm_c, graph, lag_tolerance_days=42,
    )
    assert links == []


# --------------------------------------------------------------------------- #
# The SCM generator
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def scm_frame():
    sales = pd.read_csv(ROOT / "data/generated/ad_cost_shock_v1.csv")
    frame, manifest = generate_scm_panel(
        sales, seed=42,
        disruption_start=dt.date(2026, 8, 10), disruption_end=dt.date(2026, 9, 7),
    )
    return frame, manifest


def test_generated_supply_data_cannot_receive_more_than_it_ordered(scm_frame):
    frame, _ = scm_frame
    assert (frame["Units Received"] <= frame["Units Ordered"]).all()
    assert (frame["Backorder Units"] >= 0).all()
    assert (frame["On Hand Units"] >= 0).all()


def test_the_reported_fill_rate_matches_the_quantities_reported_beside_it(scm_frame):
    """A file that states a rate its own columns contradict is a data-quality defect
    the engine would then have to explain. Better not to author one."""
    frame, _ = scm_frame
    recomputed = frame["Units Received"] / frame["Units Ordered"]
    assert (frame["Supplier Fill Rate"] - recomputed).abs().max() < 1e-3


def test_counts_stay_integers_under_pandas_three(scm_frame):
    """pandas 3 refuses the implicit upcast, so physical counts are rounded, not widened."""
    frame, _ = scm_frame
    for column in ["Units Ordered", "Units Received", "Backorder Units",
                   "On Hand Units", "Days In Period"]:
        assert pd.api.types.is_integer_dtype(frame[column].dtype), column


def test_the_disruption_actually_degrades_the_supplier_it_names(scm_frame):
    frame, manifest = scm_frame
    event = manifest["events"][0]
    assert event["rows_affected"] > 0
    assert event["column_changes"]["Supplier Fill Rate"]["mean_after"] < \
        event["column_changes"]["Supplier Fill Rate"]["mean_before"]
    assert event["column_changes"]["Lead Time Days"]["mean_after"] > \
        event["column_changes"]["Lead Time Days"]["mean_before"]


def test_generation_is_reproducible_from_its_seed(scm_frame):
    frame, _ = scm_frame
    sales = pd.read_csv(ROOT / "data/generated/ad_cost_shock_v1.csv")
    again, _ = generate_scm_panel(
        sales, seed=42,
        disruption_start=dt.date(2026, 8, 10), disruption_end=dt.date(2026, 9, 7),
    )
    pd.testing.assert_frame_equal(frame, again)


# --------------------------------------------------------------------------- #
# The graph end to end
# --------------------------------------------------------------------------- #


SMALL_CONFIG = {
    "sources": [
        {"source": "configs/sources/retail_csv.yaml",
         "contract": "configs/semantics/retail_kpis.yaml",
         "dataset": "data/generated/ad_cost_shock_v1.csv"},
        {"source": "configs/sources/scm_csv.yaml",
         "contract": "configs/semantics/scm_kpis.yaml"},
    ],
    "primary_source_id": "retail_daily",
    "graph": "configs/causal/retail_dag.yaml",
    "detection": "configs/detection/default.yaml",
    "eda": "configs/eda/default.yaml",
    "personas": "configs/agent/personas.yaml",
    "time_grain": "week",
    "entity_keys": ["Region"],
    "top_events": 2,
}


def _invoke(llm, question="what needs attention?", persona="analyst", run_id="pytest-agent"):
    app = build_graph()
    return app.invoke(
        {"question": question, "persona": persona, "run_id": run_id,
         "config": {**SMALL_CONFIG, "llm": llm, "usage": Usage()},
         "errors": [], "repair_attempts": 0, "used_fallback": False},
        {"recursion_limit": 40},
    )


@pytest.mark.slow
def test_a_clarification_ends_the_turn_without_computing_anything():
    """A question that cannot be resolved must not produce a confident answer."""
    llm = StubLlm(_intent(
        sources=["retail_daily"],
        clarification_needed="Which region did you mean by 'the usual one'?",
    ))
    state = _invoke(llm, question="how is the usual one doing?")
    assert "clarification" in state["report_markdown"].lower()
    assert not state.get("results")


@pytest.mark.slow
def test_an_unverifiable_narrative_is_replaced_rather_than_published():
    """Two failed passes must land on the template, not on the model's text."""
    fabricated = Narrative(
        headline="Margin collapsed 87.3% company-wide.",
        what_happened=[Claim(text="Margin fell 87.3%.", evidence_ids=["F404"])],
    )
    llm = StubLlm(
        _intent(sources=["retail_daily"], entity_keys=["Region"]),
        fabricated, fabricated,
    )
    state = _invoke(llm)
    assert state["used_fallback"]
    assert "87.3" not in state["report_markdown"]
    assert state["verification"].passed


@pytest.mark.slow
def test_a_verified_narrative_is_published_with_its_evidence_table():
    grounded = Narrative(
        headline="Several KPIs moved.",
        what_happened=[Claim(text="A material movement was detected.", evidence_ids=["F1"])],
        uncertainty="Estimates carry the assumptions their method states.",
    )
    llm = StubLlm(_intent(sources=["retail_daily"], entity_keys=["Region"]), grounded)
    state = _invoke(llm)
    assert state["verification"].passed, state["verification"].violations
    assert not state["used_fallback"]
    assert "## Evidence" in state["report_markdown"]
    assert state["telemetry"]["deterministic_stages"] > 0


# --------------------------------------------------------------------------- #
# The in-process orchestrator
# --------------------------------------------------------------------------- #


@pytest.mark.slow
def test_in_process_pipeline_reproduces_the_cli_chain():
    """The agent must not be running a quietly different engine.

    `pipeline.run_pipeline` rewires the same stage cores that the CLIs call through
    argv. If it drifts -- a spec copied differently, a grain applied in the wrong
    place -- the agent's findings stop being the findings the calibrated thresholds
    were measured against, and nothing else would notice.
    """
    from kpi_engine.cli.detect_anomalies import main as detect_main
    from kpi_engine.config_io import load_eda, read_json
    from kpi_engine.pipeline import run_pipeline, run_dir

    detect_main([
        "--entity-keys", "Region", "--time-grain", "week",
        "--dataset", "data/generated/ad_cost_shock_v1.csv",
        "--run-id", "pytest-cli-parity",
    ])
    cli_events = read_json(run_dir("pytest-cli-parity") / "events.json")

    result = run_pipeline(
        load_source(ROOT / "configs/sources/retail_csv.yaml"),
        load_contract(ROOT / "configs/semantics/retail_kpis.yaml"),
        load_detection(ROOT / "configs/detection/default.yaml"),
        run_id="pytest-inprocess-parity",
        eda=load_eda(ROOT / "configs/eda/default.yaml"),
        dataset="data/generated/ad_cost_shock_v1.csv",
        entity_keys=["Region"], time_grain="week",
    )

    assert [e["event_id"] for e in cli_events] == [e.event_id for e in result.events]
    assert [e["window_start"] for e in cli_events] == \
        [str(e.window_start) for e in result.events]
    assert [round(e["peak_score"], 6) for e in cli_events] == \
        [round(e.peak_score, 6) for e in result.events]


@pytest.mark.slow
def test_the_deepest_grain_still_abstains():
    """The thin-support scenario must reach abstention, not a confident answer.

    Region x Channel x Category averages about one row per cell. If the engine ever
    produced a clean explanation there, something has stopped respecting support.
    """
    from kpi_engine.config_io import load_eda
    from kpi_engine.pipeline import run_pipeline

    result = run_pipeline(
        load_source(ROOT / "configs/sources/retail_csv.yaml"),
        load_contract(ROOT / "configs/semantics/retail_kpis.yaml"),
        load_detection(ROOT / "configs/detection/default.yaml"),
        run_id="pytest-thin-grain",
        graph=CausalGraph(load_graph(ROOT / "configs/causal/retail_dag.yaml")),
        eda=load_eda(ROOT / "configs/eda/default.yaml"),
        dataset="data/generated/ad_cost_shock_v1.csv",
        entity_keys=["Region", "Channel", "Product category"],
        time_grain="week", top_events=3,
    )
    if result.bundles:
        assert any(b.abstained for b in result.bundles), \
            "Every bundle at the thinnest grain produced a confident explanation."


@pytest.mark.slow
@pytest.mark.parametrize("persona", ["analyst", "exec", "ops"])
def test_the_template_narrative_passes_its_own_verifier_for_every_persona(persona):
    """The fallback is the floor. If it cannot pass verification, there is no floor.

    Personas select different fact kinds, so a template that assumes a kind is
    present -- citing a confidence fact that this persona excluded, say -- produces
    an uncited claim for that persona alone. This caught exactly that.
    """
    # An unverifiable narrative, so the graph is forced onto the template path.
    unverifiable = Narrative(
        headline="Everything moved 91.7 points.",
        what_happened=[Claim(text="A move of 91.7 points.", evidence_ids=["F999"])],
    )
    llm = StubLlm(
        _intent(sources=["retail_daily"], entity_keys=["Region"], persona=persona),
        unverifiable, unverifiable,
    )
    state = _invoke(llm, persona=persona, run_id=f"pytest-persona-{persona}")
    assert state["used_fallback"]
    assert state["verification"].passed, state["verification"].violations


def test_the_persona_cap_keeps_cross_source_links_over_contributions():
    """The cap must not spend its budget before reaching the fact that answers the
    question. Truncating in construction order dropped every link on a run with
    forty contributions, which is precisely the run where the link matters."""
    from kpi_agent.facts import _cap

    facts = (
        [Fact(id=f"F{i}", label="c", value=1.0, display="1.0", kind="contribution")
         for i in range(1, 41)]
        + [Fact(id="F41", label="link", value=0.0, display="0 day lag", kind="link")]
    )
    kept = _cap(facts, 10)
    assert len(kept) == 10
    assert any(f.kind == "link" for f in kept)
    # Ids must still ascend, or the evidence table reads as though rows are missing.
    numbers = [int(f.id[1:]) for f in kept]
    assert numbers == sorted(numbers)


# --------------------------------------------------------------------------- #
# Provider portability
# --------------------------------------------------------------------------- #


def test_the_strict_schema_reaches_the_api_unrelaxed():
    """`extra="forbid"` must survive the trip, or an invented field is only caught late.

    The older Gemini schema dialect rejected `additionalProperties`, `$ref` and
    `anyOf`, so this module used to carry a hand-written translator that stripped
    all three. `method="json_schema"` sends the model's own JSON Schema, so the
    constraint the API enforces is the one we wrote. Nothing here should ever need
    a dialect-specific rewrite again.
    """
    for model in (AnalysisIntent, Narrative):
        schema = model.model_json_schema()
        assert schema["additionalProperties"] is False
        assert set(schema["properties"]) == set(model.model_fields)


def test_a_nullable_field_survives_as_nullable():
    """`clarification_needed` being null is how the planner says "no clarification"."""
    field = AnalysisIntent.model_json_schema()["properties"]["clarification_needed"]
    assert {"type": "null"} in field["anyOf"]
    assert {"type": "string"} in field["anyOf"]


def test_an_unpriced_model_reports_no_cost_rather_than_zero():
    """Zero would read as a free run. The honest answer is that we do not know."""
    from kpi_agent.llm import Usage

    unpriced = Usage()
    unpriced.tokens_in, unpriced.tokens_out = 10_000, 2_000
    assert unpriced.cost_usd is None

    priced = Usage(price_in_per_mtok=5.0, price_out_per_mtok=25.0)
    priced.tokens_in, priced.tokens_out = 1_000_000, 1_000_000
    assert priced.cost_usd == pytest.approx(30.0)


def test_a_cache_hit_does_not_inflate_the_reported_input_count():
    """LangChain reports an input count that already includes the cached tokens.

    Adding the cache read on top of a count that already contains it would make a
    cache hit look like it doubled the bill. Thinking tokens are likewise already
    folded into `output_tokens`.
    """
    from kpi_agent.llm import Usage

    usage = Usage()
    usage.record("Narrative", {
        "input_tokens": 10_000,
        "output_tokens": 800,
        "input_token_details": {"cache_read": 8_000},
        "output_token_details": {"reasoning": 300},
    })
    assert usage.tokens_in == 10_000
    assert usage.tokens_out == 800
    assert usage.cache_read == 8_000
    assert usage.calls == 1


def test_the_model_is_configured_in_exactly_one_place(monkeypatch):
    """One model object per run, built from the constants in `llm.py` and nothing else.

    Both calls take the same effort and output cap, which is what lets the object be
    built once and shared. Varying settings per call by copying the model shares its
    underlying httpx client and closes it when the copy is collected -- that killed
    the second call of every run until it was found, and building once is what makes
    it unreachable.
    """
    import langchain_google_genai

    from kpi_agent import llm as llm_mod

    built: list[dict] = []

    class _Chat:
        def __init__(self, **settings):
            built.append(settings)

    monkeypatch.setattr(langchain_google_genai, "ChatGoogleGenerativeAI", _Chat)

    llm_mod.build_llm()
    llm_mod.build_llm("gemini-3.7-flash")

    assert [s["model"] for s in built] == [llm_mod.DEFAULT_MODEL, "gemini-3.7-flash"]
    assert all(
        s["timeout"] == llm_mod.TIMEOUT_SECONDS
        and s["max_retries"] == llm_mod.ATTEMPTS
        and s["max_output_tokens"] == llm_mod.MAX_OUTPUT_TOKENS
        and s["reasoning_effort"] == llm_mod.REASONING_EFFORT
        for s in built
    )
    # Sampling parameters are deliberately not sent: the Gemini 3 models ignore them
    # and warn, and no quantity in a report is the model's to choose anyway.
    assert all("temperature" not in s for s in built)


def test_a_saturated_model_is_reported_as_load_not_as_a_bad_request():
    """503 "high demand" is the failure that actually happens, and it is not our bug.

    A message that does not say so sends people looking for a defect in the request.
    """
    from kpi_agent.llm import LlmUnavailable, unavailable

    error = unavailable(RuntimeError("503 UNAVAILABLE: high demand"), time.monotonic())
    assert isinstance(error, LlmUnavailable)
    assert "saturated" in str(error) and "--model" in str(error)

    other = unavailable(RuntimeError("connection reset"), time.monotonic())
    assert "saturated" not in str(other)


def test_building_the_model_loads_the_env_file():
    """`GOOGLE_API_KEY` lives in a gitignored `.env`, not in the shell.

    `load_env` used to live only in a factory, so a script that built the client
    directly failed with "no API key" while the identical CLI run worked.
    """
    import os

    from kpi_engine.config_io import project_root

    if not (project_root() / ".env").exists():
        pytest.skip("no .env in this checkout")

    before = dict(os.environ)
    try:
        os.environ.pop("GOOGLE_API_KEY", None)
        from kpi_agent.llm import build_llm

        build_llm()  # constructs only; makes no request
        assert os.environ.get("GOOGLE_API_KEY"), "building the model did not load .env"
    finally:
        os.environ.clear()
        os.environ.update(before)


@pytest.mark.slow
def test_a_report_window_scopes_the_answer_without_starving_the_baseline():
    """Restricting the *load* to a short period is not the same as asking about it.

    The detector needs 28+ periods of history before it will emit an expectation.
    Load only one quarter at monthly grain and it gets three, finds nothing, and
    reports a quiet quarter -- when the truth is that not enough history was read
    to have an opinion. `report_window` detects over everything and filters events
    at the end, which is what a question about a period actually means.
    """
    from kpi_engine.config_io import load_eda
    from kpi_engine.pipeline import run_pipeline

    args = dict(
        eda=load_eda(ROOT / "configs/eda/default.yaml"),
        dataset="data/generated/ad_cost_shock_v1.csv",
        entity_keys=[], time_grain="month",
    )
    window = (dt.date(2026, 10, 1), dt.date(2026, 12, 31))

    def _run(run_id, **extra):
        return run_pipeline(
            load_source(ROOT / "configs/sources/retail_csv.yaml"),
            load_contract(ROOT / "configs/semantics/retail_kpis.yaml"),
            load_detection(ROOT / "configs/detection/default.yaml"),
            run_id=run_id, **args, **extra,
        )

    starved = _run("pytest-window-load", date_range=window)
    scoped = _run("pytest-window-report", report_window=window)

    assert starved.panel.values["period"].nunique() <= 4, \
        "loading one quarter should read only that quarter"
    assert scoped.panel.values["period"].nunique() > 12, \
        "a report window must not restrict what the baseline is trained on"
    # Every reported event must intersect the window; none may fall wholly outside.
    for event in scoped.events:
        assert event.window_start <= window[1] and event.window_end >= window[0]


# --------------------------------------------------------------------------- #
# Grain, and the CLI's right to overrule the planner
# --------------------------------------------------------------------------- #

# The sales file covers 2025-01-01 to 2026-12-31.
COVERAGE_DAYS = 730


def test_a_grain_the_baseline_could_never_train_on_is_lowered(source_triples):
    """The regression that produced an empty report and called it a quiet quarter.

    `min_train_periods` counts PERIODS, not days. At month grain 730 days is 24
    periods against a requirement of 56, so `baseline.py` never emits an
    expectation, the detector finds nothing, and the narrator truthfully reports
    that nothing happened. Every month-grain run in `outputs/` has zero sales
    events for exactly this reason. Silently returning nothing is the worst
    available behaviour, so the grain is stepped down and the step is recorded.
    """
    resolved, problems = validate_intent(
        _intent(time_grain="month"), source_triples,
        min_train_periods=56, coverage_days=COVERAGE_DAYS,
    )
    assert resolved is not None
    assert resolved.time_grain == "week"
    assert any("Grain lowered from month to week" in p for p in problems)


def test_a_grain_the_data_supports_is_left_alone(source_triples):
    resolved, problems = validate_intent(
        _intent(time_grain="week"), source_triples,
        min_train_periods=56, coverage_days=COVERAGE_DAYS,
    )
    assert resolved.time_grain == "week"
    assert not any("Grain lowered" in p for p in problems)


def test_an_explicitly_requested_grain_stands_but_is_warned_about(source_triples):
    """An override that is quietly ignored is the same class of lie as a silent
    empty result. The caller asked for month; they get month, and a warning."""
    resolved, problems = validate_intent(
        _intent(time_grain="week"), source_triples,
        min_train_periods=56, coverage_days=COVERAGE_DAYS,
        overrides={"time_grain": "month"},
    )
    assert resolved.time_grain == "month"
    assert any("expect few or no events" in p for p in problems)


def test_the_caller_outranks_the_planner_on_grain_and_slice(source_triples):
    """A demo cannot depend on the planner's mood. `--time-grain` and
    `--entity-keys` replace what it proposed, and are then validated on the same
    terms as anything else."""
    resolved, problems = validate_intent(
        _intent(time_grain="month", entity_keys=["Channel"]), source_triples,
        min_train_periods=56, coverage_days=COVERAGE_DAYS,
        overrides={"time_grain": "week", "entity_keys": ["Supplier"]},
    )
    assert resolved.time_grain == "week"
    assert resolved.entity_keys == ["Supplier"]
    assert any("time_grain forced" in p for p in problems)
    assert any("entity_keys forced" in p for p in problems)


def test_an_absent_override_leaves_the_planners_choice_intact(source_triples):
    """`None` means "the caller said nothing", not "the caller said empty"."""
    resolved, _ = validate_intent(
        _intent(time_grain="week", entity_keys=["Region"]), source_triples,
        min_train_periods=56, coverage_days=COVERAGE_DAYS,
        overrides={"time_grain": None, "entity_keys": None},
    )
    assert resolved.time_grain == "week"
    assert resolved.entity_keys == ["Region"]


def test_an_override_to_total_level_is_honoured(source_triples):
    """An empty list is a real instruction -- analyse at total level -- and must
    not be read as an absent one."""
    resolved, _ = validate_intent(
        _intent(entity_keys=["Region"]), source_triples,
        min_train_periods=56, coverage_days=COVERAGE_DAYS,
        overrides={"entity_keys": []},
    )
    assert resolved.entity_keys == []


# --------------------------------------------------------------------------- #
# The terminal view
# --------------------------------------------------------------------------- #


def _capture_console(width: int = 120):
    from io import StringIO

    from rich.console import Console

    buffer = StringIO()
    return Console(file=buffer, width=width, force_terminal=False,
                   no_color=True, legacy_windows=False), buffer


def test_the_console_view_labels_which_half_produced_what(context):
    """The whole point of the second renderer. A reader must be able to see, without
    reading the words, which sentences a model wrote and which numbers the engine
    computed."""
    from kpi_agent.render import render_console

    console, buffer = _capture_console()
    render_console(
        console,
        narrative=_narrative(),
        context=context,
        verification=VerificationResult(passed=True, violations=[],
                                        numbers_checked=2, claims_checked=2),
        telemetry={"model": "stub", "llm_calls": 2, "deterministic_stages": 17,
                   "deterministic_ms": 2258.0, "llm_tokens_in": 100,
                   "llm_tokens_out": 50, "llm_cost_usd": None,
                   "used_fallback": False, "fallback_reason": ""},
    )
    out = buffer.getvalue()
    assert "ANALYSIS PLAN" in out
    assert "WHAT THE ENGINE COMPUTED" in out
    assert "NARRATIVE" in out
    assert "model call #2" in out
    assert "EVIDENCE" in out


def test_the_console_view_says_when_the_prose_came_from_the_template(context):
    """A template report presented as the model's is the one confusion this view
    exists to remove."""
    from kpi_agent.render import render_console

    console, buffer = _capture_console()
    render_console(
        console,
        narrative=_narrative(),
        context=context,
        verification=VerificationResult(passed=True, violations=[],
                                        numbers_checked=2, claims_checked=2),
        telemetry={"model": None, "llm_calls": 0, "deterministic_stages": 17,
                   "deterministic_ms": 2258.0, "llm_tokens_in": 0,
                   "llm_tokens_out": 0, "llm_cost_usd": None,
                   "used_fallback": True,
                   "fallback_reason": "no model available (--no-llm)"},
    )
    out = buffer.getvalue()
    assert "deterministic template" in out
    assert "no model available" in out
    assert "model call #2" not in out


def test_the_console_view_shows_only_the_facts_the_prose_rests_on(context):
    """Printing all ninety facts is what made the old output unreadable. The cited
    ones are the ones a sentence can be checked against."""
    from kpi_agent.render import render_console

    telemetry = {"model": "stub", "llm_calls": 2, "deterministic_stages": 1,
                 "deterministic_ms": 1.0, "llm_tokens_in": 1, "llm_tokens_out": 1,
                 "llm_cost_usd": None, "used_fallback": False, "fallback_reason": ""}
    verification = VerificationResult(passed=True, violations=[],
                                      numbers_checked=2, claims_checked=2)
    # F3 is cited by nothing.
    narrative = _narrative()

    console, buffer = _capture_console()
    render_console(console, narrative=narrative, context=context,
                   verification=verification, telemetry=telemetry)
    trimmed = buffer.getvalue()
    assert "2 of 3 facts shown" in trimmed

    console, buffer = _capture_console()
    render_console(console, narrative=narrative, context=context,
                   verification=verification, telemetry=telemetry,
                   show_all_facts=True)
    assert "facts shown" not in buffer.getvalue()


@pytest.mark.parametrize("persona", ["analyst", "exec", "ops"])
def test_the_console_view_renders_for_every_persona(context, persona):
    """Personas select different fact kinds, so a renderer that assumes a kind is
    present breaks for one persona alone -- the same trap the template fell into."""
    from kpi_agent.render import render_console

    console, buffer = _capture_console()
    render_console(
        console,
        narrative=_narrative(),
        context=context.model_copy(update={"persona": persona}),
        verification=VerificationResult(passed=True, violations=[],
                                        numbers_checked=2, claims_checked=2),
        telemetry={"model": "stub", "llm_calls": 2, "deterministic_stages": 1,
                   "deterministic_ms": 1.0, "llm_tokens_in": 1, "llm_tokens_out": 1,
                   "llm_cost_usd": None, "used_fallback": False, "fallback_reason": ""},
    )
    assert persona in buffer.getvalue()


def test_the_console_view_does_not_replace_the_markdown_artefact(context):
    """`agent_report.md` is the checkable record. The terminal view is a second
    presentation of the same objects, never a second source of truth -- so adding
    it must leave `render_markdown` producing exactly what it produced before."""
    from kpi_agent.render import render_console, render_markdown

    narrative = _narrative()
    verification = VerificationResult(passed=True, violations=[],
                                      numbers_checked=2, claims_checked=2)
    telemetry = {"model": "stub", "llm_calls": 2, "deterministic_stages": 1,
                 "deterministic_ms": 1.0, "llm_tokens_in": 1, "llm_tokens_out": 1,
                 "llm_cost_usd": None, "used_fallback": False, "fallback_reason": ""}

    before = render_markdown(narrative, context, verification, telemetry)
    console, _ = _capture_console()
    render_console(console, narrative=narrative, context=context,
                   verification=verification, telemetry=telemetry)
    after = render_markdown(narrative, context, verification, telemetry)
    assert before == after
    assert before.startswith("# CAC rose in the West.")
