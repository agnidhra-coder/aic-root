"""Expanding-window baseline forecaster.

The one rule that matters here: the expectation for period t is produced by a
model that has never seen period t or anything after it. In production the
future genuinely is not available, so a baseline fitted on the whole series
would understate residuals, hide the anomalies it is meant to find, and report
a precision it could not reproduce live.

Refitting is throttled to `refit_every` periods: coefficients are reused for a
few steps but lag *features* are always taken from data strictly before t, so
throttling costs a little freshness and leaks nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from kpi_engine.contracts.configs import BaselineSpec


@dataclass
class BaselineResult:
    expected: pd.Series
    residual: pd.Series
    model: str
    n_fits: int
    coverage: float

    @property
    def n_residuals(self) -> int:
        return int(self.residual.notna().sum())


def _build_features(values: pd.Series, spec: BaselineSpec) -> pd.DataFrame:
    """Lags and rolling statistics, every one shifted so period t sees only t-1 and earlier."""
    feats = pd.DataFrame(index=values.index)
    for lag in spec.lags:
        feats[f"lag_{lag}"] = values.shift(lag)
    for window in spec.rolling_windows:
        shifted = values.shift(1)
        feats[f"rmean_{window}"] = shifted.rolling(window, min_periods=max(2, window // 2)).mean()
        feats[f"rstd_{window}"] = shifted.rolling(window, min_periods=max(2, window // 2)).std()
    return feats


def _seasonal_naive(values: pd.Series, period: int) -> pd.Series:
    return values.shift(period)


def fit_expanding_baseline(
    values: pd.Series, spec: BaselineSpec, period: int = 7, refit_every: int = 7
) -> BaselineResult:
    """One-step-ahead expectations for every period with enough history behind it."""
    values = values.astype("float64")
    n = len(values)
    expected = pd.Series(np.nan, index=values.index, dtype="float64")

    if spec.model == "seasonal_naive":
        expected = _seasonal_naive(values, period)
        residual = values - expected
        return BaselineResult(
            expected=expected,
            residual=residual,
            model="seasonal_naive",
            n_fits=0,
            coverage=float(residual.notna().mean()),
        )

    feats = _build_features(values, spec)
    positions = np.arange(n)
    model: Ridge | None = None
    n_fits = 0
    fallback = values.expanding(min_periods=2).median().shift(1)

    for i in positions:
        if i < spec.min_train_periods:
            continue
        # Training rows: strictly before i, with complete features and a known target.
        train_mask = (positions < i) & feats.notna().all(axis=1).to_numpy() & values.notna().to_numpy()
        if train_mask.sum() < spec.min_train_periods:
            continue
        if model is None or (i - spec.min_train_periods) % refit_every == 0:
            model = Ridge(alpha=1.0)
            model.fit(feats[train_mask], values[train_mask])
            n_fits += 1
        row = feats.iloc[[i]]
        if row.notna().all(axis=1).iloc[0]:
            expected.iloc[i] = float(model.predict(row)[0])

    expected = expected.fillna(fallback)
    residual = values - expected
    return BaselineResult(
        expected=expected,
        residual=residual,
        model=f"ridge_lags(lags={spec.lags}, roll={spec.rolling_windows})",
        n_fits=n_fits,
        coverage=float(residual.notna().mean()),
    )


def robust_scale(residual: pd.Series, mad_floor: float = 1e-9) -> tuple[float, float]:
    """Median and MAD-derived sigma, using the 1.4826 consistency constant.

    Robust statistics are the point: the mean and standard deviation of a series
    are dragged around by the very outliers we are trying to detect, so an
    anomaly would inflate its own threshold and hide.
    """
    clean = residual.dropna()
    if clean.empty:
        return 0.0, mad_floor
    median = float(clean.median())
    mad = float((clean - median).abs().median())
    return median, max(1.4826 * mad, mad_floor)
