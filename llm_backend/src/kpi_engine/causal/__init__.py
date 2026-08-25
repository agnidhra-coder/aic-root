"""Causal attribution: exact algebra first, estimation only where algebra runs out."""

from kpi_engine.causal.algebraic import lmdi_decomposition, shapley_decomposition
from kpi_engine.causal.dag import CausalGraph
from kpi_engine.causal.did import estimate_did
from kpi_engine.causal.dml import estimate_dml
from kpi_engine.causal.its import estimate_its
from kpi_engine.causal.router import route_and_attribute

__all__ = [
    "lmdi_decomposition",
    "shapley_decomposition",
    "CausalGraph",
    "estimate_did",
    "estimate_dml",
    "estimate_its",
    "route_and_attribute",
]
