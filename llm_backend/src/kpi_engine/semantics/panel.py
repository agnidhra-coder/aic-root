"""Builds the KPI panel: raw rows -> aggregated measures -> KPI values.

The ordering matters and is the whole point of this module. Measures are summed
to the target grain *first*, then the KPI expression is applied to those sums.
Computing a ratio per raw row and averaging it instead ("mean of ratios") gives
a different -- and wrong -- answer whenever the denominator varies across rows,
which it always does here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from kpi_engine.contracts.configs import KpiContract, KpiDef, TimeGrain
from kpi_engine.semantics.expressions import evaluate

SUPPORT_COL = "_support"
PERIOD_COL = "period"


def floor_to_grain(dates: pd.Series, grain: TimeGrain) -> pd.Series:
    """Snap timestamps to the start of their period."""
    dt = pd.to_datetime(dates)
    if grain == "day":
        return dt.dt.normalize()
    if grain == "week":
        return dt.dt.to_period("W").dt.start_time
    if grain == "month":
        return dt.dt.to_period("M").dt.start_time
    raise ValueError(f"Unsupported time grain: {grain}")


@dataclass
class KpiPanel:
    """Tidy long panel of KPI values plus the aggregated measures behind them.

    `values` columns: period, *entity_keys, kpi, value, _support
    `measures` columns: period, *entity_keys, <measure alias per KPI>, _support
    """

    values: pd.DataFrame
    measures: dict[str, pd.DataFrame]
    contract: KpiContract
    entity_keys: list[str] = field(default_factory=list)

    def series(self, kpi: str, entity: dict[str, str] | None = None) -> pd.DataFrame:
        """One KPI's time series for one entity slice, sorted by period."""
        df = self.values[self.values["kpi"] == kpi]
        for key, val in (entity or {}).items():
            df = df[df[key] == val]
        return df.sort_values(PERIOD_COL).reset_index(drop=True)

    def entities(self) -> list[dict[str, str]]:
        """Distinct entity slices present in the panel."""
        if not self.entity_keys:
            return [{}]
        uniq = self.values[self.entity_keys].drop_duplicates()
        return [{k: str(r[k]) for k in self.entity_keys} for _, r in uniq.iterrows()]

    def kpi_names(self) -> list[str]:
        return list(self.values["kpi"].unique())


def _aggregate_measures(
    df: pd.DataFrame, kpi: KpiDef, group_cols: list[str]
) -> pd.DataFrame:
    """Aggregate this KPI's base measures to the grain, carrying a row-count support column."""
    agg_map: dict[str, list[str]] = {}
    for alias, m in kpi.measures.items():
        if m.column not in df.columns:
            raise KeyError(f"KPI '{kpi.name}' needs column '{m.column}', absent from the dataset")
        agg_map.setdefault(m.column, []).append(m.agg)

    grouped = df.groupby(group_cols, dropna=False, observed=True)
    frames = [grouped.size().rename(SUPPORT_COL)]
    for column, aggs in agg_map.items():
        for agg in set(aggs):
            frames.append(grouped[column].agg(agg).rename(f"{column}::{agg}"))
    out = pd.concat(frames, axis=1).reset_index()

    # Map back to the aliases the expression refers to.
    for alias, m in kpi.measures.items():
        out[alias] = out[f"{m.column}::{m.agg}"]
    return out


def _apply_guards(values: pd.Series, support: pd.Series, kpi: KpiDef) -> pd.Series:
    """Blank out cells the contract says we cannot trust, rather than reporting them."""
    out = values.astype("float64").copy()
    out[~np.isfinite(out)] = np.nan
    out[support < kpi.guards.min_rows_per_cell] = np.nan
    if kpi.guards.clip_quantiles:
        lo_q, hi_q = kpi.guards.clip_quantiles
        finite = out[out.notna()]
        if len(finite) > 10:
            out = out.clip(finite.quantile(lo_q), finite.quantile(hi_q))
    return out


def build_panel(
    df: pd.DataFrame,
    contract: KpiContract,
    date_column: str = "Date",
    kpis: list[str] | None = None,
    entity_keys: list[str] | None = None,
) -> KpiPanel:
    """Aggregate raw rows to the contract grain and evaluate every KPI expression.

    `entity_keys` overrides the contract's own keys, which is how the CLI runs the
    same contract at total, per-region, or per-region-channel-category grain.
    """
    keys = contract.entity_keys if entity_keys is None else entity_keys
    selected = kpis or [k.name for k in contract.kpis]

    work = df.copy()
    work[PERIOD_COL] = floor_to_grain(work[date_column], contract.time_grain)
    group_cols = [PERIOD_COL] + keys

    value_frames: list[pd.DataFrame] = []
    measure_frames: dict[str, pd.DataFrame] = {}

    for name in selected:
        kpi = contract.kpi(name)
        agg = _aggregate_measures(work, kpi, group_cols)
        env = {alias: agg[alias] for alias in kpi.measures}
        raw = evaluate(kpi.expression, env)
        value = _apply_guards(pd.Series(raw, index=agg.index), agg[SUPPORT_COL], kpi)

        frame = agg[group_cols + [SUPPORT_COL]].copy()
        frame["kpi"] = name
        frame["value"] = value
        value_frames.append(frame)
        measure_frames[name] = agg[group_cols + list(kpi.measures) + [SUPPORT_COL]].copy()

    values = pd.concat(value_frames, ignore_index=True) if value_frames else pd.DataFrame(
        columns=group_cols + [SUPPORT_COL, "kpi", "value"]
    )
    values = values.sort_values(group_cols + ["kpi"]).reset_index(drop=True)
    for key in keys:
        values[key] = values[key].astype(str)

    return KpiPanel(values=values, measures=measure_frames, contract=contract, entity_keys=keys)
