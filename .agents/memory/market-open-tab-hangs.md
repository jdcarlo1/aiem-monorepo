---
name: Market-open tab hangs (StockScanner)
description: Why ~55 dashboard tabs spun forever / "Load failed" at market open and the layered fix (scheduler, curl_cffi breaker, lazy-scan-in-request, NaN JSON)
---

# Market-open dashboard tab hangs — root cause + fix pattern

Symptom: at market open most dashboard tabs spin forever or show "Load failed".
Triggered/worsened after moving prod to a Reserved VM (always-on actually RUNS the
full morning scan burst that Autoscale used to skip).

## Layered causes & the durable rules

- **yfinance HTTP does NOT go through `requests`.** yfinance (this project is on
  v1.4.1) fetches Yahoo via **curl_cffi** (`new_session()` returns
  `curl_cffi.requests.Session(impersonate="chrome")`). So any timeout adapter or
  circuit breaker mounted on `requests.Session` is USELESS for yfinance.
  **Rule:** to bound/guard yfinance network calls, patch
  `curl_cffi.requests.Session.request`. Import the error as
  `from curl_cffi.requests import RequestsError` (NOT from `.exceptions`; its
  class `__name__` is "RequestException").
  - yfinance hard-codes `timeout=30` per call and `YfConfig.network.retries == 0`
    by default, so a single slow Yahoo connection hangs the Flask worker ~30s.
    The fix caps Yahoo timeouts to 8s and trips a global breaker (30s cooldown)
    on 429/503/timeout so subsequent Yahoo calls fail INSTANTLY and endpoints
    fall back to cache/DB in <1s. `_is_transient_error` only retries names like
    Timeout/ConnectionError — raising RequestsError when the breaker is open is
    non-transient, so it fails fast.

- **Yahoo also throttles via HTTP 401 "Unauthorized"/"Invalid Crumb" floods —
  NOT just 429/503.** A 401 is a *returned response* (not an exception, not
  429/503) that yfinance silently swallows as "no data" (log spam: "$X possibly
  delisted; no price data found"). So a breaker that only trips on 429/503/timeout
  NEVER trips under this throttle, and live-scan endpoints churn hundreds of slow
  401s (~15-20s) → frontend fetch times out → "Load failed" **even though the
  backend returns HTTP 200** (logs show the 200s; the issue is latency, not 500s).
  **Rule:** trip the breaker on a *burst* of 401/403 (threshold ~5 within 20s,
  not a single one — a lone 401 is a benign yfinance crumb refresh). Count hits
  under a lock in both the `requests` adapter and the `curl_cffi` patch.

- **Never run a live scan synchronously inside a web request.** `daily-top10`
  (Overview tab) called `_compute_daily_top10()` which ran
  `scan_tickers(DEFAULT_LEADERBOARD)` live whenever today's DB row was missing or
  marked `stale` (the DB loader marks any prior-day fallback `stale=True`). That
  was a ~35s block.
  **Rule:** serve cached/stale DB data immediately and refresh in a daemon thread
  (guarded against stacking). HTTP threads must never block on `scan_tickers`.

- **APScheduler defaults cause morning pileup.** Default pool is 10 workers,
  `misfire_grace_time=1s`, `coalesce=False`. The 9:30–9:45 burst saturates CPU +
  Yahoo and starves Flask HTTP threads.
  **Rule:** create `BackgroundScheduler(executors={"default": ThreadPoolExecutor(
  max_workers=4)}, job_defaults={coalesce:True, max_instances:1,
  misfire_grace_time:600})`. Also stagger/trim the morning burst (morning_inflows
  6→3 slots, news_catalyst deduped).

- **NaN/Inf = invalid JSON = infinite spinner.** Python `json` emits literal
  `NaN`/`Infinity`, which `JSON.parse` rejects → tab spins (this is what broke
  `standout-track`). **Rule:** install a Flask `DefaultJSONProvider` subclass that
  recursively maps NaN/Inf→None globally, so every response is valid JSON.

## Prod can HANG ENTIRELY (not just slow tabs)
At market open on the Reserved VM, the unbounded-scheduler + heavy morning burst
can saturate the single Flask process so badly the whole site stops responding
(domain won't load), not merely individual slow tabs. Signature in
`fetch_deployment_logs`: APScheduler "Run time of job ... was missed by N min"
spam, a Neon "SSL SYSCALL error: EOF detected" DB drop, then **logs go silent**.
- **Rule:** prod logs silent for an extended stretch = the VM process is WEDGED,
  even though `getDeploymentInfo()` still reports `hasSuccessfulBuild: true`.
  Confirm the gap with `fetchDeploymentLogs({afterTimestamp: <last seen>})`
  returning nothing. Recovery = redeploy (user clicks Publish) — that restarts the
  hung VM AND ships the fix. There is no agent-side "restart prod VM" tool;
  `restart_workflow` only touches DEV workflows.
- **Rule:** dev fixes do NOT protect prod until PUBLISHED. Don't assume an active
  incident is covered just because the fix is done in dev.

## Verifying
- Safe GET-only smoke test via code_execution Node fetch to 127.0.0.1:5050
  (NEVER hit email/admin/test/send/trigger/POST routes — they email all subs).
  Run sequentially with a small gap; rapid-fire self-DOSes Yahoo.
- Tabs are React state (default "lookup"), NOT URL-addressable → use the testing
  (Playwright) skill to click a specific tab and screenshot it.
