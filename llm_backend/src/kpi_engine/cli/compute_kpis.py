"""Stage 1: aggregate raw rows to the contract grain and evaluate every KPI.

    python -m kpi_engine.cli.compute_kpis --entity-keys Region
"""

from __future__ import annotations

import argparse


from kpi_engine.cli._common import (
    add_common_args,
    apply_overrides,
    banner,
    kv,
    new_run_id,
    resolve,
    run_dir,
)
from kpi_engine.config_io import load_contract, load_source
from kpi_engine.semantics import build_panel
from kpi_engine.sources import build_source


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--dataset", default=None, help="Override the source config's path.")
    args = parser.parse_args(argv)

    spec = load_source(resolve(args.source))
    if args.dataset:
        spec = spec.model_copy(update={"path": str(resolve(args.dataset))})
    contract = apply_overrides(load_contract(resolve(args.contract)), args)
    source = build_source(spec, base_dir=resolve("."))
    df = source.load()

    panel = build_panel(
        df, contract, spec.date_column, kpis=args.kpis, entity_keys=args.entity_keys
    )

    run_id = args.run_id or new_run_id("kpis")
    out = run_dir(run_id)
    panel.values.to_parquet(out / "kpi_panel.parquet", index=False)

    banner(f"KPI PANEL  ·  {contract.contract_id}")
    kv("dataset", spec.path)
    kv("time grain", contract.time_grain)
    kv("entity keys", panel.entity_keys or "(total)")
    kv("raw rows", f"{len(df):,}")
    kv("panel cells", f"{len(panel.values):,}")

    print(f"\n    {'kpi':<20} {'periods':>8} {'valid':>7} {'mean':>12} {'std':>10} "
          f"{'min':>10} {'max':>10} {'support':>8}")
    for name in panel.kpi_names():
        s = panel.series(name)
        v = s["value"].dropna()
        print(f"    {name:<20} {s['period'].nunique():>8} {len(v):>7} {v.mean():>12.3f} "
              f"{v.std():>10.3f} {v.min():>10.3f} {v.max():>10.3f} "
              f"{s['_support'].mean():>8.1f}")

    dropped = int(panel.values["value"].isna().sum())
    if dropped:
        print(f"\n  {dropped:,} cells blanked by contract guards "
              f"(support below min_rows_per_cell, or zero denominator)")
    print(f"\n  written -> {out / 'kpi_panel.parquet'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
