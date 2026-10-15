"""Case-level analysis that turns the scored graph into an explained investigation.

Everything the dashboard shows is computed here, in plain Python, so every
number on screen can be traced back to a function:

  * USD valuation of every transfer (case-level reference prices)
  * the theft itself (what was drained, from where)
  * per-wallet money flows, unspent balances and an inferred *role*
  * where the money ended up (endpoints) and how confident each endpoint is
  * a staged narrative of the case (theft -> conversion -> distribution -> exit)
  * chart series (activity over time, confidence decay, money-flow Sankey)
"""

from __future__ import annotations

from collections import Counter, defaultdict

import networkx as nx
import pandas as pd

from ..processing.preprocess import min_value_for
from ..scoring.confidence import BAND_ORDER, CATEGORY_HOP

SERVICE_NAMES = {"mixer": "Mixer", "exchange": "Exchange", "bridge": "Bridge", "dex": "DEX pool"}
ROLE_ORDER = ["Attacker wallet", "Swap helper", "Distributor", "Mixer depositor", "Exchange depositor",
              "Bridge user", "Holding wallet", "Pass-through", "Mixer", "Exchange", "Bridge", "DEX pool"]
ROLE_HELP = {
    "Attacker wallet": "Received the stolen funds directly from the exploited contract.",
    "Swap helper": "Mostly sent funds into DEX pools, converting tokens into another asset.",
    "Distributor": "Split funds across several wallets.",
    "Mixer depositor": "Mostly sent funds into a mixer.",
    "Exchange depositor": "Mostly sent funds to a custodial exchange.",
    "Bridge user": "Mostly sent funds to a cross-chain bridge.",
    "Holding wallet": "Still holds most of what it received.",
    "Pass-through": "Forwarded funds onward to a single wallet.",
}
PATTERN_HELP = {
    "fan_out": ("Fan-out", "One wallet pays many wallets in a short window, which multiplies the paths an investigator must follow."),
    "fan_in": ("Fan-in", "Many wallets pay into one, which merges funds and, if clean money is mixed in, dilutes attribution."),
    "peeling_chain": ("Peeling chain", "Funds hop through a long line of wallets, each forwarding most of what it received."),
    "rapid_movement": ("Rapid movement", "A wallet forwards stolen funds within minutes of receiving them, a sign of automation."),
    "circular": ("Circular transfer", "Funds return to a wallet seen earlier on the trail, often after a swap or to confuse tracing."),
}


def short(addr: str) -> str:
    return f"{addr[:6]}…{addr[-4:]}" if addr and addr.startswith("0x") else addr


def fmt_usd(x: float) -> str:
    if x >= 1e9:
        return f"${x / 1e9:.2f}B"
    if x >= 1e6:
        return f"${x / 1e6:.1f}M"
    if x >= 1e3:
        return f"${x / 1e3:.0f}K"
    return f"${x:,.0f}"


def fmt_amount(x: float, token: str) -> str:
    return f"{x:,.0f} {token}" if x >= 100 else f"{x:,.2f} {token}"


def fmt_span(td: pd.Timedelta) -> str:
    hours = td.total_seconds() / 3600
    if hours < 1:
        return f"{td.total_seconds() / 60:.0f} minutes"
    if hours < 48:
        return f"{hours:.1f} hours"
    return f"{hours / 24:.1f} days"


class Pricer:
    def __init__(self, prices: dict):
        self.prices = {k.upper(): float(v) for k, v in (prices or {}).items()}
        self.unpriced: set[str] = set()

    def __call__(self, token: str, amount: float) -> float:
        p = self.prices.get(str(token).upper())
        if p is None:
            self.unpriced.add(token)
            return 0.0
        return amount * p


def build_insights(g: nx.MultiDiGraph, scored: pd.DataFrame, patterns: pd.DataFrame, validation: dict,
                   case, contract_names: dict | None = None) -> dict:
    settings = case.settings
    extra = getattr(case, "extra", None) or {}
    price = Pricer(extra.get("prices_usd"))
    seeds = set(case.seeds)
    names = dict(contract_names or {})
    info = scored.set_index("address").to_dict("index") if not scored.empty else {}
    on_trail = set(info)

    def category(a):
        return g.nodes[a].get("category", "wallet") if a in g else "wallet"

    def display(a):
        if a in seeds:
            return "Attacker wallet"
        ent = g.nodes[a].get("entity") if a in g else ""
        return ent or names.get(a) or short(a)

    # ---- 1. Theft: inflows into the seed from outside the trail ------------------
    theft_rows = []
    for u, v, d in g.edges(data=True):
        if v in seeds and u not in on_trail and d["value"] >= max(min_value_for(d["token"], settings), 1e-9) \
                and (case.start_time is None or d["timestamp"] >= case.start_time):
            theft_rows.append({"source": u, "target": v, "token": d["token"], "amount": d["value"],
                               "usd": price(d["token"], d["value"]), "time": d["timestamp"], "tx": d["tx_hash"]})
    theft = pd.DataFrame(theft_rows, columns=["source", "target", "token", "amount", "usd", "time", "tx"])
    theft_usd = float(theft["usd"].sum())

    # ---- 2. Per-wallet flows (tainted value only) --------------------------------
    t_in, t_out = defaultdict(float), defaultdict(float)
    out_cat = defaultdict(lambda: defaultdict(float))
    recipients = defaultdict(set)
    trail_edges = []
    for u, v, k, d in g.edges(keys=True, data=True):
        if not d.get("valid") or u not in on_trail or v not in on_trail:
            continue
        usd = price(d["token"], d["tainted_value"])
        trail_edges.append((u, v, d, usd))
        t_in[v] += usd
        t_out[u] += usd
        out_cat[u][category(v)] += usd
        if category(v) == "wallet":
            recipients[u].add(v)
    # Swap proceeds: paid back to a trail wallet in the same tx by a router/WETH contract off the trail.
    swap_returns = []
    for u, v, d in g.edges(data=True):
        if d.get("swap_return") and v in on_trail and d.get("tainted_value", 0) > 0:
            usd = price(d["token"], d["tainted_value"])
            swap_returns.append((u, v, d, usd))
            t_in[v] += usd
    for s in seeds:  # the stolen funds are the attacker wallet's (untainted-sender) inflow
        t_in[s] += float(theft.loc[theft["target"] == s, "usd"].sum())

    def role(a):
        cat = category(a)
        if cat != "wallet":
            return SERVICE_NAMES.get(cat, cat.title())
        if a in seeds:
            return "Attacker wallet"
        out = t_out[a]
        if out > 0:
            shares = {c: v / out for c, v in out_cat[a].items()}
            for cat_name, role_name in (("dex", "Swap helper"), ("mixer", "Mixer depositor"),
                                        ("exchange", "Exchange depositor"), ("bridge", "Bridge user")):
                if shares.get(cat_name, 0) >= (0.4 if cat_name == "dex" else 0.5):
                    return role_name
            if len(recipients[a]) >= 2:
                return "Distributor"
        if t_in[a] > 0 and (t_in[a] - out) / t_in[a] >= 0.5:
            return "Holding wallet"
        return "Pass-through"

    roles = {a: role(a) for a in on_trail}
    residual = {a: max(t_in[a] - t_out[a], 0.0) if category(a) == "wallet" else 0.0 for a in on_trail}

    # ---- 3. Patterns per wallet -----------------------------------------------
    wallet_patterns = defaultdict(list)
    pattern_rows = []
    for i, r in enumerate(patterns.itertuples()):
        title, help_text = PATTERN_HELP.get(r.pattern, (r.pattern, ""))
        for w in r.wallets:
            wallet_patterns[w].append(title)
        pattern_rows.append({"id": i, "type": r.pattern, "title": title, "help": help_text, "detail": r.detail,
                             "start": r.start, "end": r.end, "wallets": list(r.wallets)})

    # ---- 4. Wallet records (graph nodes + table) ------------------------------
    factors = settings["scoring"]["hop_factors"]
    # Layout: one column per hop. DEX pools are side branches (funds come straight back),
    # so they get their own compact column below the hop they belong to.
    hop_cols = defaultdict(list)
    for a in on_trail:
        hop_cols[(info[a]["hop"], category(a) == "dex")].append(a)
    pos = {}
    for (hop, is_dex), members in hop_cols.items():
        members.sort(key=lambda a: (ROLE_ORDER.index(roles[a]) if roles[a] in ROLE_ORDER else 99,
                                    -info[a]["confidence"], -t_in[a]))
        n = len(members)
        if is_dex:  # compact grid, placed under the wallets of the previous hop
            cols = max(1, min(7, n))
            for i, a in enumerate(members):
                pos[a] = {"x": (hop - 1) * 380 + 70 + (i % cols) * 42, "y": 260 + (i // cols) * 42}
        else:
            gap = 64 if n <= 12 else 46
            for i, a in enumerate(members):
                pos[a] = {"x": hop * 380, "y": (i - (n - 1) / 2) * gap}

    wallets = []
    for a, r in info.items():
        path = list(r["path"])
        steps = []
        for (x, y), ht in zip(zip(path, path[1:]), r["path_hop_types"]):
            edge_factors = [d.get("factor", 0) for d in g.get_edge_data(x, y, default={}).values()]
            steps.append({"from": x, "to": y, "from_name": display(x), "to_name": display(y), "hop_type": ht,
                          "factor": max(edge_factors) if edge_factors else factors.get(ht)})
        wallets.append({
            "address": a, "name": display(a), "category": category(a), "role": roles[a],
            "hop": int(r["hop"]), "confidence": float(r["confidence"]), "band": r["band"],
            "taint": float(r["taint_ratio"]), "in_usd": t_in[a], "out_usd": t_out[a], "residual_usd": residual[a],
            "first_tainted": (theft["time"].min() if a in seeds and not theft.empty else r["first_tainted"]), "patterns": sorted(set(wallet_patterns.get(a, []))),
            "path": steps, "x": pos[a]["x"], "y": pos[a]["y"],
        })

    # ---- 5. Collapsed edges for the graph -----------------------------------
    agg = {}
    for u, v, d, usd in trail_edges:
        e = agg.setdefault((u, v), {"source": u, "target": v, "usd": 0.0, "count": 0, "tokens": Counter(),
                                    "hop_type": d.get("hop_type"), "factor": 0.0, "first": d["timestamp"],
                                    "last": d["timestamp"], "txs": []})
        e["usd"] += usd
        e["count"] += 1
        e["tokens"][d["token"]] += d["tainted_value"]
        if d.get("factor", 0) > e["factor"]:
            e["factor"], e["hop_type"] = d["factor"], d.get("hop_type")
        e["first"], e["last"] = min(e["first"], d["timestamp"]), max(e["last"], d["timestamp"])
        if len(e["txs"]) < 5:
            e["txs"].append(d["tx_hash"])
    edges = []
    for (u, v), e in agg.items():
        e["tokens"] = {t: round(x, 6) for t, x in e["tokens"].most_common()}
        e["back"] = info[v]["hop"] <= info[u]["hop"]
        edges.append(e)

    # ---- 6. Endpoints: where did the money end up? ---------------------------
    endpoints = []
    for a in on_trail:
        cat = category(a)
        if cat in ("mixer", "exchange", "bridge"):
            amount = t_in[a]
        elif cat == "wallet" and residual[a] > max(1000.0, 0.01 * t_in[a]):
            amount = residual[a]
        else:
            continue
        endpoints.append({"address": a, "name": display(a), "kind": SERVICE_NAMES.get(cat, "Still held in wallet"),
                          "category": cat, "usd": amount, "confidence": info[a]["confidence"], "band": info[a]["band"],
                          "hop": info[a]["hop"]})
    endpoints.sort(key=lambda e: -e["usd"])
    endpoint_total = sum(e["usd"] for e in endpoints)
    value_by_band = {b: sum(e["usd"] for e in endpoints if e["band"] == b) for b in BAND_ORDER}
    value_by_kind = defaultdict(float)
    for e in endpoints:
        value_by_kind[e["kind"]] += e["usd"]

    # ---- 7. Narrative -------------------------------------------------------
    stages = _narrative(trail_edges, swap_returns, theft, theft_usd, info, roles, residual, display, category, seeds)

    # ---- 8. KPIs -------------------------------------------------------------
    theft_time = theft["time"].min() if not theft.empty else None
    mixer_times = [d["timestamp"] for u, v, d, _ in trail_edges if category(v) == "mixer"]
    kpis = {
        "stolen_usd": theft_usd,
        "stolen_assets": int(theft["token"].nunique()) if not theft.empty else 0,
        "traced_usd": endpoint_total,
        "traced_share": endpoint_total / theft_usd if theft_usd else None,
        "wallets": sum(1 for a in on_trail if category(a) == "wallet"),
        "services": sum(1 for a in on_trail if category(a) != "wallet"),
        "max_hop": int(max((w["hop"] for w in wallets), default=0)),
        "patterns": len(pattern_rows),
        "transfers": sum(e["count"] for e in edges),
        "theft_time": theft_time,
        "first_mixer": min(mixer_times) if mixer_times else None,
        "time_to_mixer": fmt_span(min(mixer_times) - theft_time) if mixer_times and theft_time is not None else None,
        "mixer_deposits": len(mixer_times),
        "mixer_usd": value_by_kind.get("Mixer", 0.0),
        "low_conf_share": (value_by_band["low"] + value_by_band["very_low"]) / endpoint_total if endpoint_total else None,
    }

    summary = _summary(kpis, value_by_kind, endpoint_total, stages)

    # ---- 9. Chart series -----------------------------------------------------
    activity = defaultdict(lambda: defaultdict(float))
    for u, v, d, usd in trail_edges:
        activity[d["timestamp"].floor("D")][d.get("hop_type") or "other"] += usd
    activity_rows = [{"day": day, **vals} for day, vals in sorted(activity.items())]

    return {
        "case": {"id": case.case_id, "name": case.name, "description": (case.description or "").strip(),
                 "date": str(extra.get("date", "")), "attribution": extra.get("attribution", ""),
                 "references": extra.get("references", []), "seeds": list(case.seeds),
                 "prices": price.prices, "unpriced_tokens": sorted(price.unpriced)},
        "kpis": kpis,
        "summary": summary,
        "theft": {"total_usd": theft_usd,
                  "by_token": theft.groupby("token").agg(amount=("amount", "sum"), usd=("usd", "sum"))
                  .sort_values("usd", ascending=False).reset_index().to_dict("records") if not theft.empty else [],
                  "sources": [{"address": s, "name": names.get(s) or short(s), "usd": float(grp["usd"].sum()),
                               "tokens": sorted(grp["token"].unique())}
                              for s, grp in theft.groupby("source")] if not theft.empty else [],
                  "transfers": theft.sort_values("time").to_dict("records")},
        "stages": stages,
        "wallets": sorted(wallets, key=lambda w: (w["hop"], -w["confidence"])),
        "edges": edges,
        "patterns": pattern_rows,
        "endpoints": endpoints,
        "value_by_band": value_by_band,
        "value_by_kind": dict(value_by_kind),
        "activity": activity_rows,
        "sankey": _sankey(trail_edges, theft_usd, info, roles, residual, category, display),
        "roles": {r: ROLE_HELP.get(r, "") for r in ROLE_ORDER},
        "method": {"hop_factors": factors, "bands": settings["scoring"]["bands"],
                   "fan_in_floor": settings["scoring"]["fan_in_floor"], "patterns": settings["patterns"],
                   "tracing": {k: settings["tracing"][k] for k in ("max_hops", "max_addresses", "min_value")},
                   "allowlist": settings["tracing"].get("token_allowlist", {})},
        "validation": validation,
    }


def _summary(kpis, value_by_kind, endpoint_total, stages) -> list[str]:
    """Plain-language findings for the top of the dashboard."""
    out = []
    if kpis["theft_time"] is not None:
        out.append(f"On {kpis['theft_time']:%d %b %Y}, {fmt_usd(kpis['stolen_usd'])} in {kpis['stolen_assets']} assets "
                   "was drained into a single attacker-controlled wallet.")
    keys = [s["key"] for s in stages]
    steps = [w for k, w in (("conversion", "swapped into ETH"), ("consolidation", "regrouped"),
                            ("distribution", "split across fresh wallets")) if k in keys]
    if steps:
        out.append("The funds were then " + ", ".join(steps[:-1]) + (" and " if len(steps) > 1 else "") + steps[-1] + ".")
    if kpis["time_to_mixer"]:
        out.append(f"The first mixer deposit came {kpis['time_to_mixer']} after the theft; {kpis['mixer_deposits']} deposits "
                   f"moved {fmt_usd(kpis['mixer_usd'])} into the mixer.")
    if endpoint_total:
        shares = ", ".join(f"{v / endpoint_total:.0%} to {k.lower()}" for k, v in
                           sorted(value_by_kind.items(), key=lambda kv: -kv[1]))
        out.append(f"Of the {fmt_usd(endpoint_total)} traced to an endpoint: {shares}.")
    if kpis["low_conf_share"] is not None:
        out.append(f"{kpis['low_conf_share']:.0%} of that value ends at Low or Very Low confidence. That is where "
                   "on-chain attribution stops and off-chain evidence (exchange records, mixer analysis) is needed.")
    return out


def _narrative(trail_edges, swap_returns, theft, theft_usd, info, roles, residual, display, category, seeds) -> list[dict]:
    stages = []

    def conf_range(addrs):
        vals = [info[a]["confidence"] for a in addrs if a in info]
        return [min(vals), max(vals)] if vals else None

    if not theft.empty:
        by_tok = theft.groupby("token").agg(amount=("amount", "sum"), usd=("usd", "sum")).sort_values("usd", ascending=False)
        parts = ", ".join(fmt_amount(r.amount, t) for t, r in by_tok.head(4).iterrows())
        stages.append({
            "key": "theft", "title": "Theft", "start": theft["time"].min(), "end": theft["time"].max(),
            "text": (f"{len(theft)} transfers drained {fmt_usd(theft_usd)} across {len(by_tok)} assets "
                     f"({parts}{', …' if len(by_tok) > 4 else ''}) from {theft['source'].nunique()} contract address(es) "
                     f"into the attacker wallet {short(next(iter(seeds)))} within "
                     f"{fmt_span(theft['time'].max() - theft['time'].min())}."),
            "wallets": sorted(seeds), "confidence": [1.0, 1.0], "usd": theft_usd,
        })

    swaps = [(u, v, d, usd) for u, v, d, usd in trail_edges if category(v) == "dex"]
    returns = [(u, v, d, usd) for u, v, d, usd in trail_edges if category(u) == "dex"] + swap_returns
    if swaps:
        sent = Counter()
        for u, v, d, usd in swaps:
            sent[d["token"]] += usd
        got = Counter()
        for u, v, d, usd in returns:
            got[d["token"]] += d["tainted_value"]
        helpers = {u for u, *_ in swaps}
        times = [d["timestamp"] for _, _, d, _ in swaps + returns]
        swap_times = [d["timestamp"] for _, _, d, _ in swaps]
        stages.append({
            "key": "conversion", "title": "Conversion", "start": min(swap_times), "end": max(swap_times),
            "text": (f"{len(helpers)} wallet(s) swapped {fmt_usd(sum(sent.values()))} of "
                     f"{', '.join(t for t, _ in sent.most_common(5))} through {len({v for _, v, *_ in swaps})} DEX pools"
                     + (f", receiving {', '.join(fmt_amount(x, t) for t, x in got.most_common(3))} in return" if got else "")
                     + ". Converting to a single asset (usually ETH) is the standard step before mixing."),
            "wallets": sorted(helpers), "confidence": conf_range(helpers), "usd": sum(sent.values()),
        })

    back = [(u, v, d, usd) for u, v, d, usd in trail_edges
            if category(u) == "wallet" and category(v) == "wallet" and info[v]["hop"] < info[u]["hop"]]
    if back:
        tokens = Counter()
        for u, v, d, usd in back:
            tokens[d["token"]] += d["tainted_value"]
        times = [d["timestamp"] for _, _, d, _ in back]
        senders = {u for u, *_ in back}
        stages.append({
            "key": "consolidation", "title": "Consolidation", "start": min(times), "end": max(times),
            "text": (f"{len(senders)} wallet(s) sent {', '.join(fmt_amount(x, t) for t, x in tokens.most_common(3))} "
                     f"({fmt_usd(sum(u for *_, u in back))}) back up the trail to {len({v for _, v, *_ in back})} earlier wallet(s), "
                     "regrouping the converted funds before distribution."),
            "wallets": sorted(senders), "confidence": conf_range(senders), "usd": sum(u for *_, u in back),
        })

    dist = [(u, v, d, usd) for u, v, d, usd in trail_edges
            if category(v) == "wallet" and roles.get(u) in ("Attacker wallet", "Distributor")
            and roles.get(v) not in ("Swap helper", "Pass-through") and info[v]["hop"] > info[u]["hop"]]
    if dist:
        tiers = defaultdict(list)
        for row in dist:
            tiers[info[row[0]]["hop"]].append(row)
        parts, tier_usd = [], []
        for hop in sorted(tiers):
            rows = tiers[hop]
            senders, receivers = {u for u, *_ in rows}, {v for _, v, *_ in rows}
            amounts = Counter(round(d["tainted_value"], 1) for _, _, d, _ in rows)
            common, n_common = amounts.most_common(1)[0]
            tok = rows[0][2]["token"]
            who = "the attacker wallet" if senders <= seeds else f"{len(senders)} wallets"
            parts.append(f"{who} split {fmt_usd(sum(r[3] for r in rows))} into {len(receivers)} wallets"
                         + (f" ({n_common} identical transfers of {common:,.0f} {tok})" if n_common > 2 else ""))
            tier_usd.append(sum(r[3] for r in rows))
        times = [d["timestamp"] for _, _, d, _ in dist]
        everyone = {u for u, *_ in dist} | {v for _, v, *_ in dist}
        stages.append({
            "key": "distribution", "title": "Distribution", "start": min(times), "end": max(times),
            "text": ("In " + (f"{len(parts)} rounds" if len(parts) > 1 else "one round") + ", " + "; then ".join(parts)
                     + ". Identical, round amounts point to a script. Each split multiplies the paths to follow and lowers confidence."),
            "wallets": sorted(everyone), "confidence": conf_range(everyone - seeds), "usd": max(tier_usd),
        })

    for cat, title, verb in (("mixer", "Mixing", "deposited into"), ("exchange", "Exchange cash-out", "sent to"),
                             ("bridge", "Bridge exit", "bridged out via")):
        rows = [(u, v, d, usd) for u, v, d, usd in trail_edges if category(v) == cat]
        if not rows:
            continue
        times = [d["timestamp"] for _, _, d, _ in rows]
        depositors = {u for u, *_ in rows}
        services = {v for _, v, *_ in rows}
        sizes = Counter(round(d["value"], 2) for _, _, d, _ in rows)
        size, n_size = sizes.most_common(1)[0]
        tok = Counter(d["token"] for _, _, d, _ in rows).most_common(1)[0][0]
        extra = {
            "mixer": " A mixer pools deposits from many users, so ownership after this point cannot be established on-chain.",
            "exchange": " Exchanges are custodial; identifying the account holder requires a request to the exchange.",
            "bridge": " The funds leave Ethereum here; following them requires tracing on the destination chain.",
        }[cat]
        stages.append({
            "key": cat, "title": title, "start": min(times), "end": max(times),
            "text": (f"{len(depositors)} wallet(s) {verb} {', '.join(display(s) for s in services)} in {len(rows)} transfers "
                     f"totalling {fmt_usd(sum(u for *_, u in rows))} over {fmt_span(max(times) - min(times))}"
                     + (f"; {n_size} of them were exactly {size:,.2f} {tok}" if n_size > 2 else "") + "." + extra),
            "wallets": sorted(depositors | services), "confidence": conf_range(services), "usd": sum(u for *_, u in rows),
        })

    held = {a: r for a, r in residual.items() if r > 1000}
    if held:
        stages.append({
            "key": "unresolved", "title": "Still held", "start": None, "end": None,
            "text": (f"{fmt_usd(sum(held.values()))} of tainted value is still sitting in {len(held)} wallet(s) on the trail "
                     "(received but not yet moved). These are candidates for monitoring or a freeze request."),
            "wallets": sorted(held), "confidence": conf_range(held), "usd": sum(held.values()),
        })
    return stages


def _sankey(trail_edges, theft_usd, info, roles, residual, category, display) -> dict:
    """Money flow between wallet groups (hop x role).

    A Sankey diagram cannot contain cycles, so money flowing back up the trail
    (swap proceeds returned to the attacker) enters from a separate source node.
    """

    def group(a):
        cat = category(a)
        if cat in ("mixer", "exchange", "bridge"):
            return display(a)
        if cat == "dex":
            return "DEX pools"
        if roles[a] == "Attacker wallet":
            return "Attacker wallet"
        return f"Hop {info[a]['hop']} · {roles[a]}"

    links = defaultdict(float)
    members = defaultdict(set)
    for a in info:
        members[group(a)].add(a)
    if theft_usd:
        links[("Exploited contract", "Attacker wallet")] += theft_usd
    for u, v, d, usd in trail_edges:
        gu, gv = group(u), group(v)
        if gu == gv or usd <= 0:
            continue
        if category(u) == "dex" or info[v]["hop"] <= info[u]["hop"]:
            links[("Swap proceeds (returned)", gv)] += usd
        else:
            links[(gu, gv)] += usd
    for a, r in residual.items():
        if r > 1000:
            links[(group(a), "Still held")] += r
    nodes = sorted({n for pair in links for n in pair})

    def mean_conf(n):
        vals = [info[a]["confidence"] for a in members.get(n, ())]
        return sum(vals) / len(vals) if vals else None

    return {
        "nodes": [{"name": n, "count": len(members.get(n, ())), "confidence": mean_conf(n)} for n in nodes],
        "links": [{"source": s, "target": t, "value": v} for (s, t), v in links.items() if v > 0],
    }
