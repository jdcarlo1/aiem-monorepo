# main → dev → live sync

## Intentional split

| Branch | Role |
|--------|------|
| `main` | GitHub default. Cursor/cloud PRs merge here. CI default. |
| `dev` | Live Replit workspace branch (`git-autosync`, Publish tree). |

Merging only to `main` does **not** update production. Publish deploys the
Replit workspace tree and does **not** `git fetch` from GitHub.

## What used to guarantee sync

Nothing automatic. Humans opened periodic sync PRs (#46, #56).
`deploy-on-merge.yml` only Telegram-reminded after `main` merges — and said
“Publish now” without requiring a `dev` sync or workspace pull.

## Guarantee now

1. **`.github/workflows/sync-main-to-dev.yml`** — on every push to `main`,
   merge `origin/main` into `origin/dev` and push. On conflict: open a PR +
   Telegram, fail the job.
2. **`.github/workflows/deploy-on-merge.yml`** — on push to `dev` (or
   `workflow_dispatch` after auto-sync), Telegram:  
   `git pull --ff-only origin dev` → then **Publish**.

Do **not** change the default Cursor merge target to `dev`: that would bypass
`main` as the integration branch and fight GitHub `default_branch=main`.

## After a main merge (ops)

1. Wait for **Sync main → dev** (Actions) to go green.
2. In Replit Shell: `git pull --ff-only origin dev` (confirm SHA).
3. **Publish** (outside Mon–Fri 08:50–10:20 ET).
