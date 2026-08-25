"""Loading and locating configs.

Single place that knows where configs live and which model each maps to, so the
CLIs stay thin and a config is never read as an unvalidated dict.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel

from kpi_engine.contracts.configs import (
    CausalGraphSpec,
    DetectionSpec,
    EdaSpec,
    KpiContract,
    ScenarioSpec,
    SourceSpec,
)

T = TypeVar("T", bound=BaseModel)


def project_root() -> Path:
    """Repository root, derived from this file's location (src/kpi_engine/config_io.py)."""
    return Path(__file__).resolve().parents[2]


def load_yaml_as(path: str | Path, model: type[T]) -> T:
    with Path(path).open() as fh:
        data = yaml.safe_load(fh)
    if data is None:
        raise ValueError(f"Config {path} is empty")
    return model.model_validate(data)


def load_source(path: str | Path) -> SourceSpec:
    return load_yaml_as(path, SourceSpec)


def load_contract(path: str | Path) -> KpiContract:
    return load_yaml_as(path, KpiContract)


def load_graph(path: str | Path) -> CausalGraphSpec:
    return load_yaml_as(path, CausalGraphSpec)


def load_detection(path: str | Path) -> DetectionSpec:
    return load_yaml_as(path, DetectionSpec)


def load_eda(path: str | Path) -> EdaSpec:
    return load_yaml_as(path, EdaSpec)


def load_scenario(path: str | Path) -> ScenarioSpec:
    return load_yaml_as(path, ScenarioSpec)


def write_json(obj: BaseModel | dict | list, path: str | Path) -> Path:
    """Write a payload as indented JSON, creating parent directories."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(obj, BaseModel):
        text = obj.model_dump_json(indent=2)
    else:
        payload = [o.model_dump(mode="json") if isinstance(o, BaseModel) else o for o in obj] \
            if isinstance(obj, list) else obj
        text = json.dumps(payload, indent=2, default=str)
    p.write_text(text)
    return p


def read_json(path: str | Path) -> dict | list:
    with Path(path).open() as fh:
        return json.load(fh)
