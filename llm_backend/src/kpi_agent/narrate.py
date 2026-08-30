"""Turn the fact table into prose -- and the fallback for when that is not possible.

The narrator's context is `GroundedContext` and nothing else. It cannot reach the
panel, the raw frame, or the bundles it was derived from, so "only answer from
grounded sources" is a property of what it can see rather than a rule it is asked
to follow.

`fallback_narrative` is the same report written by template. It exists so the
failure mode of the LLM half is a plainer answer, not a missing one: an API outage,
a refusal, or two failed verification passes all land here, and the numbers are
identical because they come from the same table.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from kpi_agent.llm import MODEL, LlmUnavailable, Usage, unavailable
from kpi_agent.models import Action, Claim, GroundedContext, Narrative

log = logging.getLogger(__name__)

SYSTEM = """\
You explain KPI movements to a business audience, working from a fixed table of \
facts that a deterministic engine computed. You are a writer here, not an analyst: \
the analysis is finished, and your job is to say what it found.

Absolute rules -- these are checked mechanically after you answer, and a violation \
sends the work back:

1. Every number you write must already appear in the table. Do not compute, \
convert, sum, average, annualise or re-express anything. If a number you want is \
not in the table, the answer is to not use that number.
1b. Quote the NUMBER, never the fact's `label` or `display` string. Those are \
machine labels; pasting one into a sentence produces nonsense. Write the sentence \
in your own words and put only the figure in it.
   WRONG: "Fill Rate changed by Fill Rate -2.5% (96.28 expected, 93.91 actual)."
   WRONG: "Units Ordered contributed Units Ordered: -17.4 (+667.2% of the move)."
   RIGHT: "Fill Rate fell 2.5%, to 93.91 against an expected 96.28."
   RIGHT: "Units Ordered accounts for 667.2% of the move, offset by Units Received."
2. Every claim must cite the fact ids it rests on, in `evidence_ids`.
3. Only name drivers, levers and owners that appear in the facts or the `levers` \
list. An action must name a lever that is actually controllable, with the owner \
the data assigns to it.
3b. `general_recommendations` is the one place you may write from your own \
knowledge of this industry rather than from the table. Give exactly 3 whenever the \
facts name three or more KPIs, and never fewer than 2 -- practices worth \
considering for a KPI that moved this way, given no external knowledge base about \
this company exists. Spread them across different KPIs and different kinds of \
response rather than restating one idea three ways. They are explicitly NOT \
measured, so put NO number in `action` or `rationale`, never say the data supports \
them, and never repeat an attributed cause or a recommended action as one. Set \
`related_kpi` to a KPI name that appears in the facts. These carry no evidence ids \
because nothing here cites the table.
4. If an event abstained, it gets no cause. Say what was missing and what would \
resolve it, in `abstained_from`. Do not explain around an abstention.
5. Distinguish exact from estimated. A contribution marked exact follows from the \
KPI's definition; an estimated one carries assumptions. Never present an estimate \
as settled fact.
6. A cross-source link is only valid where the facts give you one. Do not connect \
a supply-chain movement to a sales movement on your own initiative -- if the table \
has no link fact, the connection was not licensed and does not exist.
7. A `context` fact is something the USER told us, not something the engine \
measured. You may put it in front of the reader as a candidate worth weighing, and \
you must say it is unverified and untested. It never gets a share, a magnitude or \
the word "caused". It never displaces an attributed driver: where a contribution \
explains a move, the user's factor is at most a possible reason that driver moved. \
A `context` fact may not be the only evidence for a sentence in `why` -- pair it with \
the measured evidence it bears on, or put it in `needs_attention` instead.
7b. If the user asserted something and nothing aligned with it, say so plainly in \
`abstained_from`. "Nothing in the detected windows lines up with that" is an answer, \
and a better one than quietly dropping their question.
8. A `trend` fact is descriptive. It says what a series has been doing, not that \
anything is wrong and not why. It is not a detection and carries no cause. Where a \
trend and an attributed event coexist, the event is the finding and the trend is its \
background. Where there are trends and no events, the trends are the answer -- report \
them as what to watch, not as what went wrong.

Write plainly. No hedging filler, no restating the question back, no summary of \
what you are about to say. Lead with what changed and what it means.
"""

REPAIR_PREFIX = """\
Your previous answer failed mechanical verification. The violations are listed \
below. Fix exactly these and return the whole narrative again. Do not add new \
claims to compensate -- if a number cannot be grounded, remove it or replace it \
with one from the table.

VIOLATIONS
"""


def narrate(
    llm: Any,
    usage: Usage,
    context: GroundedContext,
    *,
    repair_feedback: str | None = None,
    previous: Narrative | None = None,
) -> Narrative:
    """Model call #2: the fact table in, a structured narrative out.

    The evidence goes in the stable middle message and the instructions last, which
    keeps the cached prefix intact between the first attempt and a repair pass.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    sections = ", ".join(_sections_for(context))
    user = (
        f"Question: {context.question}\n"
        f"Understood as: {context.question_restated}\n"
        f"Audience: {context.persona}\n"
        f"{context.persona_brief}\n\n"
        f"Populate these sections: {sections}. Leave the others empty.\n"
    )
    if repair_feedback:
        user += (
            "\n" + REPAIR_PREFIX + repair_feedback
            + "\n\nYOUR PREVIOUS ANSWER\n"
            + (previous.model_dump_json(indent=2) if previous else "(unavailable)")
        )

    structured = llm.with_structured_output(
        Narrative, method="json_schema", include_raw=True
    )

    log.info("%s writing the narrative%s", MODEL,
             " (repair pass)" if repair_feedback else "")
    started = time.monotonic()
    try:
        result = structured.invoke([
            SystemMessage(SYSTEM),
            HumanMessage("EVIDENCE\n" + context.model_dump_json(indent=2)),
            HumanMessage(user),
        ])
    except Exception as exc:  # noqa: BLE001 - transport, API and timeout alike
        raise unavailable(exc, started) from exc
    log.info("%s narrative returned in %.1fs", MODEL, time.monotonic() - started)

    usage.record("Narrative", getattr(result["raw"], "usage_metadata", None))
    story = result["parsed"]
    if story is None:
        raise LlmUnavailable(
            f"The narrative did not validate against Narrative: "
            f"{result.get('parsing_error') or 'no content returned'}. "
            "An empty reply usually means the output cap was too low."
        )
    return story


def _sections_for(context: GroundedContext) -> list[str]:
    return ["what_happened", "why", "needs_attention", "actions",
            "general_recommendations", "uncertainty", "abstained_from"]


# --------------------------------------------------------------------------- #
# Deterministic fallback
# --------------------------------------------------------------------------- #


def fallback_narrative(context: GroundedContext, reason: str = "") -> Narrative:
    """The same report, written by template.

    Terser and duller than the model's version, and every number is the same,
    because both read the identical table. This is what `--no-llm` produces and
    where verification failures land.
    """
    movements = [f for f in context.facts if f.kind == "movement"]
    contributions = [f for f in context.facts if f.kind == "contribution"]
    confidences = [f for f in context.facts if f.kind == "confidence"]
    links = [f for f in context.facts if f.kind == "link"]
    trends = [f for f in context.facts if f.kind == "trend"]
    # An aligned factor has a fact id it can be cited from; an unplaced one has a
    # fact with no value. The split matters because the two belong in different
    # sections: one is context on a finding, the other is an unanswered question.
    contexts = [f for f in context.facts if f.kind == "context"]
    placed = [f for f in contexts if f.value is not None]
    unplaced = [f for f in contexts if f.value is None]

    what: list[Claim] = [
        Claim(text=f"{f.display} in {_slice(f.entity)}. {f.note or ''}".strip(),
              evidence_ids=[f.id])
        for f in movements[:8]
    ]
    # Trends carry `what_happened` when nothing was detected, and supplement it
    # when something was. Either way they are stated as descriptions -- the
    # wording never asserts that anything went wrong.
    for f in trends[: 8 if not movements else 4]:
        what.append(Claim(
            text=f"{f.display}, in {_slice(f.entity)}. Descriptive only.",
            evidence_ids=[f.id],
        ))

    exact = [f for f in contributions if f.exact]
    estimated = [f for f in contributions if not f.exact]
    why: list[Claim] = []
    abstained_kpis = {a.get("kpi") for a in context.abstentions}
    for f in (exact + estimated)[:8]:
        if f.kpi in abstained_kpis:
            continue
        qualifier = "exactly, from the KPI's definition" if f.exact else f"estimated by {f.method}"
        why.append(Claim(
            text=f"{f.display} of the move in {f.kpi} ({qualifier}).",
            evidence_ids=[f.id],
        ))

    attention: list[Claim] = []
    for f in links:
        attention.append(Claim(text=f"{f.label}. {f.note or ''}".strip(), evidence_ids=[f.id]))
    # A factor the user raised that sits on a detected event belongs in front of
    # them -- but here, not in `why`. It has no causal licence, and `verify`
    # rejects a `why` claim resting on one alone.
    for f in placed:
        attention.append(Claim(
            text=(
                f"You mentioned {f.display}. The engine did not measure this and "
                f"cannot confirm it caused anything; it is offered as a coincidence "
                f"in time for you to weigh."
            ),
            evidence_ids=[f.id],
        ))
    for f in confidences:
        if f.value is not None and f.value < 0.6:
            attention.append(Claim(
                text=f"Low confidence ({f.display}) in this explanation: {f.note}.",
                evidence_ids=[f.id],
            ))

    actions: list[Action] = []
    lever_owner = {lever["lever"]: lever["owner"] for lever in context.levers}
    for f in contributions:
        if not f.controllable or f.owner is None:
            continue
        if f.label.split(" contribution")[0] not in lever_owner:
            continue
        driver = f.label.split(" contribution")[0]
        score = next((c.value for c in confidences if c.entity == f.entity), None)
        actions.append(Action(
            driver=driver,
            lever=driver,
            action=f"Review {driver} for {_slice(f.entity)} over the flagged window.",
            owner=lever_owner[driver],
            expected_impact=f"Reversing this driver addresses {f.display}.",
            # `None`, not `0.0`. A fabricated zero reads as "we measured no
            # confidence in this" when the truth is that nothing was measured.
            confidence=float(score) if score is not None else None,
            monitoring=f"Watch {f.kpi} at {context.time_grain} grain for the next four periods.",
            evidence_ids=[f.id],
        ))
        if len(actions) >= 4:
            break

    # Five events abstaining for the same reason on the same KPI is one finding, not
    # five. Collapse them and say how many, or the section reads as a wall.
    grouped: dict[tuple[str, str], list[dict]] = {}
    for a in context.abstentions:
        grouped.setdefault((a["kpi"], a["reason_code"]), []).append(a)
    abstained = []
    for (kpi, code), group in grouped.items():
        head = group[0]
        count = f" ({len(group)} events)" if len(group) > 1 else ""
        abstained.append(
            f"{kpi} ({code}){count}: {head['message'].strip()} "
            f"What would resolve it: {_join(head['what_would_resolve_it'])}."
        )

    for f in unplaced:
        abstained.append(
            f"{f.display} — stated, but nothing in the detected windows lines up "
            f"with it. What would resolve it: a dated window for the factor, or a "
            f"column in the data that carries it."
        )

    headline = _headline(movements, links, context, trends)

    uncertainty = (
        "This report was assembled deterministically from the evidence bundles"
        + (f" ({reason})" if reason else "")
        + ". Contributions marked exact follow from each KPI's definition; the rest are "
        "estimates carrying the assumptions their method states."
        # Caveats belong here rather than in a cited claim: they describe the data as
        # a whole, so there is no single fact for them to point at, and inventing a
        # citation to satisfy the verifier would defeat what the citation is for.
        + ("" if not context.data_caveats else " " + " ".join(context.data_caveats))
    )

    return Narrative(
        headline=headline,
        what_happened=what,
        why=why,
        needs_attention=attention[:6],
        actions=actions,
        uncertainty=uncertainty,
        abstained_from=abstained,
    )


def _headline(movements: list, links: list, context: GroundedContext,
              trends: list | None = None) -> str:
    """A summary line, assembled rather than quoted.

    Using the first movement's display string verbatim -- as this did originally --
    puts a detector name and a window in the headline and reads like a log line.
    Counting is more useful and stays just as grounded: every number here is a count
    of facts in the table, not a measurement drawn from one.
    """
    trends = trends or []
    if not movements:
        if not trends:
            return "No material KPI movement was detected in the requested slice and period."
        # A survey that detected nothing has not found nothing. Saying so in one
        # line -- and saying which it is -- is the difference between a quiet
        # period and an uneventful one.
        series = len({(f.kpi, tuple(sorted(f.entity.items()))) for f in trends})
        return (
            f"No KPI moved materially, but {series} "
            f"{'series is' if series == 1 else 'series are'} trending or seasonal."
        )

    kpis: list[str] = []
    for f in movements:
        if f.kpi and f.kpi not in kpis:
            kpis.append(f.kpi)
    slices = {tuple(sorted(f.entity.items())) for f in movements}

    named = (
        _join(kpis[:3], "and") if len(kpis) <= 3
        else _join(kpis[:3], "and") + f", and {len(kpis) - 3} other KPIs,"
    )
    where = (
        "at total level" if slices == {()} else
        f"across {len(slices)} {'slice' if len(slices) == 1 else 'slices'}"
    )
    tail = (
        f" {len(links)} of them trace to a supply-side movement through the causal graph."
        if links else ""
    )
    return f"{named} moved materially {where}.{tail}"


def _join(items: list[str], conjunction: str = "or") -> str:
    items = [i.rstrip(".") for i in items if i]
    if not items:
        return "nothing identified"
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f", {conjunction} {items[-1]}"


def _slice(entity: dict[str, str]) -> str:
    return ", ".join(f"{k}={v}" for k, v in entity.items()) if entity else "the total"
