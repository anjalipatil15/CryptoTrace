"""End-to-end analysis: phases 3-7 on an already-collected transfer table."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import networkx as nx
import pandas as pd

from .analysis.insights import build_insights
from .dashboard.build import PRESENTATION, render_dashboard
from .graph.builder import build_graph
from .labels import LabelStore
from .patterns.detectors import detect_all
from .report.markdown import write_report
from .scoring.confidence import edge_table, score
from .timeline.reconstruct import build_timeline
from .validation import metrics


@dataclass
class Analysis:
    graph: nx.MultiDiGraph
    scored: pd.DataFrame
    edges: pd.DataFrame
    patterns: pd.DataFrame
    timeline: pd.DataFrame
    validation: dict
    insights: dict


def analyze(transfers, nodes, case, labels: LabelStore, ground_truth: pd.DataFrame | None = None,
            run_sensitivity: bool = True, contract_names: dict | None = None) -> Analysis:
    settings = case.settings
    g = build_graph(transfers, labels, nodes)
    scored = score(g, case.seeds, settings, case.start_time)
    edges = edge_table(g)
    patterns = detect_all(g, settings)
    timeline = build_timeline(edges, scored, patterns, case.seeds)

    gt = ground_truth if ground_truth is not None else metrics.load_ground_truth(case.ground_truth)
    validation = {}
    if not gt.empty:
        validation["wallet_identification"] = metrics.wallet_identification(scored, gt)
        validation["trace_completeness"] = metrics.trace_completeness(g, gt)
        validation["calibration"] = {"confidence_auc": metrics.confidence_auc(scored, gt)}
    if run_sensitivity:
        validation["confidence_stability"] = metrics.sensitivity(g, case.seeds, settings, score, case.start_time)
    insights = build_insights(g, scored, patterns, validation, case, contract_names)
    return Analysis(g, scored, edges, patterns, timeline, validation, insights)


def write_outputs(result: Analysis, case, out_dir: str | Path) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "wallets": out / "wallets.csv",
        "edges": out / "edges.csv",
        "patterns": out / "patterns.csv",
        "timeline": out / "timeline.csv",
    }
    result.scored.to_csv(files["wallets"], index=False)
    result.edges.to_csv(files["edges"], index=False)
    result.patterns.to_csv(files["patterns"], index=False)
    result.timeline.to_csv(files["timeline"], index=False)
    files["dashboard"] = render_dashboard(result.insights, out / "dashboard.html")
    files["present"] = render_dashboard(result.insights, out / "presentation.html", PRESENTATION)
    files["report"] = write_report(case, result.scored, result.patterns, result.timeline, result.validation,
                                   out / "report.md")
    return files
