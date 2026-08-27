"""Maps a config's `type` field to an adapter class.

Adding Postgres later: write PostgresSource(DataSource), call register_source
("postgres", PostgresSource), extend the SourceSpec.type Literal. No other file
changes.
"""

from __future__ import annotations

from pathlib import Path

from kpi_engine.contracts.configs import SourceSpec
from kpi_engine.sources.base import DataSource
from kpi_engine.sources.csv_source import CsvSource
from kpi_engine.sources.excel_source import ExcelSource

_REGISTRY: dict[str, type[DataSource]] = {"csv": CsvSource, "excel": ExcelSource}


def register_source(type_name: str, cls: type[DataSource]) -> None:
    _REGISTRY[type_name] = cls


def available_types() -> list[str]:
    return sorted(_REGISTRY)


def build_source(spec: SourceSpec, base_dir: str | Path | None = None) -> DataSource:
    """Instantiate the adapter for `spec`, resolving a relative path against base_dir.

    `base_dir` is the company root. In practice specs arriving from
    `CompanyPaths.source_spec` already carry an absolute path, so the join below
    is a fallback for a spec loaded some other way -- not the normal route.
    """
    if spec.type not in _REGISTRY:
        raise ValueError(f"Unknown source type '{spec.type}'. Registered: {available_types()}")
    resolved = spec
    if base_dir is not None and not Path(spec.path).is_absolute():
        resolved = spec.model_copy(update={"path": str(Path(base_dir) / spec.path)})
    return _REGISTRY[spec.type](resolved)
