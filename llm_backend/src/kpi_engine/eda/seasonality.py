"""Seasonality description on top of the STL fit detection already performs.

The honest-reporting rule from `detection/decompose.py` carries over: a seasonal
component is only *claimed* when its Hyndman-Wang strength clears
`EdaSpec.seasonality_floor`. On the supplied dataset that strength is near zero,
and saying "no seasonality" is more useful than describing a cycle that is noise.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from kpi_engine.contracts.payloads import SeasonalitySummary
from kpi_engine.detection.decompose import DecompositionResult

_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _label(position: int, period: int, time_grain: str) -> str:
    """Human-readable name for a position within the seasonal cycle."""
    if time_grain == "day" and period == 7:
        return _WEEKDAYS[position % 7]
    if time_grain == "month" and period == 12:
        return _MONTHS[position % 12]
    if time_grain == "week":
        return f"week {position % period + 1} of {period}"
    return f"position {position % period + 1} of {period}"


def summarise_seasonality(
    decomposition: DecompositionResult,
    periods: pd.Series,
    seasonality_floor: float,
    time_grain: str,
    fitted_period: int,
) -> SeasonalitySummary:
    """Describe the seasonal component STL already fitted.

    `fitted_period` is the period STL was actually given -- reporting anything
    else would describe a cycle the decomposition never modelled.
    """
    strength = float(decomposition.seasonal_strength)

    if not decomposition.applied:
        return SeasonalitySummary(
            detected=False,
            period=None,
            strength=strength,
            reason=decomposition.reason or "decomposition not applied",
        )

    if strength < seasonality_floor:
        return SeasonalitySummary(
            detected=False,
            period=None,
            strength=strength,
            reason=(
                f"seasonal strength {strength:.2f} is below the {seasonality_floor:.2f} floor; "
                "the repeating component is not distinguishable from noise"
            ),
        )

    seasonal = decomposition.seasonal.dropna()
    if seasonal.empty:
        return SeasonalitySummary(
            detected=False, period=None, strength=strength, reason="empty seasonal component"
        )

    # One full cycle: the seasonal component repeats, so the first `period`
    # entries carry the whole shape.
    period = fitted_period
    cycle = seasonal.to_numpy(dtype="float64")[:period]
    peak_pos = int(np.argmax(cycle))
    trough_pos = int(np.argmin(cycle))

    # Anchor labels to the calendar position the cycle actually starts on.
    offset = _calendar_offset(periods, time_grain)

    return SeasonalitySummary(
        detected=True,
        period=period,
        strength=strength,
        peak_label=_label(peak_pos + offset, period, time_grain),
        trough_label=_label(trough_pos + offset, period, time_grain),
        reason=f"seasonal strength {strength:.2f} clears the {seasonality_floor:.2f} floor",
    )


def _calendar_offset(periods: pd.Series, time_grain: str) -> int:
    """Where in the calendar cycle the series begins, so labels are not shifted."""
    if periods.empty:
        return 0
    first = pd.Timestamp(periods.iloc[0])
    if time_grain == "day":
        return int(first.dayofweek)
    if time_grain == "month":
        return int(first.month - 1)
    return 0
