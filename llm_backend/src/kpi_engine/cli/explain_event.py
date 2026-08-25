"""Stage 3: attribute a detected event to its drivers and emit an evidence bundle.

    python -m kpi_engine.cli.explain_event --run-id d-reg-wk --event-id EV-West-...
    python -m kpi_engine.cli.explain_event --run-id d-reg-wk --top 3
"""

from __future__ import annotations

import argparse

import pandas as pd

from kpi_engine.causal.dag import CausalGraph
from kpi_engine.causal.router import route_and_attribute
from kpi_engine.cli._common import (
    add_common_args,
    apply_overrides,
    banner,
    kv,
    latest_run_dir,
    resolve,
    run_dir,
)
from kpi_engine.config_io import (
    load_contract,
    load_detection,
    load_graph,
    load_source,
    read_json,
    write_json,
)
from kpi_engine.contracts.payloads import DataProfile, EventWindow
from kpi_engine.evidence import Telemetry, build_evidence_bundle, score_confidence
from kpi_engine.semantics import build_panel
from kpi_engine.sources import build_source

BASELINE_DAYS = 84


def _pre_period_count(panel: pd.DataFrame, event: EventWindow) -> int:
    df = panel.copy()
    for key, val in event.entity.items():
        df = df[df[key] == val]
    periods = pd.to_datetime(df["period"])
    return int(periods[periods < pd.Timestamp(event.window_start)].nunique())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--detection", default="configs/detection/default.yaml")
    parser.add_argument("--graph", default="configs/causal/retail_dag.yaml")
    parser.add_argument("--run-id", default=None, help="Detection run to read; defaults to latest.")
    parser.add_argument("--event-id", default=None, help="Explain one event.")
    parser.add_argument("--top", type=int, default=3, help="Explain the N highest-scoring events.")
    parser.add_argument("--dataset", default=None)
    args = parser.parse_args(argv)

    out = run_dir(args.run_id) if args.run_id else latest_run_dir()
    events = [EventWindow.model_validate(e) for e in read_json(out / "events.json")]
    if not events:
        print(f"No events in {out}. Nothing to explain.")
        return 0

    spec = load_source(resolve(args.source))
    if args.dataset:
        spec = spec.model_copy(update={"path": str(resolve(args.dataset))})
    contract = apply_overrides(load_contract(resolve(args.contract)), args)
    detection = load_detection(resolve(args.detection))
    graph = CausalGraph(load_graph(resolve(args.graph)))

    telemetry = Telemetry(
        run_id=out.name,
        dataset=spec.path,
        configs={"contract": args.contract, "graph": args.graph, "detection": args.detection},
    )

    source = build_source(spec, base_dir=resolve("."))
    with telemetry.stage("load_source") as box:
        raw = source.load()
        box["rows_out"] = len(raw)

    entity_keys = sorted({k for e in events for k in e.entity})
    with telemetry.stage("build_panel", rows_in=len(raw)) as box:
        panel = build_panel(raw, contract, spec.date_column, args.kpis, entity_keys)
        box["rows_out"] = len(panel.values)

    profile_path = resolve(f"outputs/profiles/{spec.source_id}.json")
    profile = DataProfile.model_validate(read_json(profile_path)) if profile_path.exists() else None
    blocked = profile.blocked_driver_columns() if profile else set()
    freshness = source.freshness()

    selected = (
        [e for e in events if e.event_id == args.event_id]
        if args.event_id else events[: args.top]
    )
    if not selected:
        print(f"Event '{args.event_id}' not found in {out / 'events.json'}")
        return 1

    bundles = []
    for event in selected:
        with telemetry.stage(f"attribute::{event.event_id}") as box:
            attributions = [
                route_and_attribute(
                    event, kpi, panel.values, panel.measures[kpi], raw,
                    contract, graph, entity_keys, blocked, spec.date_column,
                )
                for kpi in event.primary_kpis_affected
                if kpi in panel.measures
            ]
            box["rows_out"] = len(attributions)

        n_pre = _pre_period_count(panel.values, event)
        confidence = score_confidence(event, attributions, n_pre)
        bundles.append(
            build_evidence_bundle(
                event, attributions, confidence, freshness,
                detection.confidence_threshold, n_pre,
            )
        )

    write_json([b.model_dump(mode="json") for b in bundles], out / "evidence_bundles.json")
    manifest = telemetry.finish()
    write_json(manifest, out / "run_manifest.json")

    for bundle in bundles:
        _report(bundle)

    banner("RUNTIME TELEMETRY")
    for line in telemetry.summary_lines():
        print(f"  {line}")
    kv("total", f"{manifest.total_duration_ms:,.1f} ms")
    kv("LLM calls / tokens / cost", f"{manifest.llm_calls} / "
                                    f"{manifest.llm_tokens_in + manifest.llm_tokens_out} / "
                                    f"${manifest.llm_cost_usd:.4f}")
    print("    (every number above was produced by deterministic code or a named estimator)")
    print(f"\n  written -> {out / 'evidence_bundles.json'}")
    return 0


def _report(bundle) -> None:
    event = bundle.event
    banner(f"EVIDENCE  ·  {event.event_id}")
    kv("window", f"{event.window_start} -> {event.window_end}")
    kv("entity", event.entity or "(total)")
    kv("anomaly types", ", ".join(event.anomaly_types))
    kv("detectors", ", ".join(event.detectors))
    kv("confidence", f"{bundle.confidence.score:.3f}"
                     f"   [snr {bundle.confidence.signal_to_noise}"
                     f"  support {bundle.confidence.support_factor}"
                     f"  history {bundle.confidence.history_factor}"
                     f"  precision {bundle.confidence.estimator_precision}"
                     f"  agreement {bundle.confidence.method_agreement}]")

    if bundle.abstained:
        a = bundle.abstention
        print(f"\n  *** ABSTAINED · {a.reason_code} ***")
        print(f"    {a.message}")
        print(f"    missing: {'; '.join(a.missing_evidence)}")
        print(f"    resolve by: {'; '.join(a.what_would_resolve_it)}")

    for attribution in bundle.attributions:
        print(f"\n  KPI: {attribution.kpi}")
        if attribution.total_delta is not None:
            kv("movement vs baseline", f"{attribution.total_delta:+.4f}", indent=4)
        mc = attribution.method_choice
        kv("causal method", mc.chosen, indent=4)
        print(f"      why: {mc.reason}")
        for name, why in mc.considered.items():
            print(f"      rejected {name}: {why}")
        if mc.preconditions_checked:
            kv("preconditions", ", ".join(f"{k}={v}" for k, v in mc.preconditions_checked.items()),
               indent=4)

        exact = [c for c in attribution.contributions if c.exact]
        if exact:
            print("\n      EXACT decomposition (algebra, no model assumptions):")
            for c in exact:
                lever = "lever" if c.controllable else "context"
                owner = f" · {c.owner}" if c.owner else ""
                print(f"        {c.driver:<28} {c.contribution:+12.4f}  "
                      f"{c.share:+7.1%}  [{lever}{owner}]")
        estimated = [c for c in attribution.contributions if not c.exact]
        if estimated:
            print("\n      ESTIMATED (model-based, assumptions stated above):")
            for c in estimated:
                ci = (f"  95% CI [{c.ci_low:+.4f}, {c.ci_high:+.4f}]"
                      if c.ci_low is not None else "")
                print(f"        {c.driver:<28} {c.contribution:+12.4f}  ({c.method}){ci}")

    print("\n  deterministic methods used:")
    for method in bundle.deterministic_methods:
        print(f"    - {method}")
    print(f"  LLM-produced fields: {bundle.llm_methods or 'none'}")


if __name__ == "__main__":
    raise SystemExit(main())
