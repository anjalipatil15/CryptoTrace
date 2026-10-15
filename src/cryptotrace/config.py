"""Load global settings and per-case configuration."""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS = PROJECT_ROOT / "config" / "settings.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_settings(path: str | Path = DEFAULT_SETTINGS) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass
class Case:
    case_id: str
    name: str
    seeds: list[str]
    start_time: pd.Timestamp | None
    ground_truth: Path | None
    settings: dict
    description: str = ""
    extra: dict = field(default_factory=dict)


def _utc(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)  # YAML may already parse it as a (tz-aware) datetime
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def load_case(path: str | Path, settings_path: str | Path = DEFAULT_SETTINGS) -> Case:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    settings = _deep_merge(load_settings(settings_path), raw.get("overrides") or {})
    start = raw.get("start_time")
    gt = raw.get("ground_truth")
    return Case(
        case_id=raw["case_id"],
        name=raw.get("name", raw["case_id"]),
        seeds=[s.lower() for s in raw.get("seeds") or []],
        start_time=_utc(start) if start else None,
        ground_truth=(PROJECT_ROOT / gt) if gt else None,
        settings=settings,
        description=raw.get("description", ""),
        extra=raw,
    )


def etherscan_api_key() -> str:
    load_dotenv(PROJECT_ROOT / ".env")
    key = os.getenv("ETHERSCAN_API_KEY", "")
    if not key:
        raise RuntimeError("ETHERSCAN_API_KEY is not set. Copy .env.example to .env and add your key.")
    return key


def resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p
