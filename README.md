# CryptoTrace

**Confidence-based forensic tracing of stolen cryptocurrency on Ethereum.**

When crypto is stolen, investigators follow it from wallet to wallet, but every hop makes the
attribution less certain. CryptoTrace reconstructs the trail of stolen funds from public Ethereum
data and gives every wallet on it a **confidence score**: how sure an investigator can be that the
wallet is still on the theft trail. The research question is how fast forensic confidence decays
per hop, and where on-chain attribution stops.

It is applied to two real cases:

| Case | Role | Stolen | Result |
|---|---|---|---|
| **Harmony Horizon Bridge** (Jun 2022, Lazarus Group) | evaluation, held out | $95.6M | 11/11 publicly named pre-mixer wallets found, 16/16 documented hops rebuilt; confidence falls from 1.00 to 0.04 at Tornado Cash |
| **Nomad Bridge** (Aug 2022, 585 exploiters) | calibration | about $180M (incl. CQT at $0.10) | calibration halved how often scores change label under noise (17.7% to 8.9%); known wallets ranked near the top (AUC 0.99) |

Full results, findings and sources: [docs/RESULTS.md](docs/RESULTS.md).
New to the project? Start with the plain-language pipeline in [docs/ARCHITECTURE_REVIEW.md](docs/ARCHITECTURE_REVIEW.md).

## How it works

```
seed wallet(s) ──► 1. collect ──► 2. clean ──► 3. graph ──► 4. taint + confidence ──► 5. patterns ──► 6. timeline ──► 7. validate
                  Etherscan      3 streams,    wallets =    per-hop factors,          fan-out,         ordered          recall,
                  (cached)       spam/dust     nodes,       best path from            fan-in,          events           completeness,
                                 filtered      transfers =  the attacker              peeling, ...                      stability
                                               edges
```

1. **Collect.** A bounded, time-respecting breadth-first trace from the seed wallets. It reads three Etherscan
   streams per address (normal, internal and ERC-20 transfers) and caches every response in SQLite.
   It only follows payments made *after* stolen funds arrived, and never expands labeled services
   (mixers, exchanges, bridges, DEXes, recovery wallets) or addresses with 10,000+ transactions.
2. **Clean.** Only allowlisted tokens are followed (this removes spam and address-poisoning airdrops),
   with a per-token dust threshold.
3. **Graph.** Wallets are nodes and transfers are edges.
4. **Score.** Each transfer gets a factor by hop type, and a wallet's confidence is the best product of
   factors along any path from the attacker (Dijkstra on `-log(factor)`):

   | Hop type | Factor | Rule |
   |---|---|---|
   | direct | 0.88 | the sender paid one wallet in the 24 h window |
   | split | 0.85 | the sender paid 2-4 wallets |
   | fan-out | 0.50 | the sender paid 5 or more wallets |
   | swap | 0.85 | the receiver is a DEX (the asset changes, the owner doesn't) |
   | exchange deposit | 0.30 | custodial exchange; the trail ends |
   | bridge | 0.25 | leaves Ethereum; the trail ends |
   | mixer | 0.05 | Tornado Cash etc.; the trail ends |
   | returned | 0.92 | the victim's recovery wallet; the trail ends |

   These are the values after calibration on Nomad (they started at 0.92 / 0.80 / 0.60 / 0.90).
   Example from Harmony: attacker 1.00 × 0.88 (direct) × 0.85 (split) × 0.05 (mixer) = **0.04**.
   Scores map to six bands, from *Very high* to *Very low*. A second metric, **taint**, tracks what share of
   a wallet's funds is stolen (haircut method, in time order). It lowers the score of wallets that mix
   stolen funds with clean money.
5. **Patterns.** Detects fan-out, fan-in, peeling chains, rapid movement and circular transfers.
6. **Timeline.** Orders the events: theft, swaps, splits, mixer deposits.
7. **Validate.** Compares the trail with wallets and paths named in public investigations (recall,
   trace completeness, AUC), and tests how stable the scores are when every factor is shifted ±10% (500 runs).

**Calibration.** The factors are tuned on Nomad and then frozen before Harmony is evaluated, so the
evaluation case is never used for tuning. Public labels are too sparse to tune for accuracy, so the tuning
targets robustness: the factor set and band cut-offs whose bands change least under noise.

The design and its rationale are in [docs/ARCHITECTURE_REVIEW.md](docs/ARCHITECTURE_REVIEW.md).

## Quick start

Requires Python 3.10+ and a free Etherscan API key (https://etherscan.io/myapikey).

**One command:** put `ETHERSCAN_API_KEY=...` in a `.env` file, then run
`powershell -ExecutionPolicy Bypass -File scripts
un_all.ps1`. It sets up Python, traces both cases,
calibrates on Nomad, freezes the numbers, analyzes both cases and opens the presentations (about an hour).

Or step by step:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env          # add ETHERSCAN_API_KEY
```

```powershell
cryptotrace trace     config/cases/harmony.yaml    # collect into data/cryptotrace.db
cryptotrace analyze   config/cases/harmony.yaml    # score, detect, validate -> outputs/harmony/
cryptotrace calibrate config/cases/nomad.yaml --freeze   # tune factors on Nomad, write them to settings.yaml
python scripts/nomad_exploiters.py                 # rebuild Nomad's seed and returner lists from on-chain data
pytest                                             # tests (a hand-built fixture; never used for results)
```

`analyze --no-sensitivity` skips the 500 noise runs for a quick re-run. After the first `trace`,
`analyze` works offline from the cached data.

## Outputs

Each case writes to `outputs/<case>/`:

| File | Contents |
|---|---|
| **`presentation.html`** | 7-screen walkthrough of the case for presenting (arrow keys to move; works offline) |
| **`dashboard.html`** | interactive dashboard: story, money flow, wallet graph (click a wallet to see why it scored what it did), confidence decay, timeline, patterns, wallet table, method and validation. Needs internet for its chart libraries |
| `report.md` | written investigation report, including validation metrics |
| `wallets.csv` | every wallet on the trail: hop, confidence, band, taint, best path |
| `edges.csv` | transfers on the trail with hop type and factor |
| `patterns.csv`, `timeline.csv` | detected patterns, ordered events |
| `calibration.csv` | every calibration candidate and its scores (calibration case only) |

## Data and configuration

| Path | Contents |
|---|---|
| `config/settings.yaml` | every tunable parameter: hop factors, bands, tracing limits, token allowlist, dust thresholds |
| `config/cases/*.yaml` | one file per case: seeds (inline or from a CSV), start time, reference prices, overrides |
| `data/labels/known_entities.csv` | known mixers, exchanges, DEXes, bridges and recovery wallets, each with a source; `auto_<case>.csv` holds DEXes labeled from Etherscan's verified contract names |
| `data/ground_truth/<case>.csv` | addresses from public investigations (`address, role, path_id, step, source`). Roles starting with `post_mixer` are beyond the trace's reach by design and are excluded from in-scope recall |
| `data/ground_truth/nomad_exploiters.csv`, `nomad_returners.csv` | derived from on-chain data by `scripts/nomad_exploiters.py` |

To add a case: write `config/cases/<case>.yaml` with seed addresses from an official source, add
ground truth with a source on every row, set `prices_usd`, then run `trace` and `analyze`.

## Layout

```
src/cryptotrace/
  collection/    Etherscan client (paged, cached, retries) and the tracer
  processing/    normalizing and filtering the three transfer streams
  graph/         transaction graph
  scoring/       taint and confidence
  patterns/      laundering pattern detectors
  timeline/      event reconstruction
  validation/    metrics, sensitivity and calibration
  analysis/      the narrative and figures behind the dashboard
  dashboard/     dashboard and presentation templates
  report/        Markdown report
scripts/         data preparation (Nomad seeds and returners)
tests/           unit and end-to-end tests on a hand-built fixture
docs/            design review and results
```

## Limitations

- Public reports name only some attacker wallets, so precision is a lower bound and AUC is weak.
- On-chain tracing ends at mixers, bridges and custodial exchanges. Following funds past them needs
  off-chain evidence (exchange records, mixer analysis), which is outside this project.
- USD values use one reference price per token per case.
- Nomad is analyzed on a reduced case (its biggest exploiters) to keep it manageable.

## Ethics

The project uses only public blockchain data and addresses published by law enforcement and security
firms. It does not try to identify the people behind any address.
