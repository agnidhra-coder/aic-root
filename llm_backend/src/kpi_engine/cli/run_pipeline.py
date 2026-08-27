"""Runs the whole deterministic pipeline end to end.

    python -m kpi_engine.cli.run_pipeline --company acme-retail \
        --entity-keys Region --time-grain week --evaluate
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
from kpi_engine.cli._common import banner, new_run_id, open_from_args
from kpi_engine.tenancy import add_company_argument


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_company_argument(parser)
    parser.add_argument("--source-id", default=None,
                        help="Which declared source to run. Defaults to the primary.")
    parser.add_argument("--detection", default=None)
    parser.add_argument("--eda", default=None)
    parser.add_argument("--graph", default=None)
    parser.add_argument("--dataset", default=None)
    parser.add_argument("--entity-keys", nargs="*", default=None)
    parser.add_argument("--time-grain", default=None, choices=["day", "week", "month"])
    parser.add_argument("--kpis", nargs="*", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--top", type=int, default=3)
    parser.add_argument("--skip-profile", action="store_true")
    parser.add_argument("--skip-eda", action="store_true")
    parser.add_argument("--evaluate", action="store_true", help="Score against ground truth.")
    parser.add_argument("--truth", default=None)
    args = parser.parse_args(argv)

    paths = open_from_args(args)
    run_id = args.run_id or new_run_id("pipeline")

    def shared() -> list[str]:
        out = ["--company", args.company, "--run-id", run_id]
        if args.source_id:
            out += ["--source-id", args.source_id]
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
        profile_source.main(
            ["--company", args.company]
            + (["--source-id", args.source_id] if args.source_id else [])
        )

    if not args.skip_eda:
        banner("STAGE 1b · SERIES PROFILE (descriptive)")
        profile_series.main(shared() + (["--eda", args.eda] if args.eda else []))

    banner("STAGE 1-2 · KPI PANEL + ANOMALY DETECTION")
    detect_anomalies.main(
        shared() + (["--detection", args.detection] if args.detection else [])
    )

    banner("STAGE 3 · CAUSAL ATTRIBUTION + EVIDENCE")
    explain_event.main(
        shared()
        + (["--detection", args.detection] if args.detection else [])
        + (["--graph", args.graph] if args.graph else [])
        + ["--top", str(args.top)]
    )

    truth = (
        paths.resolve(args.truth) if args.truth
        else paths.generated_dir / "ground_truth.json"
    )
    if args.evaluate and truth.exists():
        banner("STAGE 4 · EVALUATION")
        evaluate.main(["--company", args.company, "--run-id", run_id, "--truth", str(truth)])

    print(f"\n\n  Pipeline complete. Artefacts in {paths.run_dir(run_id, create=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
