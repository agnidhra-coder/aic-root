"""Deciding when to say nothing.

An engine that always produces an explanation is not trustworthy, because some
movements genuinely cannot be explained from the available evidence. Abstention
is a first-class output: it names the missing evidence and what would resolve
it, so the analyst gets a next step instead of a confident guess.
"""

from __future__ import annotations

import numpy as np

from kpi_engine.contracts.payloads import (
    AbstainPayload,
    Attribution,
    ConfidenceBreakdown,
    EventWindow,
)


def check_abstention(
    event: EventWindow,
    attributions: list[Attribution],
    confidence: ConfidenceBreakdown,
    threshold: float,
    min_support: int = 3,
    min_history: int = 28,
    n_pre_periods: int = 0,
) -> AbstainPayload | None:
    """Return an abstention payload if the evidence will not support a claim."""
    kpi = event.primary_kpis_affected[0] if event.primary_kpis_affected else "unknown"

    if event.min_support < min_support:
        return AbstainPayload(
            event_id=event.event_id,
            kpi=kpi,
            reason_code="insufficient_support",
            message=(
                f"The thinnest period in this window rests on {event.min_support} source rows "
                f"(minimum {min_support}). A ratio computed over so few rows moves on sampling "
                f"alone, so the apparent shift may not exist."
            ),
            missing_evidence=[f"at least {min_support} rows per period in {event.entity or 'total'}"],
            what_would_resolve_it=[
                "aggregate to a coarser time grain (week or month)",
                "analyse at a shallower entity grain",
                "supply a denser extract for this slice",
            ],
        )

    if n_pre_periods and n_pre_periods < min_history:
        return AbstainPayload(
            event_id=event.event_id,
            kpi=kpi,
            reason_code="insufficient_history",
            message=(
                f"Only {n_pre_periods} periods of history precede this window (minimum "
                f"{min_history}). There is not enough baseline to say what normal looks like, "
                f"which is the situation for a newly launched product, market or channel."
            ),
            missing_evidence=[f"{min_history - n_pre_periods} further pre-period observations"],
            what_would_resolve_it=[
                "wait for more history to accumulate",
                "borrow a baseline from a comparable established slice",
            ],
        )

    failed_designs = [
        a for a in attributions
        if a.method_choice.chosen == "none"
        and "parallel" in " ".join(a.method_choice.considered.values()).lower()
    ]
    if failed_designs:
        reasons = "; ".join(sorted({r for a in failed_designs for r in a.method_choice.considered.values()}))
        return AbstainPayload(
            event_id=event.event_id,
            kpi=kpi,
            reason_code="failed_parallel_trends",
            message=(
                "No valid causal design was available. Treated and control slices were already "
                f"diverging before the window, so any difference between them reflects a "
                f"pre-existing trend rather than this event. Details: {reasons}"
            ),
            missing_evidence=["control slices with parallel pre-treatment trends"],
            what_would_resolve_it=[
                "identify a better-matched donor pool",
                "supply the intervention date so an interrupted time series can be fitted",
            ],
        )

    contradictory = confidence.method_agreement is not None and confidence.method_agreement < 0.15
    if contradictory:
        return AbstainPayload(
            event_id=event.event_id,
            kpi=kpi,
            reason_code="contradictory_methods",
            message=(
                "The exact decomposition and the controlled comparison disagree on direction. "
                "The KPI moved, but the controlled estimate does not attribute that movement to "
                "this event, which usually means the shift is a broader trend affecting all slices."
            ),
            missing_evidence=["a causal design whose estimate agrees with the observed movement"],
            what_would_resolve_it=[
                "confirm whether the movement is market-wide rather than local",
                "check for a concurrent event affecting the control slices too",
            ],
        )

    if confidence.score < threshold:
        weakest = min(
            ((k, v) for k, v in {
                "signal strength": confidence.signal_to_noise,
                "data support": confidence.support_factor,
                "history depth": confidence.history_factor,
                "estimator precision": confidence.estimator_precision,
            }.items() if v is not None),
            key=lambda kv: kv[1],
            default=("unknown", 0.0),
        )
        return AbstainPayload(
            event_id=event.event_id,
            kpi=kpi,
            reason_code="below_confidence_threshold",
            message=(
                f"Confidence {confidence.score:.2f} is below the {threshold:.2f} threshold; the "
                f"weakest factor is {weakest[0]} at {weakest[1]:.2f}. The movement is reported "
                f"without a causal claim."
            ),
            missing_evidence=[f"stronger {weakest[0]}"],
            what_would_resolve_it=[
                "widen the analysis window to accumulate signal",
                "supply operational context (campaign calendar, pricing changes) for this window",
            ],
        )

    attempted = [c for a in attributions for c in a.contributions]
    if attempted and not any(np.isfinite(c.contribution) for c in attempted):
        return AbstainPayload(
            event_id=event.event_id,
            kpi=kpi,
            reason_code="no_valid_covariates",
            message="No driver produced a finite contribution; nothing can be attributed.",
            missing_evidence=["at least one usable, non-redundant driver column"],
            what_would_resolve_it=["extend the dataset with candidate driver columns"],
        )

    return None
