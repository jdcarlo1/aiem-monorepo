---
name: AIEM vs website scan architecture
description: Canonical rule for which process owns which scanning work — AIEM is fully independent of the website backend.
---

## Rule
Heavy periodic market scans that run on their own schedule and send Telegram alerts belong in the **AIEM process** (`aiem_process.py`), NOT in `main.py` (the website/stock-api backend).

The user's explicit statement: "my Aemi is going to scan polygon everyday, not my website — AIEM is going to be fully independent off of the site. That's the only way it's gonna learn by itself."

## Process map
| Workflow | File | Purpose |
|---|---|---|
| `stock-api` | `main.py` | Flask app serving the website UI; keeps its calculator functions for live Quant-tab requests |
| `aiem-process` | `aiem_process.py` | Standalone autonomous scanner; owns all daily full-universe scanning + Telegram digests |
| `aiem-telegram` | `aiem_telegram_notifier.py` | Standalone Telegram notifier for independent picks |
| `probability-engine-scheduler` | `aiem_probability_engine/daily_scheduler.py` | Re-ranks candidates; isolated |

## Applied example: deep-ITM options scan
- `aiem_optprob.py` was created as a **zero-dependency standalone module** — no Flask, no main.py imports.
- It contains its own Tradier auth, chain/expiry/history fetches, Black-Scholes math, universe pre-filter, DB writes, and Telegram digest.
- `aiem_process.py` imports and schedules it (6 segment scans/day 10:35-15:35 ET + 4:10 PM digest).
- `main.py` kept `_compute_options_probability_matrix` only for the live Quant-tab calculator (user-facing, per-request).

## Why
AIEM needs to operate independently so its learning loop (outcomes → learning → picks) is based solely on its own scans, not on data that was pre-processed or mediated by the website backend.

## How to apply
When adding a new scheduled full-universe scan or Telegram alert:
1. Build it as a standalone module (or add to `aiem_optprob.py` / existing AIEM modules).
2. Register the job in `aiem_process.py`'s scheduler inside `main()`.
3. Do NOT add it to `main.py`'s APScheduler — that process serves live web traffic.
4. If the website UI needs to display results, AIEM writes to a shared DB table; main.py reads from it.
