# What's left to do

Everything for **Harmony** is finished: trace, analysis, dashboard and presentation are in
`outputs/harmony/`. What's left is **Nomad**, the second case. We use it to tune ("calibrate")
the confidence numbers before the final Harmony run. Then the write-up.

New to the project? Read [ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md) section "The pipeline in
plain words" first (5 minutes).

## Setup (once)

```powershell
git pull
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env      # paste the Etherscan API key into .env
pytest                      # should print "18 passed"
```

`data/cryptotrace.db` (the downloaded data) is not in git. Without it, step 1 downloads Nomad's data
from Etherscan again, which takes a few hours. If you can, get a copy of that file from Anjali
and put it in `data/`; then step 1 takes minutes.

## 1. Make Nomad smaller and re-trace it (about 15 min with the database copy)

The full Nomad case is too big: 294 attacker wallets and 2.5 million transfers, which takes minutes
just to load and far too long to calibrate. Make it smaller in `config/cases/nomad.yaml`:

```yaml
seeds_from:
  file: data/ground_truth/nomad_exploiters.csv
  min_usd: 1000000        # was 100000: the 32 biggest exploiters, 67% of the stolen value

overrides:
  tracing:
    max_addresses: 300    # was 1100
```

Then run:

```powershell
cryptotrace trace config/cases/nomad.yaml
```

It prints each wallet it visits and ends with `Stored N transfers across M addresses`. Don't open
the database file while it runs (that crashes it with "database is locked").

## 2. Calibrate (about 15-20 min)

```powershell
cryptotrace calibrate config/cases/nomad.yaml
```

This tries 81 combinations of the confidence numbers and picks the one whose results change the
least when the numbers are nudged slightly. It prints a table and a block like this:

```yaml
scoring:
  hop_factors: {direct: 0.92, split: 0.8, ...}
  bands: {very_high: 0.9, high: 0.75, ...}
```

All 81 combinations are saved in `outputs/nomad/calibration.csv`.

## 3. Freeze the numbers

Copy the printed `hop_factors` and `bands` into the `scoring:` section of `config/settings.yaml`
(replace the old values). From now on, don't change them: Harmony is the test case, and tuning
on it would be cheating.

## 4. Run both cases with the frozen numbers (about 5 min)

```powershell
cryptotrace analyze config/cases/nomad.yaml
cryptotrace analyze config/cases/harmony.yaml
```

Open `outputs/<case>/presentation.html` (short walkthrough) and `outputs/<case>/dashboard.html`
(full detail) in a browser.

## 5. Fill in the results

In [RESULTS.md](RESULTS.md), replace the three *Pending* notes with the numbers from
`outputs/nomad/report.md` and `outputs/harmony/report.md` (section "Validation"), and the chosen
numbers from step 2. Update the Nomad row of the case table (seeds: the 32 biggest exploiters plus the five named
by rekt.news; trace: 300 addresses).

## 6. Write-up

The findings, limitations and sources for the report are in [RESULTS.md](RESULTS.md). The method is
in the [README](../README.md) ("How it works") and [ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md).

## If something goes wrong

| Problem | Fix |
|---|---|
| `No module named 'cryptotrace'` | activate the venv and run `pip install -e ".[dev]"` again |
| `ETHERSCAN_API_KEY is not set` | add the key to `.env` |
| `database is locked` | something else had the database open; close it and re-run the same command (downloads are cached, so nothing is lost) |
| Network errors | they are retried automatically; if it still stops, re-run the same command |
| The dashboard is blank | it needs internet for its charts; `presentation.html` works offline |
