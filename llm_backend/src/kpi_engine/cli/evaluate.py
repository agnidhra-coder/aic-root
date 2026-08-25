"""Stage 4: score the pipeline against the injected ground truth.

Without this the thresholds in configs/detection/default.yaml would be guesses.
Detection is scored on whether real events were found and how much of the noise
was suppressed; attribution is scored on whether the true driver came out on top.

    python -m kpi_engine.cli.evaluate --run-id d-reg-wk
"""

from __future__ import annotations

import argparse
import datetime as dt

from kpi_engine.cli._common import banner, kv, latest_run_dir, resolve, run_dir
from kpi_engine.config_io import read_json, write_json
from kpi_engine.contracts.payloads import EventWindow


def _parse_date(text: str) -> dt.date:
    return dt.date.fromisoformat(text)


def _expand_truth(truth: dict, entity_values: dict[str, set[str]]) -> list[dict]:
    """One expected detection per affected entity slice.

    A system-wide event has no filters, so at region grain it should surface once
    per region; scoring it as a single expected detection would let a pipeline
    that found one region out of five look perfect.
    """
    expected: list[dict] = []
    for event in truth["events"]:
        filters = event.get("filters") or {}
        if not entity_values:
            expected.append({**event, "_entity": {}})
            continue
        key = next(iter(entity_values))
        values = filters.get(key) or sorted(entity_values[key])
        for value in values:
            expected.append({**event, "_entity": {key: str(value)}})
    return expected


def _overlaps(a_start: dt.date, a_end: dt.date, b_start: dt.date, b_end: dt.date) -> int:
    """Days of overlap between two closed intervals."""
    latest_start = max(a_start, b_start)
    earliest_end = min(a_end, b_end)
    return max(0, (earliest_end - latest_start).days + 1)


def _matches(detected: EventWindow, expected: dict, tolerance_days: int = 7) -> bool:
    """A detection matches if it hits the right slice, overlaps in time, and names a true KPI."""
    if expected["_entity"] and detected.entity != expected["_entity"]:
        return False
    true_start = _parse_date(expected["window"]["start"])
    true_end = _parse_date(expected["window"]["end"])
    overlap = _overlaps(
        detected.window_start, detected.window_end,
        true_start - dt.timedelta(days=tolerance_days),
        true_end + dt.timedelta(days=tolerance_days),
    )
    if overlap <= 0:
        return False
    affected = set(expected.get("affected_kpis") or [])
    return not affected or bool(affected & set(detected.primary_kpis_affected))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--truth", default="data/generated/ground_truth.json")
    parser.add_argument("--tolerance-days", type=int, default=7)
    args = parser.parse_args(argv)

    out = run_dir(args.run_id) if args.run_id else latest_run_dir()
    truth = read_json(resolve(args.truth))
    events = [EventWindow.model_validate(e) for e in read_json(out / "events.json")]

    entity_values: dict[str, set[str]] = {}
    for event in events:
        for key, val in event.entity.items():
            entity_values.setdefault(key, set()).add(val)

    expected = _expand_truth(truth, entity_values)
    matched_detections: set[str] = set()
    per_truth: list[dict] = []

    for exp in expected:
        hits = [e for e in events if _matches(e, exp, args.tolerance_days)]
        matched_detections.update(e.event_id for e in hits)
        true_start = _parse_date(exp["window"]["start"])
        true_end = _parse_date(exp["window"]["end"])
        best = max(
            hits,
            key=lambda e: _overlaps(e.window_start, e.window_end, true_start, true_end),
            default=None,
        )
        overlap = _overlaps(best.window_start, best.window_end, true_start, true_end) if best else 0
        union = (
            (max(best.window_end, true_end) - min(best.window_start, true_start)).days + 1
            if best else (true_end - true_start).days + 1
        )
        per_truth.append({
            "event_id": exp["event_id"],
            "entity": exp["_entity"],
            "true_window": [str(true_start), str(true_end)],
            "detected": bool(hits),
            "n_detections": len(hits),
            "best_detection": best.event_id if best else None,
            "detected_window": [str(best.window_start), str(best.window_end)] if best else None,
            "overlap_days": overlap,
            "iou": round(overlap / union, 3) if union else 0.0,
            "true_drivers": exp.get("true_drivers", []),
        })

    n_expected = len(expected)
    n_found = sum(1 for r in per_truth if r["detected"])
    recall = n_found / n_expected if n_expected else 0.0
    precision = len(matched_detections) / len(events) if events else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    causal: list[dict] = []
    bundle_path = out / "evidence_bundles.json"
    if bundle_path.exists():
        truth_by_id = {e["event_id"]: e for e in truth["events"]}
        detected_to_truth = {
            r["best_detection"]: r["event_id"] for r in per_truth if r["best_detection"]
        }
        for bundle in read_json(bundle_path):
            truth_id = detected_to_truth.get(bundle["event_id"])
            if not truth_id:
                continue
            true_drivers = set(truth_by_id[truth_id].get("true_drivers", []))
            for attribution in bundle["attributions"]:
                exact = [c for c in attribution["contributions"] if c["exact"]]
                if not exact:
                    continue
                top = max(exact, key=lambda c: abs(c["contribution"]))
                causal.append({
                    "event_id": bundle["event_id"],
                    "matched_truth": truth_id,
                    "kpi": attribution["kpi"],
                    "top_driver": top["driver"],
                    "top_share": round(top["share"], 4),
                    "true_drivers": sorted(true_drivers),
                    "top_driver_is_true": top["driver"] in true_drivers,
                    "method": attribution["method_choice"]["chosen"],
                    "abstained": bundle["abstained"],
                    "confidence": bundle["confidence"]["score"],
                })

    report = {
        "run_id": out.name,
        "scenario": truth.get("scenario_id"),
        "detection": {
            "expected_detections": n_expected,
            "found": n_found,
            "total_events_reported": len(events),
            "true_positive_events": len(matched_detections),
            "false_positive_events": len(events) - len(matched_detections),
            "recall": round(recall, 4),
            "precision": round(precision, 4),
            "f1": round(f1, 4),
            "per_truth": per_truth,
        },
        "attribution": causal,
    }
    write_json(report, out / "evaluation.json")

    banner(f"EVALUATION vs GROUND TRUTH  ·  {out.name}")
    kv("scenario", truth.get("scenario_id"))
    kv("expected detections", n_expected)
    kv("found", n_found)
    kv("events reported", len(events))
    kv("true positives", len(matched_detections))
    kv("false positives", len(events) - len(matched_detections))
    kv("recall", f"{recall:.1%}")
    kv("precision", f"{precision:.1%}")
    kv("F1", f"{f1:.3f}")

    print("\n  per true event:")
    print(f"    {'truth':<16} {'entity':<20} {'found':<6} {'overlap':>8} {'IoU':>6}  detected window")
    for r in per_truth:
        ent = ", ".join(f"{k}={v}" for k, v in r["entity"].items()) or "(total)"
        window = " -> ".join(r["detected_window"]) if r["detected_window"] else "-"
        print(f"    {r['event_id']:<16} {ent:<20} {'yes' if r['detected'] else 'NO':<6} "
              f"{r['overlap_days']:>8} {r['iou']:>6.2f}  {window}")

    if causal:
        print("\n  attribution accuracy (top exact driver vs injected truth):")
        for c in causal:
            mark = "OK " if c["top_driver_is_true"] else "MISS"
            print(f"    [{mark}] {c['kpi']:<20} top={c['top_driver']:<26} "
                  f"share={c['top_share']:+.1%}  method={c['method']:<5} "
                  f"conf={c['confidence']:.2f}")
        hits = sum(1 for c in causal if c["top_driver_is_true"])
        kv("top-driver accuracy", f"{hits}/{len(causal)} = {hits/len(causal):.0%}")

    print(f"\n  written -> {out / 'evaluation.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
