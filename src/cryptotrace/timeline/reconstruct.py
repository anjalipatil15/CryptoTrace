"""Phase 6 - chronological reconstruction of the theft and laundering."""

from __future__ import annotations

import pandas as pd

SERVICE_EVENTS = {
    "mixer": "Mixer deposit",
    "exchange": "Exchange deposit",
    "bridge": "Bridge transfer (leaves chain)",
    "dex": "DEX swap",
    "recovery": "Returned to recovery wallet",
}


def build_timeline(edges: pd.DataFrame, scored: pd.DataFrame, patterns: pd.DataFrame, seeds: list[str]) -> pd.DataFrame:
    """One row per notable event, sorted by time.

    Events: first outflow from each seed, first time each hop level is reached,
    every service interaction, and every detected pattern.
    """
    events = []
    if edges.empty or scored.empty:
        return pd.DataFrame(columns=["time", "event", "detail", "confidence"])
    conf = scored.set_index("address")["confidence"].to_dict()
    category = scored.set_index("address")["category"].to_dict()
    hop = scored.set_index("address")["hop"].to_dict()
    seeds = {s.lower() for s in seeds}

    first_out = edges[edges["from_addr"].isin(seeds)].sort_values("timestamp").groupby("from_addr").head(1)
    for r in first_out.itertuples():
        events.append((r.timestamp, "Theft funds first moved",
                       f"{r.value:,.4f} {r.token} from seed {r.from_addr[:10]}...", 1.0))

    reached = edges.assign(hop=edges["to_addr"].map(hop)).dropna(subset=["hop"])
    for h, grp in reached.sort_values("timestamp").groupby("hop"):
        if h == 0:
            continue
        r = grp.iloc[0]
        events.append((r["timestamp"], f"Hop {int(h)} reached",
                       f"{grp['to_addr'].nunique()} wallet(s) at this distance", conf.get(r["to_addr"])))

    svc = edges[edges["to_addr"].map(category).isin(SERVICE_EVENTS)]
    for r in svc.sort_values("timestamp").itertuples():
        events.append((r.timestamp, SERVICE_EVENTS[category[r.to_addr]],
                       f"{r.tainted_value:,.4f} {r.token} tainted -> {r.to_addr[:10]}...", conf.get(r.to_addr)))

    for r in patterns.itertuples():
        events.append((r.start, f"Pattern: {r.pattern.replace('_', ' ')}", r.detail,
                       min((conf.get(w, 0) for w in r.wallets), default=None)))

    df = pd.DataFrame(events, columns=["time", "event", "detail", "confidence"])
    return df.sort_values("time", kind="stable").reset_index(drop=True)
