"""Injection must leave the dataset internally consistent, and abstention must
fire before a weak claim gets made."""

import datetime as dt

import pandas as pd
import pytest

from kpi_engine.contracts.configs import (
    ColumnOp,
    EventWindowSpec,
    InjectedEvent,
    ScenarioSpec,
)
from kpi_engine.contracts.payloads import (
    ConfidenceBreakdown,
    EventWindow,
    Lineage,
    ObservedDeviation,
)
from kpi_engine.evidence.abstention import check_abstention
from kpi_engine.evidence.confidence import score_confidence
from kpi_engine.profiling.profiler import (
    find_duplicate_groups,
    find_linear_identities,
    resolve_redundancy,
)
from kpi_engine.scenarios.injector import _shape_weights, inject_scenario


@pytest.fixture
def base_frame():
    n = 60
    dates = pd.date_range("2025-01-01", periods=n)
    revenue = pd.Series(range(1000, 1000 + n), dtype="float64")
    cogs = revenue * 0.6
    return pd.DataFrame({
        "Date": dates,
        "Region": ["West" if i % 2 else "East" for i in range(n)],
        "Net Sales": revenue,
        "Total Revenue": revenue,
        "COGS": cogs,
        "Total Gross Profit": revenue - cogs,
        "New Customers": [10] * n,
    })


def _profile_for(frame):
    numeric = [c for c in frame.columns if frame[c].dtype.kind in "if"]
    duplicates = find_duplicate_groups(frame, numeric)
    skip = {c for g in duplicates for c in g[1:]}
    identities = find_linear_identities(frame, [c for c in numeric if c not in skip])
    groups, redundant = resolve_redundancy(numeric, duplicates, identities)

    class _P:
        duplicate_groups = duplicates
        identity_groups = groups
        redundant_columns = redundant

    _P.identities = identities
    return _P()


def _scenario(**overrides):
    fields = {
        "event_id": "EV-TEST",
        "target_columns": {"COGS": ColumnOp(op="multiply", value=1.5)},
        "filters": {"Region": ["West"]},
        "window": EventWindowSpec(start=dt.date(2025, 1, 20), end=dt.date(2025, 1, 30)),
        **overrides,
    }
    event = InjectedEvent(**fields)
    return ScenarioSpec(scenario_id="s", base_source_id="b", events=[event])


def test_injection_only_touches_targeted_rows(base_frame):
    out, _ = inject_scenario(base_frame, _scenario(), "Date", _profile_for(base_frame))
    untouched = out[out["Region"] == "East"]
    pd.testing.assert_series_equal(
        untouched["COGS"].reset_index(drop=True),
        base_frame[base_frame["Region"] == "East"]["COGS"].reset_index(drop=True),
    )


def test_injection_preserves_accounting_identities(base_frame):
    """Raising COGS must lower gross profit, or the mutated data could not exist."""
    profile = _profile_for(base_frame)
    out, manifest = inject_scenario(base_frame, _scenario(), "Date", profile)
    residual = (out["Net Sales"] - out["COGS"] - out["Total Gross Profit"]).abs().max()
    assert residual == pytest.approx(0.0, abs=1e-6)
    assert any("identity restored" in n for n in manifest["events"][0]["propagation"])


def test_injection_without_propagation_breaks_the_identity(base_frame):
    """Confirms the previous test is actually exercising propagation."""
    out, _ = inject_scenario(
        base_frame, _scenario(propagate=False), "Date", _profile_for(base_frame)
    )
    assert (out["Net Sales"] - out["COGS"] - out["Total Gross Profit"]).abs().max() > 1.0


def test_injection_records_ground_truth(base_frame):
    out, manifest = inject_scenario(base_frame, _scenario(), "Date", _profile_for(base_frame))
    record = manifest["events"][0]
    assert record["rows_affected"] > 0
    assert record["column_changes"]["COGS"]["mean_pct_change"] == pytest.approx(50.0, abs=1.0)


def test_injection_fails_loudly_when_nothing_matches(base_frame):
    scenario = _scenario(filters={"Region": ["Nowhere"]})
    with pytest.raises(ValueError, match="matched no rows"):
        inject_scenario(base_frame, scenario, "Date", _profile_for(base_frame))


@pytest.mark.parametrize("shape,check", [
    ("step", lambda w: (w == 1.0).all()),
    ("ramp", lambda w: w[0] < w[-1]),
    ("spike", lambda w: (w > 0).sum() == 1),
    ("decay", lambda w: w[0] > w[-1]),
])
def test_shape_weights(shape, check):
    assert check(_shape_weights(shape, 10))


# --------------------------------------------------------------- confidence

def _event(min_support=10, peak_score=8.0):
    return EventWindow(
        event_id="EV-1",
        anomaly_types=["Structural Break"],
        detectors=["changepoint_cusum"],
        window_start=dt.date(2026, 3, 16),
        window_end=dt.date(2026, 3, 30),
        entity={"Region": "West"},
        primary_kpis_affected=["CAC"],
        observed_deviations=[ObservedDeviation(
            kpi="CAC", expected=30.0, actual=40.0, abs_delta=10.0, pct_delta=33.3, material=True
        )],
        candidate_covariates=["Cost Of Ads"],
        peak_score=peak_score,
        n_flags=5,
        min_support=min_support,
        lineage=Lineage(
            source_id="s", source_path="p", contract_id="c", kpi="CAC",
            columns=["Sales Marketing Expenses"], time_grain="week",
        ),
    )


def test_thin_support_lowers_confidence():
    strong = score_confidence(_event(min_support=40), [], 120)
    weak = score_confidence(_event(min_support=1), [], 120)
    assert weak.score < strong.score


def test_short_history_lowers_confidence():
    assert score_confidence(_event(), [], 5).score < score_confidence(_event(), [], 200).score


def test_confidence_publishes_its_formula():
    assert "geometric_mean" in score_confidence(_event(), [], 100).formula


def test_abstains_on_thin_support():
    event = _event(min_support=1)
    confidence = score_confidence(event, [], 120)
    result = check_abstention(event, [], confidence, threshold=0.7, min_support=3, n_pre_periods=120)
    assert result is not None
    assert result.reason_code == "insufficient_support"
    assert result.what_would_resolve_it


def test_abstains_on_short_history():
    event = _event()
    confidence = score_confidence(event, [], 4)
    result = check_abstention(event, [], confidence, threshold=0.7, min_history=28, n_pre_periods=4)
    assert result is not None
    assert result.reason_code == "insufficient_history"


def test_abstains_below_confidence_threshold():
    event = _event(min_support=4, peak_score=0.4)
    confidence = score_confidence(event, [], 40)
    result = check_abstention(event, [], confidence, threshold=0.99, n_pre_periods=40)
    assert result is not None
    assert result.reason_code == "below_confidence_threshold"


def test_no_abstention_when_evidence_is_strong():
    event = _event(min_support=50, peak_score=12.0)
    confidence = ConfidenceBreakdown(score=0.95, formula="test")
    assert check_abstention(event, [], confidence, threshold=0.7, n_pre_periods=200) is None
