"""Double / debiased machine learning for the upstream mechanisms.

Used where the algebra runs out. The KPI's own arithmetic is exact, but "what
moved New Customers" is a behavioural question with confounders, and that needs
estimation.

The Frisch-Waugh-Lovell construction: regress the outcome on the confounders,
regress the treatment on the confounders, then relate the two sets of residuals.
This partials out confounding without asking a single linear model to get both
the nuisance relationships and the effect right at once.

Cross-fitting matters. Nuisance predictions come from folds that excluded the
row being predicted, so flexible learners cannot overfit the nuisance functions
and bias the effect toward zero -- the failure that makes naive
regress-everything-together estimates unreliable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import RidgeCV
from sklearn.model_selection import KFold
from sklearn.preprocessing import StandardScaler


@dataclass
class DmlResult:
    treatment: str
    outcome: str
    effect: float | None
    std_error: float | None
    ci_low: float | None
    ci_high: float | None
    p_value: float | None
    n_obs: int
    confounders: list[str] = field(default_factory=list)
    partial_r2: float | None = None
    valid: bool = False
    reason: str = ""


def _cross_fitted_residuals(
    X: np.ndarray, y: np.ndarray, n_folds: int, seed: int = 0
) -> np.ndarray:
    """Residualise y on X using out-of-fold predictions."""
    resid = np.empty_like(y, dtype="float64")
    folds = KFold(n_splits=n_folds, shuffle=True, random_state=seed)
    for train_idx, test_idx in folds.split(X):
        model = RidgeCV(alphas=np.logspace(-3, 3, 13))
        model.fit(X[train_idx], y[train_idx])
        resid[test_idx] = y[test_idx] - model.predict(X[test_idx])
    return resid


def estimate_dml(
    df: pd.DataFrame,
    outcome: str,
    treatment: str,
    confounders: list[str],
    n_folds: int = 5,
    min_obs: int = 60,
    seed: int = 0,
) -> DmlResult:
    """Partial-out estimate of `treatment` -> `outcome`, controlling for `confounders`.

    Callers must have already removed columns that the profiler found to be exact
    functions of others; perfectly collinear controls make the partialling step
    arbitrary and the resulting effect meaningless.
    """
    cols = [outcome, treatment, *confounders]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        return DmlResult(treatment, outcome, None, None, None, None, None, 0, confounders,
                         None, False, f"columns absent from the panel: {missing}")

    work = df[cols].replace([np.inf, -np.inf], np.nan).dropna()
    n = len(work)
    if n < min_obs:
        return DmlResult(treatment, outcome, None, None, None, None, None, n, confounders,
                         None, False, f"{n} usable rows, need {min_obs}")

    y = work[outcome].to_numpy(dtype="float64")
    t = work[treatment].to_numpy(dtype="float64")

    if confounders:
        X = StandardScaler().fit_transform(work[confounders].to_numpy(dtype="float64"))
        # Near-constant or duplicated controls destabilise the partialling step.
        keep = X.std(axis=0) > 1e-10
        X = X[:, keep]
        used = [c for c, k in zip(confounders, keep) if k]
    else:
        X = np.zeros((n, 1))
        used = []

    if X.shape[1] == 0:
        y_res, t_res = y - y.mean(), t - t.mean()
    else:
        folds = min(n_folds, max(2, n // 20))
        y_res = _cross_fitted_residuals(X, y, folds, seed)
        t_res = _cross_fitted_residuals(X, t, folds, seed)

    # Identification check. An absolute floor is not enough: with regularised
    # nuisance models a treatment that is an exact function of a control still
    # leaves a sliver of numerical residue, and dividing by it yields a huge
    # estimate with a huge interval rather than an honest refusal. What matters is
    # how much of the treatment's variation *survives* partialling out.
    denom = float(t_res @ t_res)
    total_variation = float(((t - t.mean()) ** 2).sum())
    retained = denom / total_variation if total_variation > 0 else 0.0
    if denom <= 1e-12 or retained < 0.01:
        return DmlResult(
            treatment, outcome, None, None, None, None, None, n, used, None, False,
            f"only {retained:.2%} of the treatment's variation survives partialling out the "
            f"controls; the effect is not identified and any estimate would be an artefact",
        )

    effect = float(t_res @ y_res / denom)
    resid = y_res - effect * t_res
    dof = max(n - X.shape[1] - 1, 1)
    sigma2 = float(resid @ resid / dof)
    se = float(np.sqrt(sigma2 / denom))
    crit = float(stats.t.ppf(0.975, dof))
    ss_tot = float(y_res @ y_res)

    return DmlResult(
        treatment=treatment,
        outcome=outcome,
        effect=effect,
        std_error=se,
        ci_low=effect - crit * se,
        ci_high=effect + crit * se,
        p_value=float(2 * (1 - stats.t.cdf(abs(effect / se), dof))) if se > 0 else None,
        n_obs=n,
        confounders=used,
        partial_r2=float(1 - (resid @ resid) / ss_tot) if ss_tot > 0 else None,
        valid=True,
        reason="ok",
    )
