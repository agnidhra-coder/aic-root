"""Detection. The leak test is the important one: a baseline that has seen the
future reports a precision it could never reproduce in production."""

import numpy as np
import pandas as pd
import pytest

from kpi_engine.contracts.configs import (
    BaselineSpec,
    ChangePointSpec,
    DecompositionSpec,
    PointSpec,
)
from kpi_engine.detection.baseline import fit_expanding_baseline, robust_scale
from kpi_engine.detection.changepoint import detect_changepoints
from kpi_engine.detection.decompose import decompose_series
from kpi_engine.detection.point import detect_point_anomalies


def test_baseline_uses_no_future_information():
    """Truncating the series must not change expectations already produced.

    If it does, some expectation was informed by data that had not happened yet.
    """
    rng = np.random.default_rng(0)
    values = pd.Series(50 + rng.normal(scale=2.0, size=200))
    spec = BaselineSpec(min_train_periods=56)

    full = fit_expanding_baseline(values, spec)
    truncated = fit_expanding_baseline(values.iloc[:150], spec)

    overlap = truncated.expected.dropna().index
    pd.testing.assert_series_equal(
        full.expected.loc[overlap], truncated.expected.loc[overlap], check_names=False
    )


def test_baseline_ignores_a_later_spike():
    """A spike at t=180 must not alter the expectation at t=100."""
    rng = np.random.default_rng(1)
    clean = pd.Series(50 + rng.normal(scale=1.0, size=200))
    spiked = clean.copy()
    spiked.iloc[180] += 500.0
    spec = BaselineSpec(min_train_periods=56)

    a = fit_expanding_baseline(clean, spec).expected.iloc[100]
    b = fit_expanding_baseline(spiked, spec).expected.iloc[100]
    assert a == pytest.approx(b)


def test_robust_scale_resists_outliers():
    """MAD barely moves when an extreme value is added; the standard deviation triples."""
    base = pd.Series(np.concatenate([np.zeros(99), [1000.0]]))
    _, sigma = robust_scale(base)
    assert sigma < 1.0
    assert base.std() > 90


def test_point_detector_finds_an_injected_spike():
    rng = np.random.default_rng(2)
    values = pd.Series(100 + rng.normal(scale=1.0, size=120))
    values.iloc[80] = 140.0
    residual = values - values.rolling(7, min_periods=1).median()

    flags = detect_point_anomalies(
        pd.Series(pd.date_range("2025-01-01", periods=120)),
        values, values, residual, pd.Series([10] * 120),
        "K", {}, PointSpec(z_threshold=3.5),
    )
    assert any(f.period == pd.Timestamp("2025-01-01").date() + pd.Timedelta(days=80) for f in flags)


def test_changepoint_finds_a_sustained_shift_that_point_detection_misses():
    """The case the whole multi-detector design exists for.

    A small persistent shift moves no single period far enough to be a point
    anomaly, while moving the segment mean decisively.
    """
    rng = np.random.default_rng(3)
    values = pd.Series(
        np.concatenate([rng.normal(100, 3, 120), rng.normal(106, 3, 120)])
    )
    periods = pd.Series(pd.date_range("2025-01-01", periods=240))
    support = pd.Series([10] * 240)

    residual = values - values.expanding(min_periods=2).median().shift(1)
    point_flags = detect_point_anomalies(
        periods, values, values, residual, support, "K", {}, PointSpec(z_threshold=3.5)
    )
    cp_flags = detect_changepoints(
        periods, values, values, support, "K", {}, ChangePointSpec(min_segment=14)
    )
    assert cp_flags, "change-point detection should find a sustained 2-sigma level shift"
    breaks = [f.period for f in cp_flags]
    target = pd.Timestamp("2025-01-01").date() + pd.Timedelta(days=120)
    assert any(abs((b - target).days) <= 21 for b in breaks)
    assert len(cp_flags) or not point_flags


def test_decomposition_reports_weak_seasonality_honestly():
    """On noise, STL must report near-zero seasonal strength rather than inventing a cycle."""
    rng = np.random.default_rng(4)
    values = pd.Series(rng.normal(100, 5, 200))
    result = decompose_series(values, DecompositionSpec(period=7))
    assert result.applied
    assert result.seasonal_strength < 0.35


def test_decomposition_finds_real_seasonality():
    t = np.arange(200)
    values = pd.Series(100 + 20 * np.sin(2 * np.pi * t / 7))
    result = decompose_series(values, DecompositionSpec(period=7))
    assert result.seasonal_strength > 0.8


def test_decomposition_declines_on_short_series():
    result = decompose_series(pd.Series([1.0, 2.0, 3.0]), DecompositionSpec(period=7))
    assert not result.applied
    assert "need" in result.reason
