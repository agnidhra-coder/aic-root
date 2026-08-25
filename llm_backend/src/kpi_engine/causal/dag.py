"""The governed causal graph.

Two jobs. It records which upstream measures may legitimately explain a KPI, and
it carries the ownership metadata that turns a driver into a lever someone can
actually pull. Edges are declared, never discovered: with 730 noisy periods,
structure learning would propose reversed mechanisms often enough to be worse
than useless, so direction comes from domain knowledge and `forbidden_edges`
makes the prohibited reversals explicit and checkable.
"""

from __future__ import annotations

import networkx as nx

from kpi_engine.contracts.configs import CausalGraphSpec, CausalNode


class CausalGraph:
    def __init__(self, spec: CausalGraphSpec) -> None:
        self.spec = spec
        self.nodes: dict[str, CausalNode] = {n.name: n for n in spec.nodes}
        self.graph = nx.DiGraph()
        for node in spec.nodes:
            self.graph.add_node(node.name, **node.model_dump())
        for edge in spec.edges:
            self.graph.add_edge(edge.source, edge.target, relation=edge.relation, note=edge.note)
        self.validate()

    def validate(self) -> None:
        """Reject any edge the config declared impossible, then any cycle.

        Forbidden edges are checked first deliberately. A reversed mechanism is
        usually also what closes a cycle, and "you declared an edge you forbade"
        names the actual mistake, where "the graph has a cycle" only names its
        symptom and leaves the author to work back to the offending edge.
        """
        forbidden = {(a, b) for a, b in self.spec.forbidden_edges}
        violations = [e for e in self.graph.edges if e in forbidden]
        if violations:
            raise ValueError(f"Graph declares edges it also forbids: {violations}")
        if not nx.is_directed_acyclic_graph(self.graph):
            cycle = nx.find_cycle(self.graph)
            raise ValueError(f"Causal graph '{self.spec.graph_id}' contains a cycle: {cycle}")

    def parents(self, node: str, relation: str | None = None) -> list[str]:
        return [
            p for p in self.graph.predecessors(node)
            if relation is None or self.graph.edges[p, node]["relation"] == relation
        ]

    def ancestors(self, node: str) -> set[str]:
        return nx.ancestors(self.graph, node) if node in self.graph else set()

    def upstream_causal_drivers(self, node: str) -> list[str]:
        """Measures that may explain `node`, excluding its deterministic components.

        The deterministic parents are the KPI's own arithmetic and are handled
        exactly by the algebraic layer. Re-estimating them statistically would
        rediscover the definition of the KPI and call it a finding.
        """
        deterministic = set(self.parents(node, "deterministic"))
        out: set[str] = set()
        for parent in deterministic:
            out |= self.ancestors(parent)
        out |= set(self.parents(node, "causal"))
        return sorted(out - deterministic - {node})

    def is_controllable(self, name: str) -> bool:
        node = self.nodes.get(name)
        return bool(node and node.controllable)

    def owner(self, name: str) -> str | None:
        node = self.nodes.get(name)
        return node.owner if node else None

    def column_for(self, name: str) -> str | None:
        node = self.nodes.get(name)
        return node.column if node else None

    def node_for_column(self, column: str) -> str | None:
        for name, node in self.nodes.items():
            if node.column == column:
                return name
        return None
