"""What the planner is allowed to know about the data before it plans.

The catalog is metadata only: KPI names, dimension values, date coverage, grain
sufficiency, freshness, and the graph's node names. No rows, no aggregates, no
KPI values. The planner's job is to choose a configuration, and a configuration
can be chosen from the schema alone -- letting it see data here would mean its
choice of grain and slice could be tuned to make a particular answer appear,
which is precisely the failure mode the whole design is arranged against.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from kpi_engine.causal.dag import CausalGraph
from kpi_engine.contracts.configs import KpiContract, SourceSpec
from kpi_engine.contracts.payloads import DataProfile

# Distinct values are listed so the planner can map "the West" onto `Region=West`.
# Beyond this many, listing them would be a data dump rather than a vocabulary.
MAX_ENTITY_VALUES = 40


def build_catalog(
    sources: list[tuple[SourceSpec, KpiContract, DataProfile, pd.DataFrame]],
    graph: CausalGraph,
    min_train_periods: int | None = None,
) -> dict[str, Any]:
    entries = []
    for spec, contract, profile, df in sources:
        entries.append(_source_entry(spec, contract, profile, df))

    return {
        "sources": entries,
        "causal_graph": _graph_entry(graph),
        "grain_guidance": (
            "Grain is squeezed from both sides. Below, by noise: a slice with few "
            "rows per cell produces ratios too noisy to model, and the engine will "
            "abstain rather than guess. Above, by history: the detector trains its "
            "baseline on min_train_periods PERIODS before it emits any expectation, "
            "so a grain coarse enough that the whole date_range holds fewer than "
            "that yields no findings at all. Divide each source's date_range by the "
            "grain and check it clears min_train_periods; take the coarsest grain "
            "that does, and the fewest entity keys that still answer the question."
        ),
        "min_train_periods": min_train_periods,
    }


def _source_entry(
    spec: SourceSpec, contract: KpiContract, profile: DataProfile, df: pd.DataFrame
) -> dict[str, Any]:
    dates = pd.to_datetime(df[spec.date_column], errors="coerce").dropna()
    entity_values: dict[str, list[str]] = {}
    for col in spec.entity_columns:
        if col not in df.columns:
            continue
        values = sorted(str(v) for v in df[col].dropna().unique())
        entity_values[col] = (
            values if len(values) <= MAX_ENTITY_VALUES
            else values[:MAX_ENTITY_VALUES] + [f"... and {len(values) - MAX_ENTITY_VALUES} more"]
        )

    return {
        "source_id": spec.source_id,
        "contract_id": contract.contract_id,
        "native_grain": contract.time_grain,
        "refresh_cadence": spec.refresh_cadence,
        "date_column": spec.date_column,
        "n_rows": len(df),
        "date_range": {
            "start": str(dates.min().date()) if len(dates) else None,
            "end": str(dates.max().date()) if len(dates) else None,
        },
        "freshness": {
            "as_of": str(profile.freshness.as_of),
            "max_date": str(profile.freshness.max_date) if profile.freshness.max_date else None,
            "lag_days": profile.freshness.lag_days,
            "is_stale": profile.freshness.is_stale,
        },
        "entity_columns": entity_values,
        "kpis": [
            {
                "name": k.name,
                "category": k.category,
                "description": k.description,
                "unit": k.unit,
                "direction": k.direction,
                "expression": k.expression,
                "measures": {a: m.column for a, m in k.measures.items()},
                "materiality_pct": k.materiality.min_abs_pct,
                "min_history_periods": k.guards.min_history_periods,
            }
            for k in contract.kpis
        ],
        # One entry per candidate slicing. `sufficient` is the honest signal about
        # how deep the planner may go: a slice averaging ~1.2 rows per cell cannot
        # support a modelled ratio, whatever the question asks for.
        "grain_coverage": [
            {
                "keys": cov.keys,
                "n_slices": cov.n_slices,
                "mean_rows_per_cell": round(cov.mean_rows_per_cell, 2),
                "filled_cells": cov.filled_cells,
                "possible_cells": cov.possible_cells,
                "sufficient": cov.sufficient,
            }
            for cov in profile.coverage
        ],
        # Columns bound by an exact accounting identity cannot act as independent
        # drivers; saying so here stops the planner proposing an analysis whose
        # attribution step would refuse to use half its inputs.
        "redundant_columns": profile.redundant_columns,
        "restricted_columns": contract.restricted_columns,
    }


def _graph_entry(graph: CausalGraph) -> dict[str, Any]:
    return {
        "graph_id": graph.spec.graph_id,
        "note": (
            "Hand-declared, not discovered. Only relationships listed here may be "
            "offered as explanations; a movement with no declared path to a driver "
            "is reported as unexplained, never as a guess."
        ),
        "controllable_levers": [
            {"name": n.name, "owner": n.owner, "column": n.column}
            for n in graph.spec.nodes
            if n.controllable
        ],
        "context_nodes": [n.name for n in graph.spec.nodes if not n.controllable],
        "cross_source_edges": [
            {"from": e.source, "to": e.target, "relation": e.relation, "note": e.note}
            for e in graph.spec.edges
            if e.relation == "causal"
        ],
    }
