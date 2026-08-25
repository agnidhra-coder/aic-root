"""Assembles the evidence bundle: the one object a narration layer may consume.

Everything a narrator could need must be a field here, computed by deterministic
code or a named estimator. Nothing downstream should ever reach back into the
raw data, because the moment it does, the guarantee that the narrative is
grounded in audited numbers is gone.
"""

from __future__ import annotations

import datetime as dt

from kpi_engine.contracts.payloads import (
    Attribution,
    ConfidenceBreakdown,
    EvidenceBundle,
    EventWindow,
    Freshness,
)
from kpi_engine.evidence.abstention import check_abstention


def _methods_used(attributions: list[Attribution]) -> list[str]:
    methods = {"restricted-AST KPI evaluation (ratio-of-sums)", "STL decomposition",
               "expanding-window ridge baseline", "modified z-score (MAD)",
               "PELT / CUSUM change-point", "Mahalanobis cross-KPI distance"}
    for attribution in attributions:
        for contribution in attribution.contributions:
            if contribution.method == "algebraic_lmdi":
                methods.add("exact Shapley decomposition over KPI measures")
            elif contribution.method == "did":
                methods.add("difference-in-differences with parallel-trends test")
            elif contribution.method == "its":
                methods.add("interrupted time series (segmented regression)")
            elif contribution.method == "dml":
                methods.add("cross-fitted double machine learning")
    return sorted(methods)


def build_evidence_bundle(
    event: EventWindow,
    attributions: list[Attribution],
    confidence: ConfidenceBreakdown,
    freshness: Freshness,
    threshold: float,
    n_pre_periods: int,
    min_support: int = 3,
    min_history: int = 28,
) -> EvidenceBundle:
    abstention = check_abstention(
        event, attributions, confidence, threshold, min_support, min_history, n_pre_periods
    )
    return EvidenceBundle(
        event_id=event.event_id,
        generated_at=dt.datetime.now(),
        event=event,
        attributions=attributions,
        confidence=confidence,
        abstained=abstention is not None,
        abstention=abstention,
        freshness=freshness,
        lineage=event.lineage,
        deterministic_methods=_methods_used(attributions),
        llm_methods=[],  # nothing here is produced by a language model
    )
