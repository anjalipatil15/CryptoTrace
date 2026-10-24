"""Phase 3 - directed transaction graph: wallets are nodes, transfers are edges."""

from __future__ import annotations

import networkx as nx
import pandas as pd

from ..labels import LabelStore


def build_graph(transfers: pd.DataFrame, labels: LabelStore, nodes: pd.DataFrame | None = None) -> nx.MultiDiGraph:
    """One edge per transfer (keyed by transfer_id), node attributes from labels and trace state."""
    g = nx.MultiDiGraph()
    if nodes is not None and not nodes.empty:
        # Keep the tracer's decisions (dust, tokens, limits): only transfers INTO traced addresses.
        # Wallets the trace followed keep inflows from outside too, since taint needs the clean money.
        # Endpoints (busy services, limit nodes) keep only inflows from the trail: their full
        # histories can run to millions of unrelated transfers.
        traced = set(nodes["address"])
        followed = set(nodes.loc[nodes["status"] == "expanded", "address"])
        to_followed = transfers["to_addr"].isin(followed)
        from_trail = transfers["to_addr"].isin(traced) & transfers["from_addr"].isin(traced)
        transfers = transfers[to_followed | from_trail]
    for row in transfers.itertuples(index=False):
        g.add_edge(
            row.from_addr,
            row.to_addr,
            key=row.transfer_id,
            tx_hash=row.tx_hash,
            timestamp=row.timestamp,
            value=float(row.value),
            token=row.token,
            kind=row.kind,
        )
    trace_state = {} if nodes is None or nodes.empty else nodes.set_index("address").to_dict("index")
    for addr in g.nodes:
        label = labels.get(addr)
        state = trace_state.get(addr, {})
        g.nodes[addr].update(
            entity=label.entity if label else "",
            category=label.category if label else "wallet",
            status=state.get("status", "context"),
            trace_hop=state.get("hop"),
        )
    return g


def collapse(g: nx.MultiDiGraph) -> nx.DiGraph:
    """Simple DiGraph: one edge per wallet pair with count / first & last timestamp."""
    d = nx.DiGraph()
    d.add_nodes_from(g.nodes(data=True))
    for u, v, data in g.edges(data=True):
        if d.has_edge(u, v):
            e = d[u][v]
            e["count"] += 1
            e["first_ts"] = min(e["first_ts"], data["timestamp"])
            e["last_ts"] = max(e["last_ts"], data["timestamp"])
        else:
            d.add_edge(u, v, count=1, first_ts=data["timestamp"], last_ts=data["timestamp"])
    return d
