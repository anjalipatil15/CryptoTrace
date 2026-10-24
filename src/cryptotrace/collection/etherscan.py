"""Phase 1 - Etherscan API V2 client with rate limiting and SQLite caching."""

from __future__ import annotations

import time

import requests

from ..storage.db import Database

ACTIONS = {"normal": "txlist", "internal": "txlistinternal", "erc20": "tokentx"}


class EtherscanError(RuntimeError):
    pass


class EtherscanClient:
    def __init__(self, api_key: str, settings: dict, db: Database):
        cfg = settings["etherscan"]
        self.api_key = api_key
        self.base_url = cfg["base_url"]
        self.chain_id = cfg["chain_id"]
        self.page_size = cfg["page_size"]
        self.rows_per_call = min(cfg.get("rows_per_call", 1000), self.page_size)
        self.min_interval = 1.0 / cfg["rate_limit_per_sec"]
        self.db = db
        self._last_call = 0.0
        self.session = requests.Session()

    def _get(self, params: dict, retries: int = 6) -> list[dict]:
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        full = {"chainid": self.chain_id, "apikey": self.api_key, **params}
        for attempt in range(retries):
            self._last_call = time.monotonic()
            try:
                resp = self.session.get(self.base_url, params=full, timeout=30)
                resp.raise_for_status()
                data = resp.json()
            except (requests.RequestException, ValueError) as err:  # dropped/cut-off connections, 5xx, bad JSON
                # Long traces make thousands of calls; a dropped connection should not end the run.
                if attempt == retries - 1:
                    raise EtherscanError(f"Network error after {retries} attempts: {err}") from err
                time.sleep(5 * (attempt + 1))
                continue
            result = data.get("result")
            if data.get("status") == "1":
                return result
            if isinstance(result, list):  # "No transactions found"
                return []
            if "rate limit" in str(result).lower():
                time.sleep(1.5 * (attempt + 1))
                continue
            raise EtherscanError(f"{data.get('message')}: {result}")
        raise EtherscanError("Rate limit retries exhausted")

    def transfers(self, address: str, kind: str, start_block: int = 0) -> list[dict]:
        """First page_size rows (ascending) of an address's history of one kind.

        Etherscan returns at most rows_per_call rows per request (1,000 on the free tier,
        whatever `offset` asks for), so the history is read page by page up to page_size.
        """
        address = address.lower()
        key = f"{self.chain_id}:{kind}:{address}:{start_block}:{self.page_size}:paged{self.rows_per_call}"
        cached = self.db.cache_get(key)
        if cached is not None:
            return cached
        rows: list[dict] = []
        for page in range(1, self.page_size // self.rows_per_call + 1):
            batch = self._get(
                {
                    "module": "account",
                    "action": ACTIONS[kind],
                    "address": address,
                    "startblock": start_block,
                    "endblock": 99999999,
                    "page": page,
                    "offset": self.rows_per_call,
                    "sort": "asc",
                }
            )
            rows.extend(batch)
            if len(batch) < self.rows_per_call:
                break
        self.db.cache_put(key, rows)
        return rows

    def contract_name(self, address: str) -> str:
        """Verified contract name ('' for wallets and unverified contracts)."""
        address = address.lower()
        key = f"{self.chain_id}:contract_name:{address}"
        cached = self.db.cache_get(key)
        if cached is not None:
            return cached
        result = self._get({"module": "contract", "action": "getsourcecode", "address": address})
        name = (result[0].get("ContractName") or "") if result else ""
        self.db.cache_put(key, name)
        return name

    def block_at(self, timestamp: int) -> int:
        key = f"{self.chain_id}:block_at:{timestamp}"
        cached = self.db.cache_get(key)
        if cached is not None:
            return cached
        result = self._get(
            {"module": "block", "action": "getblocknobytime", "timestamp": timestamp, "closest": "before"}
        )
        block = int(result)
        self.db.cache_put(key, block)
        return block
