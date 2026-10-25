"""Robustness calibration of the hop factors, run on the calibration case (Nomad).

Public labels are too sparse to tune seven factors for accuracy without overfitting, so the
factors are tuned for stability instead:

  1. Keep the model's ordering fixed: direct > split > fan-out. Endpoint factors (exchange,
     bridge, mixer, recovery) only score the endpoint itself and are left as set.
  2. For each candidate factor set, nudge every band cut-off (within +/- `band_window`) to the
     point farthest from the wallets' scores. A score sitting right on a cut-off flips band
     under any noise; the cut-offs keep their meaning, they just stop splitting clusters.
  3. Measure the band-change rate under +/-p noise (the same test as `metrics.sensitivity`).
  4. Keep candidates whose ranking of known positives (non-seed ground-truth wallets) is no
     worse than the current factors' by more than `auc_tolerance`, and pick the most stable.

Freeze the result in config/settings.yaml, then evaluate on the held-out case (Harmony).
"""

from __future__ import annotations

import copy
import itertools
import re
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

from ..scoring.confidence import prepare, score
from ..scoring.taint import propagate_taint
from . import metrics

GRID = {
    "direct": [0.88, 0.92, 0.95],
    "split": [0.75, 0.80, 0.85],
    "fan_out": [0.50, 0.60, 0.70],
    "swap": [0.85, 0.90, 0.95],
}


def candidates(grid: dict = GRID) -> list[dict]:
    keys = list(grid)
    out = []
    for values in itertools.product(*(grid[k] for k in keys)):
        c = dict(zip(keys, values))
        if c["direct"] > c["split"] > c["fan_out"]:
            out.append(c)
    return out


def nudge_bands(scores: np.ndarray, bands: dict, window: float = 0.05, step: float = 0.005) -> dict:
    """Move each cut-off (except the 0 floor) within +/-window to the point farthest from any score."""
    out = dict(bands)
    scores = np.asarray(scores, dtype=float)
    for name, cut in bands.items():
        if cut <= 0 or scores.size == 0:
            continue
        options = np.round(np.arange(cut - window, cut + window + 1e-9, step), 4)
        # distance to the nearest score, capped: scores farther than `window` away don't matter
        gaps = [min(float(np.min(np.abs(scores - t))), window) for t in options]
        best = max(gaps)
        # among equally good cut-offs, stay closest to the original
        out[name] = float(min((t for t, gap in zip(options, gaps) if gap >= best - 1e-12), key=lambda t: abs(t - cut)))
    return out


def calibrate(g: nx.MultiDiGraph, seeds: list[str], settings: dict, start_time, ground_truth: pd.DataFrame,
              runs: int = 100, band_window: float = 0.05, auc_tolerance: float = 0.02) -> tuple[pd.DataFrame, dict]:
    """Return (one row per candidate, the chosen settings)."""
    work = g.copy()
    seed_set = {s.lower() for s in seeds}
    taint = propagate_taint(work, [s for s in seed_set if s in work], start_time)
    prepared = prepare(work, settings, taint)  # hop types don't depend on the factors: classify once
    positives = ground_truth[~ground_truth["address"].isin(seed_set)] if not ground_truth.empty else ground_truth

    def evaluate(factors: dict, nudge: bool) -> tuple[dict, dict]:
        s = copy.deepcopy(settings)
        s["scoring"]["hop_factors"].update(factors)
        scored = score(work, seeds, s, start_time, prepared=prepared)
        wallets = scored[(scored["category"] == "wallet") & ~scored["address"].isin(seed_set)]
        if nudge:
            s["scoring"]["bands"] = nudge_bands(wallets["confidence"].to_numpy(), s["scoring"]["bands"], band_window)
        s["validation"]["sensitivity_runs"] = runs
        stab = metrics.sensitivity(work, seeds, s, score, start_time, prepared=prepared)
        auc = metrics.confidence_auc(wallets, positives) if not positives.empty else None
        row = {**factors, **{f"band_{k}": v for k, v in s["scoring"]["bands"].items() if v > 0},
               "auc_non_seed": auc, "band_change_rate": stab.get("band_change_rate_mean"),
               "rank_correlation": stab.get("rank_correlation_mean"), "nudged_bands": nudge}
        return row, s

    current = {k: settings["scoring"]["hop_factors"][k] for k in GRID}
    base_row, _ = evaluate(current, nudge=False)
    base_row["candidate"] = "current"
    rows, chosen = [base_row], None
    for i, factors in enumerate(candidates()):
        row, s = evaluate(factors, nudge=True)
        row["candidate"] = f"c{i}"
        rows.append(row)
        ok = base_row["auc_non_seed"] is None or row["auc_non_seed"] is None or \
            row["auc_non_seed"] >= base_row["auc_non_seed"] - auc_tolerance
        key = (row["band_change_rate"], -row["rank_correlation"])
        if ok and (chosen is None or key < chosen[0]):
            chosen = (key, s, row["candidate"])
    table = pd.DataFrame(rows)
    table["chosen"] = table["candidate"] == chosen[2]
    return table, chosen[1]


def freeze(settings_path: str | Path, chosen: dict) -> list[str]:
    """Write the chosen hop factors and band cut-offs into settings.yaml, keeping its comments.

    Only values inside the `scoring:` block's `hop_factors:` and `bands:` sections are replaced.
    Returns the lines that changed, as "key: old -> new".
    """
    path = Path(settings_path)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    targets = {"hop_factors": chosen["scoring"]["hop_factors"], "bands": chosen["scoring"]["bands"]}
    changed, top, section = [], None, None
    for i, line in enumerate(lines):
        if re.match(r"^\S", line):                       # top-level key, e.g. "scoring:"
            top, section = line.split(":")[0], None
            continue
        m = re.match(r"^  (\w+):\s*$", line)               # second-level section, e.g. "  bands:"
        if m:
            section = m.group(1)
            continue
        m = re.match(r"^(    (\w+):\s*)([0-9.]+)(.*)$", line, re.S)
        if top == "scoring" and section in targets and m and m.group(2) in targets[section]:
            new = targets[section][m.group(2)]
            if float(m.group(3)) != float(new):
                changed.append(f"{m.group(2)}: {m.group(3)} -> {new:g}")
                lines[i] = f"{m.group(1)}{new:g}{m.group(4)}"
    path.write_text("".join(lines), encoding="utf-8")
    return changed
