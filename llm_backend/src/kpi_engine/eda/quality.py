"""How far a series can be trusted.

This is the part a narration layer needs most and would otherwise invent. A
confident sentence about a KPI computed from 1.2 rows per cell is worse than no
sentence, so the support and coverage facts travel with every profile rather
than being left implicit.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from kpi_engine.contracts.payloads import DataQualitySummary
from kpi_engine.detection.baseline import robust_scale


def summarise_quality(
    periods: pd.Series,
    values: pd.Series,
    support: pd.Series,
    min_rows_per_cell: int,
    min_history: int,
    outlier_z: float,
) -> DataQualitySummary:
    n_periods = len(values)
    finite = values.astype("float64")
    n_missing = int(finite.isna().sum())
    n_present = n_periods - n_missing

    coverage = float(n_present / n_periods) if n_periods else 0.0
    support_vals = support.astype("float64").fillna(0.0)

    low_support_share = (
        float((support_vals < min_rows_per_cell).sum() / n_periods) if n_periods else 0.0
    )

    return DataQualitySummary(
        n_periods=n_periods,
        n_missing=n_missing,
        coverage=round(coverage, 4),
        longest_gap_periods=_longest_gap(finite),
        mean_support=round(float(support_vals.mean()), 2) if n_periods else 0.0,
        min_support=int(support_vals.min()) if n_periods else 0,
        low_support_share=round(low_support_share, 4),
        sufficient_history=n_present >= min_history,
        volatility_cv=_robust_cv(finite),
        outlier_share=_outlier_share(finite, outlier_z),
    )


def _longest_gap(values: pd.Series) -> int:
    """Longest run of consecutive missing periods."""
    missing = values.isna().to_numpy()
    longest = run = 0
    for is_missing in missing:
        run = run + 1 if is_missing else 0
        longest = max(longest, run)
    return int(longest)


def _robust_cv(values: pd.Series) -> float | None:
    """MAD-based coefficient of variation.

    Robust rather than std/mean because a single spike would otherwise report a
    stable series as wildly volatile.
    """
    clean = values.dropna()
    if len(clean) < 3:
        return None
    median, sigma = robust_scale(clean)
    if not np.isfinite(median) or median == 0:
        return None
    return round(float(sigma / abs(median)), 4)


def _outlier_share(values: pd.Series, outlier_z: float) -> float:
    """Share of points a modified z-score would call extreme.

    Descriptive only. This is *not* a detection: it runs on the raw level with no
    baseline, no seasonal adjustment and no corroboration, which is precisely why
    stage 2 exists and why nothing may branch on this number.
    """
    clean = values.dropna()
    if len(clean) < 3:
        return 0.0
    median, sigma = robust_scale(clean)
    if sigma <= 0:
        return 0.0
    z = (clean - median).abs() / sigma
    return round(float((z > outlier_z).sum() / len(clean)), 4)
