"""Stage 0: profile a source.

Run before anything else. Its output tells the causal layer which columns are
redundant and tells the pipeline which grains are too sparse to model.

    python -m kpi_engine.cli.profile_source --company acme-retail
"""

from __future__ import annotations

import argparse

from kpi_engine.cli._common import banner, kv, open_from_args, selected_source
from kpi_engine.config_io import write_json
from kpi_engine.tenancy import add_company_argument
from kpi_engine.profiling import profile_source
from kpi_engine.sources import build_source


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_company_argument(parser)
    parser.add_argument("--source-id", default=None,
                        help="Which declared source to profile. Defaults to the primary.")
    parser.add_argument("--out", default=None, help="Output JSON path.")
    args = parser.parse_args(argv)

    paths = open_from_args(args)
    spec = paths.source_spec(selected_source(paths, args))
    source = build_source(spec, base_dir=paths.root)
    profile = profile_source(source)

    out = paths.resolve(args.out) if args.out else paths.profile_path(spec.source_id)
    write_json(profile, out)

    banner(f"DATA PROFILE  ·  {spec.source_id}")
    kv("rows", f"{profile.description.n_rows:,}")
    kv("columns", len(profile.description.columns))
    kv("date range max", profile.freshness.max_date)
    kv("freshness lag (days)", profile.freshness.lag_days)
    kv("stale", profile.freshness.is_stale)

    print("\n  Duplicate column groups (exact copies):")
    if profile.duplicate_groups:
        for group in profile.duplicate_groups:
            print(f"    {' == '.join(group)}")
    else:
        print("    none")

    print("\n  Linear identities (column reproducible from others):")
    if profile.identities:
        for ident in profile.identities:
            terms = " ".join(
                f"{'+' if c > 0 else '-'} {n}" for c, n in zip(ident.coefficients, ident.components)
            )
            print(f"    {ident.target} = {terms.lstrip('+ ')}   (max resid {ident.max_abs_residual:.2e})")
    else:
        print("    none")

    print("\n  Identity groups (k columns, one constraint -> drop exactly one):")
    for group in profile.identity_groups:
        print(f"    {{{', '.join(group)}}}")
    if not profile.identity_groups:
        print("    none")

    print(f"\n  Barred as independent causal drivers ({len(profile.redundant_columns)}):")
    for col, reason in sorted(profile.redundant_columns.items()):
        print(f"    {col:<24} {reason}")

    print("\n  Grain coverage:")
    print(f"    {'grain':<44} {'slices':>7} {'filled':>8} {'possible':>9} {'rows/cell':>10}  ok")
    for cov in profile.coverage:
        label = " x ".join(cov.keys) if cov.keys else "(total)"
        print(
            f"    {label:<44} {cov.n_slices:>7} {cov.filled_cells:>8} "
            f"{cov.possible_cells:>9} {cov.mean_rows_per_cell:>10.2f}  "
            f"{'yes' if cov.sufficient else 'NO'}"
        )

    print(f"\n  written -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
