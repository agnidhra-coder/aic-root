"""Point anomalies: single periods whose value the baseline could not have predicted.

Scored with the modified z-score (median / MAD) rather than a plain z-score,
because a large anomaly inflates the standard deviation of the very series it
sits in and thereby raises the threshold it must clear -- the classic way an
outlier hides itself.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from kpi_engine.contracts.configs import PointSpec
from kpi_engine.contracts.payloads import Flag
from kpi_engine.detection.baseline import robust_scale


def detect_point_anomalies(
    periods: pd.Series,
    observed: pd.Series,
    expected: pd.Series,
    residual: pd.Series,
    support: pd.Series,
    kpi: str,
    entity: dict[str, str],
    spec: PointSpec,
) -> list[Flag]:
    if not spec.enabled:
        return []

    median, sigma = robust_scale(residual, spec.mad_floor)
    z = (residual - median) / sigma
    hits = z.abs() > spec.z_threshold

    flags: list[Flag] = []
    for pos in np.flatnonzero(hits.fillna(False).to_numpy()):
        r = float(residual.iloc[pos])
        flags.append(
            Flag(
                kpi=kpi,
                entity=entity,
                period=_as_date(periods.iloc[pos]),
                detector="point",
                anomaly_type="Point Deviation",
                observed=_f(observed.iloc[pos]),
                expected=_f(expected.iloc[pos]),
                residual=r,
                score=float(abs(z.iloc[pos])),
                threshold=spec.z_threshold,
                support=int(support.iloc[pos]) if pd.notna(support.iloc[pos]) else 0,
                direction="up" if r > 0 else "down",
            )
        )
    return flags


def _f(value: object) -> float | None:
    v = float(value) if value is not None else float("nan")
    return None if not np.isfinite(v) else v


def _as_date(value: object) -> dt.date:
    return pd.Timestamp(value).date()
