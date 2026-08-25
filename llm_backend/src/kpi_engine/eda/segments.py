"""Split a series into homogeneous phases.

A single global slope describes a series that rose steadily; it badly describes
one that was flat, jumped, then was flat again -- which is exactly the shape an
injected shock produces. Segmenting gives a narrator discrete phases to talk
about instead of one averaged-out number that is true of no part of the series.

Reuses PELT from `detection/changepoint.py` rather than re-deriving it, so the
two stages agree on where a break is.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from kpi_engine.contracts.configs import SegmentSpec, TrendSpec
from kpi_engine.contracts.payloads import SeriesSegment
from kpi_engine.detection.changepoint import pelt_breaks
from kpi_engine.eda.trend import call_direction, mann_kendall_p


def segment_series(
    periods: pd.Series, values: pd.Series, spec: SegmentSpec, trend_spec: TrendSpec
) -> list[SeriesSegment]:
    if not spec.enabled:
        return []

    frame = pd.DataFrame({"period": periods.to_numpy(), "value": values.to_numpy()}).dropna()
    if len(frame) < 2 * spec.min_segment:
        return []

    signal = frame["value"].to_numpy(dtype="float64")
    breaks = pelt_breaks(signal, spec.pelt_penalty, spec.min_segment)

    # Too many phases is the same as none for a reader; keep the largest jumps.
    if len(breaks) > spec.max_segments - 1:
        breaks = _keep_largest_breaks(signal, breaks, spec.max_segments - 1)

    bounds = [0, *breaks, len(signal)]
    segments: list[SeriesSegment] = []
    previous_mean: float | None = None

    for i, (lo, hi) in enumerate(zip(bounds[:-1], bounds[1:])):
        chunk = signal[lo:hi]
        if len(chunk) == 0:
            continue
        mean = float(np.mean(chunk))
        slope = _segment_slope(chunk)
        pct_change = (
            float((mean - previous_mean) / abs(previous_mean) * 100.0)
            if previous_mean not in (None, 0)
            else None
        )
        slope_pct = float(slope / abs(mean) * 100.0) if mean != 0 else None

        segments.append(
            SeriesSegment(
                index=i,
                start=pd.Timestamp(frame["period"].iloc[lo]).date(),
                end=pd.Timestamp(frame["period"].iloc[hi - 1]).date(),
                n_periods=len(chunk),
                mean=round(mean, 6),
                slope_per_period=round(slope, 6),
                direction=call_direction(slope, mann_kendall_p(chunk), slope_pct, trend_spec),
                pct_change_vs_previous=round(pct_change, 2) if pct_change is not None else None,
            )
        )
        previous_mean = mean

    return segments


def _segment_slope(chunk: np.ndarray) -> float:
    """Theil-Sen slope within a segment; 0 when the segment is too short to fit."""
    if len(chunk) < 3:
        return 0.0
    slope, _, _, _ = stats.theilslopes(chunk, np.arange(len(chunk), dtype="float64"))
    return float(slope)


def _keep_largest_breaks(signal: np.ndarray, breaks: list[int], keep: int) -> list[int]:
    """Retain the breaks with the biggest level shift across them."""
    if keep <= 0:
        return []
    scored = []
    bounds = [0, *breaks, len(signal)]
    for i, b in enumerate(breaks):
        left = signal[bounds[i] : b]
        right = signal[b : bounds[i + 2]]
        if len(left) == 0 or len(right) == 0:
            continue
        scored.append((abs(float(np.mean(right) - np.mean(left))), b))
    scored.sort(reverse=True)
    return sorted(b for _, b in scored[:keep])
