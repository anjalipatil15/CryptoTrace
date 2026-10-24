"""TEST FIXTURE ONLY: a small hand-built scenario with a known correct answer.

Never used for results. Real cases always come from the Etherscan API
(`cryptotrace trace` / `analyze`). Unit tests need inputs whose correct output
is known in advance, so this file builds one example of each hop type and
laundering pattern: direct hop, split, DEX swap, fan-out, fan-in mixed with
clean funds, a peeling chain that is also rapid, a circular transfer, and
exchange / mixer / bridge exits, and a return to the victim's recovery wallet.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import pandas as pd

from cryptotrace.labels import LabelStore

USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
T0 = pd.Timestamp("2022-06-23 10:00", tz="UTC")


def addr(name: str) -> str:
    return "0x" + hashlib.sha1(name.encode()).hexdigest()


@dataclass
class SyntheticCase:
    seeds: list[str]
    labels: LabelStore
    rows: list[dict]         # Etherscan-shaped rows, each tagged with "_kind"
    ground_truth: pd.DataFrame

    def fetch(self, address: str):
        raw = {"normal": [], "internal": [], "erc20": []}
        for r in self.rows:
            if address in (r["from"], r["to"]):
                raw[r["_kind"]].append(r)
        return raw, False


def build_synthetic_case() -> SyntheticCase:
    rows: list[dict] = []

    def tx(src, dst, amount, minutes, token="ETH"):
        ts = T0 + pd.Timedelta(minutes=minutes)
        base = {
            "blockNumber": str(15_000_000 + len(rows)),
            "timeStamp": str(int(ts.timestamp())),
            "hash": "0x" + hashlib.sha1(f"{len(rows)}".encode()).hexdigest() * 2,
            "from": addr(src), "to": addr(dst), "isError": "0", "gasUsed": "21000", "gasPrice": "20000000000",
        }
        if token == "USDC":
            base.update(value=str(int(amount * 10**6)), tokenSymbol="USDC", tokenDecimal="6",
                        contractAddress=USDC, logIndex=str(len(rows)), _kind="erc20")
        else:
            base.update(value=str(int(amount * 10**18)), _kind="normal")
        rows.append(base)

    tx("Bridge Contract", "Theft", 1000, 0)               # the exploit
    tx("A", "Old Friend", 5, -7 * 24 * 60)               # before the theft: must be ignored
    tx("Theft", "A", 1000, 5)                             # direct
    tx("A", "B", 400, 20); tx("A", "C", 300, 21); tx("A", "D", 300, 22)   # split
    tx("B", "Uniswap", 100, 30)                           # swap ETH -> USDC
    tx("Uniswap", "B", 150_000, 30, token="USDC")
    tx("B", "Exchange 2", 150_000, 45, token="USDC")
    for i in range(1, 7):                                 # fan-out
        tx("B", f"E{i}", 50, 60 + i)
    tx("Clean Wallet", "F", 200, 120)                     # clean funds land in F first
    for i in range(1, 5):                                 # fan-in (mixed with clean funds)
        tx(f"E{i}", "F", 50, 180 + i)
    tx("F", "Exchange 1", 300, 300)                       # exchange deposit
    tx("C", "P1", 290, 30); tx("C", "Peel 0", 10, 30)     # peeling chain, also rapid
    tx("P1", "P2", 280, 38); tx("P1", "Peel 1", 10, 38)
    tx("P2", "P3", 270, 46); tx("P2", "Peel 2", 10, 46)
    tx("P3", "P4", 260, 54); tx("P3", "Peel 3", 10, 54)
    tx("P4", "Tornado", 260, 24 * 60)                     # mixer
    tx("D", "G", 300, 90); tx("G", "H", 300, 100)
    tx("H", "D", 50, 110)                                 # circular
    tx("H", "Bridge Out", 250, 120)                       # bridge exit
    tx("E5", "Recovery", 50, 200)                         # returned to the victim

    labels = LabelStore()
    for name, cat in [("Uniswap", "dex"), ("Exchange 1", "exchange"), ("Exchange 2", "exchange"),
                      ("Tornado", "mixer"), ("Bridge Out", "bridge"),
                      ("Recovery", "recovery")]:
        labels.add(addr(name), name, cat, "test fixture")

    gt = pd.DataFrame(
        [(addr(n), "laundering", 1 if n in ("Theft", "A", "C", "P1", "P2") else None,
          ["Theft", "A", "C", "P1", "P2"].index(n) if n in ("Theft", "A", "C", "P1", "P2") else None, "test fixture")
         for n in ["Theft", "A", "B", "C", "F", "P1", "P2", "P3", "P4", "H", "Tornado", "Exchange 1"]],
        columns=["address", "role", "path_id", "step", "source"],
    )
    return SyntheticCase(seeds=[addr("Theft")], labels=labels, rows=rows, ground_truth=gt)
