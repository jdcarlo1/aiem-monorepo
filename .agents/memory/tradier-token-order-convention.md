---
name: Tradier token preference-order convention (TOKEN_2 first)
description: Canonical rule for which Tradier secret to prefer, and the bug class it prevents
---

Codebase convention: every Tradier caller must prefer `TRADIER_API_TOKEN_2` first, falling back to
`TRADIER_API_TOKEN` only if unset — never the reverse.

**Why:** `TRADIER_API_TOKEN` is currently dead/revoked (live-tested: 401 "Invalid Access Token").
`TRADIER_API_TOKEN_2` is the valid, live brokerage-account token. Any module that checks
`TRADIER_API_TOKEN` first gets a 401 on every call. If the failure is wrapped in a bare
`except: pass` (common pattern in this codebase for "optional enrichment" data), the whole feature
silently degrades to permanently-empty/NOT_AVAILABLE with no visible error — found twice
independently (`aiem_options_structure.py` GEX/skew/term-structure scan; `aiem_position_sizing.py`
HYG credit-health proxy inside `_get_contrarian_context()`).

**How to apply:** When adding or auditing any new direct Tradier HTTP call (not routed through an
existing shared helper), grep for `TRADIER_API_TOKEN` usage and confirm `TRADIER_API_TOKEN_2` is
checked first. When a Tradier-dependent feature is mysteriously always-empty/NOT_AVAILABLE with no
exception surfaced, check token order FIRST before assuming a data/logic bug — and also check for a
second, independent bug hiding behind the same bare `except`: in the `aiem_position_sizing.py` case,
a `NameError` from referencing an unbound `urllib`/`json` name (module was imported with an alias,
e.g. `import urllib.request as _ur`, but the code below referenced the bare `urllib.request.*` name)
was ALSO present and had to be fixed in the same pass — token-order alone was not sufficient there.
