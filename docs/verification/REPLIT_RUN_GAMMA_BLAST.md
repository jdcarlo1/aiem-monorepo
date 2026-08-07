# Replit: run Gamma Blast backtest (copy-paste handoff)

**For:** Replit Agent / Shell on stocksai.com (`stock-api` host)  
**From:** Cursor Cloud (no `POLYGON_API_KEY` here — you must run this)  
**Goal:** Execute the Gamma Blast backtest, **save full results**, do not discard them.

---

## 0) Pull the script (required)

The backtest lives on branch `cursor/gamma-blast-backtest-e150` (PR #39).  
Live Replit usually tracks `origin/dev` — so either merge that PR into `dev` first, or pull the branch:

```bash
cd /home/runner/workspace   # or your repo root
git fetch origin cursor/gamma-blast-backtest-e150
git checkout cursor/gamma-blast-backtest-e150
# OR merge into dev then: git pull --ff-only origin dev
```

Confirm the script exists:

```bash
ls -la artifacts/stock-scanner-api/gamma_blast_backtest.py
```

Confirm Polygon key is present on this host:

```bash
python -c "import os; print('POLYGON_OK' if os.environ.get('POLYGON_API_KEY') else 'POLYGON_MISSING')"
```

If `POLYGON_MISSING` → stop and set the secret; do not invent data.

---

## 1) Baseline run (do this first)

```bash
cd artifacts/stock-scanner-api
python gamma_blast_backtest.py --days 20 --mode synthetic --label baseline
```

- **$100 risk per trade** (script default)
- `synthetic` = Black-Scholes logic check only — **not** real options P&L
- Full ledger is auto-saved under:

`docs/verification/gamma-blast/`

Expect:

- `gamma-blast-baseline-synthetic-<date>-<stamp>.json` (full trades + config)
- `LATEST-synthetic.json`
- `RUN_INDEX.jsonl` (append)

---

## 2) Real options pricing (if Options minute aggs work)

```bash
cd artifacts/stock-scanner-api
python gamma_blast_backtest.py --days 20 --mode real --label baseline-real
```

If Polygon returns empty / NOT_AUTHORIZED on `O:SPY…` 1-min bars, paste the error and stop — do not fake fills.

---

## 3) Optional: quick TP/SL sweep (keep every variant)

```bash
cd artifacts/stock-scanner-api
python gamma_blast_backtest.py --days 20 --mode synthetic --sweep-quick
```

Archives each TP×SL variant into the same `docs/verification/gamma-blast/` folder + `RUN_INDEX.jsonl`.

---

## 4) After it finishes — report back

1. Paste the **console summary** (trades, win rate, total P&L, disclaimer line).
2. Commit the archive files so Cursor can pull them:

```bash
cd /home/runner/workspace
git add docs/verification/gamma-blast/
git status
git commit -m "Gamma Blast backtest results (full ledger archive)"
git push origin HEAD
```

3. Tell Joel/Cursor: path to `LATEST-synthetic.json` (and `LATEST-real.json` if run).

**Do not delete** anything under `docs/verification/gamma-blast/`. Joel wants to change variables later and compare which settings work best.

---

## Knobs for later (do not need now)

```bash
python gamma_blast_backtest.py --days 20 --mode synthetic \
  --take-profit 3.0 --stop-loss 0.50 \
  --range-threshold 0.01 --breakout-threshold 0.05 \
  --time-stop 45 --risk-per-trade 100 --label custom-1
```

---

## What this is NOT

- Not a live broker buy
- Not Pattern Lab Gap Fill / ORB / F3
- Not D1/D2/D3 changes — standalone script only
