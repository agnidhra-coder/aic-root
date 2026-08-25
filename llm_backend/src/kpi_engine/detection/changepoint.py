"""Structural breaks: the level or variance of the series shifts and stays shifted.

This is the modality that matters most for the kind of event being simulated. A
21-day cost shock moves each individual day by only about two sigma -- too
little for point detection to call reliably -- while moving the *segment mean*
by many standard errors. Point detectors ask "is today surprising"; these ask
"has the regime changed", and only the second question has a good answer here.

PELT searches all segmentations under a penalty; CUSUM accumulates small
persistent deviations until they breach a decision interval. They are run
together because they fail differently: PELT needs enough points either side of
the break, CUSUM detects with a lag but keeps working near series boundaries.
"""

from __future__ import annotations


import numpy as np
import pandas as pd
import ruptures as rpt

from kpi_engine.contracts.configs import ChangePointSpec
from kpi_engine.contracts.payloads import Flag
from kpi_engine.detection.baseline import robust_scale


def pelt_breaks(
    signal: np.ndarray, penalty: float, min_segment: int, model: str = "rbf"
) -> list[int]:
    """Change-point indices via PELT.

    Takes plain parameters rather than a ChangePointSpec so the descriptive EDA
    stage can segment a series without having to construct a detection config it
    does not otherwise use.
    """
    if len(signal) < 2 * min_segment:
        return []
    algo = rpt.Pelt(model=model, min_size=min_segment).fit(signal.reshape(-1, 1))
    try:
        breaks = algo.predict(pen=penalty)
    except Exception:  # noqa: BLE001 - ruptures raises on degenerate signals
        return []
    return [b for b in breaks if 0 < b < len(signal)]


def _cusum_breaks(signal: np.ndarray, spec: ChangePointSpec) -> list[tuple[int, float, str]]:
    """Two-sided CUSUM in robust sigma units; returns (index, statistic, direction)."""
    series = pd.Series(signal)
    median, sigma = robust_scale(series)
    if sigma <= 0:
        return []
    standardised = (series - median) / sigma

    pos = neg = 0.0
    hits: list[tuple[int, float, str]] = []
    for i, x in enumerate(standardised):
        if not np.isfinite(x):
            continue
        pos = max(0.0, pos + x - spec.cusum_k)
        neg = max(0.0, neg - x - spec.cusum_k)
        if pos > spec.cusum_h:
            hits.append((i, pos, "up"))
            pos = 0.0  # reset so one long shift is not reported every period
        elif neg > spec.cusum_h:
            hits.append((i, neg, "down"))
            neg = 0.0
    return hits


def detect_changepoints(
    periods: pd.Series,
    observed: pd.Series,
    signal: pd.Series,
    support: pd.Series,
    kpi: str,
    entity: dict[str, str],
    spec: ChangePointSpec,
) -> list[Flag]:
    """Run the configured change-point methods over one series."""
    if not spec.enabled:
        return []

    filled = signal.astype("float64").interpolate(limit_direction="both")
    if filled.notna().sum() < 2 * spec.min_segment:
        return []
    arr = filled.to_numpy()
    flags: list[Flag] = []

    if spec.method in ("pelt", "both"):
        for idx in pelt_breaks(arr, spec.pelt_penalty, spec.min_segment, spec.pelt_model):
            before = arr[max(0, idx - spec.min_segment) : idx]
            after = arr[idx : idx + spec.min_segment]
            if len(before) == 0 or len(after) == 0:
                continue
            shift = float(after.mean() - before.mean())
            _, sigma = robust_scale(pd.Series(arr))
            flags.append(
                _flag(
                    kpi, entity, periods, observed, support, idx,
                    detector="changepoint_pelt",
                    score=abs(shift) / sigma if sigma else 0.0,
                    threshold=spec.pelt_penalty,
                    residual=shift,
                    direction="up" if shift > 0 else "down",
                )
            )

    if spec.method in ("cusum", "both"):
        for idx, stat, direction in _cusum_breaks(arr, spec):
            flags.append(
                _flag(
                    kpi, entity, periods, observed, support, idx,
                    detector="changepoint_cusum",
                    score=float(stat),
                    threshold=spec.cusum_h,
                    residual=None,
                    direction=direction,
                )
            )
    return flags


def _flag(
    kpi: str,
    entity: dict[str, str],
    periods: pd.Series,
    observed: pd.Series,
    support: pd.Series,
    idx: int,
    *,
    detector: str,
    score: float,
    threshold: float,
    residual: float | None,
    direction: str,
) -> Flag:
    value = observed.iloc[idx]
    sup = support.iloc[idx]
    return Flag(
        kpi=kpi,
        entity=entity,
        period=pd.Timestamp(periods.iloc[idx]).date(),
        detector=detector,  # type: ignore[arg-type]
        anomaly_type="Structural Break",
        observed=float(value) if pd.notna(value) else None,
        expected=None,
        residual=residual,
        score=float(score),
        threshold=float(threshold),
        support=int(sup) if pd.notna(sup) else 0,
        direction=direction,  # type: ignore[arg-type]
    )
