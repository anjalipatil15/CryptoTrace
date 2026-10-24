# What's left to do

Everything for **Harmony** is built and checked. What's left is **Nomad**, the second case: we use it
to tune ("calibrate") the confidence numbers, then run Harmony once more with those numbers. Then
the write-up.

New to the project? Read "The pipeline in plain words" at the top of
[ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md) first (5 minutes).

## 1. Put the API key in place

Create a file called `.env` in the project folder (or copy `.env.example`) with one line:

```
ETHERSCAN_API_KEY=your-key-here
```

## 2. Run everything

In PowerShell, in the project folder:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_all.ps1
```

This does the whole project in about **45-60 minutes**, mostly waiting on Etherscan. You can leave
it running. It:

1. sets up Python (first time only),
2. downloads the Harmony data (~5 min),
3. downloads the Nomad data (~15-30 min),
4. calibrates on Nomad: tries 81 combinations of the confidence numbers, picks the most stable one
   and saves it into `config/settings.yaml` (~15-20 min),
5. analyzes both cases with the saved numbers,
6. opens both presentations in the browser.

If it stops with an error, fix what it says and run the same command again: everything already
downloaded is saved, so it picks up quickly.

Don't open `data/cryptotrace.db` while it runs (that crashes it with "database is locked").

## 3. Fill in the results

Open `outputs/nomad/report.md` and `outputs/harmony/report.md`, scroll to "Validation", and copy the
numbers into the three *Pending* parts of [RESULTS.md](RESULTS.md). The calibration step prints which
numbers changed (all 81 options are in `outputs/nomad/calibration.csv`). In the case table, set
Nomad's seeds to "34 (the 32 biggest exploiters plus two named by rekt.news)" and its trace to the
"Stored N transfers across M addresses" line from step 3.

## 4. Write-up

The findings, limitations and sources are in [RESULTS.md](RESULTS.md). The method is in the
[README](../README.md) ("How it works") and [ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md).

## Results to show

| File | What it is |
|---|---|
| `outputs/<case>/presentation.html` | short walkthrough (arrow keys; works offline) |
| `outputs/<case>/dashboard.html` | full interactive detail (needs internet for its charts) |
| `outputs/<case>/report.md` | all the numbers in text |

## If something goes wrong

| Problem | Fix |
|---|---|
| "Put your Etherscan API key in .env" | step 1 |
| "Install Python 3.10+" | install it from python.org, tick "Add to PATH", run again |
| `database is locked` | something else had the database open; close it and run again |
| network errors | retried automatically; if it still stops, run again |
| the dashboard is blank | it needs internet; `presentation.html` works offline |
