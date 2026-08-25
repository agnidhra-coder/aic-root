"""In-process orchestration of the full stage chain.

`cli/run_pipeline.py` chains the stages by rebuilding argv lists and calling each
CLI's `main()`; every intermediate result travels through JSON on disk. That is
fine for a terminal but useless to a caller that already holds the loaded configs
and wants the objects back -- which is exactly what the agent layer needs.

Every stage core is already pure and silent, so this module is wiring, not
analytics. It writes the same artefacts to `outputs/<run_id>/` as the CLIs do, so
a run driven from here stays inspectable by the same tooling.

No LLM is involved here, and none ever should be. This is the deterministic side
of the boundary; `kpi_agent` is the other side.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

from kpi_engine.causal.dag import CausalGraph
from kpi_engine.config_io import project_root, read_json, write_json
from kpi_engine.contracts.configs import DetectionSpec, EdaSpec, KpiContract, SourceSpec
from kpi_engine.contracts.payloads import (
    DataProfile,
    EventWindow,
    EvidenceBundle,
    Flag,
    Freshness,
    RunManifest,
    SeriesProfile,
)
from kpi_engine.detection.runner import run_detection
from kpi_engine.detection.windowing import build_event_windows
from kpi_engine.evidence import Telemetry, build_evidence_bundle, score_confidence
from kpi_engine.profiling import profile_source
from kpi_engine.semantics import build_panel
from kpi_engine.semantics.panel import KpiPanel
from kpi_engine.sources import build_source

# Detection deliberately does NOT make its STL period grain-aware, though
# `eda/runner.py` does for its own decomposition. Setting period=4 at weekly grain
# was tried and measured: flags fell 96 -> 73, events 11 -> 8, and recall against
# the injected ground truth fell from 100% to 83.3%. A 4-period cycle at weekly
# grain lets STL absorb a month-long shock as "seasonality" -- it removes exactly
# the signal stage 2 exists to find. The EDA stage describes and can afford the
# cleaner decomposition; detection cannot. Leave the period as the YAML sets it.


@dataclass
class PipelineResult:
    """Everything the chain produced, in memory and on disk."""

    run_id: str
    out_dir: Path
    spec: SourceSpec
    contract: KpiContract
    profile: DataProfile
    freshness: Freshness
    raw: pd.DataFrame
    panel: KpiPanel
    series_profiles: list[SeriesProfile] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    events: list[EventWindow] = field(default_factory=list)
    bundles: list[EvidenceBundle] = field(default_factory=list)
    manifest: RunManifest | None = None

    def bundle_for(self, event_id: str) -> EvidenceBundle | None:
        return next((b for b in self.bundles if b.event_id == event_id), None)


def resolve_path(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else project_root() / p


def run_dir(run_id: str, create: bool = True) -> Path:
    d = project_root() / "outputs" / run_id
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def load_or_build_profile(source, df: pd.DataFrame, source_id: str) -> DataProfile:
    """Reuse the cached profile the CLIs write, or compute and cache it."""
    path = project_root() / "outputs" / "profiles" / f"{source_id}.json"
    if path.exists():
        return DataProfile.model_validate(read_json(path))
    profile = profile_source(source, df)
    write_json(profile, path)
    return profile


def pre_period_count(panel: pd.DataFrame, event: EventWindow) -> int:
    """Distinct periods strictly before the window, within the event's slice."""
    df = panel
    for key, val in event.entity.items():
        df = df[df[key] == val]
    periods = pd.to_datetime(df["period"])
    return int(periods[periods < pd.Timestamp(event.window_start)].nunique())


def run_pipeline(
    spec: SourceSpec,
    contract: KpiContract,
    detection: DetectionSpec,
    *,
    run_id: str,
    graph: CausalGraph | None = None,
    eda: EdaSpec | None = None,
    dataset: str | None = None,
    kpis: list[str] | None = None,
    entity_keys: list[str] | None = None,
    time_grain: str | None = None,
    date_range: tuple[dt.date, dt.date] | None = None,
    report_window: tuple[dt.date, dt.date] | None = None,
    top_events: int | None = None,
    telemetry: Telemetry | None = None,
    write_artefacts: bool = True,
) -> PipelineResult:
    """Profile -> panel -> [series profile] -> detect -> attribute, in one process.

    `graph` is required to attribute; without it the run stops after detection and
    `bundles` comes back empty. `eda` is optional -- the series profiles describe
    and nothing downstream reads them, so skipping them changes no other output.

    **`date_range` and `report_window` are not the same thing, and the difference
    matters.** `date_range` restricts what is *loaded*, which also starves the
    baseline: ask for one quarter at monthly grain and the detector gets three
    periods against a 28-period history requirement, finds nothing, and says so --
    correctly, and uselessly. `report_window` loads everything, detects over the
    full history, and filters the *events* at the end. For "what happened last
    quarter" the second is almost always what was meant; `date_range` is for
    callers who genuinely cannot afford to read the rest.
    """
    if dataset:
        spec = spec.model_copy(update={"path": str(resolve_path(dataset))})
    if time_grain:
        contract = contract.model_copy(update={"time_grain": time_grain})

    out = run_dir(run_id) if write_artefacts else run_dir(run_id, create=False)
    telemetry = telemetry or Telemetry(run_id=run_id, dataset=spec.path)

    source = build_source(spec, base_dir=project_root())
    with telemetry.stage("load_source") as box:
        raw = source.load(date_range=date_range)
        box["rows_out"] = len(raw)

    with telemetry.stage("profile_source", rows_in=len(raw)) as box:
        profile = load_or_build_profile(source, raw, spec.source_id)
        box["rows_out"] = len(profile.columns)

    with telemetry.stage("build_panel", rows_in=len(raw)) as box:
        panel = build_panel(raw, contract, spec.date_column, kpis, entity_keys)
        box["rows_out"] = len(panel.values)

    result = PipelineResult(
        run_id=run_id, out_dir=out, spec=spec, contract=contract, profile=profile,
        freshness=source.freshness(), raw=raw, panel=panel,
    )

    if eda is not None:
        with telemetry.stage("profile_series", rows_in=len(panel.values)) as box:
            result.series_profiles = profile_panel_safe(panel, eda, contract, spec)
            box["rows_out"] = len(result.series_profiles)

    with telemetry.stage("detect", rows_in=len(panel.values)) as box:
        detected = run_detection(panel, detection, contract)
        box["rows_out"] = len(detected.flags)
    result.flags = detected.flags

    numeric = [c for c in raw.columns if raw[c].dtype.kind in "if"]
    with telemetry.stage("windowing", rows_in=len(detected.flags)) as box:
        result.events = build_event_windows(
            detected.flags, panel.values, contract, detection.windowing, profile,
            spec.source_id, spec.path, numeric,
        )
        if report_window:
            # Overlap, not containment: an event that began before the window and
            # is still running inside it is precisely what someone asking about
            # that window wants to know.
            start, end = report_window
            result.events = [
                e for e in result.events
                if e.window_start <= end and e.window_end >= start
            ]
        box["rows_out"] = len(result.events)

    if graph is not None and result.events:
        blocked = profile.blocked_driver_columns()
        selected = result.events[:top_events] if top_events else result.events
        panel_entity_keys = panel.entity_keys
        for event in selected:
            with telemetry.stage(f"attribute::{event.event_id}") as box:
                attributions = [
                    route_and_attribute_safe(
                        event, kpi, panel, raw, contract, graph,
                        panel_entity_keys, blocked, spec.date_column,
                    )
                    for kpi in event.primary_kpis_affected
                    if kpi in panel.measures
                ]
                attributions = [a for a in attributions if a is not None]
                box["rows_out"] = len(attributions)

            n_pre = pre_period_count(panel.values, event)
            confidence = score_confidence(event, attributions, n_pre)
            result.bundles.append(
                build_evidence_bundle(
                    event, attributions, confidence, result.freshness,
                    detection.confidence_threshold, n_pre,
                )
            )

    result.manifest = telemetry.finish()

    if write_artefacts:
        panel.values.to_parquet(out / "kpi_panel.parquet", index=False)
        write_json([f.model_dump(mode="json") for f in result.flags], out / "flags.json")
        write_json([e.model_dump(mode="json") for e in result.events], out / "events.json")
        write_json([asdict(d) for d in detected.diagnostics], out / "series_diagnostics.json")
        if result.series_profiles:
            write_json(
                [p.model_dump(mode="json") for p in result.series_profiles],
                out / "series_profiles.json",
            )
        if result.bundles:
            write_json(
                [b.model_dump(mode="json") for b in result.bundles],
                out / "evidence_bundles.json",
            )
        write_json(result.manifest, out / "run_manifest.json")

    return result


def profile_panel_safe(panel, eda, contract, spec):
    from kpi_engine.eda.runner import profile_panel

    return profile_panel(panel, eda, contract, spec.source_id, spec.path)


def route_and_attribute_safe(event, kpi, panel, raw, contract, graph, entity_keys,
                             blocked, date_column):
    """Attribution failures degrade to no attribution, not to a lost run.

    An estimator raising on one KPI should not cost the caller every other event's
    evidence; the resulting bundle simply has fewer attributions, and the
    confidence and abstention logic already handle that case.
    """
    from kpi_engine.causal.router import route_and_attribute

    try:
        return route_and_attribute(
            event, kpi, panel.values, panel.measures[kpi], raw,
            contract, graph, entity_keys, blocked, date_column,
        )
    except Exception:  # noqa: BLE001 - deliberately broad; see docstring
        return None
