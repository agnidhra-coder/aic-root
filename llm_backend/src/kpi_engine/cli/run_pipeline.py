"""Runs the whole deterministic pipeline end to end.

    python -m kpi_engine.cli.run_pipeline --entity-keys Region --time-grain week \
        --dataset data/generated/ad_cost_shock_v1.csv --evaluate
"""

from __future__ import annotations

import argparse

from kpi_engine.cli import (
    detect_anomalies,
    evaluate,
    explain_event,
    profile_series,
    profile_source,
)
from kpi_engine.cli._common import banner, new_run_id, resolve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="configs/sources/retail_csv.yaml")
    parser.add_argument("--contract", default="configs/semantics/retail_kpis.yaml")
    parser.add_argument("--detection", default="configs/detection/default.yaml")
    parser.add_argument("--eda", default="configs/eda/default.yaml")
    parser.add_argument("--graph", default="configs/causal/retail_dag.yaml")
    parser.add_argument("--dataset", default=None)
    parser.add_argument("--entity-keys", nargs="*", default=None)
    parser.add_argument("--time-grain", default=None, choices=["day", "week", "month"])
    parser.add_argument("--kpis", nargs="*", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--top", type=int, default=3)
    parser.add_argument("--skip-profile", action="store_true")
    parser.add_argument("--skip-eda", action="store_true")
    parser.add_argument("--evaluate", action="store_true", help="Score against ground truth.")
    parser.add_argument("--truth", default="data/generated/ground_truth.json")
    args = parser.parse_args(argv)

    run_id = args.run_id or new_run_id("pipeline")

    def shared() -> list[str]:
        out = ["--source", args.source, "--contract", args.contract, "--run-id", run_id]
        if args.dataset:
            out += ["--dataset", args.dataset]
        if args.entity_keys:
            out += ["--entity-keys", *args.entity_keys]
        if args.time_grain:
            out += ["--time-grain", args.time_grain]
        if args.kpis:
            out += ["--kpis", *args.kpis]
        return out

    if not args.skip_profile:
        banner("STAGE 0 · PROFILE SOURCE")
        profile_source.main(["--source", args.source])

    if not args.skip_eda:
        banner("STAGE 1b · SERIES PROFILE (descriptive)")
        profile_series.main(shared() + ["--eda", args.eda])

    banner("STAGE 1-2 · KPI PANEL + ANOMALY DETECTION")
    detect_anomalies.main(shared() + ["--detection", args.detection])

    banner("STAGE 3 · CAUSAL ATTRIBUTION + EVIDENCE")
    explain_event.main(shared() + ["--detection", args.detection, "--graph", args.graph,
                                   "--top", str(args.top)])

    if args.evaluate and resolve(args.truth).exists():
        banner("STAGE 4 · EVALUATION")
        evaluate.main(["--run-id", run_id, "--truth", args.truth])

    print(f"\n\n  Pipeline complete. Artefacts in outputs/{run_id}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
