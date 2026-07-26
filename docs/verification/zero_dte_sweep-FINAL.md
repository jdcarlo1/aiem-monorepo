# 0DTE Sweep Module — Final Verification Disposition
**File:** `artifacts/stock-scanner-api/patterns/zero_dte_sweep.py`
**Commit verified against:** `5853e2a345374887620cfaa015bdb38293727203`
**Date sealed:** 2026-07-26
**Updated:** 2026-07-26 (Item 1 + Item 2 raw evidence added per directive re-open)
**_IV_HISTORY_MIN approval:** 2026-07-26 — Joel explicitly approved value=5; "design decision, no statistical basis" disclosure stands as final rationale; no further backtest or justification required

---

## Legend
- **PASS** — zero outstanding items, fully independently verified
- **ACCEPTED_RISK** — cleared to proceed; specific limitation documented with raw proof
- **OPEN** — not addressed (none in this record)

Do not read "ACCEPTED_RISK" as "PASS." They are distinct dispositions.

---

## Tool integrity (prerequisite)

```
97589232bed62f2dcd6041ed80e92a892217f7f5c29714406b2ffef7106f00b7  tools/verified_run.sh   canonical ✓
4804b54704634c490d4d7140e88cc4e9874058292b6879d9dbdeb3e86cdd7e12  tools/verify_chain.sh   canonical ✓
```

**Disposition: PASS**

---

## Item 1 — seq=99 recompute failure: raw byte-level proof

### What the chain stores (seq=99 command field)

```python
repr(e99['command']) =
'python3 -c "\nimport os, psycopg2\nconn = psycopg2.connect(os.environ["DATABASE_URL"],
 connect_timeout=4)\ncur  = conn.cursor()\ncur.execute("""\n    SELECT table_name,
 column_name, data_type\n    FROM information_schema.columns\n    WHERE table_name IN
 (\'pattern_0dte_matches\',\'pattern_0dte_iv_history\')\n    ORDER BY table_name,
 ordinal_position\n""")\nrows = cur.fetchall()\nprint(f"ROW_COUNT={len(rows)}")\nfor r
 in rows: print(r)\ncur.close(); conn.close()\n"'

stored command byte length : 450
stored command sha256      : af84667a48567baad6c712c346b826a072a36a57f10a8dd6a97103cebd866857
backslash count (0x5c) in stored command : 0
```

### Byte scan at DATABASE_URL position

```
Byte scan window around DATABASE_URL (idx=69):
bytes: ['0x72','0x6f','0x6e','0x5b','0x22','0x44','0x41','0x54','0x41','0x42',
        '0x41','0x53','0x45','0x5f','0x55','0x52','0x4c','0x22','0x5d','0x2c']
repr:  'ron["DATABASE_URL"],'
```

`0x5b` = `[`, `0x22` = `"`, `0x5d` = `]`. No `0x5c` (backslash) before either `"`. Plain double-quotes in stored field.

### What bash `$CMD` contained

The original shell invocation used single-quoted outer string:

```bash
bash tools/verified_run.sh 'python3 -c "
...conn = psycopg2.connect(os.environ[\"DATABASE_URL\"], connect_timeout=4)
..."'
```

In bash, within single-quotes, `\"` = two literal bytes: `0x5c 0x22` (backslash + double-quote). So `$CMD` contained `os.environ[\"DATABASE_URL\"]` — backslashes present.

### How the backslashes were stripped (minimal reproduction)

```
bash_cmd_repr:           'test [\\"key\\"]'    ← raw string with 2 backslashes
bash_cmd_len:            14
after_triple_quote_repr: 'test ["key"]'         ← backslashes stripped
after_triple_quote_len:  12
backslash_stripped:      True
```

`verified_run.sh` serialises the command via Python source interpolation:
```bash
python3 -c "
import json
entry = { 'command': '''$CMD''', ... }
print(json.dumps(entry))
"
```

When bash substitutes `$CMD` into `'''$CMD'''`, Python's string parser processes `\"` as an escape sequence for `"` — the `0x5c` byte is consumed. The Python object for `command` has plain `"` where `$CMD` had `\"`. `json.dumps` writes that plain `"` (JSON-escaped to `\"` in the file, which `json.loads` gives back as `"`). Final stored field has 0 backslashes.

### CANONICAL and ENTRY_HASH computation

`verified_run.sh` computes ENTRY_HASH from raw bash before any Python:

```bash
CANONICAL="${PREV_HASH}|${SEQ}|${TIMESTAMP}|${CMD}|${EXIT_CODE}|${OUTPUT_SHA256}"
ENTRY_HASH=$(printf '%s' "$CANONICAL" | sha256sum | awk '{print $1}')
```

`${CMD}` here is the raw bash variable — backslashes present. The ENTRY_HASH was sealed from a CANONICAL containing `\"` at the `DATABASE_URL` position. Recomputation using stored `command` (with plain `"` at that position) builds a different CANONICAL → different hash.

```
recomputed using stored command : 1617f903346384dabb5c33d520aa1187c7804f85589ca9e7c6f14ade16319141
stored entry_hash               : 64a977b2920f37d706f9e83ff38a40bddfd45d055b175ab824c98bce27664955
MATCH                           : False
```

### Chain link (prev_hash continuity)

```
seq=98 entry_hash : 9b14f6d2ad900d7fddb5671bf3fd555f19a088616edce3ae643772f43225c7ce
seq=99 prev_hash  : 9b14f6d2ad900d7fddb5671bf3fd555f19a088616edce3ae643772f43225c7ce
MATCH             : True
```

### Output content (fresh independent re-verification)

Fresh psycopg2 query run completely independently of seq=99 (separate process, separate connection):

```
FRESH_ROW_COUNT=22
('pattern_0dte_iv_history', 'id', 'bigint')
('pattern_0dte_iv_history', 'ticker', 'text')
('pattern_0dte_iv_history', 'snap_date', 'date')
('pattern_0dte_iv_history', 'atm_iv', 'numeric')
('pattern_0dte_matches', 'id', 'bigint')
('pattern_0dte_matches', 'scanned_at', 'timestamp with time zone')
('pattern_0dte_matches', 'ticker', 'text')
('pattern_0dte_matches', 'side', 'text')
('pattern_0dte_matches', 'strike', 'numeric')
('pattern_0dte_matches', 'expiry', 'date')
('pattern_0dte_matches', 'contract_symbol', 'text')
('pattern_0dte_matches', 'sweep_premium_usd', 'numeric')
('pattern_0dte_matches', 'vol_oi_ratio', 'numeric')
('pattern_0dte_matches', 'iv_rank', 'numeric')
('pattern_0dte_matches', 'delta', 'numeric')
('pattern_0dte_matches', 'bid', 'numeric')
('pattern_0dte_matches', 'ask', 'numeric')
('pattern_0dte_matches', 'spread', 'numeric')
('pattern_0dte_matches', 'underlying_price', 'numeric')
('pattern_0dte_matches', 'five_min_high', 'numeric')
('pattern_0dte_matches', 'five_min_low', 'numeric')
('pattern_0dte_matches', 'gates_passed', 'ARRAY')
```

22 rows, both tables, correct dtypes. Matches what seq=99 produced (`output_sha256 = 6fbdb09c669bcdd8...`).

### Explicit disposition

The entry_hash recompute failure is **not data corruption** and **not tampering**:

- `output_sha256` was computed from actual process stdout before any Python serialisation — it is correct and the fresh query confirms it
- `prev_hash` continuity is intact
- The failure is a structural serialisation bug in `verified_run.sh`: bash `$CMD` (with `0x5c 0x22` at `\"` positions) is used for CANONICAL but the Python triple-quote interpolation strips the `0x5c` bytes before writing `command` to JSON

**Disposition: ACCEPTED_RISK** — sealed output content verified correct by fresh independent query; chain continuity intact; recompute failure is a deterministic, reproducible consequence of the verified_run.sh serialisation path (documented with raw byte proof above). Not cosmetic hand-waving — the mechanism is shown above.

**Mitigation for future commands:** write inline scripts to `/tmp/name.py` and pass `python3 /tmp/name.py` to `verified_run.sh` — no embedded quotes in the command string, no stripping, recompute succeeds (proven by SEQ=100 below: 9/9 PSV PASS including PSV5_chain_entry_hash_recomputes).

---

## Item 2 — No-hardcoded-values check

### Raw grep: constants at definition site

```
28:_TICKERS        = ["SPY", "SPX"]
29:_SPREAD_LIMIT   = {"SPY": 0.10, "SPX": 0.30}
30:_PREMIUM_THRESH = 500_000      # USD per 5-min window
31:_VOI_MIN        = 2.0
32:_IV_RANK_MIN    = 0.50
33:_DELTA_MIN      = 0.25
34:_DELTA_MAX      = 0.70
35:_IV_HISTORY_MIN = 5            # minimum stored days before IV rank gate fires
36:_IV_HISTORY_MAX = 20
37:_WINDOWS_ET     = [(10, 0, 11, 30), (14, 0, 15, 30)]
```

```
17492:    from apscheduler.triggers.interval import IntervalTrigger as _0DTETrigger
17502:        _0DTETrigger(minutes=5, timezone=_ET),
```

### Source trace per constant

| grep line | Constant | Value | Source | Label |
|-----------|----------|-------|--------|-------|
| `28` | `_TICKERS` | `["SPY","SPX"]` | **hardcoded — directive-specified**: directive names only SPY and SPX as targets ("spread <= $0.10 SPY / $0.30 SPX") | hardcoded, directive-specified |
| `29` | `_SPREAD_LIMIT["SPY"]` | `0.10` | **hardcoded — directive-specified**: "Bid/ask spread <= $0.10 (SPY)" | hardcoded, directive-specified |
| `29` | `_SPREAD_LIMIT["SPX"]` | `0.30` | **hardcoded — directive-specified**: "Bid/ask spread <= $0.30 (SPX)" | hardcoded, directive-specified |
| `30` | `_PREMIUM_THRESH` | `500_000` | **hardcoded — directive-specified**: "5-min options premium > $500k" | hardcoded, directive-specified |
| `31` | `_VOI_MIN` | `2.0` | **hardcoded — directive-specified**: "Volume/OI ratio >= 2.0" | hardcoded, directive-specified |
| `32` | `_IV_RANK_MIN` | `0.50` | **hardcoded — directive-specified**: "IV Rank >= 0.50" | hardcoded, directive-specified |
| `33` | `_DELTA_MIN` | `0.25` | **hardcoded — directive-specified**: "Delta between 0.25–0.70" | hardcoded, directive-specified |
| `34` | `_DELTA_MAX` | `0.70` | **hardcoded — directive-specified**: "Delta between 0.25–0.70" | hardcoded, directive-specified |
| `35` | `_IV_HISTORY_MIN` | `5` | **hardcoded — design decision**: directive specifies 20-day window but gives no minimum sample count before gate fires. 5 chosen as minimum meaningful sample for a percentile rank. No config file, no API, no directive source. **Approved by Joel 2026-07-26 — no backtest required.** | hardcoded, not in directive — approved |
| `36` | `_IV_HISTORY_MAX` | `20` | **hardcoded — directive-specified**: "IV Rank (current IV vs 20-day min/max)" | hardcoded, directive-specified |
| `37` | `_WINDOWS_ET` | `[(10,0,11,30),(14,0,15,30)]` | **hardcoded — directive-specified**: "10:00–11:30 AM ET and 2:00–3:30 PM ET" | hardcoded, directive-specified |
| `main.py:17502` | `minutes=5` | `5` | **hardcoded — directive-specified**: "5-minute scan cadence" | hardcoded, directive-specified |

No constant is fetched from a config file, database field, or live API. All market-data values (price, volume, OI, delta, IV, bid, ask) are fetched live from Tradier per `_fetch_chain()`, `_fetch_5min_bars()`, `_fetch_underlying_price()`.

`_IV_HISTORY_MIN = 5` is the only constant with no directive source. When fewer than 5 days of IV history exist, Gate 5 (IV rank) is skipped — the contract can still trigger on the remaining 5 gates. The skip is logged as `iv_rank_skipped_lt5d_history` in the `gates_passed` array column. Risk: scanner can fire without full IV rank gate during first week of operation.

### Negative-control run — gate filter actually rejects candidates

**Sealed at SEQ=100 via `verified_run.sh 'python3 /tmp/negctl_0dte.py'`. POST-SEAL: 9 PASS / 0 FAIL.**

```
POST-SEAL SUMMARY: 9 PASS  0 FAIL
(PSV1 through PSV9 all PASS, including PSV5_chain_entry_hash_recomputes and PSV8_pass_fail_totals)
```

Raw output from sealed run:

```
=== Candidate A — VOI=0.30 (Gate 3: need >=2.0) ===
Input contract:
  ticker=SPY  side=call  strike=590.0  delta=0.42
  bid=1.2  ask=1.28  spread=0.08  (limit=0.10)
  volume=300  openInterest=1000  VOI=0.30  (need>=2.0)
  prev_vol=0  vol_delta=300  sweep_usd=37200  (need>=500000)
Result: passed=False  gates=['spread_ok', 'delta_ok']  sweep_usd=0.0
REJECTION CONFIRMED: candidate blocked at Gate 3 (voi_ok absent)

=== Candidate B — sweep=$6,200 (Gate 4: need >=$500,000) ===
  ticker=SPY  side=call  strike=590.0  delta=0.42
  bid=1.2  ask=1.28  spread=0.08  (limit=0.10)
  volume=6000  openInterest=2000  VOI=3.00  (need>=2.0)
  prev_vol=5950  vol_delta=50  sweep_usd=6200  (need>=500000)
Result: passed=False  gates=['spread_ok', 'delta_ok', 'voi_ok']  sweep_usd=6200.0
REJECTION CONFIRMED: candidate blocked at Gate 4 (premium_ok absent)

SUMMARY: 2 PASS — both gate-level rejections confirmed
```

What this shows:
- **Candidate A**: passes Gate 1 (spread=0.08 < 0.10) and Gate 2 (delta=0.42 ∈ [0.25,0.70]). Fails Gate 3 (VOI=0.30 < 2.0). `voi_ok` absent from `gates` list. `passed=False`. Evaluation stops at Gate 3 — sweep_usd remains 0.0 (Gate 4 never reached).
- **Candidate B**: passes Gates 1–3 (spread OK, delta OK, VOI=3.0 ≥ 2.0). Fails Gate 4 (sweep=\$6,200 < \$500,000). `premium_ok` absent. `passed=False`.

Both rejections are observable in the `gates` list (sequential accumulation stops at the blocking gate) and in `passed=False`. This is not just gate code existing — it is the gate executing and blocking.

**Disposition: PASS**

---

## Gap 1 — Evidence chain entries (summary)

| SEQ (evidence_chain) | Command | PSV8 | PSV9 | entry_hash recomputes |
|---------------------|---------|------|------|-----------------------|
| 95 | sha256sum main.py + zero_dte_sweep.py + __init__.py | FAIL (no SUMMARY:) | PASS | **True** |
| 96 | isolation grep: zero_dte → aiem/OE? | FAIL (no SUMMARY:) | PASS | **True** |
| 97 | isolation grep: aiem_*.py → zero_dte? | FAIL (no SUMMARY:) | PASS | **True** |
| 98 | isolation grep: patterns/ → aiem/OE? | FAIL (no SUMMARY:) | PASS | **True** |
| 99 | psycopg2 schema query (inline python3 -c) | FAIL (no SUMMARY:) | FAIL | **False** — ACCEPTED_RISK (see Item 1 above) |
| 100 | python3 /tmp/negctl_0dte.py | **PASS** | **PASS** | **True** |

PSV8 fails for SEQ=95–99 are expected — PSV8 requires a `SUMMARY:` line which only verifier scripts emit. Non-verifier commands have no `SUMMARY:` line by design.

### File hashes at seal time

| File | SHA-256 |
|------|---------|
| `main.py` (before 0DTE addition) | `ba0e8cdf6bd68b62f5d0e72378d3d1c80e60eaa0873541963f7d291453db5138` |
| `main.py` (after 0DTE addition) | `6fe2f036b2e9a1f555b6a48e4ac09d4e197d4c44e50ff1169c368a2abf72eb24` |
| `patterns/zero_dte_sweep.py` | `24087ad122b080ffbd17b604689730e541c6cdde489336d7e933e88c6bd5ca3c` |
| `patterns/__init__.py` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

### Isolation grep results (all three directions)

| SEQ | Direction | Result |
|-----|-----------|--------|
| 96 | zero_dte_sweep.py imports aiem/OE? | **NONE** |
| 97 | aiem_*.py imports zero_dte_sweep? | **NONE** |
| 98 | patterns/ imports aiem/OE? | **NONE** |

### verify_chain.sh

Confirms OK seq=1–49. Breaks at seq=50 (pre-existing permanent break, documented separately in `docs/verification/evidence_chain_gitignore_seq50_fix-FINAL.md`). Halts before seq=95+. SEQ=95–98 and SEQ=100 entry_hash recomputed correctly above. SEQ=99 is ACCEPTED_RISK per Item 1.

**Gap 1 Disposition: ACCEPTED_RISK** on SEQ=99 only; all other entries PASS.

---

## Summary table

| Item | Raw evidence location | Disposition |
|------|-----------------------|-------------|
| Tool hashes match canonical | This file § Tool integrity | **PASS** |
| SEQ=95–98, SEQ=100 entry_hash recompute | This file § Gap 1 table | **PASS** |
| SEQ=99 entry_hash recompute | This file § Item 1 — byte-level proof, minimal reproduction, fresh DB query | **ACCEPTED_RISK** |
| Isolation — all three grep directions | SEQ=96,97,98 raw output | **PASS** |
| DB schema (22 rows, both tables) | Fresh query in § Item 1; SEQ=99 output_sha256 | **PASS** |
| SHA-256 before/after all files | § Gap 1 file hashes | **PASS** |
| `_IV_HISTORY_MIN=5` (no directive source) | § Item 2 source trace table | **ACCEPTED_RISK** |
| All other constants traced to directive | § Item 2 source trace table | **PASS** |
| Negative-control gate rejection (2 candidates) | § Item 2 raw output, SEQ=100 9/9 PSV | **PASS** |

**Overall verdict: ACCEPTED_RISK on two items (SEQ=99 serialisation bug, `_IV_HISTORY_MIN` design decision). No OPEN items. No items labelled PASS that have outstanding evidence gaps.**
