"""Stage 2: detect anomalies and cluster them into event windows.

    python -m kpi_engine.cli.detect_anomalies --company acme-retail --entity-keys Region
"""

from __future__ import annotations

import argparse
from dataclasses import asdict

from kpi_engine.cli._common import (
    add_common_args,
    apply_overrides,
    banner,
    kv,
    new_run_id,
    open_from_args,
    selected_source,
)
from kpi_engine.config_io import load_detection, read_json, write_json
from kpi_engine.contracts.payloads import DataProfile
from kpi_engine.detection.runner import run_detection
from kpi_engine.detection.windowing import build_event_windows
from kpi_engine.profiling import profile_source
from kpi_engine.semantics import build_panel
from kpi_engine.sources import build_source


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--detection", default=None,
                        help="Override the company's detection config.")
    parser.add_argument("--dataset", default=None, help="Override the source config's path.")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)

    paths = open_from_args(args)
    source_id = selected_source(paths, args)
    spec = paths.source_spec(source_id)
    if args.dataset:
        spec = spec.model_copy(update={"path": str(paths.resolve(args.dataset))})
    contract = apply_overrides(paths.contract(source_id), args)
    detection = (
        load_detection(paths.resolve(args.detection)) if args.detection else paths.detection()
    )

    source = build_source(spec, base_dir=paths.root)
    df = source.load()
    panel = build_panel(df, contract, spec.date_column, args.kpis, args.entity_keys)

    profile_path = paths.profile_path(spec.source_id)
    if profile_path.exists():
        profile = DataProfile.model_validate(read_json(profile_path))
    else:
        profile = profile_source(source, df)
        write_json(profile, profile_path)

    result = run_detection(panel, detection, contract)
    numeric = [c for c in df.columns if df[c].dtype.kind in "if"]
    events = build_event_windows(
        result.flags, panel.values, contract, detection.windowing, profile,
        spec.source_id, spec.path, numeric,
    )

    run_id = args.run_id or new_run_id("detect")
    out = paths.run_dir(run_id)
    panel.values.to_parquet(out / "kpi_panel.parquet", index=False)
    write_json([f.model_dump(mode="json") for f in result.flags], out / "flags.json")
    write_json([e.model_dump(mode="json") for e in events], out / "events.json")
    write_json([asdict(d) for d in result.diagnostics], out / "series_diagnostics.json")

    banner(f"ANOMALY DETECTION  ·  {run_id}")
    kv("dataset", spec.path)
    kv("entity keys", panel.entity_keys or "(total)")
    kv("series analysed", len([d for d in result.diagnostics if d.skipped is None]))
    kv("series skipped (history)", len([d for d in result.diagnostics if d.skipped]))
    kv("flags", len(result.flags))
    kv("event windows", len(events))

    by_detector: dict[str, int] = {}
    for f in result.flags:
        by_detector[f.detector] = by_detector.get(f.detector, 0) + 1
    print("\n  flags by detector:")
    for name, count in sorted(by_detector.items(), key=lambda x: -x[1]):
        kv(name, count, indent=4)

    applied = [d for d in result.diagnostics if d.decomposition_applied]
    if applied:
        mean_seasonal = sum(d.seasonal_strength for d in applied) / len(applied)
        print(f"\n  mean seasonal strength across series: {mean_seasonal:.4f}")
        if mean_seasonal < 0.05:
            print("    -> effectively no weekly seasonality in this data; STL is removing "
                  "almost nothing, and detections are not seasonal artefacts.")

    print(f"\n  top event windows ({min(len(events), 10)} of {len(events)}):")
    for ev in events[:10]:
        moves = ", ".join(
            f"{d.kpi} {d.pct_delta:+.1f}%" for d in ev.observed_deviations
            if d.pct_delta is not None and d.material
        ) or "no material KPI move"
        print(f"\n    {ev.event_id}")
        kv("window", f"{ev.window_start} -> {ev.window_end}", indent=6)
        kv("entity", ev.entity or "(total)", indent=6)
        kv("types", ", ".join(ev.anomaly_types), indent=6)
        kv("detectors", ", ".join(ev.detectors), indent=6)
        kv("material moves", moves, indent=6)
        kv("peak score / flags", f"{ev.peak_score:.2f} / {ev.n_flags}", indent=6)

    print(f"\n  written -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
