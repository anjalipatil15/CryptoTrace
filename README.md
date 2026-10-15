# CryptoTrace

**Confidence-based forensic tracing of stolen cryptocurrency.** CryptoTrace reconstructs
the trail of stolen funds on Ethereum and measures how confident an investigator can be
at each hop, from the theft wallet to mixers, exchanges and bridges.

See [docs/ARCHITECTURE_REVIEW.md](docs/ARCHITECTURE_REVIEW.md) for the design and the
changes made to the original proposal.

## Setup

```powershell
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env      # then add your ETHERSCAN_API_KEY
```

## Usage

All results come from real Ethereum data fetched with your Etherscan API key.

```powershell
cryptotrace trace   config/cases/harmony.yaml    # Phases 1-2: collect into data/cryptotrace.db
cryptotrace analyze config/cases/harmony.yaml    # Phases 3-7: outputs/harmony/
pytest                                           # unit tests (use a small test-only fixture)
```

Outputs per case (in `outputs/<case_id>/`):

| File | Contents |
|---|---|
| `wallets.csv` | every on-trail wallet: hop, confidence, band, taint ratio, best path |
| `edges.csv` | on-trail transfers with hop type and confidence factor |
| `patterns.csv` | detected fan-out / fan-in / peeling / rapid / circular structures |
| `timeline.csv` | chronological event reconstruction |
| **`dashboard.html`** | **interactive investigation dashboard**: open it in a browser |
| `report.md` | plain-text investigation report incl. validation metrics |

The dashboard is a single self-contained file (charts load from a CDN, so an internet
connection is needed). It contains:

- **Overview**: headline figures and key findings, written from the data
- **Story**: the case stage by stage (theft, conversion, consolidation, distribution, mixing, still held)
- **Money flow**: Sankey diagram of where the value went, destinations, value per confidence band
- **Graph**: interactive wallet graph; click a wallet to see its path and why it scored what it did
- **Confidence**: decay by hop, band distribution, sensitivity analysis
- **Timeline, Patterns, Wallets, Method**: activity chart, detected laundering patterns, sortable wallet table, scoring rules and validation

## Before a real trace

1. **Verify the seed addresses** in `config/cases/*.yaml` against the official incident reports.
2. **Fill the ground truth** in `data/ground_truth/<case>.csv` (`address,role,path_id,step,source`)
   from the FBI release, Chainalysis / TRM reports and court documents. Fill `path_id`/`step`
   for documented ordered paths so trace completeness can be measured.
3. **Extend the labels** in `data/labels/known_entities.csv`. Every row needs a source.
   DEX pools are labeled automatically from Etherscan's verified contract names (`data/labels/auto_<case>.csv`).
4. **Set reference prices** (`prices_usd`) in the case file so the dashboard can value the transfers.

## Layout

```
config/            settings.yaml (all tunable parameters) + one YAML per case
data/labels/       known entities (mixers, exchanges, DEXes, bridges)
data/ground_truth/ public-investigation addresses for validation
src/cryptotrace/   the pipeline (one sub-package per phase; analysis/ + dashboard/ build the frontend)
tests/             unit tests; tests/synthetic_case.py is a test-only fixture, never used for results
notebooks/         exploration
outputs/           generated results (git-ignored)
```
