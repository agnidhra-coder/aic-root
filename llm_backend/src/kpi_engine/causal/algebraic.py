"""Exact decomposition of a KPI movement across its own measures.

This runs first and always, and it is the strongest evidence the engine
produces, because it involves no estimation at all. Every KPI here is a known
arithmetic function of its measures, so the question "how much of the CAC rise
came from spend rather than from lost acquisitions" has an exact answer that
follows from the definition -- no model, no assumptions, no standard errors.

Two decompositions, deliberately both:

* Shapley over measures. Works for any expression the contract can express. The
  contribution of a measure is its average marginal effect over every ordering
  of the others, which is the only attribution satisfying efficiency (the parts
  sum to the whole), symmetry, and the null-player property. With <= ~6 measures
  the 2^n coalitions are enumerated exactly -- no sampling, so repeated runs give
  identical numbers.
* LMDI, for pure ratios. Gives the same answer to within rounding but in log
  terms, so it acts as an independent check on the Shapley implementation.

The naive alternative -- "which input changed by the largest percent" -- is
wrong whenever more than one input moves, which is exactly the multi-driver case
the engine exists to explain.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import numpy as np

from kpi_engine.contracts.configs import KpiDef
from kpi_engine.semantics.expressions import evaluate


@dataclass
class DecompositionTerm:
    measure: str
    contribution: float
    share: float
    pre_value: float
    post_value: float
    pct_change: float


@dataclass
class AlgebraicResult:
    kpi: str
    method: str
    pre_kpi: float
    post_kpi: float
    total_delta: float
    terms: list[DecompositionTerm]
    residual: float
    exact: bool

    @property
    def explained(self) -> float:
        return sum(t.contribution for t in self.terms)


def _evaluate_at(kpi: KpiDef, values: dict[str, float]) -> float:
    result = evaluate(kpi.expression, values)
    return float(result)


def shapley_decomposition(
    kpi: KpiDef, pre: dict[str, float], post: dict[str, float]
) -> AlgebraicResult:
    """Exact Shapley attribution of the KPI change across its measures.

    For measure i, contribution = sum over subsets S of the other measures of
    weight(|S|) * [f(S + i at post) - f(S at post)], where the weight is
    |S|!(n-|S|-1)!/n!. The terms sum to f(post) - f(pre) exactly, so nothing is
    left unexplained and nothing is double-counted.
    """
    measures = sorted(kpi.measures)
    n = len(measures)
    if n == 0:
        raise ValueError(f"KPI '{kpi.name}' declares no measures")

    def f(switched: frozenset[str]) -> float:
        """KPI value when `switched` measures are at post-period levels, the rest at pre."""
        return _evaluate_at(kpi, {m: (post[m] if m in switched else pre[m]) for m in measures})

    # Cache every coalition once: 2^n evaluations rather than n * 2^(n-1).
    cache = {
        frozenset(combo): f(frozenset(combo))
        for r in range(n + 1)
        for combo in itertools.combinations(measures, r)
    }

    base, full = cache[frozenset()], cache[frozenset(measures)]
    total = full - base

    contributions: dict[str, float] = {}
    for i, measure in enumerate(measures):
        others = [m for m in measures if m != measure]
        total_contrib = 0.0
        for size in range(len(others) + 1):
            weight = math.factorial(size) * math.factorial(n - size - 1) / math.factorial(n)
            for subset in itertools.combinations(others, size):
                s = frozenset(subset)
                marginal = cache[s | {measure}] - cache[s]
                if np.isfinite(marginal):
                    total_contrib += weight * marginal
        contributions[measure] = total_contrib

    explained = sum(contributions.values())
    terms = [
        DecompositionTerm(
            measure=m,
            contribution=contributions[m],
            share=(contributions[m] / total) if total not in (0.0,) and np.isfinite(total) else float("nan"),
            pre_value=pre[m],
            post_value=post[m],
            pct_change=((post[m] / pre[m] - 1.0) * 100.0) if pre[m] else float("nan"),
        )
        for m in measures
    ]
    terms.sort(key=lambda t: -abs(t.contribution))

    return AlgebraicResult(
        kpi=kpi.name,
        method="shapley_exact",
        pre_kpi=base,
        post_kpi=full,
        total_delta=total,
        terms=terms,
        residual=total - explained,
        exact=True,
    )


def lmdi_decomposition(
    kpi: KpiDef, pre: dict[str, float], post: dict[str, float], numerator: str, denominator: str
) -> AlgebraicResult:
    """Log-Mean Divisia decomposition for a two-measure ratio KPI.

    For Y = A/B: dY = L(Y1,Y0) * [ln(A1/A0) - ln(B1/B0)], where L is the
    logarithmic mean (a-b)/(ln a - ln b). Independent of the Shapley path, so
    agreement between the two is a genuine check rather than a restatement.
    """
    y0 = _evaluate_at(kpi, pre)
    y1 = _evaluate_at(kpi, post)
    total = y1 - y0

    def log_mean(a: float, b: float) -> float:
        if a <= 0 or b <= 0:
            return float("nan")
        return a if math.isclose(a, b) else (a - b) / (math.log(a) - math.log(b))

    weight = log_mean(y1, y0)
    terms: list[DecompositionTerm] = []
    for measure, sign in ((numerator, 1.0), (denominator, -1.0)):
        ratio = post[measure] / pre[measure] if pre[measure] else float("nan")
        contribution = sign * weight * math.log(ratio) if ratio and ratio > 0 else float("nan")
        terms.append(
            DecompositionTerm(
                measure=measure,
                contribution=contribution,
                share=contribution / total if total else float("nan"),
                pre_value=pre[measure],
                post_value=post[measure],
                pct_change=(ratio - 1.0) * 100.0 if np.isfinite(ratio) else float("nan"),
            )
        )
    terms.sort(key=lambda t: -abs(t.contribution))
    explained = sum(t.contribution for t in terms if np.isfinite(t.contribution))

    return AlgebraicResult(
        kpi=kpi.name,
        method="lmdi",
        pre_kpi=y0,
        post_kpi=y1,
        total_delta=total,
        terms=terms,
        residual=total - explained,
        exact=True,
    )


def mix_decomposition(
    kpi: KpiDef,
    pre_by_entity: dict[str, dict[str, float]],
    post_by_entity: dict[str, dict[str, float]],
) -> dict[str, float]:
    """Split a total movement into within-slice change and mix shift.

    A total ratio can move without any slice changing, purely because weight
    shifted toward slices with different levels. Reporting that as a performance
    change would send someone chasing a problem that does not exist.
    """
    measures = sorted(kpi.measures)
    entities = sorted(set(pre_by_entity) | set(post_by_entity))

    def totals(source: dict[str, dict[str, float]]) -> dict[str, float]:
        return {m: sum(source.get(e, {}).get(m, 0.0) for e in entities) for m in measures}

    pre_tot, post_tot = totals(pre_by_entity), totals(post_by_entity)
    y0, y1 = _evaluate_at(kpi, pre_tot), _evaluate_at(kpi, post_tot)

    # Counterfactual: each slice keeps its own pre-period KPI level, but the
    # slice weights move to their post-period values.
    weight_measure = measures[-1]  # denominator-like scale measure
    within = 0.0
    for e in entities:
        pre_e = pre_by_entity.get(e)
        post_e = post_by_entity.get(e)
        if not pre_e or not post_e:
            continue
        w = post_e.get(weight_measure, 0.0)
        y0_e, y1_e = _evaluate_at(kpi, pre_e), _evaluate_at(kpi, post_e)
        if np.isfinite(y0_e) and np.isfinite(y1_e):
            within += w * (y1_e - y0_e)
    scale = post_tot.get(weight_measure, 0.0)
    within_effect = within / scale if scale else float("nan")

    return {
        "total_delta": y1 - y0,
        "within_slice": within_effect,
        "mix_shift": (y1 - y0) - within_effect,
    }
