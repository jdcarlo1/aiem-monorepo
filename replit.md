# [Project name]

_Replace the heading above with the project's name, and this line with one sentence describing what this app does for users._

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required env: `DATABASE_URL` — Postgres connection string

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

_Populate as you build — short repo map plus pointers to the source-of-truth file for DB schema, API contracts, theme files, etc._

## Architecture decisions

_Populate as you build — non-obvious choices a reader couldn't infer from the code (3-5 bullets)._

## Product

_Describe the high-level user-facing capabilities of this app once they exist._

## User preferences

- **Backtesting — always delegate to AIEM:** Any time the user asks for a backtest, historical win rate, signal analysis, or data-driven research query against the scanner's DB tables, I must route it to AIEM (via the chat interface or by instructing the user to ask AIEM directly) rather than running it myself. I may only do the work myself if AIEM is explicitly unavailable or broken. The only exception is when a file edit is needed at the end — that part stays with me.

- **On-demand scoring trigger:** when the user types `score TICKER` (one or more
  symbols, e.g. `score ASTS RKLB`), run each through the full 8-layer Smart Money
  Pressure engine via `GET /stock-api/conviction-stack/score/<ticker>` and report
  total points, tier, and which layers fired.
- **Verification standing rule (precursor signal / backtest code):** any time a
  module, function, or significant change is added under
  `artifacts/stock-scanner-api/` (currently enforced for `precursor_signals.py`,
  `event_study_backtest.py`, and any other `.py` file via the generalized
  pre-commit hook), add corresponding test cases to
  `artifacts/stock-scanner-api/verify_signals.py` in the SAME change. Tests must
  check actual correctness on known-answer synthetic inputs (not just "doesn't
  crash"), and any backtest/prediction code needs a no-lookahead-bias check.
  Run `verify_signals.py` and show the full raw terminal output (not a summary)
  before saying the work is done. Never use `git commit --no-verify` on this
  repo. If a test fails, fix the underlying code — never delete, weaken, or
  skip a failing test to make the suite pass.

## Gotchas

_Populate as you build — sharp edges, "always run X before Y" rules._

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
