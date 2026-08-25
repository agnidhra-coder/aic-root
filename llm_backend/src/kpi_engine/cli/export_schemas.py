"""Dump JSON Schema for every config and payload model.

This is the hook for the LLM stage that comes later. With these schemas, having a
model author a KPI contract or a causal graph becomes constrained generation
validated against a spec, rather than parsing free text and hoping. The same
schemas describe the payloads a narration layer will consume.

    python -m kpi_engine.cli.export_schemas
"""

from __future__ import annotations

import argparse

from kpi_engine.cli._common import banner, resolve
from kpi_engine.config_io import write_json
from kpi_engine.contracts import configs as config_models
from kpi_engine.contracts import payloads as payload_models

CONFIG_MODELS = [
    config_models.SourceSpec,
    config_models.KpiContract,
    config_models.CausalGraphSpec,
    config_models.DetectionSpec,
    config_models.EdaSpec,
    config_models.ScenarioSpec,
]
PAYLOAD_MODELS = [
    payload_models.DataProfile,
    payload_models.SeriesProfile,
    payload_models.Flag,
    payload_models.EventWindow,
    payload_models.Attribution,
    payload_models.EvidenceBundle,
    payload_models.AbstainPayload,
    payload_models.RunManifest,
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="schemas")
    args = parser.parse_args(argv)
    out_dir = resolve(args.out_dir)

    banner("JSON SCHEMA EXPORT")
    for group, models in (("config", CONFIG_MODELS), ("payload", PAYLOAD_MODELS)):
        print(f"\n  {group} models:")
        for model in models:
            path = out_dir / group / f"{model.__name__}.json"
            write_json(model.model_json_schema(), path)
            n_props = len(model.model_json_schema().get("properties", {}))
            print(f"    {model.__name__:<22} {n_props:>3} properties  -> {path.relative_to(resolve('.'))}")

    print("\n  These constrain LLM-authored configs later: a generated contract is "
          "\n  validated against its schema before it can reach a computation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
