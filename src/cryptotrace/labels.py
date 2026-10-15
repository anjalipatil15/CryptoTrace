"""Known-entity labels (mixers, exchanges, DEXes, bridges).

Labels decide where tracing stops and which hop factor applies, so the label
set is part of the evidence: keep a `source` for every row.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .config import PROJECT_ROOT

DEFAULT_LABELS = PROJECT_ROOT / "data" / "labels" / "known_entities.csv"
AUTO_SOURCE = "etherscan verified contract name"


def auto_labels_path(case_id: str) -> Path:
    return PROJECT_ROOT / "data" / "labels" / f"auto_{case_id}.csv"


def labels_for_case(case_id: str) -> "LabelStore":
    auto = auto_labels_path(case_id)
    return LabelStore.from_csv(DEFAULT_LABELS, *([auto] if auto.exists() else []))


# Categories where tracing stops (we never fetch these addresses' history:
# exchange hot wallets alone have millions of transactions).
TERMINAL_CATEGORIES = {"mixer", "exchange", "bridge", "dex"}


@dataclass(frozen=True)
class Label:
    entity: str
    category: str
    source: str = ""


class LabelStore:
    def __init__(self, labels: dict[str, Label] | None = None):
        self._labels = {k.lower(): v for k, v in (labels or {}).items()}

    @classmethod
    def from_csv(cls, *paths: str | Path) -> "LabelStore":
        labels: dict[str, Label] = {}
        for path in paths or (DEFAULT_LABELS,):
            df = pd.read_csv(path, dtype=str).fillna("")
            for row in df.itertuples(index=False):
                labels[row.address.lower()] = Label(row.entity, row.category.lower(), row.source)
        return cls(labels)

    def get(self, address: str) -> Label | None:
        return self._labels.get(address.lower())

    def category(self, address: str) -> str | None:
        label = self.get(address)
        return label.category if label else None

    def is_terminal(self, address: str) -> bool:
        return self.category(address) in TERMINAL_CATEGORIES

    def add(self, address: str, entity: str, category: str, source: str = "manual") -> None:
        self._labels[address.lower()] = Label(entity, category.lower(), source)

    def to_csv(self, path: str | Path, source: str | None = None) -> int:
        rows = [{"address": a, "entity": l.entity, "category": l.category, "source": l.source}
                for a, l in self._labels.items() if source is None or l.source == source]
        pd.DataFrame(rows, columns=["address", "entity", "category", "source"]).to_csv(path, index=False)
        return len(rows)

    def __len__(self) -> int:
        return len(self._labels)
