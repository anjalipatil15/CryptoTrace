"""Phase 7 - validation against public investigations, and robustness of the scores.

Ground-truth CSV columns: address, role, path_id, step, source
  * address / role / source  -- addresses named by the FBI, Chainalysis, TRM, court docs
  * path_id / step (optional) -- an ordered, publicly documented path, for completeness

Public reports list *some* attacker wallets, never all of them, so precision
against them is only a lower bound. Recall and calibration are the main metrics.
"""

from __future__ import annotations

import copy
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

from .._compat import spearman


def load_ground_truth(path: str | Path | None) -> pd.DataFrame:
    if not path or not Path(path).exists():
        return pd.DataFrame(columns=["address", "role", "path_id", "step", "source"])
    df = pd.read_csv(path, dtype={"address": str})
    df["address"] = df["address"].str.lower()
    return df


def wallet_identification(scored: pd.DataFrame, gt: pd.DataFrame) -> dict:
    traced = set(scored["address"]) if not scored.empty else set()
    truth = set(gt["address"])
    hit = traced & truth
    return {
        "ground_truth_wallets": len(truth),
        "traced_wallets": len(traced),
        "found": len(hit),
        "recall": len(hit) / len(truth) if truth else None,
        "precision_lower_bound": len(hit) / len(traced) if traced and truth else None,
        "missed": sorted(truth - traced),
    }


def trace_completeness(g: nx.MultiDiGraph, gt: pd.DataFrame) -> dict:
    """Share of documented path steps (consecutive ground-truth wallets) present as on-trail edges."""
    paths = gt.dropna(subset=["path_id", "step"]) if "path_id" in gt else gt.iloc[0:0]
    on_trail = {(u, v) for u, v, d in g.edges(data=True) if d.get("valid")}
    total = found = 0
    for _, grp in paths.sort_values("step").groupby("path_id"):
        steps = list(grp["address"])
        for a, b in zip(steps, steps[1:]):
            total += 1
            found += (a, b) in on_trail
    return {"documented_steps": total, "reconstructed_steps": found,
            "completeness": found / total if total else None}


def confidence_auc(scored: pd.DataFrame, gt: pd.DataFrame) -> float | None:
    """P(a random confirmed wallet scores higher than a random unconfirmed one) -- Mann-Whitney AUC."""
    if scored.empty or gt.empty:
        return None
    truth = set(gt["address"])
    pos = scored.loc[scored["address"].isin(truth), "confidence"].to_numpy()
    neg = scored.loc[~scored["address"].isin(truth), "confidence"].to_numpy()
    if len(pos) == 0 or len(neg) == 0:
        return None
    ranks = pd.Series(np.concatenate([pos, neg])).rank().to_numpy()
    return float((ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def sensitivity(g: nx.MultiDiGraph, seeds, settings: dict, score_fn, start_time=None) -> dict:
    """Confidence stability: perturb every hop factor by +/-p and measure how rankings and bands move."""
    cfg = settings["validation"]
    rng = np.random.default_rng(cfg["random_seed"])
    base = score_fn(g.copy(), seeds, settings, start_time).set_index("address")
    if base.empty:
        return {"runs": 0}
    corrs, band_changes, abs_changes = [], [], []
    for _ in range(cfg["sensitivity_runs"]):
        s = copy.deepcopy(settings)
        for k, v in s["scoring"]["hop_factors"].items():
            s["scoring"]["hop_factors"][k] = float(np.clip(v * (1 + rng.uniform(-1, 1) * cfg["sensitivity_perturbation"]), 1e-4, 1.0))
        run = score_fn(g.copy(), seeds, s, start_time).set_index("address").reindex(base.index)
        corrs.append(spearman(base["confidence"], run["confidence"].fillna(0)))
        band_changes.append(float((base["band"] != run["band"]).mean()))
        abs_changes.append(float((base["confidence"] - run["confidence"].fillna(0)).abs().mean()))
    return {
        "runs": len(corrs),
        "perturbation": cfg["sensitivity_perturbation"],
        "rank_correlation_mean": float(np.nanmean(corrs)),
        "rank_correlation_min": float(np.nanmin(corrs)),
        "band_change_rate_mean": float(np.mean(band_changes)),
        "mean_abs_confidence_change": float(np.mean(abs_changes)),
    }
