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
from collections import Counter, defaultdict

import networkx as nx
import pandas as pd

from .taint import propagate_taint

BAND_ORDER = ["very_high", "high", "medium", "lower", "low", "very_low"]
CATEGORY_HOP = {"mixer": "mixer", "exchange": "exchange_deposit", "bridge": "bridge", "dex": "swap",
                "recovery": "recovery_return"}


def band_for(score: float, bands: dict) -> str:
    for name in BAND_ORDER:
        if score >= bands[name]:
            return name
    return "very_low"


def _window_recipients(g: nx.MultiDiGraph, window: pd.Timedelta) -> dict[tuple, int]:
    """Per on-trail edge (u, v, key): distinct non-DEX wallets u paid within +/- window of it.

    A sliding window over each sender's outflows in time order. Counting afresh for every edge
    is quadratic, which bot senders with tens of thousands of transfers make unusable.
    """
    out = defaultdict(list)
    for u, v, k, data in g.edges(keys=True, data=True):
        if data.get("valid") and u != v:
            out[u].append((data["timestamp"], v, k))
    counts: dict[tuple, int] = {}
    for u, rows in out.items():
        rows.sort(key=lambda r: r[0])
        live: Counter = Counter()
        lo = hi = 0
        for t, v, k in rows:
            while hi < len(rows) and rows[hi][0] <= t + window:
                if g.nodes[rows[hi][1]].get("category") != "dex":
                    live[rows[hi][1]] += 1
                hi += 1
            while rows[lo][0] < t - window:
                r = rows[lo][1]
                if r in live:
                    live[r] -= 1
                    if not live[r]:
                        del live[r]
                lo += 1
            counts[(u, v, k)] = len(live)
    return counts


def classify_edges(g: nx.MultiDiGraph, settings: dict, taint: dict) -> None:
    """Set `hop_type`, `fan_in` and `factor` on every valid edge (in place)."""
    factors = settings["scoring"]["hop_factors"]
    floor = settings["scoring"]["fan_in_floor"]
    pat = settings["patterns"]
    window = pd.Timedelta(hours=pat["window_hours"])

    recipients = _window_recipients(g, window)
    # Fan-in counts ALL distinct senders (clean ones too) -- that's what creates ambiguity.
    all_senders = {n: {u for u, _ in g.in_edges(n)} for n in g.nodes}

    for u, v, k, data in g.edges(keys=True, data=True):
        if not data.get("valid"):
            data.update(hop_type=None, fan_in=False, factor=0.0)
            continue
        category = g.nodes[v].get("category", "wallet")
        if category in CATEGORY_HOP:
            hop_type = CATEGORY_HOP[category]
        else:
            n_recipients = recipients.get((u, v, k), 0)
            if n_recipients >= pat["fan_out_min_recipients"]:
                hop_type = "fan_out"
            elif n_recipients >= pat["split_min_recipients"]:
                hop_type = "split"
            else:
                hop_type = "direct"
        fan_in = category not in CATEGORY_HOP and len(all_senders[v]) >= pat["fan_in_min_senders"]
        mult = floor + (1 - floor) * taint["token_ratio"].get((v, data["token"]), 0.0) if fan_in else 1.0
        data.update(hop_type=hop_type, fan_in=fan_in, fan_in_mult=mult, factor=factors[hop_type] * mult)


def prepare(g: nx.MultiDiGraph, settings: dict, taint: dict) -> dict:
    """Classify every edge once and keep, per wallet pair, the best fan-in multiplier per hop type.

    Hop types and fan-in multipliers don't depend on the hop factors, so sensitivity and
    calibration can re-score with new factors from this table instead of re-classifying every
    transfer (which on Nomad means half a million edges per run).
    """
    classify_edges(g, settings, taint)
    pairs: dict[tuple, dict] = defaultdict(dict)
    for u, v, data in g.edges(data=True):
        # Don't route paths *through* terminal services: they end the trail.
        if data.get("hop_type") is None or u == v or g.nodes[u].get("category") in CATEGORY_HOP:
            continue
        opts = pairs[(u, v)]
        if data["fan_in_mult"] > opts.get(data["hop_type"], -1.0):
            opts[data["hop_type"]] = data["fan_in_mult"]
    return {"pairs": dict(pairs), "taint": taint}


def score(g: nx.MultiDiGraph, seeds: list[str], settings: dict, start_time: pd.Timestamp | None = None,
          taint: dict | None = None, prepared: dict | None = None) -> pd.DataFrame:
    """Run taint + classification + max-product path search. Returns one row per on-trail wallet.

    Neither taint nor the hop classification depends on the hop factors, so callers that re-score
    the same graph many times (sensitivity, calibration) pass `prepared` (from `prepare`) once.
    """
    seeds = [s.lower() for s in seeds if s.lower() in g]
    if prepared is None:
        if taint is None:
            taint = propagate_taint(g, seeds, start_time)
        prepared = prepare(g, settings, taint)
    taint = prepared["taint"]
    factors = settings["scoring"]["hop_factors"]

    # Best (highest) factor per wallet pair; cost = -log(factor).
    best = nx.DiGraph()
    for (u, v), opts in prepared["pairs"].items():
        hop_type, factor = max(((h, factors[h] * m) for h, m in opts.items()), key=lambda x: x[1])
        if factor > 0:
            best.add_edge(u, v, factor=factor, hop_type=hop_type, cost=-math.log(factor))

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
