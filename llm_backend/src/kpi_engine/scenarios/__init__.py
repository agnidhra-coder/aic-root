"""Synthetic event injection, so detection and attribution can be measured against truth."""

from kpi_engine.scenarios.injector import inject_scenario, propagate_identities

__all__ = ["inject_scenario", "propagate_identities"]
