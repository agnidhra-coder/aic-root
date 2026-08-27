"""Stage 0b: write a scenario-injected dataset plus its ground-truth manifest.

    python -m kpi_engine.cli.inject_scenario --company acme-retail \
        --scenario configs/scenarios/ad_cost_shock.yaml
"""

from __future__ import annotations

import argparse

import pandas as pd

from kpi_engine.cli._common import banner, kv, open_from_args, selected_source
from kpi_engine.config_io import read_json, write_json
from kpi_engine.tenancy import add_company_argument
from kpi_engine.contracts.payloads import DataProfile
from kpi_engine.profiling import profile_source
from kpi_engine.scenarios import inject_scenario
from kpi_engine.sources import build_source


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_company_argument(parser)
    parser.add_argument("--scenario", default="configs/scenarios/ad_cost_shock.yaml",
                        help="Company-relative path to the scenario YAML.")
    parser.add_argument("--source-id", default=None,
                        help="Which declared source to inject into. Defaults to the primary.")
    parser.add_argument("--out-dir", default="data/generated",
                        help="Company-relative output directory.")
    args = parser.parse_args(argv)

    paths = open_from_args(args)
    scenario = paths.scenario(args.scenario)
    # The base file, not the injected one: `source_spec` folds in the binding's
    # dataset override, which for the demo company already points at the output
    # of a previous run of this command.
    source_id = selected_source(paths, args)
    spec = paths.base_source_spec(source_id)
    source = build_source(spec, base_dir=paths.root)
    df = source.load()

    # Identity propagation needs the profile; reuse a cached one when present.
    profile_path = paths.profile_path(spec.source_id)
    if profile_path.exists():
        profile = DataProfile.model_validate(read_json(profile_path))
    else:
        profile = profile_source(source, df)
        write_json(profile, profile_path)

    injected, manifest = inject_scenario(df, scenario, spec.date_column, profile)

    out_dir = paths.resolve(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data_path = out_dir / f"{scenario.scenario_id}.csv"
    truth_path = out_dir / "ground_truth.json"

    to_write = injected.copy()
    to_write[spec.date_column] = pd.to_datetime(to_write[spec.date_column]).dt.strftime("%Y-%m-%d")
    to_write.to_csv(data_path, index=False)
    # Company-relative, not project-relative. A project-relative string baked into
    # tenant data would make the folder unmovable and would lie the moment a
    # company is copied to a new slug.
    manifest["dataset_path"] = str(data_path.relative_to(paths.root))
    manifest["company_id"] = paths.slug
    write_json(manifest, truth_path)

    banner(f"SCENARIO INJECTED  ·  {scenario.scenario_id}")
    kv("base rows", f"{len(df):,}")
    for rec in manifest["events"]:
        print(f"\n  {rec['event_id']}  [{rec['shape']}]  "
              f"{rec['window']['start']} -> {rec['window']['end']}")
        kv("filters", rec["filters"] or "(all rows)", indent=4)
        kv("rows affected", f"{rec['rows_affected']:,}", indent=4)
        for col, ch in rec["column_changes"].items():
            kv(f"{col}", f"{ch['op']} {ch['value']}  "
                         f"mean {ch['mean_before']:,.2f} -> {ch['mean_after']:,.2f} "
                         f"({ch['mean_pct_change']:+.1f}%)", indent=6)
        for note in rec["propagation"]:
            kv("propagated", note, indent=6)

    print(f"\n  dataset -> {data_path}")
    print(f"  truth   -> {truth_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
