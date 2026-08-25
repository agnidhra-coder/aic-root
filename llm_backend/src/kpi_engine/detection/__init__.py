"""Anomaly detection: decomposition, baselines, three detector modalities, windowing."""

from kpi_engine.detection.baseline import BaselineResult, fit_expanding_baseline
from kpi_engine.detection.changepoint import detect_changepoints
from kpi_engine.detection.decompose import DecompositionResult, decompose_series
from kpi_engine.detection.multivariate import detect_multivariate
from kpi_engine.detection.point import detect_point_anomalies
from kpi_engine.detection.windowing import build_event_windows

__all__ = [
    "BaselineResult",
    "fit_expanding_baseline",
    "detect_changepoints",
    "DecompositionResult",
    "decompose_series",
    "detect_multivariate",
    "detect_point_anomalies",
    "build_event_windows",
]
