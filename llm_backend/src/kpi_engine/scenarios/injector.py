"""Stamps known movements onto a base dataset and records what it did.

Why this exists: the supplied dataset is statistically flat -- monthly means of
every KPI are constant across all 24 months, lag-1 autocorrelation of daily
revenue is 0.01, and there is no weekday effect. Run detectors on it and they
surface tail noise; run causal estimators and they recover approximately zero.
Neither module could be shown to work, or shown to be calibrated.

So we manufacture movements whose cause we know exactly. The ground-truth
manifest this writes is what `cli.evaluate` scores the pipeline against.

Injection respects the accounting identities found by the profiler: raising
COGS also lowers Total Gross Profit. Without that the mutated data would be
internally contradictory and the algebraic decomposition would be attributing
movements in a dataset that could not exist.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np
import pandas as pd

from kpi_engine.contracts.configs import InjectedEvent, ScenarioSpec
from kpi_engine.contracts.payloads import DataProfile


def _shape_weights(shape: str, n: int) -> np.ndarray:
    """Intensity profile across the window, in [0, 1].

    step  - full effect for the whole window (a repricing that stays)
    ramp  - linear onset (a gradual rollout)
    spike - single-period peak (a one-day outage)
    decay - full effect that fades (a shock that is corrected)
    """
    if n <= 0:
        return np.array([])
    if shape == "step":
        return np.ones(n)
    if shape == "ramp":
        return np.linspace(1.0 / n, 1.0, n)
    if shape == "spike":
        w = np.zeros(n)
        w[n // 2] = 1.0
        return w
    if shape == "decay":
        return np.exp(-np.arange(n) / max(n / 3.0, 1.0))
    raise ValueError(f"Unknown shape '{shape}'")


def _assign(df: pd.DataFrame, column: str, values: pd.Series, idx: pd.Index | None = None) -> None:
    """Write values back respecting the column's dtype.

    Integer columns here are physical counts -- customers, transactions, units.
    Scaling one produces fractions, so it is rounded back to a whole number
    rather than silently widening the column to float; a fractional customer
    would be a data-quality defect the pipeline would then have to explain.
    pandas 3 refuses the implicit upcast outright, which surfaces the choice.
    """
    if pd.api.types.is_integer_dtype(df[column].dtype):
        rounded = np.rint(values).astype("int64")
        if idx is None:
            df[column] = rounded
        else:
            df.loc[idx, column] = rounded
        return
    if idx is None:
        df[column] = values.astype("float64")
    else:
        df[column] = df[column].astype("float64")
        df.loc[idx, column] = values.astype("float64")


def _row_mask(df: pd.DataFrame, event: InjectedEvent, date_column: str) -> pd.Series:
    """Rows inside the event window and matching every dimension filter."""
    dates = pd.to_datetime(df[date_column])
    mask = (dates >= pd.Timestamp(event.window.start)) & (dates <= pd.Timestamp(event.window.end))
    for col, allowed in event.filters.items():
        if col not in df.columns:
            raise KeyError(f"Event '{event.event_id}' filters on absent column '{col}'")
        mask &= df[col].astype(str).isin([str(a) for a in allowed])
    return mask


def propagate_identities(
    df: pd.DataFrame, mutated: set[str], profile: DataProfile
) -> tuple[pd.DataFrame, list[str]]:
    """Restore the dataset's exact relationships after a mutation.

    Duplicate columns are re-synced to whichever member was changed. For an
    identity group, the redundant member the profiler singled out is recomputed
    from the others, so the equation still holds exactly.
    """
    out = df
    notes: list[str] = []

    for group in profile.duplicate_groups:
        touched = [c for c in group if c in mutated]
        if not touched:
            continue
        src = touched[0]
        for col in group:
            if col != src and col in out.columns:
                _assign(out, col, out[src].astype("float64"))
                notes.append(f"{col} := {src} (duplicate sync)")

    # Restore each identity group that a mutation disturbed. The member recomputed
    # must be one that was *not* mutated -- otherwise the change just gets overwritten
    # and the equation stays broken. Preference order: the redundant column the
    # profiler already designated, then any non-duplicate-representative member.
    # Relying on the designated column alone breaks whenever it is the one mutated.
    dup_representatives = {group[0] for group in profile.duplicate_groups}
    handled: set[frozenset[str]] = set()

    for ident in profile.identities:
        members = frozenset([ident.target, *ident.components])
        if members in handled or not (members & mutated) or members <= mutated:
            continue
        if any(c not in out.columns for c in members):
            continue

        candidates = [
            f for f in profile.identities
            if frozenset([f.target, *f.components]) == members
            and f.target not in mutated
            and all(c in out.columns for c in f.components)
        ]
        if not candidates:
            continue
        chosen = (
            next((f for f in candidates if f.target in profile.redundant_columns), None)
            or next((f for f in candidates if f.target not in dup_representatives), None)
            or candidates[0]
        )

        recomputed = sum(
            coef * out[col] for coef, col in zip(chosen.coefficients, chosen.components)
        )
        _assign(out, chosen.target, recomputed.astype("float64"))
        terms = " ".join(
            f"{'+' if c > 0 else '-'}{n}" for c, n in zip(chosen.coefficients, chosen.components)
        )
        notes.append(f"{chosen.target} := {terms} (identity restored)")
        mutated = mutated | {chosen.target}
        handled.add(members)

    return out, notes


def _apply_event(
    df: pd.DataFrame, event: InjectedEvent, date_column: str, profile: DataProfile | None
) -> dict[str, Any]:
    """Mutate `df` in place for one event; return what was done, for the manifest."""
    mask = _row_mask(df, event, date_column)
    n_rows = int(mask.sum())
    if n_rows == 0:
        raise ValueError(
            f"Event '{event.event_id}' matched no rows. Check its window and filters."
        )

    idx = df.index[mask]
    dates = pd.to_datetime(df.loc[idx, date_column])
    span = (event.window.end - event.window.start).days + 1
    weights_by_day = _shape_weights(event.shape, span)
    day_offset = (dates - pd.Timestamp(event.window.start)).dt.days.to_numpy()
    weights = weights_by_day[np.clip(day_offset, 0, span - 1)]

    changes: dict[str, dict[str, float]] = {}
    for column, op in event.target_columns.items():
        if column not in df.columns:
            raise KeyError(f"Event '{event.event_id}' targets absent column '{column}'")
        before = df.loc[idx, column].astype("float64")
        if op.op == "multiply":
            after = before * (1.0 + (op.value - 1.0) * weights)
        elif op.op == "add":
            after = before + op.value * weights
        else:  # set
            after = before * (1.0 - weights) + op.value * weights
        _assign(df, column, after, idx)
        after = df.loc[idx, column].astype("float64")  # post-rounding truth
        changes[column] = {
            "op": op.op,
            "value": op.value,
            "mean_before": float(before.mean()),
            "mean_after": float(after.mean()),
            "mean_pct_change": float((after.sum() / before.sum() - 1.0) * 100) if before.sum() else float("nan"),
        }

    notes: list[str] = []
    if event.propagate and profile is not None:
        df, notes = propagate_identities(df, set(event.target_columns), profile)

    return {
        "event_id": event.event_id,
        "description": event.description,
        "window": {"start": str(event.window.start), "end": str(event.window.end)},
        "shape": event.shape,
        "filters": event.filters,
        "rows_affected": n_rows,
        "column_changes": changes,
        "propagation": notes,
        "affected_kpis": event.affected_kpis,
        "true_drivers": event.true_drivers,
    }


def inject_scenario(
    df: pd.DataFrame,
    scenario: ScenarioSpec,
    date_column: str = "Date",
    profile: DataProfile | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Apply every event in the scenario. Returns the mutated frame and the truth manifest."""
    np.random.seed(scenario.seed)
    out = df.copy()
    records = [_apply_event(out, ev, date_column, profile) for ev in scenario.events]

    manifest = {
        "scenario_id": scenario.scenario_id,
        "description": scenario.description,
        "base_source_id": scenario.base_source_id,
        "generated_at": dt.datetime.now().isoformat(),
        "seed": scenario.seed,
        "n_rows": int(len(out)),
        "events": records,
    }
    return out, manifest
