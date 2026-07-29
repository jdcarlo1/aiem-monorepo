# NCLEX AI — HIGH CVE Reachability Analysis
Date: 2026-07-29
Tool: pnpm audit (post-update: vite, postcss, http-proxy-middleware updated 2026-07-29)
Remaining count: 8 HIGH

This document distinguishes "genuinely unreachable" (structural — no code path exists) from
"unlikely" (code path exists but exploitability requires improbable conditions).

---

## HIGH CVEs — One-line reachability verdict per finding

---

### 1. form-data — CRLF injection (GHSA-hmw2-7cc7-3qxx)
- Vulnerable versions: >=4.0.0 <4.0.6
- Dependency path: `artifacts__api-server > @anthropic-ai/sdk > form-data`
- `@anthropic-ai/sdk` is a **runtime** dep (package.json `dependencies`), actively used in
  `routes/analyze.ts`, `routes/catalyst.ts`, `routes/morning-brief.ts`.
- The CRLF injection fires only when calling Anthropic's **Files API** (`client.files.upload()`)
  with a user-controlled filename or field name. Our routes use `messages.create()` only —
  confirmed by grep: no call to `files.upload`, `FormData`, `multipart`, or `filename` in any
  Anthropic-calling file.
- **Verdict: GENUINELY UNREACHABLE** — the SDK's multipart code path is dead in our usage.

---

### 2. linkify-it — ReDoS, quadratic scan loop (GHSA-22p9-wv53-3rq4)
- Vulnerable versions: <=5.0.0
- Dependency path: `lib__api-spec > orval > typedoc > markdown-it > linkify-it`
- `orval` is an OpenAPI code-generation tool. `typedoc` is a TypeScript documentation generator.
  Neither runs at request time. The path starts at `lib__api-spec` (the workspace's API spec
  package), which is a dev/build artifact — it has no presence in the production runtime bundle.
- **Verdict: GENUINELY UNREACHABLE** — entire chain is build-time tooling; no request handler
  parses user-supplied text through markdown-it.

---

### 3. brace-expansion — DoS via exponential expansion (GHSA-3jxr-9vmj-r5cp)
- Vulnerable versions: >=3.0.0 <5.0.7
- Dependency path: `lib__api-spec > orval > typedoc > minimatch > brace-expansion`
- Same `lib__api-spec > orval` chain as above — build-time tooling only.
- No request handler calls `minimatch()` or any glob API with user-supplied patterns.
- **Verdict: GENUINELY UNREACHABLE** — build-time only.

---

### 4. js-yaml — quadratic CPU via merge-key chains (GHSA-52cp-r559-cp3m)
- Vulnerable versions: >=4.0.0 <4.3.0
- Dependency path: `lib__api-spec > orval > js-yaml`
- Again under `lib__api-spec > orval` — YAML is parsed by orval when generating TypeScript
  client code from the OpenAPI spec. This happens at developer build time, not request time.
- No endpoint accepts or parses YAML from callers.
- **Verdict: GENUINELY UNREACHABLE** — build-time only.

---

### 5. linkify-it — Quadratic DoS via `mailto:` validator (GHSA-v245-v573-v5vm)
- Vulnerable versions: <=5.0.1
- Dependency path: `lib__api-spec > orval > typedoc > markdown-it > linkify-it`
- Same chain as finding #2.
- **Verdict: GENUINELY UNREACHABLE** — build-time only.

---

### 6. fast-uri — host confusion via literal backslash (GHSA-v2hh-gcrm-f6hx)
- Vulnerable versions: >=3.0.0 <=3.1.3
- Dependency path: `lib__api-spec > orval > @scalar/openapi-parser > ajv > fast-uri`
- `@scalar/openapi-parser` and `ajv` are used by orval to validate the OpenAPI spec at build
  time. Neither is loaded in the production API server bundle.
- **Verdict: GENUINELY UNREACHABLE** — build-time only.

---

### 7. brace-expansion — DoS via unbounded expansion causing OOM (GHSA-mh99-v99m-4gvg)
- Vulnerable versions: <=5.0.7
- Dependency path: `lib__api-spec > orval > typedoc > minimatch > brace-expansion`
- Same chain as finding #3 — different CVE, same package, same path.
- **Verdict: GENUINELY UNREACHABLE** — build-time only.

---

### 8. fast-uri — host confusion via failed IDN canonicalization (GHSA-4c8g-83qw-93j6)
- Vulnerable versions: >=3.0.0 <3.1.3
- Dependency path: `lib__api-spec > orval > @scalar/openapi-parser > ajv > fast-uri`
- Same chain as finding #6 — different CVE, same package, same path.
- **Verdict: GENUINELY UNREACHABLE** — build-time only.

---

## Summary Table

| # | Package | CVE | Path root | Verdict |
|---|---|---|---|---|
| 1 | form-data | GHSA-hmw2-7cc7-3qxx | api-server (runtime) | GENUINELY UNREACHABLE — Files API not called |
| 2 | linkify-it | GHSA-22p9-wv53-3rq4 | lib__api-spec (build) | GENUINELY UNREACHABLE — build tool chain |
| 3 | brace-expansion | GHSA-3jxr-9vmj-r5cp | lib__api-spec (build) | GENUINELY UNREACHABLE — build tool chain |
| 4 | js-yaml | GHSA-52cp-r559-cp3m | lib__api-spec (build) | GENUINELY UNREACHABLE — build tool chain |
| 5 | linkify-it | GHSA-v245-v573-v5vm | lib__api-spec (build) | GENUINELY UNREACHABLE — build tool chain |
| 6 | fast-uri | GHSA-v2hh-gcrm-f6hx | lib__api-spec (build) | GENUINELY UNREACHABLE — build tool chain |
| 7 | brace-expansion | GHSA-mh99-v99m-4gvg | lib__api-spec (build) | GENUINELY UNREACHABLE — build tool chain |
| 8 | fast-uri | GHSA-4c8g-83qw-93j6 | lib__api-spec (build) | GENUINELY UNREACHABLE — build tool chain |

**Corrected summary (per `pnpm list --prod --depth Infinity` on artifacts/api-server):**

7 of 8 HIGH severity findings (linkify-it ×2, brace-expansion ×2, js-yaml, fast-uri ×2) trace exclusively through the orval/typedoc build-toolchain in the `lib__api-spec` workspace package and are entirely absent from the api-server production dependency tree. The remaining 1 HIGH finding (form-data, GHSA-hmw2-7cc7-3qxx) is present in the production tree via `@anthropic-ai/sdk → @types/node-fetch → form-data 4.0.5`; it is unreachable at runtime because the API server never calls `client.files.upload()` or any multipart code path in the Anthropic SDK (confirmed: no call to `files.upload`, `FormData`, `multipart`, or `filename` in any Anthropic-calling route). The one production-path CVE whose dependency path cannot be argued unreachable is body-parser (via express), which the audit rates severity **LOW**, not HIGH.
