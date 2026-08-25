"""Swappable data sources. Adding a backend is one class plus one registry entry."""

from kpi_engine.sources.base import DataSource
from kpi_engine.sources.registry import build_source, load_source_spec, register_source

__all__ = ["DataSource", "build_source", "load_source_spec", "register_source"]
