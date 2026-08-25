"""Stage 1b: describe what every KPI series is actually doing.

Detection (stage 2) answers "is this point unusual?". This stage answers the
question that comes before it -- trend, seasonality, phases, and how far the
series can be trusted at all. It emits no flags and nothing downstream branches
on it, so it cannot affect detection; it exists to give a later narration layer
the baseline context an EvidenceBundle alone does not carry.

    python -m kpi_engine.cli.profile_series --entity-keys Region --time-grain week
"""

from __future__ import annotations

import argparse
from collections import Counter

from kpi_engine.cli._common import (
    DEFAULT_EDA,
    add_common_args,
    apply_overrides,
    banner,
    kv,
    new_run_id,
    resolve,
    run_dir,
)
from kpi_engine.config_io import load_contract, load_eda, load_source, write_json
from kpi_engine.eda import profile_panel
from kpi_engine.semantics import build_panel
from kpi_engine.sources import build_source


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--eda", default=DEFAULT_EDA)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--dataset", default=None, help="Override the source config's path.")
    parser.add_argument(
        "--top", type=int, default=10, help="How many strongest trends to print."
    )
    args = parser.parse_args(argv)

    spec = load_source(resolve(args.source))
    if args.dataset:
        spec = spec.model_copy(update={"path": str(resolve(args.dataset))})
    contract = apply_overrides(load_contract(resolve(args.contract)), args)
    eda_spec = load_eda(resolve(args.eda))

    source = build_source(spec, base_dir=resolve("."))
    df = source.load()
    panel = build_panel(
        df, contract, spec.date_column, kpis=args.kpis, entity_keys=args.entity_keys
    )

    profiles = profile_panel(panel, eda_spec, contract, spec.source_id, spec.path)

    run_id = args.run_id or new_run_id("eda")
    out = run_dir(run_id)
    write_json([p.model_dump(mode="json") for p in profiles], out / "series_profiles.json")

    _report(profiles, contract, spec, args.top, out)
    return 0


def _report(profiles, contract, spec, top: int, out) -> None:
    banner(f"SERIES PROFILE  ·  {contract.contract_id}")
    kv("dataset", spec.path)
    kv("time grain", contract.time_grain)
    kv("series profiled", len(profiles))

    usable = [p for p in profiles if p.usable]
    kv("usable", f"{len(usable)} of {len(profiles)}")

    if not profiles:
        print("\n  no series to profile")
        return

    directions = Counter(p.trend.direction for p in usable)
    kv("directions", ", ".join(f"{k}={v}" for k, v in directions.most_common()) or "(none usable)")

    seasonal = sum(1 for p in usable if p.seasonality.detected)
    kv("with seasonality", f"{seasonal} of {len(usable)}")
    if usable:
        kv("mean trend strength", f"{sum(p.trend.trend_strength for p in usable)/len(usable):.3f}")
        kv(
            "mean seasonal strength",
            f"{sum(p.seasonality.strength for p in usable)/len(usable):.3f}",
        )
        kv("mean segments", f"{sum(len(p.segments) for p in usable)/len(usable):.1f}")

    moving = [p for p in usable if p.trend.direction in ("rising", "falling")]
    moving.sort(key=lambda p: abs(p.trend.slope_pct_per_period or 0), reverse=True)

    if moving:
        print(f"\n  strongest trends ({min(top, len(moving))} of {len(moving)} moving series):")
        print(f"    {'kpi':<20} {'entity':<18} {'dir':<8} {'%/period':>10} "
              f"{'total %':>9} {'p':>8} {'segs':>5}")
        for p in moving[:top]:
            ent = ", ".join(p.entity.values()) or "(total)"
            print(f"    {p.kpi:<20} {ent:<18} {p.trend.direction:<8} "
                  f"{p.trend.slope_pct_per_period or 0:>+10.2f} "
                  f"{p.trend.total_change_pct or 0:>+9.1f} {p.trend.p_value:>8.4f} "
                  f"{len(p.segments):>5}")
    else:
        print("\n  no series shows a significant, material trend -- "
              "flat is the honest description of this data")

    unusable = [p for p in profiles if not p.usable]
    if unusable:
        print(f"\n  {len(unusable)} series not characterised (insufficient history or support)")
        for p in unusable[:3]:
            ent = ", ".join(p.entity.values()) or "(total)"
            print(f"    {p.kpi} ({ent}): {p.unusable_reason}")

    print(f"\n  written -> {out / 'series_profiles.json'}")


if __name__ == "__main__":
    raise SystemExit(main())
