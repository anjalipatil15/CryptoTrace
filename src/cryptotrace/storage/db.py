"""SQLite storage: raw API response cache, normalized transfers, and trace state.

Caching raw responses makes every analysis reproducible offline and keeps
re-runs from burning the Etherscan rate limit.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_cache (
    key         TEXT PRIMARY KEY,
    response    TEXT NOT NULL,
    fetched_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS transfers (
    case_id       TEXT NOT NULL,
    transfer_id   TEXT NOT NULL,
    tx_hash       TEXT NOT NULL,
    block_number  INTEGER,
    timestamp     TEXT,
    from_addr     TEXT,
    to_addr       TEXT,
    value         REAL,
    token         TEXT,
    token_address TEXT,
    kind          TEXT,
    gas_used      INTEGER,
    gas_price     INTEGER,
    PRIMARY KEY (case_id, transfer_id)
);
CREATE TABLE IF NOT EXISTS trace_nodes (
    case_id   TEXT NOT NULL,
    address   TEXT NOT NULL,
    hop       INTEGER,
    status    TEXT,          -- expanded | terminal | service | limit
    PRIMARY KEY (case_id, address)
);
"""


class Database:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(SCHEMA)

    # --- API cache -----------------------------------------------------------
    def cache_get(self, key: str):
        row = self.conn.execute("SELECT response FROM api_cache WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def cache_put(self, key: str, response) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO api_cache (key, response) VALUES (?, ?)", (key, json.dumps(response))
        )
        self.conn.commit()

    # --- Transfers -----------------------------------------------------------
    def save_transfers(self, case_id: str, df: pd.DataFrame) -> None:
        if df.empty:
            return
        rows = df.assign(case_id=case_id, timestamp=df["timestamp"].astype(str))
        cols = [
            "case_id", "transfer_id", "tx_hash", "block_number", "timestamp", "from_addr", "to_addr",
            "value", "token", "token_address", "kind", "gas_used", "gas_price",
        ]
        self.conn.executemany(
            f"INSERT OR IGNORE INTO transfers ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            rows[cols].itertuples(index=False, name=None),
        )
        self.conn.commit()

    def load_transfers(self, case_id: str) -> pd.DataFrame:
        df = pd.read_sql_query("SELECT * FROM transfers WHERE case_id = ?", self.conn, params=(case_id,))
        if not df.empty:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return df.drop(columns=["case_id"])

    # --- Trace state ---------------------------------------------------------
    def save_node(self, case_id: str, address: str, hop: int, status: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO trace_nodes (case_id, address, hop, status) VALUES (?, ?, ?, ?)",
            (case_id, address, hop, status),
        )
        self.conn.commit()

    def load_nodes(self, case_id: str) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT address, hop, status FROM trace_nodes WHERE case_id = ?", self.conn, params=(case_id,)
        )

    def clear_case(self, case_id: str) -> None:
        """Drop a case's transfers and trace state (the API cache is kept)."""
        self.conn.execute("DELETE FROM transfers WHERE case_id = ?", (case_id,))
        self.conn.execute("DELETE FROM trace_nodes WHERE case_id = ?", (case_id,))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
