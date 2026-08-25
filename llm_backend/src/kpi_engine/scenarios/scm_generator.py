"""Generate a supply-chain table that is coupled to the sales data, not parallel to it.

A second source is only worth having if a movement in one can be explained by the
other. So this does not invent an independent random table: `Units Received` is
derived from the sales file's `Units Sold` aggregated to region x category x week,
and `Purchase Price Per Unit` from its `COGS`. The two files describe the same
business from two systems.

The grain is deliberately different -- weekly here against daily there, with a
`Supplier` dimension the sales file does not carry. That is the "heterogeneous
sources, different grains and cadences" case the engine has to reconcile, and it
means the agent's cross-source linking has to align windows rather than join keys.

The planted disruption is aligned with the sales scenario's system-wide supplier
cost event, so the ground truth carries a *cross-source* cause: lead times and
fill rates degrade here, COGS and margin move there, and the DAG is what licenses
the connection between them.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

WEEK_ANCHOR = "W-MON"

# Two suppliers per region x category. One of them is the one that fails.
SUPPLIERS = ["Northwind Foods", "Kestrel Logistics", "Delta Sourcing"]
DISRUPTED_SUPPLIER = "Kestrel Logistics"

# Baselines chosen to sit in plausible retail ranges and to leave headroom for the
# disruption to be visible without saturating (a fill rate already at 0.99 cannot
# fall convincingly, and one at 0.5 makes every week look like a crisis).
BASE_FILL_RATE = 0.965
BASE_LEAD_TIME_DAYS = 11.0
BASE_DAYS_OF_SUPPLY = 21.0
BASE_STOCKOUT_DAYS = 0.35
FREIGHT_PER_UNIT = 1.85


def _shape_weights(shape: str, n: int) -> np.ndarray:
    """Intensity across the window, in [0, 1]. Mirrors `injector._shape_weights`."""
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


def _weekly_base(
    sales: pd.DataFrame, date_column: str, entity_columns: list[str]
) -> pd.DataFrame:
    """Aggregate the sales file to the weekly grain this source reports at."""
    df = sales.copy()
    dates = pd.to_datetime(df[date_column])
    # Period start, not period end: a week labelled by its Monday sorts and joins
    # the same way a daily date does, which keeps the windowing logic uniform.
    df["Week Start"] = dates.dt.to_period(WEEK_ANCHOR).dt.start_time.dt.date

    grouped = (
        df.groupby(["Week Start", *entity_columns], as_index=False)
        .agg({"Units Sold": "sum", "COGS": "sum"})
        .sort_values(["Week Start", *entity_columns])
        .reset_index(drop=True)
    )
    return grouped


def generate_scm_panel(
    sales: pd.DataFrame,
    *,
    date_column: str = "Date",
    entity_columns: list[str] | None = None,
    seed: int = 42,
    disruption_start: dt.date | None = None,
    disruption_end: dt.date | None = None,
    disruption_shape: str = "ramp",
    n_suppliers: int = 2,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build the weekly supply-chain table and the manifest describing what was planted."""
    entity_columns = entity_columns or ["Region", "Product category"]
    rng = np.random.default_rng(seed)

    base = _weekly_base(sales, date_column, entity_columns)
    suppliers = SUPPLIERS[:n_suppliers]

    # Fan each region x category x week across its suppliers. The split is fixed per
    # (region, category) rather than redrawn weekly: a supplier's share of a category
    # is a commercial arrangement, not weekly noise, and a share that jitters every
    # week would make the disrupted supplier's footprint impossible to attribute.
    share_key = base[entity_columns].drop_duplicates().reset_index(drop=True)
    shares: dict[tuple, np.ndarray] = {}
    for row in share_key.itertuples(index=False):
        key = tuple(row)
        raw = rng.dirichlet(np.full(len(suppliers), 6.0))
        shares[key] = raw

    rows: list[dict[str, Any]] = []
    for rec in base.to_dict("records"):
        key = tuple(rec[c] for c in entity_columns)
        split = shares[key]
        for supplier, share in zip(suppliers, split):
            rows.append(
                {
                    "Week Start": rec["Week Start"],
                    **{c: rec[c] for c in entity_columns},
                    "Supplier": supplier,
                    "_units_sold": rec["Units Sold"] * share,
                    "_cogs": rec["COGS"] * share,
                }
            )

    df = pd.DataFrame(rows)
    n = len(df)

    # --- Undisturbed operating state -------------------------------------------
    fill_rate = np.clip(BASE_FILL_RATE + rng.normal(0.0, 0.018, n), 0.60, 0.999)
    lead_time = np.clip(BASE_LEAD_TIME_DAYS + rng.normal(0.0, 1.4, n), 2.0, 60.0)
    days_of_supply = np.clip(BASE_DAYS_OF_SUPPLY + rng.normal(0.0, 2.6, n), 3.0, 90.0)
    stockout = np.clip(rng.gamma(1.2, BASE_STOCKOUT_DAYS, n), 0.0, 7.0)
    unit_price = np.where(
        df["_units_sold"].to_numpy() > 0,
        df["_cogs"].to_numpy() / np.maximum(df["_units_sold"].to_numpy(), 1e-9),
        0.0,
    )

    # --- The planted disruption -------------------------------------------------
    manifest_event: dict[str, Any] | None = None
    if disruption_start and disruption_end:
        weeks = pd.to_datetime(df["Week Start"])
        start = pd.Timestamp(disruption_start).to_period(WEEK_ANCHOR).start_time
        end = pd.Timestamp(disruption_end).to_period(WEEK_ANCHOR).start_time
        in_window = (weeks >= start) & (weeks <= end)
        mask = (in_window & (df["Supplier"] == DISRUPTED_SUPPLIER)).to_numpy()

        span_weeks = int(((end - start).days // 7) + 1)
        weights_by_week = _shape_weights(disruption_shape, span_weeks)
        offset = ((weeks - start).dt.days // 7).to_numpy()
        w = np.zeros(n)
        w[mask] = weights_by_week[np.clip(offset[mask], 0, span_weeks - 1)]

        before = {
            "Supplier Fill Rate": float(fill_rate[mask].mean()) if mask.any() else float("nan"),
            "Lead Time Days": float(lead_time[mask].mean()) if mask.any() else float("nan"),
            "Stockout Days": float(stockout[mask].mean()) if mask.any() else float("nan"),
            "Purchase Price Per Unit": float(unit_price[mask].mean()) if mask.any() else float("nan"),
        }

        fill_rate = fill_rate * (1.0 - 0.28 * w)
        lead_time = lead_time * (1.0 + 0.85 * w)
        stockout = stockout + 3.1 * w
        unit_price = unit_price * (1.0 + 0.12 * w)
        days_of_supply = days_of_supply * (1.0 - 0.30 * w)

        fill_rate = np.clip(fill_rate, 0.30, 0.999)
        stockout = np.clip(stockout, 0.0, 7.0)

        manifest_event = {
            "event_id": "EV-SCM-001",
            "description": (
                "Supplier disruption at "
                f"{DISRUPTED_SUPPLIER}: fill rate falls, lead times stretch, stockout "
                "days rise, and the unit purchase price is renegotiated upward. This is "
                "the supply-side cause of the system-wide COGS increase in the sales file."
            ),
            "window": {"start": str(disruption_start), "end": str(disruption_end)},
            "shape": disruption_shape,
            "filters": {"Supplier": [DISRUPTED_SUPPLIER]},
            "rows_affected": int(mask.sum()),
            "affected_kpis": ["Fill Rate", "Weighted Lead Time", "Stockout Rate", "Days Of Supply"],
            "true_drivers": [
                "Supplier Fill Rate", "Lead Time Days", "Stockout Days", "Purchase Price Per Unit",
            ],
            "cross_source_effect": {
                "source_id": "retail_daily",
                "kpis": ["Net Profit Margin", "Inventory Turnover"],
                "via": "Purchase Price Per Unit -> COGS",
            },
            "column_changes": {
                "Supplier Fill Rate": {"op": "multiply", "value": 0.72,
                                       "mean_before": before["Supplier Fill Rate"],
                                       "mean_after": float(fill_rate[mask].mean()) if mask.any() else float("nan")},
                "Lead Time Days": {"op": "multiply", "value": 1.85,
                                   "mean_before": before["Lead Time Days"],
                                   "mean_after": float(lead_time[mask].mean()) if mask.any() else float("nan")},
                "Stockout Days": {"op": "add", "value": 3.1,
                                  "mean_before": before["Stockout Days"],
                                  "mean_after": float(stockout[mask].mean()) if mask.any() else float("nan")},
                "Purchase Price Per Unit": {"op": "multiply", "value": 1.12,
                                            "mean_before": before["Purchase Price Per Unit"],
                                            "mean_after": float(unit_price[mask].mean()) if mask.any() else float("nan")},
            },
        }

    # --- Derive the reported columns so the identities hold exactly ------------
    # Units Received is what the sales file actually sold; orders are what it took to
    # get that, given the fill rate. Deriving orders from receipts (rather than the
    # reverse) is what keeps the two sources consistent -- receipts are the shared
    # quantity, and inventing them independently would put the files in contradiction.
    units_received = np.rint(np.maximum(df["_units_sold"].to_numpy(), 0.0)).astype("int64")
    units_ordered = np.rint(units_received / np.clip(fill_rate, 1e-6, None)).astype("int64")
    units_ordered = np.maximum(units_ordered, units_received)
    backorder = units_ordered - units_received
    on_hand = np.rint(units_received * days_of_supply / 7.0).astype("int64")
    on_hand = np.maximum(on_hand, 0)

    out = pd.DataFrame(
        {
            "Week Start": df["Week Start"],
            **{c: df[c] for c in entity_columns},
            "Supplier": df["Supplier"],
            "Units Ordered": units_ordered,
            "Units Received": units_received,
            "Backorder Units": backorder,
            "On Hand Units": on_hand,
            # Reported fill rate is recomputed from the integers actually shipped, not
            # carried over from the float that generated them -- otherwise the file
            # would state a rate its own quantities contradict.
            "Supplier Fill Rate": np.where(
                units_ordered > 0, units_received / np.maximum(units_ordered, 1), 1.0
            ).round(4),
            "Lead Time Days": np.round(lead_time, 2),
            "Stockout Days": np.round(stockout, 2),
            "Inbound Freight Cost": np.round(units_received * FREIGHT_PER_UNIT
                                             * (1.0 + rng.normal(0.0, 0.05, n)), 2),
            "Purchase Price Per Unit": np.round(unit_price, 4),
            # Pre-multiplied so the weighted lead-time KPI is a ratio of sums rather
            # than a mean of per-row ratios, which is the contract's whole point.
            "Lead Time Unit Days": np.round(lead_time * units_received, 2),
            # Constant by construction, and that is the point: it gives the stockout
            # rate a denominator that aggregates, so the KPI stays a ratio of sums
            # instead of needing a hard-coded 7 inside the expression.
            "Days In Period": np.full(n, 7, dtype="int64"),
        }
    ).sort_values(["Week Start", *entity_columns, "Supplier"]).reset_index(drop=True)

    manifest = {
        "scenario_id": "scm_supply_v1",
        "description": (
            "Weekly supplier performance derived from the sales file's unit volumes "
            "and cost of goods, carrying a planted supplier disruption whose sales-side "
            "consequence is the system-wide COGS event."
        ),
        "base_source_id": "scm_weekly",
        "derived_from": "retail_daily",
        "generated_at": dt.datetime.now().isoformat(),
        "seed": seed,
        "n_rows": len(out),
        "suppliers": suppliers,
        "events": [manifest_event] if manifest_event else [],
    }
    return out, manifest


def write_scm(
    out_csv: Path, manifest_path: Path, frame: pd.DataFrame, manifest: dict[str, Any]
) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out_csv, index=False)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2))
