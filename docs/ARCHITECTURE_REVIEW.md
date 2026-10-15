# Architecture Review and Recommended Changes

Review of *Confidence-Based Reconstruction of Cryptocurrency Theft Transaction Trails*
(proposal + flow diagrams). It covers what holds up, what needs to change, and how
the codebase in this repository puts those changes into practice.

## Verdict

**The project is sound and can be built in a semester.** The research question ("how does
forensic confidence degrade per hop?") is well-scoped and new. The data is public
and free to access, and the Python stack fits the job. The case choice (Harmony as
the primary case) is good.

The proposal leaves out four things that you will run into as soon as you work with
real Ethereum data. Fix them in the design now:

1. The confidence "scores" are labels with no arithmetic, so they can't be computed or validated.
2. Ethereum uses accounts, not UTXOs. Without time-ordering rules, tracing explodes and misattributes funds.
3. Token swaps through DEXes are missing. The Harmony attacker swapped tokens to ETH before using Tornado.
4. The validation loop tunes and tests on the same data, which is circular.

## 1. Confidence scoring: from labels to a model (most important)

**Problem.** Figure 3 draws the seven patterns as a *sequence* (Direct, then Single
Intermediate, then Splits, ... then Mixer), as if funds always pass through them in that order.
They are really *hop types* that can occur in any order and combine. The scores are
ordinal words ("Lower", "Low") with no rule for combining them along a path. A
wallet reached by a split, then a fan-out, then a direct hop has no defined score.
Also, "Direct" and "Single Intermediate" are not different patterns. They are
1 hop and 2 hops of the same pattern.

**Change.** Use a multiplicative per-hop model:

```
confidence(wallet) = max over paths from seed of  product(hop_factor(edge))
```

| Hop type | Factor | Rule |
|---|---|---|
| direct | 0.92 | sender paid 1 wallet in the 24h window |
| split | 0.80 | sender paid 2-4 wallets |
| fan_out | 0.60 | sender paid >= 5 wallets |
| swap | 0.90 | receiver is a DEX router/pool |
| exchange_deposit | 0.30 | receiver is a labeled exchange (trail ends) |
| bridge | 0.25 | receiver is a bridge (trail ends, cross-chain out of scope) |
| mixer | 0.05 | receiver is a mixer (trail ends) |
| fan-in adjustment | x (0.6 + 0.4 x taint) | receiver has >= 3 senders; penalty scales with how much *clean* money is mixed in |

The resulting number maps back onto your original wording (bands: Very High >= 0.90,
High >= 0.75, Medium >= 0.55, Lower >= 0.35, Low >= 0.15, Very Low below that). The
proposal's table still holds: "Single intermediate = High" falls out naturally because
0.92 x 0.92 = 0.85.

The model has three advantages over the labels:
- Every score can be explained: the report shows the exact path and the factor at each hop.
- It can be validated. Since the weights are explicit parameters, you can run a sensitivity
  analysis on them (see section 4).
- It is computed with Dijkstra on `-log(factor)`, which is a standard algorithm and easy to defend in a viva.

**Add taint as a second metric.** Confidence answers "is this wallet on the trail?".
Taint answers "what share of its funds is stolen?". Taint uses the haircut method in
time order. Without it, fan-in is mis-scored. If an attacker consolidates *only*
stolen funds, the result is *not* ambiguous. Mixing stolen funds with clean funds is.

## 2. Ethereum's account model needs tracing rules

Once stolen ETH lands in a wallet, it can't be told apart from the wallet's other
balance. If the trace naively follows every outgoing transaction, two things go wrong:
- **Time violations.** The trace follows payments made *before* the stolen funds arrived.
  Rule: only follow an outflow if the sender already held tainted funds at that moment.
- **Graph explosion.** One hop into a Binance hot wallet adds millions of transactions.
  Rule: never expand labeled services (exchange, mixer, bridge, DEX). Treat any unlabeled
  address with a full page of history (>= 10,000 txs) as an unknown service.
  Also add a hop limit, an address limit, and a per-token dust threshold.

This needs a **label dataset**, which the proposal does not mention. Etherscan's labels
are not available through the free API. Start from
`data/labels/known_entities.csv` (Tornado Cash pools from the OFAC SDN list, Uniswap
routers, major exchange hot wallets) and extend it as you go. Each row must carry a
source, because the labels are part of your evidence.

Also: **filter spam tokens.** Attacker wallets get flooded with address-poisoning
airdrops, so only follow allowlisted tokens (USDT, USDC, DAI, WETH, WBTC, plus any
specific to the case). Use the allowlist's symbol rather than the on-chain one,
because symbols can be spoofed.

## 3. Missing data: internal transactions and swaps

- Phase 1 must collect three Etherscan streams: `txlist` (normal), `txlistinternal`
  (ETH moved by contracts, e.g. a swap that returns ETH or a Tornado withdrawal) and `tokentx`
  (ERC-20). Without internal transactions, swap outputs and contract payouts are invisible.
- **Swaps.** The trace follows *wallets*, not coins, so a swap mostly takes care of itself:
  the converted tokens come back to the same wallet. What matters is that (a) the DEX is
  not expanded as if it were a wallet, (b) taint carries over to the new token, and
  (c) the swap is not counted as a fan-out or a circular transfer. All three are handled.

## 4. Validation design

- **Circular feedback loop (Figure 7).** If you refine the rules until Harmony matches the FBI
  report and then report accuracy on Harmony, the result is overfit. Instead, **calibrate on
  Nomad, evaluate on Harmony** (held out). (Ronin was dropped from scope: too large for the time available.)
- **Ground truth is partial.** Public reports name *some* attacker wallets, never all of them.
  Report recall as the main metric and present precision only as a lower bound.
- **Define the metrics exactly** (the proposal's "confidence stability" is vague):
  - *Wallet recall*: share of wallets named in public reports that appear on the trail.
  - *Trace completeness*: share of documented path steps reconstructed.
  - *Calibration AUC*: do confirmed wallets score higher than unconfirmed ones? (0.5 means chance.)
  - *Confidence stability*: perturb every factor by +/-10%, 50 times, and report the Spearman rank
    correlation and the share of wallets whose band changes.

  The real Harmony run gives the same picture: rankings stay fairly stable (mean rho 0.92), but about 31% of
  bands flip under +/-10% noise. Discrete bands are fragile near their thresholds,
  which is worth discussing in the report.

## 5. Smaller fixes to the documents

| Where | Issue | Fix |
|---|---|---|
| Fig. 1 vs Sec. 7 | Fig. 1 has 7 layers (with Timeline); Sec. 7 has 6 | Add Timeline Reconstruction to Sec. 7 |
| Fig. 3 | Drawn as a sequence, which implies an order | Redraw as a table/legend of hop type and factor, plus one worked path example |
| Sec. 8 | Etherscan API version not stated | Use **API V2** (`api.etherscan.io/v2/api?chainid=1`); V1 has been retired. Free tier: 5 calls/s |
| Sec. 9 | Storage of raw responses not planned | Cache every API response in SQLite, so analysis is reproducible offline and re-runs cost no quota |
| Sec. 5.2 | Harmony laundering continues past Tornado (Railgun, bridges to BTC in Jan 2023) | State plainly that the trace ends at mixer/bridge exits; this is what makes the confidence decay visible |
| Sec. 5.4 | Nomad has hundreds of independent attackers | Frame it as the attribution-ambiguity / calibration case, not a single-attacker trail |
| Phase 5 | "Peeling chain" comes from Bitcoin (UTXO change outputs) | On Ethereum, define it as: each wallet forwards >= 70% of its outflow to one next wallet |
| Tech stack | Web3.py is listed as core | Etherscan covers all needs; keep Web3.py as an optional extra for receipts/logs |
| Tech stack | Plotly / PyVis for visuals | Replaced by one interactive HTML dashboard (ECharts for charts, Cytoscape.js for the graph) generated by the pipeline |
| Ethics | Not discussed | Add a short note: public data only, no de-anonymization, addresses come from official sources |

## 6. Revised pipeline (as implemented)

```
Phase 1  collection/etherscan.py  Etherscan V2 client (rate-limited, SQLite cache)
         collection/tracer.py     bounded, time-respecting BFS; stops at labeled services
Phase 2  processing/preprocess.py normalize 3 tx streams, dedup, drop failed/zero/spam
Phase 3  graph/builder.py         MultiDiGraph: wallets = nodes, transfers = edges
Phase 4  scoring/taint.py         haircut taint, time-ordered (defines which edges are on the trail)
         scoring/confidence.py    hop classification -> factors -> max-product path -> bands
Phase 5  patterns/detectors.py    fan-out, fan-in, peeling chain, rapid movement, circular
Phase 6  timeline/reconstruct.py  chronological events (hops, services, patterns)
Phase 7  validation/metrics.py    recall, completeness, calibration AUC, sensitivity
Output   analysis/insights.py     roles, USD values, endpoints, staged narrative, chart series
         dashboard/               self-contained HTML dashboard (ECharts + Cytoscape.js)
         report/                  Markdown report, CSVs
```

## 7. Suggested schedule

| Weeks | Work |
|---|---|
| 1-2 | Verify the seed addresses and build ground-truth CSVs from the FBI / Harmony / Chainalysis sources; extend the labels |
| 3-4 | `trace` the Harmony case; inspect the graph and tune the limits (hops, dust, allowlist) |
| 5-6 | Calibrate factors on Nomad; freeze the factors |
| 7-8 | Evaluate on Harmony (held out); run the sensitivity analysis |
| 9-10 | Write up the results; figures from `outputs/` |
