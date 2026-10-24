"""Investigation report (Markdown)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..scoring.confidence import BAND_ORDER


def _table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_none_\n"
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in df.itertuples(index=False):
        lines.append("| " + " | ".join(str(x) for x in row) + " |")
    return "\n".join(lines) + "\n"


def write_report(case, scored, patterns, timeline, validation: dict, out_path: str | Path) -> Path:
    bands = scored["band"].value_counts().reindex(BAND_ORDER, fill_value=0) if not scored.empty else pd.Series(dtype=int)
    services = scored[scored["category"] != "wallet"] if not scored.empty else scored
    parts = [
        f"# Investigation Report - {case.name}\n",
        f"{case.description.strip()}\n",
        "## Summary\n",
        f"- Seed wallets: {', '.join(case.seeds)}",
        f"- Wallets on reconstructed trail: {len(scored)}",
        f"- Maximum hop distance: {int(scored['hop'].max()) if not scored.empty else 0}",
        f"- Service endpoints reached: {len(services)}",
        f"- Laundering patterns detected: {len(patterns)}\n",
        "## Confidence distribution\n",
        _table(bands.rename_axis("band").reset_index(name="wallets")),
        "## Service endpoints (where on-chain attribution ends)\n",
        _table(services[["address", "entity", "category", "hop", "confidence", "band"]].round(3)) if not services.empty else "_none_\n",
        "## Laundering patterns\n",
        _table(patterns.groupby("pattern").size().rename("count").reset_index()) if not patterns.empty else "_none_\n",
        "## Timeline\n",
        _table(timeline.assign(confidence=timeline["confidence"].round(3))) if not timeline.empty else "_none_\n",
        "## Validation\n",
    ]
    for section, metrics in validation.items():
        parts.append(f"### {section.replace('_', ' ').title()}\n")
        for k, v in (metrics or {}).items():
            if isinstance(v, dict):
                parts.append(f"- {k}:")
                parts.extend(f"  - {sk}: {sv}" for sk, sv in v.items())
            elif isinstance(v, list) and len(v) > 5:
                parts.append(f"- {k} ({len(v)}): " + ", ".join(str(x) for x in v))
            else:
                parts.append(f"- {k}: {round(v, 4) if isinstance(v, float) else v}")
        parts.append("")
    out_path = Path(out_path)
    out_path.write_text("\n".join(parts), encoding="utf-8")
    return out_path
