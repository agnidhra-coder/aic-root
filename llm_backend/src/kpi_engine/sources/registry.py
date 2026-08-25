"""Maps a config's `type` field to an adapter class.

Adding Postgres later: write PostgresSource(DataSource), call register_source
("postgres", PostgresSource), extend the SourceSpec.type Literal. No other file
changes.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from kpi_engine.contracts.configs import SourceSpec
from kpi_engine.sources.base import DataSource
from kpi_engine.sources.csv_source import CsvSource
from kpi_engine.sources.excel_source import ExcelSource

_REGISTRY: dict[str, type[DataSource]] = {"csv": CsvSource, "excel": ExcelSource}


def register_source(type_name: str, cls: type[DataSource]) -> None:
    _REGISTRY[type_name] = cls


def available_types() -> list[str]:
    return sorted(_REGISTRY)


def load_source_spec(path: str | Path) -> SourceSpec:
    """Read and validate a source YAML. Invalid configs raise before any I/O happens."""
    with Path(path).open() as fh:
        return SourceSpec.model_validate(yaml.safe_load(fh))


def build_source(spec: SourceSpec, base_dir: str | Path | None = None) -> DataSource:
    """Instantiate the adapter for `spec`, resolving a relative path against base_dir."""
    if spec.type not in _REGISTRY:
        raise ValueError(f"Unknown source type '{spec.type}'. Registered: {available_types()}")
    resolved = spec
    if base_dir is not None and not Path(spec.path).is_absolute():
        resolved = spec.model_copy(update={"path": str(Path(base_dir) / spec.path)})
    return _REGISTRY[spec.type](resolved)
