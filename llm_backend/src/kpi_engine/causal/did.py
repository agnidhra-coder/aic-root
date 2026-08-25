"""Difference-in-differences.

Applies when an event hits some slices and leaves comparable ones alone. The
untreated slices absorb whatever else was happening that period, so the
treated-minus-control difference isolates the event.

The parallel-trends assumption is the whole basis of the estimate, so it is
tested rather than asserted: if treated and control were already diverging
before the event, the estimator attributes a pre-existing drift to it. A failed
test returns `valid=False`, and the router treats that as grounds to abstain
rather than quietly reporting a number it cannot support.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats


@dataclass
class DidResult:
    effect: float | None
    std_error: float | None
    ci_low: float | None
    ci_high: float | None
    p_value: float | None
    n_treated_pre: int
    n_treated_post: int
    n_control_pre: int
    n_control_post: int
    parallel_trends_p: float | None
    parallel_trends_ok: bool
    control_slices: list[str] = field(default_factory=list)
    valid: bool = False
    reason: str = ""


def _mean_sem(values: pd.Series) -> tuple[float, float, int]:
    clean = values.dropna()
    n = len(clean)
    if n == 0:
        return float("nan"), float("nan"), 0
    sem = clean.std(ddof=1) / np.sqrt(n) if n > 1 else float("nan")
    return float(clean.mean()), float(sem), n


def test_parallel_trends(
    treated: pd.DataFrame, control: pd.DataFrame, value_col: str = "value", alpha: float = 0.05
) -> tuple[float | None, bool]:
    """Regress the treated-minus-control gap on time over the pre-period.

    A slope indistinguishable from zero supports parallel trends. Reported as a
    p-value so the decision threshold stays visible rather than buried.
    """
    gap = (
        treated.set_index("period")[value_col]
        .subtract(control.set_index("period")[value_col], fill_value=np.nan)
        .dropna()
    )
    if len(gap) < 8:
        return None, False
    values = gap.to_numpy()
    # A gap with no variance at all is perfectly parallel. linregress returns a NaN
    # p-value there (it cannot compute a t-statistic with zero residual variance),
    # and NaN > alpha is False, so the ideal case would otherwise be rejected.
    if float(np.nanstd(values)) < 1e-12:
        return 1.0, True
    t = np.arange(len(values), dtype="float64")
    result = stats.linregress(t, values)
    if not np.isfinite(result.pvalue):
        return None, False
    return float(result.pvalue), bool(result.pvalue > alpha)


def estimate_did(
    panel: pd.DataFrame,
    kpi: str,
    entity_key: str,
    treated_value: str,
    window: tuple[dt.date, dt.date],
    pre_periods: int = 84,
    min_controls: int = 2,
) -> DidResult:
    """Estimate the event effect on `kpi` for the treated slice against its peers."""
    df = panel[panel["kpi"] == kpi].copy()
    df["period"] = pd.to_datetime(df["period"])
    start, end = pd.Timestamp(window[0]), pd.Timestamp(window[1])
    pre_start = start - pd.Timedelta(days=pre_periods)

    controls = sorted(set(df[entity_key].unique()) - {treated_value})
    if len(controls) < min_controls:
        return DidResult(
            None, None, None, None, None, 0, 0, 0, 0, None, False, controls, False,
            f"only {len(controls)} control slices available, need {min_controls}",
        )

    treated = df[df[entity_key] == treated_value]
    control = df[df[entity_key].isin(controls)].groupby("period", as_index=False)["value"].mean()

    t_pre = treated[(treated["period"] >= pre_start) & (treated["period"] < start)]
    t_post = treated[(treated["period"] >= start) & (treated["period"] <= end)]
    c_pre = control[(control["period"] >= pre_start) & (control["period"] < start)]
    c_post = control[(control["period"] >= start) & (control["period"] <= end)]

    tp_m, tp_se, tp_n = _mean_sem(t_pre["value"])
    to_m, to_se, to_n = _mean_sem(t_post["value"])
    cp_m, cp_se, cp_n = _mean_sem(c_pre["value"])
    co_m, co_se, co_n = _mean_sem(c_post["value"])

    if min(tp_n, to_n, cp_n, co_n) < 3:
        return DidResult(
            None, None, None, None, None, tp_n, to_n, cp_n, co_n, None, False, controls, False,
            "fewer than 3 periods in one of the four DiD cells",
        )

    effect = (to_m - tp_m) - (co_m - cp_m)
    se = float(np.sqrt(np.nansum(np.square([tp_se, to_se, cp_se, co_se]))))
    dof = max(tp_n + to_n + cp_n + co_n - 4, 1)
    crit = float(stats.t.ppf(0.975, dof))
    p_value = float(2 * (1 - stats.t.cdf(abs(effect / se), dof))) if se > 0 else None

    pt_p, pt_ok = test_parallel_trends(
        t_pre[["period", "value"]], c_pre[["period", "value"]]
    )

    return DidResult(
        effect=float(effect),
        std_error=se,
        ci_low=float(effect - crit * se),
        ci_high=float(effect + crit * se),
        p_value=p_value,
        n_treated_pre=tp_n,
        n_treated_post=to_n,
        n_control_pre=cp_n,
        n_control_post=co_n,
        parallel_trends_p=pt_p,
        parallel_trends_ok=pt_ok,
        control_slices=controls,
        valid=bool(pt_ok),
        reason="ok" if pt_ok else (
            "parallel pre-trends rejected: treated and control were already diverging, "
            "so the difference cannot be attributed to the event"
            if pt_p is not None else "insufficient pre-period overlap to test parallel trends"
        ),
    )
