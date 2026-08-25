"""Clusters individual flags into reportable events.

A three-week shift produces dozens of flags across several detectors and KPIs.
Reporting each one separately would bury the analyst and, worse, would ask the
causal layer to explain the same underlying event dozens of times. Flags close
in time on the same entity are therefore merged into one EventWindow, which is
the unit everything downstream reasons about.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

import pandas as pd

from kpi_engine.contracts.configs import KpiContract, WindowingSpec
from kpi_engine.contracts.payloads import (
    AnomalyType,
    DataProfile,
    EventWindow,
    Flag,
    Lineage,
    ObservedDeviation,
)

_TYPE_PRECEDENCE: list[AnomalyType] = [
    "Structural Break",
    "Multivariate Divergence",
    "Sustained Trend Drift",
    "Point Deviation",
]


def _entity_key(entity: dict[str, str]) -> tuple:
    return tuple(sorted(entity.items()))


PERIOD_DAYS = {"day": 1, "week": 7, "month": 30}


def _cluster(flags: list[Flag], merge_gap_days: int) -> list[list[Flag]]:
    """Group flags whose periods are within `merge_gap_days` of the running cluster.

    The gap is expressed in periods in config and converted to days by the caller.
    Comparing a period count directly against a day difference would mean that at
    weekly grain no two consecutive weeks ever merge, shattering one real event
    into a separate finding per week.
    """
    ordered = sorted(flags, key=lambda f: f.period)
    clusters: list[list[Flag]] = []
    current: list[Flag] = []
    for flag in ordered:
        if current and (flag.period - max(f.period for f in current)).days > merge_gap_days:
            clusters.append(current)
            current = []
        current.append(flag)
    if current:
        clusters.append(current)
    return clusters


def _deviation(
    panel: pd.DataFrame,
    kpi: str,
    entity: dict[str, str],
    start: dt.date,
    end: dt.date,
    contract: KpiContract,
    baseline_days: int = 56,
) -> ObservedDeviation:
    """Compare the window mean against the preceding baseline mean for one KPI.

    Materiality is judged on both a percentage move and an absolute one, because
    either alone misleads: a 40% swing on a tiny base is noise, and a small
    percentage on a large base can still be the biggest number on the page.
    """
    df = panel[panel["kpi"] == kpi]
    for key, val in entity.items():
        df = df[df[key] == val]
    periods = pd.to_datetime(df["period"])
    in_window = df[(periods >= pd.Timestamp(start)) & (periods <= pd.Timestamp(end))]["value"]
    pre = df[
        (periods < pd.Timestamp(start))
        & (periods >= pd.Timestamp(start) - pd.Timedelta(days=baseline_days))
    ]["value"]

    actual = float(in_window.mean()) if in_window.notna().any() else None
    expected = float(pre.mean()) if pre.notna().any() else None
    abs_delta = actual - expected if actual is not None and expected is not None else None
    pct_delta = (
        (abs_delta / expected * 100.0) if abs_delta is not None and expected not in (None, 0) else None
    )

    material = False
    if abs_delta is not None:
        try:
            m = contract.kpi(kpi).materiality
            material = abs(pct_delta or 0.0) >= m.min_abs_pct and abs(abs_delta) >= m.min_business_impact
        except KeyError:
            material = False

    return ObservedDeviation(
        kpi=kpi,
        expected=expected,
        actual=actual,
        abs_delta=abs_delta,
        pct_delta=pct_delta,
        material=material,
    )


def build_event_windows(
    flags: list[Flag],
    panel: pd.DataFrame,
    contract: KpiContract,
    spec: WindowingSpec,
    profile: DataProfile | None,
    source_id: str,
    source_path: str,
    numeric_columns: list[str],
    run_prefix: str = "EV",
) -> list[EventWindow]:
    """Merge flags per entity into events, attaching deviations and legal covariates."""
    by_entity: dict[tuple, list[Flag]] = defaultdict(list)
    for flag in flags:
        by_entity[_entity_key(flag.entity)].append(flag)

    blocked = profile.blocked_driver_columns() if profile else set()
    reasons = profile.redundant_columns if profile else {}
    allowed = [c for c in numeric_columns if c not in blocked]
    excluded = {c: reasons.get(c, "redundant") for c in numeric_columns if c in blocked}

    period_days = PERIOD_DAYS[contract.time_grain]
    merge_gap_days = spec.merge_gap * period_days

    events: list[EventWindow] = []
    counter = 0
    for key, entity_flags in sorted(by_entity.items()):
        entity = dict(key)
        for cluster in _cluster(entity_flags, merge_gap_days):
            start = min(f.period for f in cluster)
            end = max(f.period for f in cluster)
            if (end - start).days + 1 < spec.min_window_len * period_days:
                continue
            # Corroboration: a single flag on a heavy-tailed ratio series is far more
            # often noise than an incident. Both bars must be cleared -- enough flags
            # AND agreement across independent detector families. Requiring only one
            # or the other lets through clusters where a single detector fired twice
            # on the same piece of noise.
            n_detectors = len({f.detector.split("_")[0] for f in cluster})
            if len(cluster) < spec.min_flags or n_detectors < spec.min_detectors:
                continue
            counter += 1

            kpis = sorted({k for f in cluster for k in f.kpi.split("+")} & {c.name for c in contract.kpis})
            types = [t for t in _TYPE_PRECEDENCE if t in {f.anomaly_type for f in cluster}]
            deviations = [_deviation(panel, k, entity, start, end, contract) for k in kpis]
            if spec.require_material and not any(d.material for d in deviations):
                continue

            slug = "-".join(f"{v}" for v in entity.values()) or "TOTAL"
            events.append(
                EventWindow(
                    event_id=f"{run_prefix}-{slug}-{start:%Y%m%d}-{counter:03d}",
                    anomaly_types=types,
                    detectors=sorted({f.detector for f in cluster}),
                    window_start=start,
                    window_end=end,
                    entity=entity,
                    primary_kpis_affected=sorted(
                        [d.kpi for d in deviations if d.material]
                        or kpis,
                        key=lambda k: -abs(
                            next((d.pct_delta or 0.0) for d in deviations if d.kpi == k)
                        ),
                    ),
                    observed_deviations=deviations,
                    candidate_covariates=allowed,
                    excluded_covariates=excluded,
                    peak_score=float(max(f.score for f in cluster)),
                    n_flags=len(cluster),
                    min_support=int(min(f.support for f in cluster)),
                    lineage=Lineage(
                        source_id=source_id,
                        source_path=source_path,
                        contract_id=contract.contract_id,
                        kpi=",".join(kpis),
                        columns=sorted(
                            {m.column for k in kpis for m in contract.kpi(k).measures.values()}
                        ),
                        time_grain=contract.time_grain,
                        entity_filter=entity,
                    ),
                )
            )
    return sorted(events, key=lambda e: (-e.peak_score, e.window_start))
