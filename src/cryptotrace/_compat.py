"""Small numeric helpers (kept here to avoid a scipy dependency)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def spearman(a: pd.Series, b: pd.Series) -> float:
    ra, rb = pd.Series(a).rank().to_numpy(), pd.Series(b).rank().to_numpy()
    if np.std(ra) == 0 or np.std(rb) == 0:
        return 1.0 if np.array_equal(ra, rb) else float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])
