"""Interrupted time series.

The fallback when an event hits everything at once and no untreated slice
survives to act as a control. The counterfactual then has to come from the
series' own pre-period behaviour, projected forward.

That is a strictly weaker design than DiD: anything else that changed at the
same moment is absorbed into the estimate. The result therefore carries a wider
interval and the router records that it settled for ITS because controls were
unavailable, not because ITS was preferable.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats


@dataclass
class ItsResult:
    level_change: float | None
    slope_change: float | None
    std_error: float | None
    ci_low: float | None
    ci_high: float | None
    p_value: float | None
    counterfactual_mean: float | None
    observed_mean: float | None
    n_pre: int
    n_post: int
    r_squared: float | None
    valid: bool = False
    reason: str = ""


def estimate_its(
    panel: pd.DataFrame,
    kpi: str,
    entity: dict[str, str],
    window: tuple[dt.date, dt.date],
    pre_periods: int = 120,
    min_pre: int = 20,
) -> ItsResult:
    """Segmented regression: level and slope shift at the intervention point.

        y_t = b0 + b1*t + b2*after_t + b3*(t - t0)*after_t

    b2 is the immediate level change and b3 the change in trajectory.
    """
    df = panel[panel["kpi"] == kpi].copy()
    for key, val in entity.items():
        df = df[df[key] == val]
    df["period"] = pd.to_datetime(df["period"])

    start, end = pd.Timestamp(window[0]), pd.Timestamp(window[1])
    pre_start = start - pd.Timedelta(days=pre_periods)
    df = df[(df["period"] >= pre_start) & (df["period"] <= end)].dropna(subset=["value"])
    df = df.sort_values("period").reset_index(drop=True)

    if df.empty:
        return ItsResult(None, None, None, None, None, None, None, None, 0, 0, None, False,
                         "no observations in the analysis span")

    t = np.arange(len(df), dtype="float64")
    after = (df["period"] >= start).to_numpy().astype("float64")
    n_pre, n_post = int((1 - after).sum()), int(after.sum())
    if n_pre < min_pre or n_post < 3:
        return ItsResult(None, None, None, None, None, None, None, None, n_pre, n_post, None,
                         False, f"need >= {min_pre} pre and >= 3 post periods, have {n_pre}/{n_post}")

    t0 = float(t[after == 1][0])
    X = np.column_stack([np.ones_like(t), t, after, (t - t0) * after])
    y = df["value"].to_numpy(dtype="float64")

    coefs, residuals, rank, _ = np.linalg.lstsq(X, y, rcond=None)
    if rank < X.shape[1]:
        return ItsResult(None, None, None, None, None, None, None, None, n_pre, n_post, None,
                         False, "design matrix is rank deficient")

    fitted = X @ coefs
    resid = y - fitted
    dof = len(y) - X.shape[1]
    sigma2 = float(resid @ resid / dof)
    cov = sigma2 * np.linalg.pinv(X.T @ X)
    se_level = float(np.sqrt(cov[2, 2]))
    crit = float(stats.t.ppf(0.975, dof))
    ss_tot = float(((y - y.mean()) ** 2).sum())

    # Counterfactual: pre-period trend extended through the window.
    counterfactual = coefs[0] + coefs[1] * t
    observed_mean = float(y[after == 1].mean())
    cf_mean = float(counterfactual[after == 1].mean())

    return ItsResult(
        level_change=float(coefs[2]),
        slope_change=float(coefs[3]),
        std_error=se_level,
        ci_low=float(coefs[2] - crit * se_level),
        ci_high=float(coefs[2] + crit * se_level),
        p_value=float(2 * (1 - stats.t.cdf(abs(coefs[2] / se_level), dof))) if se_level > 0 else None,
        counterfactual_mean=cf_mean,
        observed_mean=observed_mean,
        n_pre=n_pre,
        n_post=n_post,
        r_squared=float(1 - (resid @ resid) / ss_tot) if ss_tot > 0 else None,
        valid=True,
        reason="ok",
    )
