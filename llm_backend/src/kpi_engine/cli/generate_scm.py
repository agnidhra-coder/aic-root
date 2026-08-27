"""Stage 0c: derive the weekly supply-chain source from the sales file.

    python -m kpi_engine.cli.generate_scm --company acme-retail

Writes the company's data/generated/scm_weekly_v1.csv plus a ground-truth manifest
naming the planted supplier disruption and the sales-side KPIs it should explain.
"""

from __future__ import annotations

import argparse
import datetime as dt

import pandas as pd

from kpi_engine.cli._common import banner, kv, open_from_args
from kpi_engine.tenancy import add_company_argument
from kpi_engine.scenarios.scm_generator import generate_scm_panel, write_scm


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_company_argument(parser)
    parser.add_argument("--sales", default="data/generated/ad_cost_shock_v1.csv",
                        help="Company-relative path to the sales file to derive from.")
    parser.add_argument("--out", default="data/generated/scm_weekly_v1.csv")
    parser.add_argument("--manifest", default="data/generated/scm_ground_truth.json")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--date-column", default="Date")
    parser.add_argument("--entity-columns", nargs="*", default=["Region", "Product category"])
    # Defaults align with EV-COGS-002 in the company's scenario config: the
    # supply-side cause and the sales-side effect must share a window or the
    # cross-source link has nothing to align.
    parser.add_argument("--disruption-start", default="2026-08-10")
    parser.add_argument("--disruption-end", default="2026-09-07")
    parser.add_argument("--shape", default="ramp", choices=["step", "ramp", "spike", "decay"])
    args = parser.parse_args(argv)

    paths = open_from_args(args)
    sales = pd.read_csv(paths.resolve(args.sales))
    frame, manifest = generate_scm_panel(
        sales,
        date_column=args.date_column,
        entity_columns=args.entity_columns,
        seed=args.seed,
        disruption_start=dt.date.fromisoformat(args.disruption_start),
        disruption_end=dt.date.fromisoformat(args.disruption_end),
        disruption_shape=args.shape,
    )
    write_scm(paths.resolve(args.out), paths.resolve(args.manifest), frame, manifest)

    banner("SUPPLY-CHAIN SOURCE GENERATED")
    kv("derived from", args.sales)
    kv("rows", len(frame))
    kv("weeks", frame["Week Start"].nunique())
    kv("suppliers", ", ".join(manifest["suppliers"]))
    kv("entity columns", ", ".join(args.entity_columns))
    for event in manifest["events"]:
        print(f"\n  {event['event_id']}  {event['window']['start']} -> {event['window']['end']}")
        kv("supplier", ", ".join(event["filters"]["Supplier"]), indent=4)
        kv("rows affected", event["rows_affected"], indent=4)
        for col, ch in event["column_changes"].items():
            kv(col, f"{ch['mean_before']:.3f} -> {ch['mean_after']:.3f}", indent=6)
        kv("cross-source effect", event["cross_source_effect"]["via"], indent=4)

    print(f"\n  written -> {paths.resolve(args.out)}")
    print(f"  written -> {paths.resolve(args.manifest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
