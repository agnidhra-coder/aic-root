"""Place the user's own context against the events the engine found.

The engine already asks for this. `evidence/abstention.py` tells a reader that
what would resolve a low-confidence explanation is "operational context
(campaign calendar, pricing changes) for this window" -- and until now there was
no way to supply it. This module is that channel.

What it does is deliberately small. A factor the user asserted is matched against
each detected `EventWindow` by two pieces of arithmetic -- do the windows touch,
do the slices contradict -- and nothing else. What it produces is a
`ContextAlignment`, which says "these two things sit on top of each other in time"
and stops there.

**An alignment is not a link, and the difference is the whole point.** A
`CrossSourceLink` requires a declared path through the causal graph before two
movements may be connected; that licence is what separates reading a graph from
mining a coincidence (`linking.py`). Nothing licenses an alignment, because the
user's factor is not in the graph and is not in the data. So an alignment is
offered to the reader as a coincidence worth knowing and never as a cause: the
note says so, the fact built from it says so, the narrator is told so, and
`verify.unlicensed_context_cause` is what makes it true rather than requested.

The window arithmetic is `linking.window_relation`, imported rather than copied.
Two implementations of "do these windows relate" would drift, and the day they
did, a link and an alignment would disagree about the same two dates.
"""

from __future__ import annotations

import datetime as dt
import logging

from kpi_engine.contracts.payloads import EventWindow

from kpi_agent.linking import DEFAULT_LAG_TOLERANCE_DAYS, shared_entity, window_relation
from kpi_agent.models import ContextAlignment, ExogenousFactor

log = logging.getLogger(__name__)


def align_factors(
    factors: list[ExogenousFactor],
    results: dict,
    *,
    lag_tolerance_days: int = DEFAULT_LAG_TOLERANCE_DAYS,
) -> tuple[list[ContextAlignment], list[ExogenousFactor]]:
    """Returns `(alignments, unaligned)`.

    `results` is the `source_id -> PipelineResult` mapping the run produced; only
    `.events` is read from it.

    A factor with no dates aligns to nothing. That is not a failure to try: a
    vague mention smeared across every event in the run would manufacture a
    relationship out of the fact that the user typed a sentence, and every event
    would then carry the same unfalsifiable footnote. It is reported unaligned
    instead, which is an answer -- "you mentioned this and nothing in the detected
    windows lines up with it" -- rather than a silence.
    """
    alignments: list[ContextAlignment] = []
    unaligned: list[ExogenousFactor] = []

    for factor in factors:
        window = _factor_window(factor)
        if window is None:
            unaligned.append(factor)
            continue

        found: list[ContextAlignment] = []
        for source_id, result in results.items():
            for event in getattr(result, "events", []):
                alignment = _align_one(factor, window, event, source_id, lag_tolerance_days)
                if alignment is not None:
                    found.append(alignment)

        if found:
            # Strongest first: the most overlap, then the least lag. A reader
            # scanning one line should see the closest coincidence, not the
            # first event that happened to be constructed.
            found.sort(key=lambda a: (-a.overlap_days, a.lag_days))
            alignments.extend(found)
        else:
            unaligned.append(factor)

    log.info("%d context factor(s): %d alignment(s), %d unplaced",
             len(factors), len(alignments), len(unaligned))
    return alignments, unaligned


def _align_one(
    factor: ExogenousFactor,
    window: tuple[dt.date, dt.date],
    event: EventWindow,
    source_id: str,
    lag_tolerance_days: int,
) -> ContextAlignment | None:
    # Entity, on `linking`'s terms: disagreeing on a key both carry rejects, and
    # an empty intersection is a match. A system-wide event genuinely can be the
    # one a region-scoped factor bears on; West cannot be explained by the North.
    scope = {factor.entity_key: factor.entity_value} if (
        factor.entity_key and factor.entity_value
    ) else {}
    if shared_entity(scope, event.entity) is None:
        return None

    overlap, lag = window_relation(window, (event.window_start, event.window_end))
    if lag > lag_tolerance_days:
        return None

    moved = [d.kpi for d in event.observed_deviations if d.material]
    if factor.affects_kpis:
        moved = [k for k in moved if k in factor.affects_kpis]
        if not moved:
            return None

    entity_match = "exact" if scope and factor.entity_key in event.entity else "unscoped"

    return ContextAlignment(
        factor_label=factor.label,
        event_id=event.event_id,
        source_id=source_id,
        overlap_days=overlap,
        lag_days=lag,
        entity_match=entity_match,
        kpis_moved=moved,
        direction_agrees=_direction_agrees(factor, event, moved),
        note=(
            f"{factor.label} was stated to cover "
            f"{window[0]} to {window[1]}; {event.event_id} runs "
            f"{event.window_start} to {event.window_end}"
            + (f", overlapping {overlap} day(s)" if overlap else f", {lag} day(s) apart")
            + ". This is a coincidence in time, not a causal path: the engine did "
            "not measure this factor and no declared edge connects it to anything."
        ),
    )


def _direction_agrees(
    factor: ExogenousFactor, event: EventWindow, kpis: list[str]
) -> bool | None:
    """Whether the user's expected direction matches the sign that was observed.

    `None` rather than `False` when either side did not say. A hypothesis with no
    stated direction has not been contradicted, and recording it as disagreement
    would put a thumb on the scale against the user.
    """
    if factor.expected_direction == "unknown" or not kpis:
        return None
    signs = {
        d.pct_delta > 0
        for d in event.observed_deviations
        if d.material and d.kpi in kpis and d.pct_delta not in (None, 0)
    }
    if len(signs) != 1:
        return None
    return next(iter(signs)) == (factor.expected_direction == "increase")


def _factor_window(factor: ExogenousFactor) -> tuple[dt.date, dt.date] | None:
    """The dates the user gave, as a window. `None` when they gave none.

    One date is a one-day window rather than an open-ended one. "It rained on the
    3rd" is a claim about the 3rd; treating it as "from the 3rd onwards" would
    silently widen the user's own statement until it touched something.
    """
    start = _parse(factor.date_start)
    end = _parse(factor.date_end)
    if start is None and end is None:
        return None
    start = start or end
    end = end or start
    return (start, end) if start <= end else (end, start)


def _parse(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None
