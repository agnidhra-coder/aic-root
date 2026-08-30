"""Check the narrative against the fact table, deterministically.

The system prompt tells the narrator to quote only from the fact table. That is an
instruction, and instructions are not guarantees. This module is the guarantee: it
re-reads what came back and rejects it mechanically, with no model involved in
deciding whether the model behaved.

The design principle is that a failure here must be *cheap and recoverable*. A
violation produces a message specific enough to repair, one repair pass is allowed,
and a second failure drops to a deterministic template. The engine's worst case is
a terse true report, never a fluent false one.

What is deliberately NOT checked: style, completeness, whether the explanation is
a good one. Those are judgement, and a verifier that adjudicated them would be
another opinion rather than a check.
"""

from __future__ import annotations

import re

from kpi_engine.causal.dag import CausalGraph

from kpi_agent.models import Claim, GroundedContext, Narrative, VerificationResult, Violation

# Numbers as they appear in prose: 12, 1,240.5, -3.2, +18%, 0.79.
_NUMBER = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")

# Relative tolerance for matching a quoted number to a fact. Generous enough to
# survive the narrator rounding 44.997 to 45.0, tight enough that 45 and 54 are
# different numbers.
REL_TOLERANCE = 0.02
ABS_TOLERANCE = 0.051

# Small integers are almost always structural -- "3 regions", "two detectors", a
# year, a date component -- rather than a quoted measurement. Requiring a fact for
# every "1" would make ordinary sentences unwritable while catching nothing: the
# numbers that matter are the ones a reader would act on.
STRUCTURAL_MAX = 12

_CAUSAL_LANGUAGE = re.compile(
    r"\b(caused|because|due to|driven by|drove|responsible for|attributable to|"
    r"resulted from|led to|explains?|explained by)\b",
    re.IGNORECASE,
)
_UP = re.compile(r"\b(rose|rise|increased?|grew|growth|up|higher|climbed|jump(?:ed)?|gain(?:ed)?)\b", re.I)
_DOWN = re.compile(r"\b(fell|fall|decreased?|declined?|dropped?|down|lower|shrank|contracted|loss)\b", re.I)


def verify(
    narrative: Narrative, context: GroundedContext, graph: CausalGraph
) -> VerificationResult:
    violations: list[Violation] = []
    values = _fact_values(context)
    known_ids = {f.id for f in context.facts}
    node_names = {n.name for n in graph.spec.nodes}
    owners = {n.owner for n in graph.spec.nodes if n.owner}
    abstained_kpis = {a["kpi"] for a in context.abstentions if a.get("kpi")}

    numbers_checked = 0
    claims_checked = 0

    sections: list[tuple[str, list[Claim]]] = [
        ("what_happened", narrative.what_happened),
        ("why", narrative.why),
        ("needs_attention", narrative.needs_attention),
    ]

    # The headline carries no evidence ids of its own, so it is checked against the
    # whole table: it is a summary of cited claims, not an uncited assertion.
    numbers_checked += _check_numbers(
        narrative.headline, "headline", values, violations
    )

    for section, claims in sections:
        for i, claim in enumerate(claims):
            where = f"{section}[{i}]"
            claims_checked += 1

            if not claim.evidence_ids:
                violations.append(Violation(
                    code="missing_evidence", where=where,
                    detail="Claim carries no evidence_ids. Every sentence must cite at "
                           "least one fact id.",
                ))
            for fid in claim.evidence_ids:
                if fid not in known_ids:
                    violations.append(Violation(
                        code="unknown_evidence_id", where=where,
                        detail=f"'{fid}' is not a fact in the table. Valid ids run F1 "
                               f"to F{len(context.facts)}.",
                    ))

            numbers_checked += _check_numbers(
                claim.text, where, values, violations,
                extra_values=_claim_values(claim.evidence_ids, context),
            )
            _check_direction(claim, where, context, violations)

            # A user's hypothesis is not evidence for itself. Overlapping in time
            # with an event licenses nothing -- unlike a cross-source link, which
            # needs a declared path through the graph before two movements may be
            # connected. So a cause may rest on measured evidence, or on measured
            # evidence *and* the user's context, but never on the context alone.
            if section == "why" and claim.evidence_ids:
                cited = [context.fact(i) for i in claim.evidence_ids]
                known = [f for f in cited if f is not None]
                if known and all(f.kind == "context" for f in known):
                    violations.append(Violation(
                        code="unlicensed_context_cause", where=where,
                        detail="This cause rests only on context the user supplied, "
                               "which the engine did not measure and no causal path "
                               "licenses. Cite the measured evidence it bears on as "
                               "well, or move the sentence to needs_attention.",
                    ))

            if section == "why" and _CAUSAL_LANGUAGE.search(claim.text):
                for kpi in abstained_kpis:
                    if kpi and kpi.lower() in claim.text.lower():
                        violations.append(Violation(
                            code="causal_claim_on_abstention", where=where,
                            detail=f"The evidence for {kpi} abstained, so no cause may be "
                                   f"asserted for it. Move this to abstained_from and say "
                                   f"what would resolve it.",
                        ))

    for i, action in enumerate(narrative.actions):
        where = f"actions[{i}]"
        claims_checked += 1
        if action.driver not in node_names:
            violations.append(Violation(
                code="unknown_driver", where=where,
                detail=f"'{action.driver}' is not a node in the causal graph.",
            ))
        if action.lever not in node_names:
            violations.append(Violation(
                code="unknown_driver", where=where,
                detail=f"Lever '{action.lever}' is not a node in the causal graph.",
            ))
        elif not graph.is_controllable(action.lever):
            violations.append(Violation(
                code="uncontrollable_lever", where=where,
                detail=f"'{action.lever}' is declared not controllable. An action must "
                       f"name a lever someone can actually set.",
            ))
        else:
            declared_owner = graph.owner(action.lever)
            if declared_owner and action.owner != declared_owner:
                violations.append(Violation(
                    code="unknown_owner", where=where,
                    detail=f"The graph assigns '{action.lever}' to {declared_owner}, "
                           f"not {action.owner}.",
                ))
        if action.owner not in owners:
            violations.append(Violation(
                code="unknown_owner", where=where,
                detail=f"'{action.owner}' is not an owner named anywhere in the graph.",
            ))
        for fid in action.evidence_ids:
            if fid not in known_ids:
                violations.append(Violation(
                    code="unknown_evidence_id", where=where,
                    detail=f"'{fid}' is not a fact in the table.",
                ))
        cited_confidence = [
            f for f in (context.fact(i) for i in action.evidence_ids)
            if f is not None and f.kind == "confidence" and f.value is not None
        ]
        if action.confidence is None:
            # Null is the honest answer for a recommendation that rests on a
            # description rather than a measured explanation -- a trend has no
            # `EvidenceBundle` and therefore no score. It is not a way out of
            # quoting one that exists: citing a confidence fact and then
            # declining to state it is a dodge, not a caveat.
            if cited_confidence:
                violations.append(Violation(
                    code="confidence_not_grounded", where=where,
                    detail=f"This action cites a computed confidence score "
                           f"({cited_confidence[0].display}) but leaves confidence "
                           f"null. State the score it cites, or stop citing it.",
                ))
        elif not 0.0 <= action.confidence <= 1.0:
            violations.append(Violation(
                code="confidence_not_grounded", where=where,
                detail=f"Confidence {action.confidence} is outside 0-1.",
            ))
        else:
            scores = [f.value for f in context.facts
                      if f.kind == "confidence" and f.value is not None]
            if scores and not any(abs(action.confidence - s) <= 0.05 for s in scores):
                violations.append(Violation(
                    code="confidence_not_grounded", where=where,
                    detail=f"Confidence {action.confidence:.2f} matches no computed "
                           f"confidence score. Available: "
                           f"{', '.join(f'{s:.2f}' for s in sorted(set(scores)))}.",
                ))
        numbers_checked += _check_numbers(
            action.expected_impact, where, values, violations,
            extra_values=_claim_values(action.evidence_ids, context),
        )

    # General recommendations are checked on inverted terms. Everything else here
    # asks "is this number in the table"; these are declared ungrounded up front,
    # so the rule is that they carry no measurement at all. An empty pool turns
    # `_check_numbers` into exactly that check without a second implementation.
    known_kpis = {f.kpi for f in context.facts if f.kpi}
    for i, rec in enumerate(narrative.general_recommendations):
        where = f"general_recommendations[{i}]"
        claims_checked += 1
        if rec.related_kpi not in known_kpis:
            violations.append(Violation(
                code="unknown_kpi", where=where,
                detail=f"'{rec.related_kpi}' is not a KPI in the fact table. A "
                       f"recommendation may be general, but it must be about a KPI "
                       f"this run actually looked at.",
            ))
        for field, text in (("action", rec.action), ("rationale", rec.rationale)):
            numbers_checked += _check_numbers(
                text, f"{where}.{field}", [], violations,
                repair="A general recommendation is not measured, so it may carry "
                       "no figure at all. Remove the number and say it in words.",
            )

    return VerificationResult(
        passed=not violations,
        violations=violations,
        numbers_checked=numbers_checked,
        claims_checked=claims_checked,
    )


def _fact_values(context: GroundedContext) -> list[float]:
    """Every number the ENGINE computed, and that a narrator may quote anywhere.

    Both the fact's own `value` and any number embedded in its `display` string
    count: a display of "Cost Of Ads: +1,234 (61.4% of the move)" makes 61.4 a
    quotable number even though the fact's `value` is 1234.

    `context` facts are deliberately excluded. They carry figures the *user*
    asserted -- "42 degrees", "three days of rain" -- and folding those into one
    flat pool would let a narrator ground an unrelated 42 anywhere in the report
    on the strength of the user having typed it. They are readmitted per-claim by
    `_claim_values`, so a user's number is quotable only in a sentence that cites
    the fact carrying it, which is where it is attributed to them.
    """
    values: list[float] = []
    for fact in context.facts:
        if fact.kind == "context":
            continue
        if fact.value is not None:
            values.append(float(fact.value))
        values.extend(_extract(fact.display))
        if fact.note:
            values.extend(_extract(fact.note))
    for entry in context.events:
        if entry.get("confidence") is not None:
            values.append(float(entry["confidence"]))
    for link in context.links:
        values.extend([float(link.lag_days), float(link.overlap_days)])
    return values


def _claim_values(evidence_ids: list[str], context: GroundedContext) -> list[float]:
    """The user-asserted numbers this particular sentence has earned the right to quote."""
    values: list[float] = []
    for fid in evidence_ids:
        fact = context.fact(fid)
        if fact is None or fact.kind != "context":
            continue
        if fact.value is not None:
            values.append(float(fact.value))
        values.extend(_extract(fact.display))
        if fact.note:
            values.extend(_extract(fact.note))
    return values


def _extract(text: str) -> list[float]:
    out: list[float] = []
    for match in _NUMBER.findall(text or ""):
        try:
            out.append(float(match.replace(",", "")))
        except ValueError:
            continue
    return out


def _check_numbers(
    text: str,
    where: str,
    values: list[float],
    violations: list[Violation],
    extra_values: list[float] | None = None,
    repair: str | None = None,
) -> int:
    """Reject every non-structural number that no fact backs.

    `repair` replaces the instruction half of the message. The default tells the
    narrator to quote from the table; a caller passing an empty `values` pool is
    saying no number belongs here at all, and "quote it from a fact" would be the
    wrong repair to hand back for that.
    """
    checked = 0
    if extra_values:
        values = [*values, *extra_values]
    for token in _NUMBER.findall(text or ""):
        try:
            number = float(token.replace(",", ""))
        except ValueError:
            continue
        if abs(number) <= STRUCTURAL_MAX and float(number).is_integer():
            continue
        checked += 1
        if not _matches(number, values):
            violations.append(Violation(
                code="ungrounded_number", where=where,
                detail=repair or (
                    f"'{token}' appears in no fact. Quote numbers only from a "
                    f"fact's display value, or drop the number."
                ),
            ))
    return checked


def _matches(number: float, values: list[float]) -> bool:
    for value in values:
        if abs(number - value) <= max(ABS_TOLERANCE, abs(value) * REL_TOLERANCE):
            return True
        # A percentage stated as 61.4 against a share stored as 0.614.
        if abs(number - value * 100.0) <= max(ABS_TOLERANCE, abs(value * 100.0) * REL_TOLERANCE):
            return True
    return False


def _check_direction(
    claim: Claim, where: str, context: GroundedContext, violations: list[Violation]
) -> None:
    """A claim citing a movement must not describe it in the opposite direction.

    Only checked when the cited facts agree on a sign, and only when the sentence
    is unambiguously directional -- a sentence containing both "rose" and "fell"
    is describing two things, and guessing which one the fact belongs to would
    generate false violations.

    `trend` counts here alongside `movement` and `contribution`: a trend fact's
    value is a signed end-to-end change, so "X is climbing" over a falling series
    is the same error in a descriptive sentence as in a causal one.
    """
    signs = {
        (f.value > 0) for f in (
            context.fact(i) for i in claim.evidence_ids
        ) if f and f.kind in {"movement", "contribution", "trend"}
        and f.value not in (None, 0)
    }
    if len(signs) != 1:
        return
    positive = next(iter(signs))
    says_up, says_down = bool(_UP.search(claim.text)), bool(_DOWN.search(claim.text))
    if says_up == says_down:
        return
    if positive and says_down:
        violations.append(Violation(
            code="direction_contradicts_evidence", where=where,
            detail="The cited fact is a positive movement, but the sentence describes "
                   "a decrease.",
        ))
    if not positive and says_up:
        violations.append(Violation(
            code="direction_contradicts_evidence", where=where,
            detail="The cited fact is a negative movement, but the sentence describes "
                   "an increase.",
        ))
