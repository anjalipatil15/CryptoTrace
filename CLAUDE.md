# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

CryptoTrace reconstructs the trail of stolen funds on Ethereum and scores how confident an investigator can be at each hop (theft wallet → mixers / exchanges / bridges). It is a research project: the design rationale, validation methodology and the reasons behind each rule are in [docs/ARCHITECTURE_REVIEW.md](docs/ARCHITECTURE_REVIEW.md). Read it before changing scoring or tracing logic.

## Commands

Windows / PowerShell, Python 3.11 venv in `.venv`:

```powershell
py -3.11 -m venv .venv; .venv\Scripts\activate; pip install -e ".[dev]"
copy .env.example .env                              # set ETHERSCAN_API_KEY (Etherscan API V2)

cryptotrace trace   config/cases/harmony.yaml       # phases 1-2: Etherscan -> data/cryptotrace.db
cryptotrace analyze config/cases/harmony.yaml       # phases 3-7: -> outputs/harmony/
cryptotrace analyze <case> --no-sensitivity         # skip the slow +/-10% perturbation runs

pytest                                              # all tests
pytest tests/test_pipeline.py::test_swap_preserves_taint   # single test
```

No linter or formatter is configured.

## Architecture

Two CLI stages ([cli.py](src/cryptotrace/cli.py)) connected by SQLite:

1. **`trace`**: [collection/tracer.py](src/cryptotrace/collection/tracer.py) runs a bounded BFS from the case seeds. It fetches three Etherscan streams per address (`txlist`, `txlistinternal`, `tokentx`) through [collection/etherscan.py](src/cryptotrace/collection/etherscan.py), normalizes and filters them in [processing/preprocess.py](src/cryptotrace/processing/preprocess.py), and stores transfers and node state in [storage/db.py](src/cryptotrace/storage/db.py). Raw API responses are cached in the `api_cache` table, and `clear_case` keeps that cache, so re-running a trace is cheap.
2. **`analyze`**: [pipeline.py](src/cryptotrace/pipeline.py) `analyze()` loads the stored transfers and runs: graph build ([graph/builder.py](src/cryptotrace/graph/builder.py), a `networkx.MultiDiGraph`), then taint ([scoring/taint.py](src/cryptotrace/scoring/taint.py)), then edge classification and confidence ([scoring/confidence.py](src/cryptotrace/scoring/confidence.py)), then patterns ([patterns/detectors.py](src/cryptotrace/patterns/detectors.py)), timeline, validation ([validation/metrics.py](src/cryptotrace/validation/metrics.py)), and finally `analysis/insights.py`, which builds one dict that feeds both the dashboard and the report. `write_outputs()` writes the CSVs, `report.md` and `dashboard.html`.

Key concepts that span several modules:

- **Confidence model**: each edge gets a hop type (direct / split / fan_out / swap / exchange_deposit / bridge / mixer) with a multiplicative factor. A wallet's confidence is the max-product path from a seed, computed with Dijkstra on `-log(factor)`. A fan-in adjustment uses the receiver's **taint** ratio, so taint is computed before confidence. Scores map to named bands (Very High … Very Low).
- **Taint**: time-ordered haircut propagation per token. A swap's return leg in the same tx inherits the taint of what was sent (`swap_return`).
- **Tracing rules**: follow outflows only after tainted funds arrived (`only_follow_after_receipt`). Never expand terminal categories (`TERMINAL_CATEGORIES` in [labels.py](src/cryptotrace/labels.py): mixer, exchange, bridge, dex) or addresses with a full first page of history (unlabeled services). Follow only allowlisted tokens, using the allowlist's symbol rather than the on-chain one (spoofable). Apply per-token dust thresholds.
- **Labels are evidence**: [data/labels/known_entities.csv](data/labels/known_entities.csv) needs a `source` on every row. `trace` auto-labels DEX contracts from Etherscan verified contract names into `data/labels/auto_<case>.csv`, and `labels_for_case()` merges both files.
- **Configuration**: every tunable number is in [config/settings.yaml](config/settings.yaml). A case YAML (`config/cases/*.yaml`) holds seeds, `start_time`, `ground_truth`, `prices_usd` (used to value transfers in the dashboard) and `overrides`, which are deep-merged over the settings by [config.py](src/cryptotrace/config.py). Paths are resolved relative to `PROJECT_ROOT`. Seed addresses are lowercased.
- **Dashboard**: [dashboard/template.html](src/cryptotrace/dashboard/template.html) is a single self-contained page. `render_dashboard()` replaces the placeholders `__CASE_TITLE__` and `/*__CASE_DATA__*/null` with the insights JSON. The template loads its charts from a CDN.

## Conventions

- Results always come from real Etherscan data. [tests/synthetic_case.py](tests/synthetic_case.py) is a hand-built, test-only fixture with known answers (it injects a fake `fetch` into `trace`). Never use it to produce results.
- Validation: calibrate on Nomad, evaluate on held-out Harmony (don't tune on Harmony). Ground truth is partial, so recall is the main metric and precision is only a lower bound.
- `outputs/`, `data/*.db` and `.env` are git-ignored.
