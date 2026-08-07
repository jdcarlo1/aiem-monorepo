# Deploy audit — Monday 2026-08-03 → Friday 2026-08-07

**Live site = Replit = `origin/dev`.**  
Cursor agents merged almost everything to `main`. Only PRs whose merge commit is an ancestor of `origin/dev` are live.

Checked: 2026-08-07 against `origin/dev` tip `8f530c66` and sync branch PR #46 tip `64fd5b92` (= `origin/main`).

## Verdict

| | Count |
|---|---:|
| Merged PRs since Monday | **31** |
| Actually on live (`dev`) | **4** |
| Merged to `main` but **not** live | **27** |
| Covered by PR #46 (`main` → `dev`) | **all 27** |

PR #46 tip equals `main` tip — one merge into `dev` + Publish + stock-api restart deploys the entire backlog.

## LIVE on site (YES)

| PR | Date | Title |
|---:|---|---|
| #9 | Aug 4 | Guard patches / push-gate hardening |
| #10 | Aug 4 | Deploy-on-merge Telegram reminder |
| #11 | Aug 4 | Phase 6 trigger engine + OE scheduler sync |
| #33 | Aug 6 | Install Pattern Lab on `dev` (Gap Fill / ORB / F3 menu) |

## NOT on site (NO) — all included in PR #46

| PR | Date | Title |
|---:|---|---|
| #12 | Aug 4 | Connect Neon via DATABASE_URL |
| #13 | Aug 4 | Wide-net pre-move CALL + zero-pick fixes |
| #14 | Aug 5 | Remaining AIEM/ASE wiring (items 1–9) |
| #15 | Aug 5 | Non-negotiable Cursor standing rules |
| #16 | Aug 5 | Clean main |
| #17 | Aug 5 | Deploy-proof `/run-now` pattern_score |
| #18 | Aug 5 | Joel's Morning Alerts (PLTR-style flow) |
| #19 | Aug 5 | `aiem_morning_scan` Neon `score` column fix |
| #20 | Aug 6 | Four AIEM loop breaks |
| #21 | Aug 6 | Pattern Lab Gap Fill + ORB (main merge; live UI came via #33) |
| #23 | Aug 6 | Pattern Discovery Framework |
| #24 | Aug 6 | AIEM communication protocol |
| #26 | Aug 6 | StockScanner E2E audit / formula fixes |
| #27 | Aug 6 | Quant signals → decisions + AI picks |
| #28 | Aug 6 | Stock Scanner live tab pass |
| #30 | Aug 6 | Paper `lock_contention` + scanner_ai pin |
| #31 | Aug 6 | F3 SPY 0DTE Pattern Lab + OE (main; live via #33) |
| #34 | Aug 6 | Insider Radar mobile black screen |
| #35 | Aug 6 | Dev→main merge |
| #36 | Aug 6 | Resolve Dev→main conflicts |
| #37 | Aug 6 | CI: disable scheduled Actions |
| #38 | Aug 7 | OE/AIEM terminal unjam + morning_scan |
| #39 | Aug 7 | Gamma Blast backtest handoff |
| #40 | Aug 7 | SPY asymmetric 23-strategy BT |
| #42 | Aug 7 | Top-3 asym packages → Pattern Lab + OE |
| #43 | Aug 7 | Asym `aiem_paper_trades` persist dry-run |
| #45 | Aug 7 | Narrow-wing butterfly + bullish RR |

## Open (not merged yet — not on `main` or live)

| PR | Base | Title |
|---:|---|---|
| #32 | main | F3 real options pricing backtest |
| #41 | main | F3 3m real Polygon backtest results |
| #44 | main | SPY catalog untested strategies BT |
| #46 | **dev** | **This sync — merge this to put the 27 missing PRs live** |

## After merging #46

1. Merge PR #46 into `dev`
2. Replit **Publish**
3. Restart **stock-api** (and dashboards if separate)
4. Hard refresh — Pattern Lab should show asym cards; morning-scan / Insider Radar / scanner fixes from the backlog should be in the build

## Going forward

Do **not** require two sends. Either:
- open Cursor PRs with **base = `dev`**, or  
- after every `main` merge, open/merge a `main` → `dev` sync before Publish.
