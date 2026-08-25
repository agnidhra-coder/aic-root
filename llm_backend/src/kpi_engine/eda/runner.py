"""Assemble one SeriesProfile per (KPI, entity slice).

STL is fitted once per series and shared between the trend and seasonality
summaries -- the decomposition is the expensive step and there is no reason to
pay for it twice.
"""

from __future__ import annotations

import pandas as pd

from kpi_engine.contracts.configs import EdaSpec, KpiContract
from kpi_engine.contracts.payloads import (
    DataQualitySummary,
    Lineage,
    SeasonalitySummary,
    SeriesProfile,
    TrendSummary,
)
from kpi_engine.detection.decompose import decompose_series
from kpi_engine.eda.quality import summarise_quality
from kpi_engine.eda.seasonality import summarise_seasonality
from kpi_engine.eda.segments import segment_series
from kpi_engine.eda.trend import summarise_trend
from kpi_engine.semantics.panel import PERIOD_COL, SUPPORT_COL, KpiPanel

_GRAIN_PERIOD = {"day": 7, "week": 4, "month": 12}


def profile_panel(
    panel: KpiPanel,
    spec: EdaSpec,
    contract: KpiContract,
    source_id: str,
    source_path: str,
) -> list[SeriesProfile]:
    profiles: list[SeriesProfile] = []

    for kpi in panel.kpi_names():
        kpi_def = contract.kpi(kpi)
        lineage = Lineage(
            source_id=source_id,
            source_path=source_path,
            contract_id=contract.contract_id,
            kpi=kpi,
            columns=sorted({m.column for m in kpi_def.measures.values()}),
            time_grain=contract.time_grain,
            entity_filter={},
        )
        for entity in panel.entities():
            frame = panel.series(kpi, entity)
            if frame.empty:
                continue
            profile = _profile_series(
                frame, kpi, entity, spec, contract, kpi_def.guards.min_rows_per_cell, lineage
            )
            profiles.append(profile)

    return profiles


def _profile_series(
    frame: pd.DataFrame,
    kpi: str,
    entity: dict[str, str],
    spec: EdaSpec,
    contract: KpiContract,
    min_rows_per_cell: int,
    lineage: Lineage,
) -> SeriesProfile:
    periods = frame[PERIOD_COL]
    values = frame["value"].astype("float64")
    support = frame[SUPPORT_COL] if SUPPORT_COL in frame else pd.Series(0, index=frame.index)

    quality = summarise_quality(
        periods, values, support, min_rows_per_cell, spec.min_history, spec.outlier_z
    )

    # Period is grain-aware: a 7-step cycle means a week at daily grain and seven
    # months at monthly grain, which would be nonsense.
    decomposition_spec = spec.decomposition.model_copy(
        update={"period": _GRAIN_PERIOD.get(contract.time_grain, spec.decomposition.period)}
    )
    indexed = pd.Series(values.to_numpy(), index=pd.to_datetime(periods.to_numpy()))
    decomposition = decompose_series(indexed, decomposition_spec)

    trend = summarise_trend(values, spec.trend, decomposition)
    seasonality = summarise_seasonality(
        decomposition,
        periods,
        spec.seasonality_floor,
        contract.time_grain,
        decomposition_spec.period,
    )
    segments = segment_series(periods, values, spec.segments, spec.trend)

    usable, reason = _usability(quality, spec)
    entity_lineage = lineage.model_copy(update={"entity_filter": entity})

    return SeriesProfile(
        kpi=kpi,
        entity=entity,
        period_start=pd.Timestamp(periods.iloc[0]).date(),
        period_end=pd.Timestamp(periods.iloc[-1]).date(),
        time_grain=contract.time_grain,
        trend=trend,
        seasonality=seasonality,
        quality=quality,
        segments=segments,
        headline=build_headline(kpi, entity, trend, seasonality, quality, segments, usable, reason),
        usable=usable,
        unusable_reason=reason,
        lineage=entity_lineage,
    )


def _usability(quality: DataQualitySummary, spec: EdaSpec) -> tuple[bool, str | None]:
    """Refuse to characterise a series that cannot support a claim.

    Same philosophy as evidence/abstention.py: an honest "not enough history"
    beats a confident slope through five points.
    """
    present = quality.n_periods - quality.n_missing
    if present < spec.min_history:
        return False, (
            f"only {present} periods with data, below the {spec.min_history} required to "
            "characterise a trend"
        )
    if quality.low_support_share > 0.5:
        return False, (
            f"{quality.low_support_share:.0%} of periods are below the minimum rows-per-cell "
            "guard; the series is mostly interpolated emptiness"
        )
    return True, None


def build_headline(
    kpi: str,
    entity: dict[str, str],
    trend: TrendSummary,
    seasonality: SeasonalitySummary,
    quality: DataQualitySummary,
    segments: list,
    usable: bool,
    reason: str | None,
) -> str:
    """One deterministic sentence describing the series.

    Template-generated, never LLM. It exists so the artifact is readable on its
    own and so a later narrator can be checked against a known-correct baseline.
    """
    where = ", ".join(f"{k}={v}" for k, v in entity.items()) or "overall"

    if not usable:
        return f"{kpi} ({where}): not characterised -- {reason}."

    n = trend.n_periods

    if trend.direction == "flat":
        core = f"{kpi} ({where}) is flat over {n} periods"
    else:
        pct = f"{trend.slope_pct_per_period:+.1f}%/period" if trend.slope_pct_per_period else ""
        total = (
            f", {trend.total_change_pct:+.1f}% end to end"
            if trend.total_change_pct is not None
            else ""
        )
        core = (
            f"{kpi} ({where}) is {trend.direction} over {n} periods "
            f"({pct}{total}, p={trend.p_value:.3f})"
        )

    parts = [core]
    if len(segments) > 1:
        parts.append(f"{len(segments)} distinct phases")
    if seasonality.detected:
        parts.append(f"seasonality period {seasonality.period} (strength {seasonality.strength:.2f})")
    else:
        parts.append("no seasonality")
    if quality.volatility_cv is not None:
        parts.append(f"volatility {quality.volatility_cv:.2f}")

    return "; ".join(parts) + "."
