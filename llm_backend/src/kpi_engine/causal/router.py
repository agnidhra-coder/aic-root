"""Chooses a causal method per event and records why the others were rejected.

There is no single best estimator; each needs conditions the data may or may not
supply. DiD needs comparable untreated slices and parallel pre-trends. ITS needs
a stable pre-period and assumes nothing else changed at the same moment. DML
needs enough non-collinear observations.

So the router checks preconditions, picks the strongest method those conditions
permit, and emits the whole deliberation. That record is evidence: "DiD, because
four donor regions were unaffected and pre-trends held (p=0.62)" is a claim a
reviewer can audit, while a bare number is not.

Attribution is always two-layer:
  Layer A  exact algebra over the KPI's own measures -- no assumptions
  Layer B  estimation for the upstream behavioural mechanism -- assumptions stated
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from kpi_engine.causal.algebraic import lmdi_decomposition, shapley_decomposition
from kpi_engine.causal.dag import CausalGraph
from kpi_engine.causal.did import estimate_did
from kpi_engine.causal.dml import estimate_dml
from kpi_engine.causal.its import estimate_its
from kpi_engine.contracts.configs import KpiContract
from kpi_engine.contracts.payloads import (
    Attribution,
    Contribution,
    EventWindow,
    MethodChoice,
)

BASELINE_DAYS = 84


def _window_means(
    measures: pd.DataFrame,
    entity: dict[str, str],
    aliases: list[str],
    window: tuple[dt.date, dt.date],
    baseline_days: int = BASELINE_DAYS,
) -> tuple[dict[str, float], dict[str, float], int, int]:
    """Mean of each measure inside the event window and over the preceding baseline."""
    df = measures.copy()
    df["period"] = pd.to_datetime(df["period"])
    for key, val in entity.items():
        if key in df.columns:
            df = df[df[key].astype(str) == str(val)]

    start, end = pd.Timestamp(window[0]), pd.Timestamp(window[1])
    post = df[(df["period"] >= start) & (df["period"] <= end)]
    pre = df[(df["period"] < start) & (df["period"] >= start - pd.Timedelta(days=baseline_days))]

    pre_vals = {a: float(pre[a].mean()) for a in aliases}
    post_vals = {a: float(post[a].mean()) for a in aliases}
    return pre_vals, post_vals, len(pre), len(post)


def _algebraic_contributions(
    kpi_name: str,
    contract: KpiContract,
    graph: CausalGraph,
    measures: pd.DataFrame,
    entity: dict[str, str],
    window: tuple[dt.date, dt.date],
) -> tuple[list[Contribution], dict, float | None, float | None]:
    """Layer A: split the KPI movement across its own measures, exactly."""
    kpi = contract.kpi(kpi_name)
    aliases = sorted(kpi.measures)
    pre, post, n_pre, n_post = _window_means(measures, entity, aliases, window)

    if n_pre == 0 or n_post == 0 or any(not np.isfinite(v) for v in {**pre, **post}.values()):
        return [], {"error": "insufficient measure coverage in window or baseline"}, None, None

    result = shapley_decomposition(kpi, pre, post)

    # Independent cross-check for two-measure ratios: LMDI reaches the same split
    # by a different route, so disagreement means an implementation fault.
    cross_check: dict[str, object] = {}
    if len(aliases) == 2 and "/" in kpi.expression:
        num, den = kpi.expression.split("/", 1)
        num, den = num.strip(" ()"), den.strip(" ()")
        if num in kpi.measures and den in kpi.measures:
            lmdi = lmdi_decomposition(kpi, pre, post, num, den)
            deltas = {
                t.measure: abs(t.share - next(s.share for s in result.terms if s.measure == t.measure))
                for t in lmdi.terms
                if np.isfinite(t.share)
            }
            cross_check = {
                "lmdi_shares": {t.measure: round(t.share, 6) for t in lmdi.terms},
                "max_share_disagreement": round(max(deltas.values()), 6) if deltas else None,
            }

    contributions = []
    for term in result.terms:
        column = kpi.measures[term.measure].column
        node = graph.node_for_column(column)
        contributions.append(
            Contribution(
                driver=column,
                contribution=term.contribution,
                share=term.share,
                method="algebraic_lmdi",
                exact=True,
                controllable=graph.is_controllable(node) if node else False,
                owner=graph.owner(node) if node else None,
            )
        )

    diagnostics = {
        "shapley_residual": result.residual,
        "pre_periods": n_pre,
        "post_periods": n_post,
        "measure_pre": pre,
        "measure_post": post,
        "measure_pct_change": {t.measure: round(t.pct_change, 4) for t in result.terms},
        **cross_check,
    }
    return contributions, diagnostics, result.total_delta, result.explained


def _choose_quasi_experiment(
    panel: pd.DataFrame,
    kpi_name: str,
    event: EventWindow,
    entity_keys: list[str],
) -> tuple[MethodChoice, dict]:
    """Layer B routing: DiD when donors exist and trends are parallel, else ITS."""
    window = (event.window_start, event.window_end)
    considered: dict[str, str] = {}
    checks: dict[str, bool] = {}

    key = entity_keys[0] if entity_keys else None
    treated = event.entity.get(key) if key else None

    if key and treated:
        n_controls = panel[key].nunique() - 1
        checks["donor_slices_available"] = n_controls >= 2
        if n_controls >= 2:
            did = estimate_did(panel, kpi_name, key, treated, window)
            checks["parallel_pre_trends"] = did.parallel_trends_ok
            if did.valid:
                return (
                    MethodChoice(
                        chosen="did",
                        reason=(
                            f"{len(did.control_slices)} untreated {key} slices were available as "
                            f"controls and parallel pre-trends held "
                            f"(p={did.parallel_trends_p:.3f} > 0.05), so the "
                            f"treated-minus-control difference isolates the event"
                        ),
                        considered={
                            "its": "not needed; controls were available, and DiD nets out "
                                   "whatever else moved in the same period",
                            "dml": "reserved for the upstream mechanism, not the event effect",
                        },
                        preconditions_checked=checks,
                    ),
                    {"did": did.__dict__},
                )
            considered["did"] = did.reason
        else:
            considered["did"] = (
                f"only {n_controls} other {key} slices exist; an event this wide leaves "
                "no comparable untreated group"
            )
    else:
        considered["did"] = "panel has no entity dimension, so there is no untreated slice"

    its = estimate_its(panel, kpi_name, event.entity, window)
    checks["stable_pre_period"] = its.valid
    if its.valid:
        return (
            MethodChoice(
                chosen="its",
                reason=(
                    f"no usable control group, so the counterfactual comes from the series' own "
                    f"pre-period trend over {its.n_pre} periods. Weaker than DiD: anything else "
                    f"that changed at the same moment is absorbed into this estimate"
                ),
                considered=considered,
                preconditions_checked=checks,
            ),
            {"its": its.__dict__},
        )

    considered["its"] = its.reason
    return (
        MethodChoice(
            chosen="none",
            reason="no causal design was supportable; only the exact algebraic split is reported",
            considered=considered,
            preconditions_checked=checks,
        ),
        {},
    )


def _upstream_mechanism(
    raw: pd.DataFrame,
    graph: CausalGraph,
    contract: KpiContract,
    kpi_name: str,
    blocked: set[str],
    event: EventWindow,
    date_column: str = "Date",
) -> tuple[list[Contribution], dict]:
    """Layer B: estimate what moved the KPI's non-deterministic upstream measure.

    Two things make this an attribution rather than a loose regression:

    * The sample is restricted to the entity slice the event occurred in. A
      mechanism fitted across all regions answers a different question from the
      one being asked about this one.
    * The estimated coefficient dY/dT is multiplied by the treatment's actual
      movement during the window. A coefficient says how the outcome responds; only
      coefficient x observed change says how much of *this* event it accounts for,
      and only that is comparable with the exact contributions from Layer A.
    """
    kpi = contract.kpi(kpi_name)
    measure_columns = {m.column for m in kpi.measures.values()}

    # The interesting upstream node is a KPI input that is itself an outcome
    # rather than a lever; a lever's movement needs no further explanation here.
    targets = [
        c for c in measure_columns
        if (node := graph.node_for_column(c)) and not graph.is_controllable(node)
    ]
    if not targets:
        return [], {"skipped": "every measure of this KPI is a directly controllable lever"}

    outcome_col = targets[0]
    outcome_node = graph.node_for_column(outcome_col)

    # Restrict to the slice the event happened in.
    scoped = raw
    for key, val in event.entity.items():
        if key in scoped.columns:
            scoped = scoped[scoped[key].astype(str) == str(val)]
    if len(scoped) < 60:
        return [], {
            "skipped": f"only {len(scoped)} rows in slice {event.entity}; too few to estimate "
                       f"a mechanism for '{outcome_col}'"
        }

    direct = [
        col for p in graph.parents(outcome_node, "causal")
        if (col := graph.column_for(p)) and col in scoped.columns and col not in blocked
    ]
    if not direct:
        return [], {"skipped": f"'{outcome_node}' has no estimable causal parents in the graph"}

    candidates = [
        col for d in graph.upstream_causal_drivers(outcome_node)
        if (col := graph.column_for(d)) and col in scoped.columns
        and col not in blocked and col not in measure_columns
    ]

    # Treatment movement during the window, versus the preceding baseline.
    dates = pd.to_datetime(scoped[date_column])
    start, end = pd.Timestamp(event.window_start), pd.Timestamp(event.window_end)
    in_window = scoped[(dates >= start) & (dates <= end)]
    baseline = scoped[(dates < start) & (dates >= start - pd.Timedelta(days=BASELINE_DAYS))]

    contributions: list[Contribution] = []
    diagnostics: dict[str, object] = {
        "outcome": outcome_col,
        "estimator": "dml_cross_fitted",
        "slice_rows": int(len(scoped)),
        "note": "contribution = estimated dOutcome/dTreatment x observed treatment change "
                "in this window; units are those of the upstream outcome, not the KPI",
    }

    for treatment in direct:
        confounders = [c for c in candidates if c != treatment][:6]
        result = estimate_dml(scoped, outcome_col, treatment, confounders)

        t_pre = float(baseline[treatment].mean()) if len(baseline) else float("nan")
        t_post = float(in_window[treatment].mean()) if len(in_window) else float("nan")
        delta_t = t_post - t_pre

        diagnostics[f"dml::{treatment}"] = {
            "coefficient": result.effect,
            "se": result.std_error,
            "p": result.p_value,
            "n": result.n_obs,
            "confounders": result.confounders,
            "treatment_pre": t_pre,
            "treatment_post": t_post,
            "treatment_delta": delta_t,
            "valid": result.valid,
            "reason": result.reason,
        }
        if not result.valid or result.effect is None or not np.isfinite(delta_t):
            continue

        # A coefficient statistically indistinguishable from zero attributes nothing.
        if result.p_value is not None and result.p_value > 0.10:
            diagnostics[f"dml::{treatment}"]["dropped"] = (
                f"coefficient not distinguishable from zero (p={result.p_value:.3f}); "
                "no contribution attributed"
            )
            continue

        node = graph.node_for_column(treatment)
        contributions.append(
            Contribution(
                driver=f"{treatment} -> {outcome_col}",
                contribution=float(result.effect * delta_t),
                share=float("nan"),
                method="dml",
                exact=False,
                std_error=abs(result.std_error * delta_t) if result.std_error else None,
                ci_low=float(min(result.ci_low * delta_t, result.ci_high * delta_t)),
                ci_high=float(max(result.ci_low * delta_t, result.ci_high * delta_t)),
                controllable=graph.is_controllable(node) if node else False,
                owner=graph.owner(node) if node else None,
            )
        )
    return contributions, diagnostics


def route_and_attribute(
    event: EventWindow,
    kpi_name: str,
    panel: pd.DataFrame,
    measures: pd.DataFrame,
    raw: pd.DataFrame,
    contract: KpiContract,
    graph: CausalGraph,
    entity_keys: list[str],
    blocked_columns: set[str],
    date_column: str = "Date",
) -> Attribution:
    """Full attribution for one KPI within one event window."""
    window = (event.window_start, event.window_end)

    algebraic, alg_diag, total_delta, explained = _algebraic_contributions(
        kpi_name, contract, graph, measures, event.entity, window
    )
    method_choice, quasi_diag = _choose_quasi_experiment(panel, kpi_name, event, entity_keys)
    upstream, up_diag = _upstream_mechanism(
        raw, graph, contract, kpi_name, blocked_columns, event, date_column
    )

    quasi_contributions: list[Contribution] = []
    if method_choice.chosen == "did" and "did" in quasi_diag:
        d = quasi_diag["did"]
        quasi_contributions.append(
            Contribution(
                driver=f"event effect on {kpi_name} (vs untreated {entity_keys[0]} slices)",
                contribution=float(d["effect"]),
                share=float(d["effect"] / total_delta) if total_delta else float("nan"),
                method="did",
                exact=False,
                std_error=d["std_error"],
                ci_low=d["ci_low"],
                ci_high=d["ci_high"],
            )
        )
    elif method_choice.chosen == "its" and "its" in quasi_diag:
        i = quasi_diag["its"]
        quasi_contributions.append(
            Contribution(
                driver=f"event effect on {kpi_name} (vs own pre-period trend)",
                contribution=float(i["level_change"]),
                share=float(i["level_change"] / total_delta) if total_delta else float("nan"),
                method="its",
                exact=False,
                std_error=i["std_error"],
                ci_low=i["ci_low"],
                ci_high=i["ci_high"],
            )
        )

    return Attribution(
        event_id=event.event_id,
        kpi=kpi_name,
        total_delta=total_delta,
        explained_delta=explained,
        residual_delta=(total_delta - explained) if None not in (total_delta, explained) else None,
        method_choice=method_choice,
        contributions=algebraic + quasi_contributions + upstream,
        diagnostics={
            "algebraic": alg_diag,
            "quasi_experiment": {k: _jsonable(v) for k, v in quasi_diag.items()},
            "upstream": up_diag,
        },
    )


def _jsonable(obj: object) -> object:
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.floating, np.integer)):
        return obj.item()
    if isinstance(obj, (dt.date, dt.datetime)):
        return obj.isoformat()
    return obj
