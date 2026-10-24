"""Render the investigation dashboard: one self-contained HTML file per case."""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

TEMPLATE = Path(__file__).with_name("template.html")
# Short, slide-style walkthrough of the same data for presenting a case (no external scripts).
PRESENTATION = Path(__file__).with_name("presentation.html")


def _jsonable(o):
    if isinstance(o, (pd.Timestamp, datetime, date)):
        return None if pd.isna(o) else o.isoformat()
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if math.isnan(o) else float(o)
    if isinstance(o, (set, tuple)):
        return list(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if o is pd.NaT:
        return None
    raise TypeError(f"not JSON serializable: {type(o)}")


def _clean(o):
    """Replace NaN/inf floats (invalid JSON) with None, recursively."""
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if o is pd.NaT:
        return None
    return o


def render_dashboard(insights: dict, out_path: str | Path, template: Path = TEMPLATE) -> Path:
    payload = json.dumps(_clean(insights), default=_jsonable, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")  # never close the <script> tag early
    html = template.read_text(encoding="utf-8")
    html = html.replace("__CASE_TITLE__", insights["case"]["name"]).replace("/*__CASE_DATA__*/null", payload)
    out_path = Path(out_path)
    out_path.write_text(html, encoding="utf-8")
    return out_path
