"""Causal layer. The algebraic tests check mathematical axioms rather than
hardcoded outputs, so they stay meaningful if the implementation changes."""

import numpy as np
import pandas as pd
import pytest

from kpi_engine.causal.algebraic import lmdi_decomposition, shapley_decomposition
from kpi_engine.causal.dag import CausalGraph
from kpi_engine.causal.did import estimate_did
from kpi_engine.causal.did import test_parallel_trends as check_parallel_trends
from kpi_engine.causal.dml import estimate_dml
from kpi_engine.causal.its import estimate_its


# --------------------------------------------------------------- exact algebra

def test_shapley_efficiency_axiom(contract):
    """Contributions must sum exactly to the total movement: nothing lost, nothing invented."""
    cac = contract.kpi("CAC")
    pre = {"spend": 100_000.0, "new_customers": 3_000.0}
    post = {"spend": 115_000.0, "new_customers": 2_400.0}
    result = shapley_decomposition(cac, pre, post)
    assert result.residual == pytest.approx(0.0, abs=1e-9)
    assert sum(t.contribution for t in result.terms) == pytest.approx(result.total_delta)


def test_shapley_matches_hand_computed_answer(contract):
    """+15% spend against -20% acquisitions is a 38.57 / 61.43 split, by hand."""
    cac = contract.kpi("CAC")
    result = shapley_decomposition(
        cac, {"spend": 100.0, "new_customers": 100.0}, {"spend": 115.0, "new_customers": 80.0}
    )
    shares = {t.measure: t.share for t in result.terms}
    assert shares["spend"] == pytest.approx(0.3857, abs=1e-3)
    assert shares["new_customers"] == pytest.approx(0.6143, abs=1e-3)


def test_shapley_null_player(contract):
    """A measure that did not move contributes nothing."""
    cac = contract.kpi("CAC")
    result = shapley_decomposition(
        cac, {"spend": 100.0, "new_customers": 50.0}, {"spend": 120.0, "new_customers": 50.0}
    )
    contributions = {t.measure: t.contribution for t in result.terms}
    assert contributions["new_customers"] == pytest.approx(0.0, abs=1e-9)
    assert contributions["spend"] == pytest.approx(result.total_delta)


def test_lmdi_agrees_with_shapley(contract):
    """Two independent derivations; disagreement would mean one is implemented wrong."""
    cac = contract.kpi("CAC")
    pre = {"spend": 100_000.0, "new_customers": 3_000.0}
    post = {"spend": 115_000.0, "new_customers": 2_400.0}
    shap = {t.measure: t.share for t in shapley_decomposition(cac, pre, post).terms}
    lmdi = {
        t.measure: t.share
        for t in lmdi_decomposition(cac, pre, post, "spend", "new_customers").terms
    }
    for measure in shap:
        assert shap[measure] == pytest.approx(lmdi[measure], abs=0.02)


def test_shapley_handles_multi_term_expression(contract):
    """Net margin is not a bare ratio; efficiency must still hold."""
    result = shapley_decomposition(
        contract.kpi("Net Profit Margin"),
        {"total_revenue": 1_000_000.0, "total_expenses": 776_000.0},
        {"total_revenue": 1_050_000.0, "total_expenses": 880_000.0},
    )
    assert result.residual == pytest.approx(0.0, abs=1e-9)


# ------------------------------------------------------------------------ DAG

def test_graph_rejects_cycles(graph_spec):
    spec = graph_spec.model_copy(deep=True)
    spec.edges.append(type(spec.edges[0])(source="CAC", target="Cost Of Ads", relation="causal"))
    spec.edges.append(type(spec.edges[0])(source="Cost Of Ads", target="CAC", relation="causal"))
    with pytest.raises(ValueError, match="cycle"):
        CausalGraph(spec)


def test_graph_rejects_self_declared_forbidden_edge(graph_spec):
    """A graph that both declares and forbids an edge is incoherent and must not load.

    Checked ahead of the cycle rule, since a reversed mechanism is usually also
    what closes the cycle and the forbidden-edge message names the real mistake.
    """
    spec = graph_spec.model_copy(deep=True)
    spec.edges.append(
        type(spec.edges[0])(source="CAC", target="New Customers", relation="causal")
    )
    with pytest.raises(ValueError, match="forbids"):
        CausalGraph(spec)


def test_deterministic_parents_excluded_from_estimated_drivers(graph_spec):
    """Re-estimating a KPI's own definition would rediscover arithmetic and call it a finding."""
    graph = CausalGraph(graph_spec)
    deterministic = set(graph.parents("CAC", "deterministic"))
    assert deterministic == {"Sales Marketing Expenses", "New Customers"}
    assert not deterministic & set(graph.upstream_causal_drivers("CAC"))


def test_ownership_metadata_present(graph_spec):
    graph = CausalGraph(graph_spec)
    assert graph.is_controllable("Cost Of Ads")
    assert graph.owner("Cost Of Ads") == "Performance Marketing"
    assert not graph.is_controllable("New Customers")


# ------------------------------------------------------------------------ DML

def test_dml_recovers_effect_under_confounding():
    """The naive regression is biased here; the cross-fitted estimate should not be."""
    rng = np.random.default_rng(0)
    n = 600
    c1, c2 = rng.normal(size=n), rng.normal(size=n)
    t = 2.0 * c1 - 1.0 * c2 + rng.normal(size=n)
    y = 3.0 * t + 5.0 * c1 + 2.0 * c2 + rng.normal(size=n)
    df = pd.DataFrame({"y": y, "t": t, "c1": c1, "c2": c2})

    naive = np.polyfit(df["t"], df["y"], 1)[0]
    assert abs(naive - 3.0) > 0.5  # confounded, as expected

    result = estimate_dml(df, "y", "t", ["c1", "c2"])
    assert result.valid
    assert result.ci_low < 3.0 < result.ci_high


def test_dml_refuses_when_treatment_has_no_independent_variation():
    """A treatment that is an exact function of a control cannot be identified."""
    rng = np.random.default_rng(1)
    n = 200
    c = rng.normal(size=n)
    df = pd.DataFrame({"y": rng.normal(size=n), "t": 2.0 * c, "c": c})
    result = estimate_dml(df, "y", "t", ["c"])
    assert not result.valid
    assert "variation" in result.reason


def test_dml_refuses_on_too_few_rows():
    df = pd.DataFrame({"y": [1.0, 2.0, 3.0], "t": [1.0, 2.0, 3.0], "c": [1.0, 0.0, 1.0]})
    assert not estimate_dml(df, "y", "t", ["c"]).valid


# ------------------------------------------------------------------------ DiD

def _did_panel(treated_effect: float, control_drift: float = 0.0) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    periods = pd.date_range("2025-01-01", periods=120, freq="D")
    rows = []
    for i, period in enumerate(periods):
        after = period >= pd.Timestamp("2025-04-01")
        for region in ["A", "B", "C"]:
            base = 10.0 + rng.normal(scale=0.3)
            if region == "A":
                base += treated_effect * after + control_drift * i / 120.0
            rows.append({"period": period, "Region": region, "kpi": "K", "value": base})
    return pd.DataFrame(rows)


def test_did_recovers_a_known_effect():
    panel = _did_panel(treated_effect=2.0)
    result = estimate_did(
        panel, "K", "Region", "A", (pd.Timestamp("2025-04-01").date(), pd.Timestamp("2025-04-30").date())
    )
    assert result.valid
    assert result.ci_low < 2.0 < result.ci_high


def test_did_abstains_when_pre_trends_diverge():
    """A pre-existing divergence would otherwise be reported as the event's effect."""
    panel = _did_panel(treated_effect=0.0, control_drift=6.0)
    result = estimate_did(
        panel, "K", "Region", "A", (pd.Timestamp("2025-04-01").date(), pd.Timestamp("2025-04-30").date())
    )
    assert not result.valid
    assert "parallel" in result.reason.lower()


def test_did_needs_control_slices():
    panel = _did_panel(2.0)
    panel = panel[panel["Region"] == "A"]
    result = estimate_did(
        panel, "K", "Region", "A", (pd.Timestamp("2025-04-01").date(), pd.Timestamp("2025-04-30").date())
    )
    assert not result.valid
    assert "control" in result.reason


def test_parallel_trends_accepts_parallel_series():
    periods = pd.date_range("2025-01-01", periods=40, freq="D")
    treated = pd.DataFrame({"period": periods, "value": np.linspace(10, 12, 40)})
    control = pd.DataFrame({"period": periods, "value": np.linspace(8, 10, 40)})
    p_value, ok = check_parallel_trends(treated, control)
    assert ok and p_value > 0.05


# ------------------------------------------------------------------------ ITS

def test_its_detects_a_level_shift():
    rng = np.random.default_rng(5)
    periods = pd.date_range("2025-01-01", periods=150, freq="D")
    after = periods >= pd.Timestamp("2025-05-01")
    values = 20.0 + rng.normal(scale=0.4, size=150) - 3.0 * after
    panel = pd.DataFrame({"period": periods, "kpi": "K", "value": values})
    result = estimate_its(
        panel, "K", {}, (pd.Timestamp("2025-05-01").date(), pd.Timestamp("2025-05-31").date())
    )
    assert result.valid
    assert result.ci_low < -3.0 < result.ci_high
