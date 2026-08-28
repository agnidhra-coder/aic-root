"""Loading and locating configs.

Single place that knows where configs live and which model each maps to, so the
CLIs stay thin and a config is never read as an unvalidated dict.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel

from kpi_engine.contracts.catalogue import SeedCatalog
from kpi_engine.contracts.configs import (
    CausalGraphSpec,
    DetectionSpec,
    EdaSpec,
    KpiContract,
    ScenarioSpec,
    SourceSpec,
)
from kpi_engine.contracts.tenancy import CompanyRegistry, CompanySpec

T = TypeVar("T", bound=BaseModel)


def project_root() -> Path:
    """Repository root, derived from this file's location (src/kpi_engine/config_io.py).

    It locates exactly four things and nothing else:

    1. `user/`, when `$KPI_USER_ROOT` is unset (see `kpi_engine.tenancy.user_root`)
    2. `templates/`
    3. `schemas/`, for `cli.export_schemas`
    4. `.env`

    It is **not** a data or config anchor. Every path to a dataset or a config is
    company-relative and resolves through `tenancy.CompanyPaths`; a project-relative
    path to either is a bug, and `test_no_project_relative_path_literals_in_src`
    fails on one.
    """
    return Path(__file__).resolve().parents[2]


def load_yaml_as(path: str | Path, model: type[T]) -> T:
    with Path(path).open() as fh:
        data = yaml.safe_load(fh)
    if data is None:
        raise ValueError(f"Config {path} is empty")
    return model.model_validate(data)


def load_source(path: str | Path) -> SourceSpec:
    return load_yaml_as(path, SourceSpec)


def load_seed_catalog(path: str | Path) -> SeedCatalog:
    """The KPI vocabulary. Product-level reference, never copied into a tenant."""
    return load_yaml_as(path, SeedCatalog)


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


def load_company(path: str | Path) -> CompanySpec:
    return load_yaml_as(path, CompanySpec)


def load_registry(path: str | Path) -> CompanyRegistry:
    return load_yaml_as(path, CompanyRegistry)


def load_personas(path: str | Path) -> dict[str, dict[str, Any]]:
    """The persona table, which is presentation config rather than a payload model.

    Not a Pydantic model: the personas file is a mapping of name -> knobs that
    `kpi_agent.facts` reads directly, and the names are extended by adding a
    persona rather than by changing a schema. It lives here so every YAML read in
    the codebase goes through one module.
    """
    with Path(path).open() as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict) or "personas" not in data:
        raise ValueError(f"Personas config {path} has no top-level `personas:` mapping")
    return data["personas"]


def write_yaml(obj: BaseModel | dict, path: str | Path) -> Path:
    """Write a config as YAML, atomically, creating parent directories.

    Atomic because `user/metadata.yaml` is read-modify-write from both the CLI and
    the API: a torn write there would lose the registry, not one entry of it. The
    temp file is created in the destination directory so `os.replace` stays on one
    filesystem and is therefore actually atomic.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = obj.model_dump(mode="json") if isinstance(obj, BaseModel) else obj
    text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return p


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
