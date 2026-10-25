# Results and findings

Input for the write-up. The design rationale is in [ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md);
every number here can be regenerated with the commands in the [README](../README.md).

## Cases

| | Harmony Horizon Bridge (evaluation, held out) | Nomad Bridge (calibration) |
|---|---|---|
| Date | 23-24 Jun 2022 | 1-2 Aug 2022 |
| Stolen | $95.6M in 8 assets (at incident-day prices) | $169.3M excluding CQT across all 585 exploiters; $109.7M (7 assets) into the 34 seeds |
| Attacker | Lazarus Group (FBI, Jan 2023) | 585 independent addresses |
| Seeds | 1 wallet, named by Merkle Science and Elliptic | 34: the 32 exploit recipients with >= $1M, plus two more named by rekt.news |
| Ground truth | 55 sourced addresses, 8 documented paths | 46 sourced addresses: 41 recovery-wallet returners, 5 rekt.news addresses |
| Trace | 45 addresses, 1,205 transfers, max hop 3 | 300 addresses read, 1,957 reached, 1,952 scored, max hop 4. A first run from all 294 exploiters with >= $100K reached 18,094 addresses and 5.3M transfers, too large to analyze |

## Harmony

Held-out evaluation with the numbers frozen after calibrating on Nomad (500 noise runs). The
uncalibrated run is shown for comparison.

| Metric | Frozen (final) | Uncalibrated | Meaning |
|---|---|---|---|
| In-scope recall (wallets before the mixer) | **11 / 11** | 11 / 11 | Every publicly named pre-mixer wallet is on the trail |
| Trace completeness | **16 / 16 steps** | 16 / 16 | Every documented hop is rebuilt |
| Overall recall | 11 / 55 | 11 / 55 | The other 44 are post-mixer wallets (Railgun withdrawals, Circle-blacklisted, laundering contracts). Expected: tracing stops at mixers |
| Precision (lower bound) | 24% | 24% | The denominator includes the 22 service endpoints |
| AUC (all / wallets only) | 0.57 / 0.58 | 0.51 / 0.58 | See finding 1 |
| Rank stability under ±10% noise | ρ = **0.67** | 0.92 | See finding 9 |
| Band change rate | **35%** | 27% | See findings 5 and 9 |
| Bands | 1 very high, 11 high, 32 medium, 1 very low | 2 / 7 / 35 / 0 / 0 / 1 | |

The reconstruction matches Elliptic's independent analysis: **14 depositor wallets and 857
deposits of exactly 100 ETH** into Tornado Cash, after the seed split its funds into 18,036 ETH pools and then
about 6,000 ETH pools. Confidence falls from 1.00 at the attacker wallet to **0.04** at the Tornado Cash Router
(1.00 × 0.88 direct × 0.85 split × 0.05 mixer).

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

### Results (frozen numbers)

| Metric | Value | Meaning |
|---|---|---|
| Stolen into the seeds | $109.7M in 7 assets, 154 transfers within 2.3 hours | from the bridge router only (`theft_sources` in the case file) |
| Trail | 1,952 wallets and services scored, up to hop 4 | |
| Time to first mixer deposit | 2.7 hours | 367 deposits, $46.3M, into Tornado Cash |
| Returned to Nomad | $19.4M from 9 wallets on the trail | the "returned" hop |
| Known wallets found | 13 / 45 | all 5 rekt.news addresses; 8 of 36 direct returners; **0 of 4 indirect returners** |
| AUC (all / wallets only) | 0.986 / 0.991 | known wallets score well above the rest |
| Rank stability under ±10% noise | ρ = 0.99 (min 0.95) | |
| Band change rate | 9% | |
| Patterns | 743 | 312 rapid movement, 156 fan-in, 114 circular, 90 peeling chains, 71 fan-out |

Most of the 36 returners the bridge paid directly are not on the trail because they took less than $1M
and are not seeds. The 4 indirect returners are not reached within the 300-address limit.

## Calibration

`cryptotrace calibrate config/cases/nomad.yaml --freeze`, 100 noise runs per candidate:

| | direct | split | fan-out | swap | Very high | High | Medium | Lower | Low | Band change (Nomad) | ρ (Nomad) | AUC (Nomad) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| before | 0.92 | 0.80 | 0.60 | 0.90 | 0.90 | 0.75 | 0.55 | 0.35 | 0.15 | 17.7% | 0.980 | 0.974 |
| **chosen** | **0.88** | **0.85** | **0.50** | **0.85** | **0.93** | **0.735** | **0.59** | **0.37** | **0.11** | **8.9%** | **0.991** | **0.971** |

On Nomad, calibration halves the band-change rate. Exchange (0.30), bridge (0.25), mixer (0.05) and
returned (0.92) are not tuned. Because the Very high cut-off (0.93) is now above a direct hop (0.88), only
the attacker wallets themselves are Very high.

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
2. **DEX pools score as high as confirmed attacker wallets.** Uncalibrated, pools (0.72) outranked the confirmed
   Tornado depositors (0.64); with the frozen numbers both score 0.72. The pool itself is not an attacker wallet,
   so AUC is also reported on wallets only.
3. **Merkle Science misprints H8's address** (it repeats H7's). On-chain, the second 18,036 ETH recipient that
   funded H9-H11 is `0x809dc735e80be1578ad4fe04a30aa6b7e280c5e4`; the amounts match both Merkle and Elliptic.
4. **The attribution horizon is the mixer.** Everything after Tornado Cash (the January 2023 Railgun round, the
   exchanges, the BTC addresses the FBI published) is out of on-chain reach: 0 of 44 post-mixer wallets are reached.
5. **Bands are fragile; rankings are not.** Under ±10% noise ρ stays at 0.92 while 27% of wallets change band.
   Scores cluster at products like 0.736 and 0.64, and a cut-off that sits inside a cluster flips all of it.
   With 50 runs this estimate moved between 25% and 31%, so 500 runs are used.
9. **Stability tuned on one case does not carry over to the other.** Calibration halved Nomad's band-change rate
   (17.7% → 8.9%) but made Harmony's worse (27% → 35%, ρ 0.92 → 0.67). With the chosen numbers a direct hop (0.88)
   and a split (0.85) are nearly equal, so noise reorders wallets; and most Harmony wallets score 0.722 or 0.748,
   either side of the new High cut-off (0.735). The cut-offs were placed between Nomad's score clusters, which sit
   elsewhere. Recall and completeness are unaffected. Tuning again on Harmony would defeat the held-out test, so
   this is reported as the result: band cut-offs are case-specific.
10. **Bot-heavy cases need bounds.** On Nomad, the loop detector first found 1.86 million circular transfers among
   bots and busy services at the trail's edge (a 1 GB dashboard). It now only looks at followed wallets, capped at 500.
11. **Seeds that are bots need a named theft source.** Without one, years of the bots' unrelated income counted
   as stolen. The case now names the Nomad bridge router as the only theft source.
6. **The largest Nomad exploiters never moved most of the funds.** `0x56d8…` (~$47M) still holds 37.5M CQT and about
   10,200 WETH; `0xbf29…` (~$40M) never moved its 38.7M USDC. A shallow trail can mean the money stayed put.
7. **Etherscan returns at most 1,000 rows per call.** The first version read one call per address, which silently
   truncated busy addresses and meant the 10,000-transaction "service" rule could never trigger. Fixed by paging;
   Harmony was unaffected (none of its wallets reached 1,000 rows).
8. **Labeling gaps:** CoW Protocol's `GPv2Settlement` was expanded as a wallet (pattern added); Curve pools verify
   as the generic `Vyper_contract` and were labeled by hand. Check every expanded contract in a `trace` log.

## Limitations

- Ground truth is partial on both cases, so precision is a lower bound and AUC is weak (finding 1).
- Nomad is analyzed on a reduced case (the 34 biggest exploiters, 300 read addresses): the full trace from 294
  exploiters reached 18,094 addresses and 5.3M transfers, mostly the histories of bots and busy services.
- Nomad's case starts at midnight on 1 Aug 2022, but the exploit began at 21:32 UTC. Many seeds are bots that
  were active earlier that day, so some of their morning transfers count as moving stolen funds; this is why the
  dashboard lists distribution and conversion events before the theft. Starting the case at 21:32 and
  re-running `calibrate --freeze` and `analyze` would fix it (about 20 minutes).
- On Nomad, money that loops back up the trail is counted at each endpoint it reaches, so "traced to a destination"
  ($252M) exceeds the amount stolen ($110M).
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
