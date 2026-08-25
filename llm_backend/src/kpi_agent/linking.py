"""Connect a sales-side event to a supply-side one -- but only where the graph says so.

Two events overlapping in time and sharing a region is a coincidence until something
says the two are mechanically related. With 105 weeks, five regions and four
categories, coincidences are abundant: at any usable tolerance, most sales events
will overlap *some* supply event somewhere. Emitting those as explanations would be
the exact failure the engine is built to avoid -- a fluent story assembled from
correlation.

So the test here is not "do these overlap". It is "does the declared causal graph
contain a path from a supply-side node that moved to a driver of the sales-side KPI
that moved". Overlap is then only a filter on candidates the graph already permits.
No path, no link, and the agent reports the movement as unexplained by supply.

The lag is reported, not required: a supplier disruption reaches cost of goods when
the affected stock is sold, which is days to weeks later. Reporting the lag lets a
reader judge the mechanism; requiring a particular lag would be fitting one.
"""

from __future__ import annotations

import datetime as dt

from kpi_engine.causal.dag import CausalGraph
from kpi_engine.contracts.configs import KpiContract
from kpi_engine.contracts.payloads import EventWindow, EvidenceBundle

from kpi_agent.models import CrossSourceLink

# A supply-side cause and its sales-side consequence are not simultaneous: goods
# received late are costed when sold. Six weeks is generous enough to catch a slow
# sell-through and short enough that two unrelated quarters cannot be joined.
DEFAULT_LAG_TOLERANCE_DAYS = 42


def link_events(
    sales_events: list[EventWindow],
    scm_events: list[EventWindow],
    sales_contract: KpiContract,
    scm_contract: KpiContract,
    graph: CausalGraph,
    *,
    lag_tolerance_days: int = DEFAULT_LAG_TOLERANCE_DAYS,
    sales_bundles: list[EvidenceBundle] | None = None,
) -> list[CrossSourceLink]:
    links: list[CrossSourceLink] = []
    bundles = {b.event_id: b for b in (sales_bundles or [])}

    for sales in sales_events:
        sales_kpis = _material_kpis(sales)
        if not sales_kpis:
            continue
        # Every measure the affected KPIs can be moved by, per the graph.
        permitted = _upstream_union(graph, sales_kpis)
        if not permitted:
            continue

        for scm in scm_events:
            shared = _shared_entity(sales.entity, scm.entity)
            if shared is None:
                continue

            scm_kpis = _material_kpis(scm)
            movers = _moved_supply_nodes(scm_kpis, scm_contract, graph)
            licensed = [n for n in movers if n in permitted]
            if not licensed:
                continue

            overlap, lag = _window_relation(sales, scm)
            if lag > lag_tolerance_days:
                continue

            for node in licensed:
                for kpi in sales_kpis:
                    path = _path(graph, node, kpi)
                    if not path:
                        continue
                    links.append(
                        CrossSourceLink(
                            sales_event_id=sales.event_id,
                            scm_event_id=scm.event_id,
                            shared_entity=shared,
                            sales_window=(str(sales.window_start), str(sales.window_end)),
                            scm_window=(str(scm.window_start), str(scm.window_end)),
                            overlap_days=overlap,
                            lag_days=lag,
                            scm_kpis_moved=scm_kpis,
                            sales_kpis_moved=[kpi],
                            dag_path=path,
                            dag_edge=(path[0], path[1]) if len(path) > 1 else (node, kpi),
                            relation=_relation(graph, path),
                            note=(
                                f"{node} moved in {scm.event_id} and the declared graph "
                                f"reaches {kpi} via {' -> '.join(path)}. "
                                f"Windows overlap {overlap}d, lag {lag}d. "
                                + (
                                    f"Sales-side confidence {bundles[sales.event_id].confidence.score:.2f}."
                                    if sales.event_id in bundles else
                                    "No sales-side attribution was computed for this event."
                                )
                            ),
                        )
                    )
    return _dedupe(links)


def _material_kpis(event: EventWindow) -> list[str]:
    material = [d.kpi for d in event.observed_deviations if d.material]
    return material or list(event.primary_kpis_affected)


def _upstream_union(graph: CausalGraph, kpis: list[str]) -> set[str]:
    out: set[str] = set()
    for kpi in kpis:
        try:
            out.update(graph.upstream_causal_drivers(kpi))
        except Exception:  # noqa: BLE001 - a KPI absent from the graph has no drivers
            continue
    return out


def _moved_supply_nodes(
    scm_kpis: list[str], scm_contract: KpiContract, graph: CausalGraph
) -> list[str]:
    """Graph nodes corresponding to the columns behind the supply KPIs that moved.

    A supply KPI is a ratio; what the graph knows about are its underlying columns.
    So a move in `Fill Rate` is translated into a move in `Supplier Fill Rate` and
    `Units Ordered`, which are things the graph has edges for.
    """
    nodes: list[str] = []
    for kpi in scm_kpis:
        try:
            kpi_def = scm_contract.kpi(kpi)
        except KeyError:
            continue
        for measure in kpi_def.measures.values():
            node = graph.node_for_column(measure.column)
            if node and node not in nodes:
                nodes.append(node)
    return nodes


def _shared_entity(a: dict[str, str], b: dict[str, str]) -> dict[str, str] | None:
    """The slice both events sit in, or None if they contradict.

    Two events that share no dimension at all are still comparable -- a system-wide
    sales event and a supplier-wide supply event genuinely can be the same story --
    so an empty intersection is a match, not a rejection. What rejects is *disagreeing*
    on a key they both carry: West cannot be explained by something in the North.
    """
    shared: dict[str, str] = {}
    for key in set(a) & set(b):
        if a[key] != b[key]:
            return None
        shared[key] = a[key]
    return shared


def _window_relation(sales: EventWindow, scm: EventWindow) -> tuple[int, int]:
    """(overlap in days, lag in days). Lag is 0 when the windows overlap at all."""
    s0, s1 = _as_date(sales.window_start), _as_date(sales.window_end)
    c0, c1 = _as_date(scm.window_start), _as_date(scm.window_end)
    overlap = (min(s1, c1) - max(s0, c0)).days + 1
    if overlap > 0:
        return overlap, 0
    # Disjoint: the gap between them, in whichever order they fall.
    gap = (s0 - c1).days if s0 > c1 else (c0 - s1).days
    return 0, int(gap)


def _as_date(value) -> dt.date:
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))


def _path(graph: CausalGraph, source: str, target: str) -> list[str] | None:
    import networkx as nx

    try:
        return list(nx.shortest_path(graph.graph, source, target))
    except Exception:  # noqa: BLE001 - no path, or a node the graph does not carry
        return None


def _relation(graph: CausalGraph, path: list[str]) -> str:
    """`causal` if any hop is estimated; `deterministic` only when every hop is arithmetic."""
    relations = set()
    for a, b in zip(path, path[1:]):
        edge = next(
            (e for e in graph.spec.edges if e.source == a and e.target == b), None
        )
        relations.add(edge.relation if edge else "unknown")
    return "deterministic" if relations == {"deterministic"} else "causal"


def _dedupe(links: list[CrossSourceLink]) -> list[CrossSourceLink]:
    """One link per (sales event, supply event, sales KPI), keeping the shortest path.

    A longer path through the same pair of events is the same claim with more hops
    between it and the evidence, and stating both would double-count one mechanism.
    """
    best: dict[tuple[str, str, str], CrossSourceLink] = {}
    for link in links:
        key = (link.sales_event_id, link.scm_event_id, link.sales_kpis_moved[0])
        current = best.get(key)
        if current is None or len(link.dag_path) < len(current.dag_path):
            best[key] = link
    return sorted(best.values(), key=lambda link: (link.sales_event_id, len(link.dag_path)))
