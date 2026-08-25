"""Tests for the descriptive series-profiling stage.

The through-line: this stage must describe what is there and stay silent about
what is not. Most of these tests assert the *negative* case -- flat data reads as
flat, noise reports no seasonality, a short series refuses to be characterised --
because a describer that manufactures structure is worse than none.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kpi_engine.contracts.configs import DecompositionSpec, EdaSpec, SegmentSpec, TrendSpec
from kpi_engine.detection.changepoint import pelt_breaks
from kpi_engine.detection.decompose import decompose_series
from kpi_engine.eda.quality import summarise_quality
from kpi_engine.eda.seasonality import summarise_seasonality
from kpi_engine.eda.segments import segment_series
from kpi_engine.eda.trend import mann_kendall_p, summarise_trend


def _periods(n: int, freq: str = "W") -> pd.Series:
    return pd.Series(pd.date_range("2025-01-01", periods=n, freq=freq))


# --------------------------------------------------------------------------- trend


def test_flat_noise_is_reported_as_flat():
    """The load-bearing test: the supplied dataset is noise and must read as flat."""
    rng = np.random.default_rng(0)
    values = pd.Series(rng.normal(100, 10, 104))

    trend = summarise_trend(values, TrendSpec())

    assert trend.direction == "flat"
    assert trend.p_value > 0.05


def test_linear_ramp_is_detected_with_the_right_slope():
    rng = np.random.default_rng(1)
    true_slope = 0.5
    values = pd.Series(100 + true_slope * np.arange(104) + rng.normal(0, 3, 104))

    trend = summarise_trend(values, TrendSpec())

    assert trend.direction == "rising"
    assert trend.p_value < 0.01
    assert trend.slope_per_period == pytest.approx(true_slope, abs=0.1)


def test_falling_series_is_labelled_falling():
    rng = np.random.default_rng(2)
    values = pd.Series(200 - 0.8 * np.arange(104) + rng.normal(0, 3, 104))

    assert summarise_trend(values, TrendSpec()).direction == "falling"


def test_slope_is_robust_to_a_single_spike():
    """Why Theil-Sen and not OLS.

    One outlier must not turn a flat series into a trending one, nor materially
    move a real slope. OLS fails this: the spike is leveraged by its position.
    """
    rng = np.random.default_rng(3)
    base = 100 + 0.5 * np.arange(104) + rng.normal(0, 3, 104)

    clean = summarise_trend(pd.Series(base), TrendSpec())

    spiked = base.copy()
    spiked[10] = 5000.0
    contaminated = summarise_trend(pd.Series(spiked), TrendSpec())

    assert contaminated.slope_per_period == pytest.approx(clean.slope_per_period, rel=0.05)
    assert contaminated.direction == clean.direction

    # Contrast: least squares is dragged badly by the same point.
    ols_clean = np.polyfit(np.arange(104), base, 1)[0]
    ols_spiked = np.polyfit(np.arange(104), spiked, 1)[0]
    assert abs(ols_spiked - ols_clean) > abs(
        contaminated.slope_per_period - clean.slope_per_period
    )


def test_significant_but_tiny_drift_is_called_flat():
    """Statistical significance is not business relevance."""
    values = pd.Series(100 + 0.01 * np.arange(200))  # perfectly monotonic, 0.01%/period

    trend = summarise_trend(values, TrendSpec(flat_slope_pct=0.5))

    assert trend.p_value < 0.05  # it is real
    assert trend.direction == "flat"  # and it does not matter


def test_short_series_is_indeterminate_not_guessed():
    trend = summarise_trend(pd.Series([1.0, 5.0, 2.0, 9.0]), TrendSpec(min_periods=8))
    assert trend.direction == "indeterminate"


def test_mann_kendall_returns_no_evidence_on_a_constant_series():
    """A constant series must give p=1.0, never NaN -- `nan > alpha` is False,
    which would silently call a trend on a flat line."""
    assert mann_kendall_p(np.full(50, 7.0)) == 1.0


# --------------------------------------------------------------------------- seasonality


def test_real_seasonality_is_found():
    rng = np.random.default_rng(4)
    n = 210
    values = 100 + 20 * np.sin(2 * np.pi * np.arange(n) / 7) + rng.normal(0, 2, n)
    series = pd.Series(values, index=pd.date_range("2025-01-01", periods=n))

    decomposition = decompose_series(series, DecompositionSpec(period=7))
    summary = summarise_seasonality(decomposition, _periods(n, "D"), 0.45, "day", 7)

    assert summary.detected
    assert summary.strength > 0.8
    assert summary.period == 7


def test_noise_reports_no_seasonality():
    """Mirrors test_decomposition_reports_weak_seasonality_honestly.

    STL always fits *some* repeating wiggle, so strength > 0 is not evidence. The
    floor is calibrated above the p99 of this null; see configs/eda/default.yaml.
    """
    rng = np.random.default_rng(5)
    series = pd.Series(rng.normal(100, 10, 210), index=pd.date_range("2025-01-01", periods=210))

    decomposition = decompose_series(series, DecompositionSpec(period=7))
    summary = summarise_seasonality(decomposition, _periods(210, "D"), 0.45, "day", 7)

    assert not summary.detected
    assert "floor" in summary.reason


def test_seasonality_reports_the_period_it_was_fitted_with():
    """Reporting any other period would describe a cycle STL never modelled."""
    rng = np.random.default_rng(6)
    n = 120
    values = 100 + 15 * np.sin(2 * np.pi * np.arange(n) / 4) + rng.normal(0, 1, n)
    series = pd.Series(values, index=pd.date_range("2025-01-01", periods=n, freq="W"))

    decomposition = decompose_series(series, DecompositionSpec(period=4))
    summary = summarise_seasonality(decomposition, _periods(n), 0.45, "week", 4)

    assert summary.period == 4


# --------------------------------------------------------------------------- segments


def test_sustained_regime_change_is_segmented_at_the_break():
    rng = np.random.default_rng(7)
    values = pd.Series(np.concatenate([rng.normal(100, 8, 60), rng.normal(130, 8, 60)]))

    segments = segment_series(_periods(120), values, SegmentSpec(), TrendSpec())

    assert len(segments) == 2
    assert segments[0].n_periods == pytest.approx(60, abs=6)
    assert segments[1].mean > segments[0].mean
    assert segments[1].pct_change_vs_previous > 20


def test_brief_transient_is_not_reported_as_a_phase():
    """A three-period excursion that reverts is an *event*, not a phase.

    This is the shape the injected ad-cost scenario actually has, and PELT
    rightly declines to segment it. Calling it a regime change would be wrong;
    catching it is stage 2's job, and stage 2 does catch it.
    """
    rng = np.random.default_rng(8)
    values = pd.Series(
        np.concatenate([rng.normal(100, 8, 60), rng.normal(130, 8, 3), rng.normal(100, 8, 42)])
    )

    segments = segment_series(_periods(105), values, SegmentSpec(), TrendSpec())

    assert len(segments) <= 1


def test_segment_count_is_capped():
    rng = np.random.default_rng(9)
    chunks = [rng.normal(100 + 40 * i, 4, 20) for i in range(10)]
    values = pd.Series(np.concatenate(chunks))

    segments = segment_series(_periods(200), values, SegmentSpec(max_segments=4), TrendSpec())

    assert len(segments) <= 4


def test_segmentation_disabled_returns_nothing():
    values = pd.Series(np.arange(100, dtype=float))
    assert segment_series(_periods(100), values, SegmentSpec(enabled=False), TrendSpec()) == []


def test_pelt_prefers_rbf_over_l2_for_level_shifts():
    """Pins the cost-function choice: l2 shatters a clean two-regime series."""
    rng = np.random.default_rng(10)
    signal = np.concatenate([rng.normal(100, 8, 60), rng.normal(130, 8, 60)])

    assert pelt_breaks(signal, 12.0, 4, "rbf") == [60]
    assert len(pelt_breaks(signal, 12.0, 4, "l2")) > 3


# --------------------------------------------------------------------------- quality


def test_quality_counts_gaps_and_support():
    values = pd.Series([1.0, 2.0, np.nan, np.nan, np.nan, 3.0, 4.0, 5.0])
    support = pd.Series([10, 10, 0, 0, 0, 10, 2, 10])

    q = summarise_quality(_periods(8), values, support, 3, 12, 3.5)

    assert q.n_periods == 8
    assert q.n_missing == 3
    assert q.longest_gap_periods == 3
    assert q.coverage == pytest.approx(5 / 8)
    assert q.low_support_share == pytest.approx(4 / 8)  # three zeros plus the 2
    assert not q.sufficient_history


def test_volatility_is_robust_to_a_single_spike():
    rng = np.random.default_rng(11)
    base = rng.normal(100, 5, 100)
    support = pd.Series(np.full(100, 10))

    stable = summarise_quality(_periods(100), pd.Series(base), support, 3, 12, 3.5)

    spiked = base.copy()
    spiked[50] = 10_000.0
    contaminated = summarise_quality(_periods(100), pd.Series(spiked), support, 3, 12, 3.5)

    assert contaminated.volatility_cv == pytest.approx(stable.volatility_cv, rel=0.1)


def test_outlier_share_is_zero_on_clean_noise():
    rng = np.random.default_rng(12)
    values = pd.Series(rng.normal(100, 5, 200))
    support = pd.Series(np.full(200, 10))

    q = summarise_quality(_periods(200), values, support, 3, 12, 3.5)

    assert q.outlier_share < 0.02


# --------------------------------------------------------------------------- end to end


def _spec() -> EdaSpec:
    return EdaSpec()


def test_profile_panel_marks_thin_series_unusable(tmp_path):
    """A series below min_history is refused, not characterised."""
    from kpi_engine.eda.runner import _usability
    from kpi_engine.contracts.payloads import DataQualitySummary

    thin = DataQualitySummary(
        n_periods=5, n_missing=0, coverage=1.0, longest_gap_periods=0,
        mean_support=10.0, min_support=10, low_support_share=0.0,
        sufficient_history=False, volatility_cv=0.1, outlier_share=0.0,
    )
    usable, reason = _usability(thin, _spec())

    assert not usable
    assert "5 periods" in reason


def test_profile_panel_refuses_a_mostly_empty_series():
    from kpi_engine.eda.runner import _usability
    from kpi_engine.contracts.payloads import DataQualitySummary

    sparse = DataQualitySummary(
        n_periods=100, n_missing=0, coverage=1.0, longest_gap_periods=0,
        mean_support=1.0, min_support=0, low_support_share=0.9,
        sufficient_history=True, volatility_cv=0.1, outlier_share=0.0,
    )
    usable, reason = _usability(sparse, _spec())

    assert not usable
    assert "rows-per-cell" in reason
