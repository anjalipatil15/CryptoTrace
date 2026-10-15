"""Time-ordered haircut taint propagation.

For every wallet and token we track tainted vs. total inflow. Each outgoing
transfer carries taint in proportion to the sender's taint ratio *at that
moment* (haircut method). Seeds are fully tainted.

Taint answers "what fraction of these funds is stolen?" -- a separate
question from confidence ("how sure are we this wallet is on the trail?").
The scoring engine uses taint to judge fan-in: consolidating only stolen
funds is not ambiguous, mixing them with clean funds is.

Swaps: if a wallet sends tainted funds out and receives an asset back in the
SAME transaction, the incoming leg is a swap proceed and inherits the taint of
what was sent (marked `swap_return`). This matters because DEX routers and the
WETH contract that pay out the proceeds are not on the trail themselves.
A wallet that sends a token it never received directly (it got it by
swapping) sends it with its highest taint ratio across tokens.
"""

from __future__ import annotations

from collections import defaultdict

import networkx as nx
import pandas as pd


def _time_groups(g: nx.MultiDiGraph) -> list[list]:
    """Edges grouped by timestamp (one block), groups in time order."""
    groups = defaultdict(list)
    for e in g.edges(keys=True, data=True):
        groups[e[3]["timestamp"]].append(e)
    return [sorted(groups[ts], key=lambda e: e[2]) for ts in sorted(groups)]


def propagate_taint(g: nx.MultiDiGraph, seeds: list[str], start_time: pd.Timestamp | None = None) -> dict:
    """Annotate edges with `tainted_value` and `valid`; return per-node results.

    An edge is *valid* (on the trail) if its sender already held tainted funds
    when it was sent -- this is what makes the trail time-respecting.
    """
    seeds = {s.lower() for s in seeds}
    tainted_in: dict[tuple[str, str], float] = defaultdict(float)
    total_in: dict[tuple[str, str], float] = defaultdict(float)
    node_tokens: dict[str, set[str]] = defaultdict(set)
    arrival: dict[str, pd.Timestamp] = {}
    start = start_time if start_time is not None else pd.Timestamp.min.tz_localize("UTC")
    for s in seeds:
        arrival[s] = start

    def ratio(node: str, token: str) -> float:
        if node in seeds:
            return 1.0
        key = (node, token)
        if total_in[key] > 0:
            return tainted_in[key] / total_in[key]
        # Token never received directly -> came from a swap; inherit best ratio.
        return _best_ratio(node)

    def _best_ratio(node: str) -> float:
        ratios = [tainted_in[(node, t)] / total_in[(node, t)] for t in node_tokens[node] if total_in[(node, t)] > 0]
        return max(ratios, default=0.0)

    tx_out_ratio: dict[tuple[str, str], float] = {}  # (tx_hash, sender) -> taint ratio of what it sent

    def held(u: str, ts) -> bool:
        return u in arrival and arrival[u] <= ts

    def process(u, v, data) -> None:
        r = ratio(u, data["token"]) if held(u, data["timestamp"]) else 0.0
        tainted = data["value"] * r
        data["tainted_value"] = tainted
        data["valid"] = tainted > 0
        data["swap_return"] = False
        if tainted > 0:
            key = (data["tx_hash"], u)
            tx_out_ratio[key] = max(tx_out_ratio.get(key, 0.0), r)
        elif (data["tx_hash"], v) in tx_out_ratio:
            # v paid tainted funds out in this very transaction and is paid back: a swap.
            tainted = data["value"] * tx_out_ratio[(data["tx_hash"], v)]
            data["tainted_value"] = tainted
            data["swap_return"] = True
        node_tokens[v].add(data["token"])
        total_in[(v, data["token"])] += data["value"]
        tainted_in[(v, data["token"])] += tainted
        if tainted > 0 and (v not in arrival or data["timestamp"] < arrival[v]):
            arrival[v] = data["timestamp"]

    for group in _time_groups(g):
        # Within one block, process edges whose sender already holds taint first, repeatedly.
        # A swap's legs (wallet -> DEX -> wallet) share a timestamp and form a cycle,
        # so the deposit leg must go before the return leg for taint to carry over.
        pending = group
        while pending:
            ready = [e for e in pending if held(e[0], e[3]["timestamp"])]
            if not ready:
                ready = pending
            for u, v, _, data in ready:
                process(u, v, data)
            pending = [e for e in pending if e not in ready]

    node_ratio = {}
    for node in g.nodes:
        if node in seeds:
            node_ratio[node] = 1.0
            continue
        node_ratio[node] = _best_ratio(node)

    token_ratio = {key: (tainted_in[key] / total_in[key]) for key in total_in if total_in[key] > 0}
    return {"arrival": arrival, "node_ratio": node_ratio, "token_ratio": token_ratio}
