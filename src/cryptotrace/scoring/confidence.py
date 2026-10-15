"""Phase 4 - rule-based confidence scoring.

Model
-----
1. Every on-trail edge u -> v gets a *hop type* from transparent rules
   (mixer / exchange_deposit / bridge / swap by the receiver's label,
   otherwise direct / split / fan_out by how many wallets u paid in the window).
2. The hop type gives a multiplicative factor (config `scoring.hop_factors`).
   If v consolidates funds from several senders (fan-in), the factor is further
   scaled by v's taint ratio: fan_in_floor + (1 - fan_in_floor) * taint.
3. A wallet's confidence is the best (max-product) path from any seed, found
   with Dijkstra on -log(factor). The path itself is kept as the explanation.
4. Scores map to the proposal's ordinal bands (Very High ... Very Low).

So "Single intermediate wallet = High" is not a separate rule: two direct
hops (0.92^2 = 0.85) land in the High band on their own.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections import defaultdict

import networkx as nx
import pandas as pd

from .taint import propagate_taint

BAND_ORDER = ["very_high", "high", "medium", "lower", "low", "very_low"]
CATEGORY_HOP = {"mixer": "mixer", "exchange": "exchange_deposit", "bridge": "bridge", "dex": "swap"}


def band_for(score: float, bands: dict) -> str:
    for name in BAND_ORDER:
        if score >= bands[name]:
            return name
    return "very_low"


def _recipient_index(g: nx.MultiDiGraph) -> dict[str, tuple[list, list]]:
    """Per sender: on-trail outflows sorted by time, as (timestamps, recipients)."""
    out = defaultdict(list)
    for u, v, data in g.edges(data=True):
        if data.get("valid") and u != v:
            out[u].append((data["timestamp"], v))
    return {u: ([t for t, _ in sorted(rows)], [r for _, r in sorted(rows)]) for u, rows in out.items()}


def classify_edges(g: nx.MultiDiGraph, settings: dict, taint: dict) -> None:
    """Set `hop_type`, `fan_in` and `factor` on every valid edge (in place)."""
    factors = settings["scoring"]["hop_factors"]
    floor = settings["scoring"]["fan_in_floor"]
    pat = settings["patterns"]
    window = pd.Timedelta(hours=pat["window_hours"])

    index = _recipient_index(g)
    # Fan-in counts ALL distinct senders (clean ones too) -- that's what creates ambiguity.
    all_senders = {n: {u for u, _ in g.in_edges(n)} for n in g.nodes}

    for u, v, data in g.edges(data=True):
        if not data.get("valid"):
            data.update(hop_type=None, fan_in=False, factor=0.0)
            continue
        category = g.nodes[v].get("category", "wallet")
        if category in CATEGORY_HOP:
            hop_type = CATEGORY_HOP[category]
        else:
            ts, rec = index.get(u, ([], []))
            lo, hi = bisect_left(ts, data["timestamp"] - window), bisect_right(ts, data["timestamp"] + window)
            n_recipients = len({r for r in rec[lo:hi] if g.nodes[r].get("category") != "dex"})
            if n_recipients >= pat["fan_out_min_recipients"]:
                hop_type = "fan_out"
            elif n_recipients >= pat["split_min_recipients"]:
                hop_type = "split"
            else:
                hop_type = "direct"
        factor = factors[hop_type]
        fan_in = category not in CATEGORY_HOP and len(all_senders[v]) >= pat["fan_in_min_senders"]
        if fan_in:
            factor *= floor + (1 - floor) * taint["token_ratio"].get((v, data["token"]), 0.0)
        data.update(hop_type=hop_type, fan_in=fan_in, factor=factor)


def score(g: nx.MultiDiGraph, seeds: list[str], settings: dict, start_time: pd.Timestamp | None = None) -> pd.DataFrame:
    """Run taint + classification + max-product path search. Returns one row per on-trail wallet."""
    seeds = [s.lower() for s in seeds if s.lower() in g]
    taint = propagate_taint(g, seeds, start_time)
    classify_edges(g, settings, taint)

    # Best (highest) factor per wallet pair; cost = -log(factor).
    best = nx.DiGraph()
    for u, v, k, data in g.edges(keys=True, data=True):
        if data.get("factor", 0) > 0 and u != v:
            if not best.has_edge(u, v) or data["factor"] > best[u][v]["factor"]:
                best.add_edge(u, v, factor=data["factor"], hop_type=data["hop_type"], key=k,
                              cost=-math.log(data["factor"]))
    # Don't route paths *through* terminal services: they end the trail.
    for n in list(best.nodes):
        if g.nodes[n].get("category") in CATEGORY_HOP:
            best.remove_edges_from(list(best.out_edges(n)))

    best.add_nodes_from(seeds)
    dist, paths = nx.multi_source_dijkstra(best, set(seeds), weight="cost")

    bands = settings["scoring"]["bands"]
    rows = []
    for node, cost in dist.items():
        conf = math.exp(-cost)
        path = paths[node]
        hop_types = [best[a][b]["hop_type"] for a, b in zip(path, path[1:])]
        attrs = g.nodes[node]
        rows.append(
            {
                "address": node,
                "entity": attrs.get("entity", ""),
                "category": attrs.get("category", "wallet"),
                "hop": len(path) - 1,
                "confidence": conf,
                "band": band_for(conf, bands),
                "taint_ratio": taint["node_ratio"].get(node, 0.0),
                "first_tainted": taint["arrival"].get(node),
                "path": path,
                "path_hop_types": hop_types,
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(["hop", "confidence"], ascending=[True, False]).reset_index(drop=True)


def edge_table(g: nx.MultiDiGraph) -> pd.DataFrame:
    rows = [
        {"from_addr": u, "to_addr": v, "transfer_id": k, **{
            c: d.get(c) for c in ("tx_hash", "timestamp", "value", "token", "tainted_value", "hop_type", "fan_in", "factor")
        }}
        for u, v, k, d in g.edges(keys=True, data=True) if d.get("valid")
    ]
    return pd.DataFrame(rows)
