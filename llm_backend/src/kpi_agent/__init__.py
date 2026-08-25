"""LangGraph agent over the deterministic KPI engine.

The engine computes; this narrates. The boundary is enforced rather than
described: the narrator's entire context is a fact table derived from evidence
bundles, and `verify.py` rejects any claim it cannot resolve back to one.
"""

from kpi_agent.graph import DEFAULT_CONFIG, build_graph, run_agent, stream_agent
from kpi_agent.llm import DEFAULT_MODEL, LlmUnavailable, Usage, build_llm
from kpi_agent.models import AnalysisIntent, GroundedContext, Narrative, VerificationResult

__all__ = [
    "AnalysisIntent",
    "DEFAULT_CONFIG",
    "DEFAULT_MODEL",
    "GroundedContext",
    "LlmUnavailable",
    "Narrative",
    "Usage",
    "VerificationResult",
    "build_graph",
    "build_llm",
    "run_agent",
    "stream_agent",
]
