"""Seasonal-trend decomposition.

Purpose is to stop normal cycles being reported as anomalies: a Sunday traffic
peak is not an incident. Everything downstream works on the remainder.

This module also reports *seasonal strength* rather than assuming seasonality
exists. On the supplied dataset it is close to zero, and saying so is more
useful than silently subtracting a seasonal component that is really noise --
which would inflate the remainder and manufacture false positives.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from statsmodels.tsa.seasonal import STL

from kpi_engine.contracts.configs import DecompositionSpec


@dataclass
class DecompositionResult:
    trend: pd.Series
    seasonal: pd.Series
    remainder: pd.Series
    seasonal_strength: float
    trend_strength: float
    applied: bool
    reason: str

    @property
    def deseasonalised(self) -> pd.Series:
        return self.trend + self.remainder if self.applied else self.remainder


def _strength(component: pd.Series, remainder: pd.Series) -> float:
    """Hyndman-Wang strength: 1 - Var(remainder) / Var(component + remainder), floored at 0."""
    combined = (component + remainder).var()
    if not np.isfinite(combined) or combined <= 0:
        return 0.0
    return float(max(0.0, 1.0 - remainder.var() / combined))


def decompose_series(series: pd.Series, spec: DecompositionSpec) -> DecompositionResult:
    """STL on a period-indexed series. Falls back cleanly when the series is too short.

    Gaps are interpolated for the fit only; the returned remainder is re-masked to
    the original NaN positions so a missing period never becomes a detection.
    """
    values = series.astype("float64")
    mask = values.notna()
    n_valid = int(mask.sum())

    def _flat(reason: str) -> DecompositionResult:
        zeros = pd.Series(0.0, index=series.index)
        median = values.median() if n_valid else 0.0
        trend = pd.Series(median, index=series.index)
        return DecompositionResult(
            trend=trend,
            seasonal=zeros,
            remainder=values - trend,
            seasonal_strength=0.0,
            trend_strength=0.0,
            applied=False,
            reason=reason,
        )

    if not spec.enabled:
        return _flat("decomposition disabled in config")
    required = spec.period * spec.min_periods_required
    if n_valid < required:
        return _flat(f"only {n_valid} valid periods, need {required} ({spec.min_periods_required} cycles)")

    filled = values.interpolate(limit_direction="both")
    try:
        res = STL(filled, period=spec.period, robust=spec.robust).fit()
    except Exception as exc:  # noqa: BLE001 - STL raises a variety of ValueErrors
        return _flat(f"STL failed: {exc}")

    trend = pd.Series(res.trend, index=series.index)
    seasonal = pd.Series(res.seasonal, index=series.index)
    remainder = pd.Series(res.resid, index=series.index).where(mask)

    return DecompositionResult(
        trend=trend,
        seasonal=seasonal,
        remainder=remainder,
        seasonal_strength=_strength(seasonal, remainder.fillna(0.0)),
        trend_strength=_strength(trend - trend.mean(), remainder.fillna(0.0)),
        applied=True,
        reason="ok",
    )
