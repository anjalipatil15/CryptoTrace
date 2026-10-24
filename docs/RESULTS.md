# Results and findings

Input for the write-up. The design rationale is in [ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md);
every number here can be regenerated with the commands in the [README](../README.md).

## Cases

| | Harmony Horizon Bridge (evaluation, held out) | Nomad Bridge (calibration) |
|---|---|---|
| Date | 23-24 Jun 2022 | 1-2 Aug 2022 |
| Stolen | $95.6M in 8 assets (at incident-day prices) | $169.3M excluding CQT; about 113.55M CQT on top (~$11M at $0.10) |
| Attacker | Lazarus Group (FBI, Jan 2023) | 585 independent addresses |
| Seeds | 1 wallet, named by Merkle Science and Elliptic | 34: the 32 exploit recipients with >= $1M, plus two more named by rekt.news |
| Ground truth | 55 sourced addresses, 8 documented paths | 46 sourced addresses: 41 recovery-wallet returners, 5 rekt.news addresses |
| Trace | 45 addresses, 1,205 transfers, max hop 3 | up to 300 addresses (pending). A first run from all 294 exploiters with >= $100K reached 18,094 addresses and 5.3M transfers, too large to analyze |

## Harmony

*Uncalibrated factors, 500 noise runs. Final numbers after freezing the calibrated factors: pending.*

| Metric | Value | Meaning |
|---|---|---|
| In-scope recall (wallets before the mixer) | **11 / 11** | Every publicly named pre-mixer wallet is on the trail |
| Trace completeness | **16 / 16 steps** | Every documented hop is rebuilt |
| Overall recall | 11 / 55 | The other 44 are post-mixer wallets (Railgun withdrawals, Circle-blacklisted, laundering contracts). Expected: tracing stops at mixers |
| Precision (lower bound) | 24% | The denominator includes the 22 service endpoints |
| Calibration AUC | 0.51 (0.58 on wallets only) | See finding 1 |
| Rank stability under ±10% factor noise | ρ = 0.92 mean | Rankings hold |
| Band change rate | 27% | Bands are fragile near their cut-offs (finding 5) |

The reconstruction matches Elliptic's independent analysis: **14 depositor wallets and 857
deposits of exactly 100 ETH** into Tornado Cash, after the seed split its funds into 18,036 ETH pools and then
about 6,000 ETH pools. Confidence falls from 1.00 at the attacker wallet to **0.04** at the Tornado Cash Router.

## Nomad

### Data (on-chain, reproducible with `python scripts/nomad_exploiters.py`)

- The Nomad ERC-20 bridge router (`0x88a6…30a3`) paid **585 addresses** in **991 transactions** between
  21:32:31 UTC (first exploit transaction) and 00:03:52 UTC (bridge paused). Nobody withdrew between 21:00 and the
  first exploit transaction, so every recipient in the window took exploit funds.
- 293 recipients took >= $100K each (99.9% of the priced value); the 32 with >= $1M took 67%. Those 32 are the seeds.
- **41 addresses returned $31.56M** to Nomad's published recovery wallet (`0x94a8…f154`) before 2023 (returns under
  $1,000 are dropped as dust). **4 of them were not paid by the bridge directly**, so a trace from the
  exploiters has to find the path to them. That makes them the independent recall test.
- Some recipients are bot contracts that forward to a wallet: `0xbf29…` (~$40M per rekt.news) was paid
  through helper contracts that each passed on exactly 202,440 USDC.

### Results

*Pending: run `scripts/run_all.ps1` ([NEXT_STEPS.md](NEXT_STEPS.md)).*

## Calibration

*Pending: run `scripts/run_all.ps1` ([NEXT_STEPS.md](NEXT_STEPS.md)).*

Method (see ARCHITECTURE_REVIEW §4): 81 factor sets (direct, split, fan-out, swap) that keep
direct > split > fan-out. For each set, band cut-offs are nudged by up to 0.05 away from clusters of
scores. The chosen set has the lowest band-change rate under ±10% noise, without lowering the AUC of
the known Nomad wallets by more than 0.02. All candidates are written to `outputs/nomad/calibration.csv`.

## Findings

1. **Harmony's AUC is near chance because the ground truth is incomplete, not because the model fails.** The
   11 "unconfirmed" wallets on the trail follow exactly the same scripted pattern as the confirmed ones
   (18,036 ETH pools, then about 6,000 ETH splits, then 100 ETH Tornado deposits). Elliptic counts 14 depositor wallets;
   Merkle Science names 6. The unnamed ones count as negatives and pull the AUC down. AUC needs known-clean
   wallets to mean anything.
2. **DEX pools (0.72) score higher than confirmed Tornado depositors (0.64).** The swap factor is high because a
   swap doesn't change the owner, but the pool itself is not an attacker wallet. Report AUC on wallets only.
3. **Merkle Science misprints H8's address** (it repeats H7's). On-chain, the second 18,036 ETH recipient that
   funded H9-H11 is `0x809dc735e80be1578ad4fe04a30aa6b7e280c5e4`; the amounts match both Merkle and Elliptic.
4. **The attribution horizon is the mixer.** Everything after Tornado Cash (the January 2023 Railgun round, the
   exchanges, the BTC addresses the FBI published) is out of on-chain reach: 0 of 44 post-mixer wallets are reached.
5. **Bands are fragile; rankings are not.** Under ±10% noise ρ stays at 0.92 while 27% of wallets change band.
   Scores cluster at products like 0.736 and 0.64, and a cut-off that sits inside a cluster flips all of it.
   With 50 runs this estimate moved between 25% and 31%, so 500 runs are used.
6. **The largest Nomad exploiters never moved most of the funds.** `0x56d8…` (~$47M) still holds 37.5M CQT and about
   10,200 WETH; `0xbf29…` (~$40M) never moved its 38.7M USDC. A shallow trail can mean the money stayed put.
7. **Etherscan returns at most 1,000 rows per call.** The first version read one call per address, which silently
   truncated busy addresses and meant the 10,000-transaction "service" rule could never trigger. Fixed by paging;
   Harmony was unaffected (none of its wallets reached 1,000 rows).
8. **Labeling gaps:** CoW Protocol's `GPv2Settlement` was expanded as a wallet (pattern added); Curve pools verify
   as the generic `Vyper_contract` and were labeled by hand. Check every expanded contract in a `trace` log.

## Limitations

- Ground truth is partial on both cases, so precision is a lower bound and AUC is weak (finding 1).
- Nomad is analyzed on a reduced case (the biggest exploiters, 300 traced addresses): the full trace from 294
  exploiters reached 18,094 addresses and 5.3M transfers, mostly the histories of bots and busy services.
- CQT is priced at $0.10, from the exploiters' first sales on 1 Aug 2022 ($0.086-0.108). Their dumping later
  pushed it to $0.03-0.06, so any single price for CQT is approximate.
- Prices are single reference prices per case, so USD totals are approximate.
- The trace follows Ethereum only; bridges and mixers end it.

## Sources

- Merkle Science, *Hack Track: Analysis of Harmony's Horizon Bridge Exploit* (2022): https://www.merklescience.com/blog/hack-track-analysis-of-harmonys-horizon-bridge-exploit
- Elliptic, *The Harmony Horizon Bridge Hack, Part 1* briefing note (2023): https://hub.elliptic.co/hubfs/Harmony%20Horizon%20Bridge%20Hack%20P1%20briefing%20note%20final.pdf
- Blockchain Group, *Harmony's Horizon Bridge Exploit: A Crypto Money Laundering Case Study*: https://blockchaingroup.io/guides/harmonys-horizon-bridge-exploit-a-crypto-money-laundering-case-study/
- rekt.news, *Nomad Bridge - REKT*: https://rekt.news/nomad-rekt
- Mandiant / Google Cloud, *Dissecting the Nomad Bridge Hack*: https://cloud.google.com/blog/topics/threat-intelligence/dissecting-nomad-bridge-hack
- Nomad recovery address (reported by BankInfoSecurity): https://www.bankinfosecurity.com/nomad-recovers-11m-190m-stolen-in-frenzied-attack-a-19697
- PeckShield figures (via CryptoSlate): https://cryptoslate.com/nomad-bridge-drained-of-190m-after-hundreds-of-addresses-copy-hackers-code/
- On-chain data: Etherscan API V2 (https://docs.etherscan.io)
