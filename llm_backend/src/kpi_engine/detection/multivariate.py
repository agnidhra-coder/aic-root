"""Cross-KPI anomalies: each KPI looks normal alone, their combination does not.

The case this exists for: ad revenue holds steady, ad cost climbs, acquisitions
slip. No single series leaves its usual range, but that *joint* configuration
has never occurred. Mahalanobis distance measures deviation in units of the
historical covariance, so it sees the broken correlation that per-series
thresholds cannot.

The covariance is estimated on an expanding window and shrunk toward its
diagonal; with five KPIs and short history a raw sample covariance is easily
near-singular, and inverting it turns noise into enormous distances.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import chi2

from kpi_engine.contracts.configs import MultivariateSpec
from kpi_engine.contracts.payloads import Flag


def _shrink(cov: np.ndarray, intensity: float) -> np.ndarray:
    """Ledoit-Wolf style shrinkage toward a scaled identity."""
    k = cov.shape[0]
    mu = np.trace(cov) / k
    return (1.0 - intensity) * cov + intensity * mu * np.eye(k)


def detect_multivariate(
    residuals: pd.DataFrame,
    periods: pd.Series,
    support: pd.Series,
    entity: dict[str, str],
    spec: MultivariateSpec,
) -> list[Flag]:
    """Flag periods whose joint residual vector is extreme under a chi-square cutoff.

    `residuals` is periods x KPIs. Standardised per column first so a KPI measured
    in currency does not dominate one measured as a ratio.
    """
    if not spec.enabled or residuals.shape[1] < 2:
        return []

    usable = residuals.dropna()
    if len(usable) < spec.min_train_periods:
        return []

    k = residuals.shape[1]
    cutoff = float(chi2.ppf(1.0 - spec.alpha, df=k))
    scaled = _standardise(residuals)

    flags: list[Flag] = []
    values = scaled.to_numpy()
    valid = ~np.isnan(values).any(axis=1)

    for i in range(len(scaled)):
        if not valid[i]:
            continue
        # Expanding window: covariance from strictly earlier periods only.
        history = values[:i][valid[:i]]
        if len(history) < spec.min_train_periods:
            continue
        mu = history.mean(axis=0)
        cov = _shrink(np.cov(history, rowvar=False), spec.shrinkage)
        try:
            inv = np.linalg.pinv(cov)
        except np.linalg.LinAlgError:
            continue
        delta = values[i] - mu
        d2 = float(delta @ inv @ delta)
        if d2 > cutoff:
            contributors = _top_contributors(delta, inv, list(scaled.columns))
            flags.append(
                Flag(
                    kpi="+".join(contributors),
                    entity=entity,
                    period=pd.Timestamp(periods.iloc[i]).date(),
                    detector="multivariate",
                    anomaly_type="Multivariate Divergence",
                    observed=None,
                    expected=None,
                    residual=None,
                    score=float(np.sqrt(d2)),
                    threshold=float(np.sqrt(cutoff)),
                    support=int(support.iloc[i]) if pd.notna(support.iloc[i]) else 0,
                    direction="unknown",
                )
            )
    return flags


def _standardise(residuals: pd.DataFrame) -> pd.DataFrame:
    """Robust per-column standardisation, so units do not decide importance."""
    out = residuals.astype("float64").copy()
    for col in out.columns:
        s = out[col]
        median = s.median()
        mad = (s - median).abs().median()
        scale = 1.4826 * mad if mad and np.isfinite(mad) and mad > 0 else s.std()
        out[col] = (s - median) / (scale if scale and np.isfinite(scale) and scale > 0 else 1.0)
    return out


def _top_contributors(delta: np.ndarray, inv: np.ndarray, names: list[str], top: int = 2) -> list[str]:
    """Which KPIs carry the distance, so the flag names something interpretable."""
    per_term = delta * (inv @ delta)
    order = np.argsort(-np.abs(per_term))
    return [names[i] for i in order[:top]]
