"""Drives every detector across every KPI and entity slice in a panel."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from kpi_engine.contracts.configs import DetectionSpec, KpiContract
from kpi_engine.contracts.payloads import Flag
from kpi_engine.detection.baseline import fit_expanding_baseline
from kpi_engine.detection.changepoint import detect_changepoints
from kpi_engine.detection.decompose import decompose_series
from kpi_engine.detection.multivariate import detect_multivariate
from kpi_engine.detection.point import detect_point_anomalies
from kpi_engine.semantics.panel import PERIOD_COL, SUPPORT_COL, KpiPanel


@dataclass
class SeriesDiagnostics:
    kpi: str
    entity: dict[str, str]
    n_periods: int
    n_valid: int
    seasonal_strength: float
    trend_strength: float
    decomposition_applied: bool
    decomposition_reason: str
    baseline_model: str
    baseline_coverage: float
    skipped: str | None = None


@dataclass
class DetectionOutput:
    flags: list[Flag] = field(default_factory=list)
    diagnostics: list[SeriesDiagnostics] = field(default_factory=list)
    residuals: dict[str, pd.DataFrame] = field(default_factory=dict)


def _regular_index(df: pd.DataFrame, grain: str) -> pd.DataFrame:
    """Reindex onto a gap-free period axis.

    Detectors that use lags need position to mean elapsed time. With missing
    periods left out, `lag_7` would silently reach back an unpredictable distance.
    """
    freq = {"day": "D", "week": "W-MON", "month": "MS"}[grain]
    idx = pd.date_range(df[PERIOD_COL].min(), df[PERIOD_COL].max(), freq=freq)
    out = df.set_index(PERIOD_COL).reindex(idx)
    out.index.name = PERIOD_COL
    return out.reset_index()


def run_detection(panel: KpiPanel, spec: DetectionSpec, contract: KpiContract) -> DetectionOutput:
    """Run point, change-point and multivariate detection over the whole panel."""
    out = DetectionOutput()

    for entity in panel.entities():
        residual_frame: dict[str, pd.Series] = {}
        axis: pd.Series | None = None
        support_axis: pd.Series | None = None

        for kpi_name in panel.kpi_names():
            series_df = panel.series(kpi_name, entity)
            if series_df.empty:
                continue
            regular = _regular_index(series_df, contract.time_grain)
            periods = regular[PERIOD_COL]
            values = regular["value"].astype("float64")
            support = regular[SUPPORT_COL].fillna(0)
            guards = contract.kpi(kpi_name).guards

            n_valid = int(values.notna().sum())
            if n_valid < guards.min_history_periods:
                out.diagnostics.append(
                    SeriesDiagnostics(
                        kpi=kpi_name, entity=entity, n_periods=len(regular), n_valid=n_valid,
                        seasonal_strength=0.0, trend_strength=0.0,
                        decomposition_applied=False, decomposition_reason="n/a",
                        baseline_model="n/a", baseline_coverage=0.0,
                        skipped=f"only {n_valid} valid periods, contract requires "
                                f"{guards.min_history_periods}",
                    )
                )
                continue

            decomp = decompose_series(values, spec.decomposition)
            # Detect on the deseasonalised signal so a weekly cycle is not an incident.
            signal = decomp.deseasonalised if decomp.applied else values
            baseline = fit_expanding_baseline(signal, spec.baseline, spec.decomposition.period)

            out.flags += detect_point_anomalies(
                periods, values, baseline.expected, baseline.residual, support,
                kpi_name, entity, spec.point,
            )
            out.flags += detect_changepoints(
                periods, values, signal, support, kpi_name, entity, spec.changepoint,
            )

            residual_frame[kpi_name] = baseline.residual
            axis, support_axis = periods, support
            out.diagnostics.append(
                SeriesDiagnostics(
                    kpi=kpi_name, entity=entity, n_periods=len(regular), n_valid=n_valid,
                    seasonal_strength=round(decomp.seasonal_strength, 4),
                    trend_strength=round(decomp.trend_strength, 4),
                    decomposition_applied=decomp.applied,
                    decomposition_reason=decomp.reason,
                    baseline_model=baseline.model,
                    baseline_coverage=round(baseline.coverage, 4),
                )
            )

        if residual_frame and axis is not None and support_axis is not None:
            residuals = pd.DataFrame(residual_frame)
            out.flags += detect_multivariate(
                residuals, axis, support_axis, entity, spec.multivariate
            )
            key = "|".join(f"{k}={v}" for k, v in sorted(entity.items())) or "TOTAL"
            out.residuals[key] = residuals.assign(**{PERIOD_COL: axis})

    return out
