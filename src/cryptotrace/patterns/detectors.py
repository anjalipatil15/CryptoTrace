"""Phase 5 - laundering pattern detection on the on-trail subgraph.

Every detector returns plain dict rows (pattern, start/end time, wallets,
detail) so results can go straight into the timeline and the report.
"""

from __future__ import annotations

from collections import defaultdict

import networkx as nx
import pandas as pd


def _trail_edges(g: nx.MultiDiGraph) -> list[tuple[str, str, dict]]:
    return sorted(
        ((u, v, d) for u, v, d in g.edges(data=True) if d.get("valid") and u != v),
        key=lambda e: e[2]["timestamp"],
    )


def _windowed_counterparties(events: list[tuple[pd.Timestamp, str]], window: pd.Timedelta):
    """Max distinct counterparties inside any sliding window. Returns (count, start, end, parties)."""
    best = (0, None, None, set())
    lo = 0
    for hi in range(len(events)):
        while events[hi][0] - events[lo][0] > window:
            lo += 1
        parties = {p for _, p in events[lo:hi + 1]}
        if len(parties) > best[0]:
            best = (len(parties), events[lo][0], events[hi][0], parties)
    return best


def detect_fan_out(g, settings) -> list[dict]:
    cfg = settings["patterns"]
    window = pd.Timedelta(hours=cfg["window_hours"])
    out = defaultdict(list)
    for u, v, d in _trail_edges(g):
        if g.nodes[v].get("category") != "dex":  # a swap is not a distribution
            out[u].append((d["timestamp"], v))
    rows = []
    for u, events in out.items():
        n, start, end, parties = _windowed_counterparties(events, window)
        if n >= cfg["fan_out_min_recipients"]:
            rows.append({"pattern": "fan_out", "start": start, "end": end, "wallets": [u, *sorted(parties)],
                         "detail": f"{u} paid {n} wallets within {cfg['window_hours']}h"})
    return rows


def detect_fan_in(g, settings) -> list[dict]:
    cfg = settings["patterns"]
    window = pd.Timedelta(hours=cfg["window_hours"])
    inc = defaultdict(list)
    for u, v, d in _trail_edges(g):
        inc[v].append((d["timestamp"], u))
    rows = []
    for v, events in inc.items():
        if g.nodes[v].get("category") not in (None, "wallet"):
            continue  # deposits into services are not consolidation
        n, start, end, parties = _windowed_counterparties(events, window)
        if n >= cfg["fan_in_min_senders"]:
            rows.append({"pattern": "fan_in", "start": start, "end": end, "wallets": [*sorted(parties), v],
                         "detail": f"{n} on-trail wallets consolidated into {v}"})
    return rows


def detect_peeling_chains(g, settings) -> list[dict]:
    """Chains where each wallet forwards most of its outflow to one next wallet."""
    cfg = settings["patterns"]
    share = cfg["peeling_main_share"]
    totals = defaultdict(float)
    per_dest = defaultdict(lambda: defaultdict(float))
    first_ts = {}
    for u, v, d in _trail_edges(g):
        totals[(u, d["token"])] += d["tainted_value"]
        per_dest[(u, d["token"])][v] += d["tainted_value"]
        first_ts.setdefault((u, v), d["timestamp"])
    main = {}
    for (u, _token), dests in per_dest.items():
        v, amount = max(dests.items(), key=lambda kv: kv[1])
        if totals[(u, _token)] > 0 and amount / totals[(u, _token)] >= share:
            if g.nodes[v].get("category") in (None, "wallet"):
                main.setdefault(u, v)
    has_pred = set(main.values())
    rows = []
    for start in [u for u in main if u not in has_pred] + [u for u in main if u in has_pred]:
        chain, seen = [start], {start}
        while chain[-1] in main and main[chain[-1]] not in seen:
            nxt = main[chain[-1]]
            chain.append(nxt)
            seen.add(nxt)
        if len(chain) >= cfg["peeling_min_length"] and not any(set(chain) <= set(r["wallets"]) for r in rows):
            rows.append({"pattern": "peeling_chain", "start": first_ts[(chain[0], chain[1])],
                         "end": first_ts[(chain[-2], chain[-1])], "wallets": chain,
                         "detail": f"{len(chain)}-wallet chain forwarding >= {share:.0%} each hop"})
    return rows


def detect_rapid_movement(g, settings) -> list[dict]:
    """Wallets that forwarded stolen funds within `rapid_max_gap_minutes` of first receiving them."""
    gap = pd.Timedelta(minutes=settings["patterns"]["rapid_max_gap_minutes"])
    received, sent = {}, {}
    for u, v, d in _trail_edges(g):  # time-ordered, so the first value seen is the earliest
        received.setdefault(v, d["timestamp"])
        if u in received and d["timestamp"] >= received[u]:
            sent.setdefault(u, d["timestamp"])
    rows = []
    for wallet, t_out in sent.items():
        if g.nodes[wallet].get("category") not in (None, "wallet"):
            continue  # a DEX returning swapped tokens is not laundering behaviour
        held_for = t_out - received[wallet]
        if held_for <= gap:
            rows.append({"pattern": "rapid_movement", "start": received[wallet], "end": t_out,
                         "wallets": [wallet],
                         "detail": f"{wallet} forwarded funds {held_for.total_seconds() / 60:.0f} min after receiving them"})
    return rows


def detect_circular(g, settings) -> list[dict]:
    cfg = settings["patterns"]
    d = nx.DiGraph()
    first = {}
    for u, v, data in _trail_edges(g):
        d.add_edge(u, v)
        first.setdefault((u, v), data["timestamp"])
    rows = []
    for cycle in nx.simple_cycles(d, length_bound=cfg["circular_max_length"]):
        if len(cycle) < 2 or any(g.nodes[n].get("category") == "dex" for n in cycle):
            continue  # wallet -> DEX -> wallet is a swap, not a circular transfer
        times = [first[(a, b)] for a, b in zip(cycle, cycle[1:] + cycle[:1])]
        rows.append({"pattern": "circular", "start": min(times), "end": max(times), "wallets": cycle,
                     "detail": f"funds returned to {cycle[0]} after {len(cycle)} hops"})
    return rows


DETECTORS = {
    "fan_out": detect_fan_out,
    "fan_in": detect_fan_in,
    "peeling_chain": detect_peeling_chains,
    "rapid_movement": detect_rapid_movement,
    "circular": detect_circular,
}


def detect_all(g: nx.MultiDiGraph, settings: dict) -> pd.DataFrame:
    rows = [row for fn in DETECTORS.values() for row in fn(g, settings)]
    return pd.DataFrame(rows, columns=["pattern", "start", "end", "wallets", "detail"])
