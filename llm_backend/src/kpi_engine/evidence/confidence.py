"""Confidence scoring.

A number without a stated basis is not evidence, so the score is a published
formula over four observable factors rather than a learned or hand-tuned
opinion. Each factor is in [0, 1] and the score is their weighted geometric
mean, which means a single collapsed factor drags the result down instead of
being averaged away by three healthy ones -- an estimate resting on eight rows
should not be rescued by a tidy standard error.
"""

from __future__ import annotations

import numpy as np

from kpi_engine.contracts.payloads import (
    Attribution,
    ConfidenceBreakdown,
    EventWindow,
)

FORMULA = (
    "confidence = weighted_geometric_mean("
    "signal_to_noise^0.30, support^0.25, history^0.20, "
    "estimator_precision^0.15, method_agreement^0.10)"
)
_WEIGHTS = {
    "signal_to_noise": 0.30,
    "support": 0.25,
    "history": 0.20,
    "estimator_precision": 0.15,
    "method_agreement": 0.10,
}


def _saturate(value: float, scale: float) -> float:
    """Map [0, inf) to [0, 1), reaching ~0.5 at `scale`. Diminishing returns by design."""
    if not np.isfinite(value) or value <= 0:
        return 0.0
    return float(value / (value + scale))


def _signal_to_noise(event: EventWindow) -> float:
    """How far the detector score sits above its own threshold."""
    if event.peak_score <= 0 or not np.isfinite(event.peak_score):
        return 0.0
    return _saturate(event.peak_score, 6.0)


def _support(event: EventWindow, min_required: int = 5) -> float:
    """Thin cells produce unstable ratios however clean the statistics look."""
    return _saturate(float(event.min_support), float(min_required))


def _history(n_pre_periods: int, required: int = 28) -> float:
    if n_pre_periods <= 0:
        return 0.0
    return float(min(1.0, n_pre_periods / (2.0 * required)))


def _estimator_precision(attributions: list[Attribution]) -> float | None:
    """Ratio of effect to its standard error, across estimated contributions only.

    Exact algebraic contributions have no standard error and are excluded rather
    than scored as perfect, which would let Layer A mask a shaky Layer B.
    """
    ratios = [
        abs(c.contribution) / c.std_error
        for a in attributions
        for c in a.contributions
        if not c.exact and c.std_error and c.std_error > 0 and np.isfinite(c.contribution)
    ]
    if not ratios:
        return None
    return _saturate(float(np.median(ratios)), 2.0)


def _method_agreement(attributions: list[Attribution]) -> float | None:
    """Do the exact split and the quasi-experimental estimate point the same way?

    Disagreement in sign is a genuine warning: the algebra says the KPI moved one
    way while the controlled comparison says the event pushed it the other, which
    usually means the movement was not caused by the event at all.
    """
    scores: list[float] = []
    for attribution in attributions:
        exact = [c for c in attribution.contributions if c.exact]
        estimated = [c for c in attribution.contributions if c.method in ("did", "its")]
        if not exact or not estimated:
            continue
        total = attribution.total_delta
        if total is None or not np.isfinite(total) or total == 0:
            continue
        for est in estimated:
            if not np.isfinite(est.contribution):
                continue
            same_sign = np.sign(est.contribution) == np.sign(total)
            magnitude = min(abs(est.contribution / total), 1.0)
            scores.append(magnitude if same_sign else 0.0)
    if not scores:
        return None
    return float(np.mean(scores))


def score_confidence(
    event: EventWindow,
    attributions: list[Attribution],
    n_pre_periods: int,
    min_support_required: int = 5,
) -> ConfidenceBreakdown:
    """Combine the factors into one auditable score."""
    factors: dict[str, float | None] = {
        "signal_to_noise": _signal_to_noise(event),
        "support": _support(event, min_support_required),
        "history": _history(n_pre_periods),
        "estimator_precision": _estimator_precision(attributions),
        "method_agreement": _method_agreement(attributions),
    }

    # Renormalise over the factors that are actually measurable for this event,
    # so an absent factor neither helps nor silently penalises.
    present = {k: v for k, v in factors.items() if v is not None}
    total_weight = sum(_WEIGHTS[k] for k in present)
    if total_weight <= 0:
        score = 0.0
    else:
        log_sum = sum(
            _WEIGHTS[k] * np.log(max(v, 1e-6)) for k, v in present.items()
        )
        score = float(np.exp(log_sum / total_weight))

    return ConfidenceBreakdown(
        score=round(min(max(score, 0.0), 1.0), 4),
        signal_to_noise=_round(factors["signal_to_noise"]),
        support_factor=_round(factors["support"]),
        history_factor=_round(factors["history"]),
        estimator_precision=_round(factors["estimator_precision"]),
        method_agreement=_round(factors["method_agreement"]),
        formula=FORMULA,
    )


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 4)
