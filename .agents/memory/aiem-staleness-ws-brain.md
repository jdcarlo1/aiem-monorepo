---
name: AIEM staleness filter + Wall Street brain
description: 3-layer scoring pipeline in aiem_autonomous.py premarket scan; staleness_filter.py + aiem_verification_and_trading_brain.py at project root; HMAC verification endpoints in main.py
---

## The 3-Layer Pipeline (aiem_autonomous.py `aiem_premarket_scan`)

Each premarket candidate passes through three sequential gates after the 9-layer scorer:

**Layer 1: `_score_multiday()`** (built into aiem_autonomous.py)
- Fetches 12-day history via `_get_multiday_context()` (includes `vwap` column)
- Single-day spike ≥15% and ≥3× avg prior → EXHAUSTION: −30 penalty, cap 55/65
- 3+ consecutive up days → CONTINUATION: +20 bonus

**Layer 2: `evaluate_signal_with_data()`** (staleness_filter.py)
- Check 1: gap extended >30% above open → hard SKIP
- Check 2: catalyst decay — news >48h = −4; >24h = −2; no news = −2 (nano float)
- Check 3: move_day tag — in premarket_mode=True, yesterday gap = move_day 2
- Check 4: day-2 above VWAP + decreasing volume → −3
- Threshold: conviction < 70 → SKIP

**Layer 3: `apply_wall_street_pattern_with_data()`** (aiem_verification_and_trading_brain.py)
- PIPE price auto-extracted from news via regex
- Catalyst type auto-classified (delisting/reverse_split/spac_merger/pipe/clinical)
- PATTERN_PIPE_FADE (−10), PATTERN_SYMPATHY_PLAY (−5), PATTERN_DAY2_DISTRIBUTION (−5)
- PATTERN_DELISTING_SQUEEZE (−15), PATTERN_REVERSE_SPLIT (−20), PATTERN_SPAC_MERGER_POP (note only)
- Second-pass threshold check: conviction < 70 after WS → SKIP

## Today's 5 Bad Picks — Validated Results
- TNMG 95→55→SKIP (MOVE_DAY_2, no catalyst)
- DCOY 95→55→SKIP (CATALYST_STALE_48h, MOVE_DAY_2)
- CNET 95→65→SKIP (EXTENDED_FROM_GAP 35%)
- KNDI 95→65→SKIP (CATALYST_STALE_48h 8 days old)
- JATT 95→95→88→PASS (PATTERN_SYMPATHY_PLAY −5 but still ≥70)

## HMAC Verification System
- `AIEM_HMAC_SECRET` set as shared env var (32-byte hex)
- 6 verification questions testing staleness code understanding
- Endpoints (both require ADMIN_TOKEN):
  - GET  /stock-api/aiem/verification/challenge?q=<1-6>&token=...
  - POST /stock-api/aiem/verification/verify  body: {challenge, answer}
- Challenge TTL: 300s; replay-proof (nonce+qid+issued_at in HMAC payload)

## Why
Single-day exploding gaps (TNMG +107%, DCOY +80%) are fade candidates, not continuation plays. The old 9-layer scorer rewarded RVOL + gap magnitude — exactly the signature of Day-1 exhaustion plays. These 3 layers specifically counter that.
