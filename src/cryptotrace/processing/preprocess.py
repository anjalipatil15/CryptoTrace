"""Phase 2 - turn raw Etherscan rows into one canonical transfer table.

Canonical columns (one row per value movement):
    transfer_id, tx_hash, block_number, timestamp (UTC), from_addr, to_addr,
    value (decimal-adjusted float), token (symbol), token_address ("ETH" for ether),
    kind (normal | internal | erc20), gas_used, gas_price
"""

from __future__ import annotations

import pandas as pd

COLUMNS = [
    "transfer_id", "tx_hash", "block_number", "timestamp", "from_addr", "to_addr",
    "value", "token", "token_address", "kind", "gas_used", "gas_price",
]

ETH = "ETH"


def _to_int(x) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return 0


def normalize(rows: list[dict], kind: str) -> pd.DataFrame:
    """Normalize rows from Etherscan `txlist` / `txlistinternal` / `tokentx`."""
    out = []
    for r in rows:
        if r.get("isError", "0") == "1":  # failed tx moves no value
            continue
        to_addr = (r.get("to") or "").lower()
        if not to_addr:  # contract creation
            continue
        if kind == "erc20":
            decimals = _to_int(r.get("tokenDecimal")) or 0
            token, token_address = r.get("tokenSymbol", "?"), r.get("contractAddress", "").lower()
        else:
            decimals, token, token_address = 18, ETH, ETH
        value = _to_int(r.get("value")) / (10 ** decimals)
        tx_hash = r.get("hash", "").lower()
        # tokentx has logIndex; internal txs have traceId. Fall back to value-level identity.
        sub = r.get("logIndex") or r.get("traceId") or ""
        transfer_id = f"{kind}:{tx_hash}:{sub}:{r.get('from', '').lower()}:{to_addr}:{token_address}:{r.get('value')}"
        out.append(
            {
                "transfer_id": transfer_id,
                "tx_hash": tx_hash,
                "block_number": _to_int(r.get("blockNumber")),
                "timestamp": pd.Timestamp(_to_int(r.get("timeStamp")), unit="s", tz="UTC"),
                "from_addr": r.get("from", "").lower(),
                "to_addr": to_addr,
                "value": value,
                "token": token,
                "token_address": token_address,
                "kind": kind,
                "gas_used": _to_int(r.get("gasUsed")),
                "gas_price": _to_int(r.get("gasPrice")),
            }
        )
    return pd.DataFrame(out, columns=COLUMNS)


def clean(df: pd.DataFrame, settings: dict) -> pd.DataFrame:
    """Dedup, drop zero-value and non-allowlisted tokens, sort chronologically."""
    if df.empty:
        return df.reindex(columns=COLUMNS)
    tracing = settings["tracing"]
    allow = {k.lower() for k in (tracing.get("token_allowlist") or {})}
    df = df.drop_duplicates("transfer_id")
    df = df[df["value"] > 0]
    df = df[(df["token_address"] == ETH) | df["token_address"].isin(allow)] if allow else df
    # Use the allowlist's canonical symbol (token symbols on-chain are not trustworthy).
    symbols = {k.lower(): v for k, v in (tracing.get("token_allowlist") or {}).items()}
    df = df.assign(token=df["token_address"].map(symbols).fillna(df["token"]))
    return df.sort_values(["timestamp", "block_number", "transfer_id"]).reset_index(drop=True)


def min_value_for(token: str, settings: dict) -> float:
    mins = settings["tracing"].get("min_value") or {}
    return float(mins.get(token, mins.get("default", 0)))
