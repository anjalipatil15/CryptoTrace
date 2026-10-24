"""Phase 1 - bounded, time-respecting breadth-first trace from seed wallets.

Ethereum is account-based: once tainted funds land in a wallet they are
indistinguishable from its other balance. Two rules keep the trace honest
and the graph from exploding:

  * only follow outflows that happen AFTER tainted funds first arrived, and
  * never expand labeled services (exchange, mixer, bridge, DEX) or
    unlabeled high-activity addresses -- they are recorded as terminal nodes.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from ..labels import AUTO_SOURCE, LabelStore
from ..processing.preprocess import clean, min_value_for, normalize

# fetch(address) -> (raw rows by kind, truncated?)
Fetcher = Callable[[str], tuple[dict[str, list[dict]], bool]]


@dataclass
class TraceResult:
    transfers: pd.DataFrame
    nodes: pd.DataFrame  # address, hop, status
    log: list[str] = field(default_factory=list)


def contract_classifier(client, settings: dict) -> Callable[[str], tuple[str, str] | None]:
    """Label unlisted DEX contracts (pools, pairs, routers) by their verified contract name."""
    patterns = [p.lower() for p in settings["tracing"].get("dex_contract_patterns") or []]

    def classify(address: str):
        name = client.contract_name(address)
        if name and any(p in name.lower() for p in patterns):
            return name, "dex"
        return None

    return classify


def etherscan_fetcher(client, settings: dict) -> Fetcher:
    threshold = settings["tracing"]["service_tx_count_threshold"]

    def fetch(address: str):
        raw = {kind: client.transfers(address, kind) for kind in ("normal", "internal", "erc20")}
        truncated = any(len(rows) >= threshold for rows in raw.values())
        return raw, truncated

    return fetch


def trace(
    seeds: list[str],
    fetch: Fetcher,
    labels: LabelStore,
    settings: dict,
    start_time: pd.Timestamp | None = None,
    on_node: Callable[[str, int, str], None] | None = None,
    classify: Callable[[str], tuple[str, str] | None] | None = None,
) -> TraceResult:
    cfg = settings["tracing"]
    max_hops, max_addresses = cfg["max_hops"], cfg["max_addresses"]
    after_receipt = cfg.get("only_follow_after_receipt", True)

    earliest = pd.Timestamp.min.tz_localize("UTC") if start_time is None else start_time
    queue = deque((s.lower(), 0, earliest) for s in seeds)
    first_taint: dict[str, pd.Timestamp] = {s.lower(): earliest for s in seeds}
    hop_of: dict[str, int] = {s.lower(): 0 for s in seeds}
    status: dict[str, str] = {}
    fetched = 0  # addresses whose history was read (expanded or service)
    frames: list[pd.DataFrame] = []
    log: list[str] = []

    def mark(addr: str, hop: int, st: str) -> None:
        status[addr] = st
        if on_node:
            on_node(addr, hop, st)

    while queue:
        addr, hop, since = queue.popleft()
        if addr in status:
            continue
        if labels.is_terminal(addr):
            mark(addr, hop, "terminal")
            continue
        if hop > max_hops:  # recorded as the trail's frontier, never fetched
            mark(addr, hop, "limit")
            continue
        if fetched >= max_addresses:
            mark(addr, hop, "limit")
            continue
        # Contract-name lookups cost an API call each, so only addresses about to be fetched get one.
        if classify and not labels.get(addr) and hop > 0 and (found := classify(addr)):
            labels.add(addr, found[0], found[1], source=AUTO_SOURCE)
            log.append(f"{addr}: auto-labeled {found[1]} ({found[0]})")
            mark(addr, hop, "terminal")
            continue
        fetched += 1

        raw, truncated = fetch(addr)
        df = clean(pd.concat([normalize(rows, kind) for kind, rows in raw.items()], ignore_index=True), settings)
        frames.append(df)
        if truncated:
            mark(addr, hop, "service")
            log.append(f"{addr}: high-activity address treated as unlabeled service")
            continue
        mark(addr, hop, "expanded")

        out = df[df["from_addr"] == addr]
        if after_receipt:
            out = out[out["timestamp"] >= since]
        for row in out.itertuples(index=False):
            if row.value < min_value_for(row.token, settings) or row.to_addr == addr:
                continue
            nxt = row.to_addr
            if nxt not in first_taint or row.timestamp < first_taint[nxt]:
                first_taint[nxt] = row.timestamp
            if nxt not in hop_of:
                hop_of[nxt] = hop + 1
                queue.append((nxt, hop + 1, first_taint[nxt]))

    # Anything discovered but never dequeued (e.g. beyond max_hops) is recorded too.
    for addr, hop in hop_of.items():
        if addr not in status:
            mark(addr, hop, "terminal" if labels.is_terminal(addr) else "limit")

    transfers = pd.concat(frames, ignore_index=True) if frames else clean(pd.DataFrame(), settings)
    transfers = transfers.drop_duplicates("transfer_id").sort_values("timestamp").reset_index(drop=True)
    nodes = pd.DataFrame(
        [{"address": a, "hop": hop_of.get(a, 0), "status": s} for a, s in status.items()]
    )
    return TraceResult(transfers=transfers, nodes=nodes, log=log)
