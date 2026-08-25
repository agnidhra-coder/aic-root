"""Trend estimation: direction, rate, and whether the direction is real.

Two deliberate choices here.

**Theil-Sen, not OLS.** The least-squares slope is dragged by exactly the spikes
that stage 2 exists to flag, so on a flat series with one outlier OLS reports a
trend that is not there. Theil-Sen (median of pairwise slopes) has a ~29%
breakdown point and ignores it. `tests/test_eda.py` pins this: adding one large
spike must not move the reported slope.

**Mann-Kendall, not the regression t-test.** It is rank-based, so it assumes no
particular error distribution -- these ratio KPIs have heavy tails and the
normal-theory p-value would be overconfident.

A direction is only *named* when it is both statistically significant and large
enough to matter; see `TrendSpec.flat_slope_pct`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from kpi_engine.contracts.configs import TrendSpec
from kpi_engine.contracts.payloads import TrendDirection, TrendSummary
from kpi_engine.detection.decompose import DecompositionResult


def mann_kendall_p(values: np.ndarray) -> float:
    """Two-sided Mann-Kendall p-value for monotonic trend, with tie correction.

    Returns 1.0 (no evidence) rather than NaN on degenerate input, so callers
    never have to reason about NaN comparisons -- `nan > alpha` is False, which
    would silently call a trend on a constant series.
    """
    n = len(values)
    if n < 4:
        return 1.0

    signs = np.sign(values[None, :] - values[:, None])
    s = float(np.sum(np.triu(signs, k=1)))

    _, counts = np.unique(values, return_counts=True)
    tie_term = float(np.sum(counts * (counts - 1) * (2 * counts + 5)))
    var_s = (n * (n - 1) * (2 * n + 5) - tie_term) / 18.0
    if var_s <= 0:
        return 1.0

    # Continuity correction: S is a discrete statistic approximated by a normal.
    if s > 0:
        z = (s - 1) / np.sqrt(var_s)
    elif s < 0:
        z = (s + 1) / np.sqrt(var_s)
    else:
        return 1.0
    return float(2 * (1 - stats.norm.cdf(abs(z))))


def summarise_trend(
    values: pd.Series, spec: TrendSpec, decomposition: DecompositionResult | None = None
) -> TrendSummary:
    """Characterise the underlying level of one series.

    `decomposition` supplies the Hyndman-Wang trend strength when available so
    STL is not re-fitted; the slope itself is computed on the raw series.
    """
    clean = values.astype("float64").dropna()
    n = len(clean)
    strength = float(decomposition.trend_strength) if decomposition is not None else 0.0

    if n < spec.min_periods:
        return TrendSummary(
            direction="indeterminate",
            slope_per_period=0.0,
            slope_pct_per_period=None,
            trend_strength=strength,
            r_squared=0.0,
            p_value=1.0,
            total_change_pct=None,
            n_periods=n,
        )

    y = clean.to_numpy(dtype="float64")
    x = np.arange(n, dtype="float64")

    slope, intercept, _, _ = stats.theilslopes(y, x)
    p_value = mann_kendall_p(y)

    fitted = intercept + slope * x
    ss_res = float(np.sum((y - fitted) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r_squared = float(max(0.0, 1.0 - ss_res / ss_tot)) if ss_tot > 0 else 0.0

    median_level = float(np.median(y))
    slope_pct = (
        float(slope / abs(median_level) * 100.0)
        if median_level != 0 and np.isfinite(median_level)
        else None
    )

    # Fitted endpoints, not raw first/last: a noisy final point should not become
    # the headline number.
    start_level, end_level = float(fitted[0]), float(fitted[-1])
    total_change_pct = (
        float((end_level - start_level) / abs(start_level) * 100.0) if start_level != 0 else None
    )

    direction = call_direction(slope, p_value, slope_pct, spec, total_change_pct)

    return TrendSummary(
        direction=direction,
        slope_per_period=float(slope),
        slope_pct_per_period=slope_pct,
        trend_strength=strength,
        r_squared=r_squared,
        p_value=p_value,
        total_change_pct=total_change_pct,
        n_periods=n,
    )


def call_direction(
    slope: float,
    p_value: float,
    slope_pct: float | None,
    spec: TrendSpec,
    total_change_pct: float | None = None,
) -> TrendDirection:
    """Name a direction only when it is both significant and materially large.

    Materiality is satisfied by *either* a meaningful per-period rate or a
    meaningful cumulative move. Requiring the rate alone is wrong: a 0.4%/period
    drift is below any sensible rate bar yet compounds to +52% over two years,
    which is the single most important thing to say about that series.
    """
    if p_value > spec.alpha:
        return "flat"

    fast_enough = slope_pct is not None and abs(slope_pct) >= spec.flat_slope_pct
    far_enough = total_change_pct is not None and abs(total_change_pct) >= spec.min_total_change_pct
    if not (fast_enough or far_enough):
        return "flat"

    return "rising" if slope > 0 else "falling"
