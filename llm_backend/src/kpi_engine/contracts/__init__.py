"""Pydantic contracts for every config and every payload the engine emits.

Configs are hand-written today and LLM-generated later; because they are all
Pydantic models, `kpi_engine.cli.export_schemas` can dump JSON Schema that
constrains that later generation instead of parsing free text.
"""

from kpi_engine.contracts.configs import (
    CausalGraphSpec,
    CausalNode,
    DetectionSpec,
    KpiDef,
    KpiContract,
    MeasureDef,
    ScenarioSpec,
    SourceSpec,
)
from kpi_engine.contracts.payloads import (
    AbstainPayload,
    Attribution,
    Contribution,
    EvidenceBundle,
    EventWindow,
    Flag,
    Freshness,
    Lineage,
    MethodChoice,
    RunManifest,
    SourceDescription,
    StageTelemetry,
)

__all__ = [
    "CausalGraphSpec",
    "CausalNode",
    "DetectionSpec",
    "KpiDef",
    "KpiContract",
    "MeasureDef",
    "ScenarioSpec",
    "SourceSpec",
    "AbstainPayload",
    "Attribution",
    "Contribution",
    "EvidenceBundle",
    "EventWindow",
    "Flag",
    "Freshness",
    "Lineage",
    "MethodChoice",
    "RunManifest",
    "SourceDescription",
    "StageTelemetry",
]
