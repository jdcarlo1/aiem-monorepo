# AIEM Institutional Terminal — Phase 9 of 12
## Section 12: Indicator Laboratory (IND-001–030)
## Status: COMPLETE — SEQ=100 EXIT=1 PASS=6 FAIL=9 PARTIAL=12 NOT_IMPLEMENTED=3

---

## Chain Integrity

| Field | Value |
|---|---|
| SEQ | 100 |
| EXIT | 1 (correct — 9 FAIL items) |
| TS | 2026-07-23T20:13:09Z |
| TS_END | 2026-07-23T20:13:12Z |
| archive_sha256 | 6a6943d9e3bac1c746a6d7fd1cda5973f96c98c498890ac192ec9096b33f96cc |
| log_sha256 | c21229489b237de2e5be2e87af2526d8d0918fc5d012e676d38c76d35be7d20e |
| entry_hash | 15b14b6489cbeae03ed578b90c75443b2fb719bb94934d9f1c075358f5be0b00 |
| prev_hash | 9e41b3da6bd89c3079b8a9263b99e0a99369c0da54cfc88c5c4ed6765d25d6fc |
| verified_run.sh sha256 | 58534be51d9445e13c1838532a7d94c2773d6e152d435e6f620ddba64a9f3bf5 |
| verify_chain.sh sha256 | ca7896c7c832ef53430dfd07319418000d9139566c9e52720f587aa9c9840d1f |
| verify_phase9_ind.py sha256 | d702812f8ccc02b4159c10ab5377978a66306065823d4b28e8f033ed7031bd4b |
| Post-seal checks | PSV 9/9 PASS |

---

## Indicator Infrastructure Context

Three distinct production indicator stores exist, none of which is universally registered:

| Store | Rows / Columns | Registry | Source |
|---|---|---|---|
| `oe_indicator_registry` | 79 rows | **Formal** (options engine only) | `aiem_options_scheduler.py` |
| `oe_indicator_snapshots` | 2,529 rows | via canonical_id FK | options pipeline per-trace |
| `polygon_indicators_daily` | ~3.36M rows | **None** | `aiem_process.py` / stat runner |
| `layer9_scores` | 1,587 rows | **None** | `layer9_statistical_edge.py` |
| `oe_indicator_attribution` | **0 rows** | designed, empty | attribution system never populated |
| conviction stack L1-L9 (~39 indicators) | inline computation | **None** | `main.py` / `scoring.py` / `ensemble_combiner.py` |

The Phase 9 spec note (`cross-reference against the existing 39-indicator audit; Thompson Sampling #06 and Bayesian Statistics #24 confirmed inert`) applies: the ~39 conviction-stack indicators are entirely absent from any formal registry and fall outside the scope of `oe_indicator_registry`.

---

## Verdict Table

| Item | Verdict | Key Evidence |
|---|---|---|
| IND-001 | **PARTIAL** | `oe_indicator_registry` covers 79 options-engine indicators; polygon tech (19 cols), layer9 statistical (14 fields), conviction stack (~39) absent from registry |
| IND-002 | **PASS** | `COUNT(*)=79`, `COUNT(DISTINCT canonical_id)=79`, duplicates=[] |
| IND-003 | **PASS** | null_or_empty_names=0/79; `name` NOT NULL in schema |
| IND-004 | **PARTIAL** | `source_file` populated 79/79 but all='aiem_options_scheduler.py'; `source_function` all='_execute_job' (wrapper, not per-indicator granularity); polygon/layer9 undocumented |
| IND-005 | **PARTIAL** | File-level location recorded; function-level not granular; polygon/layer9 have no source-file field |
| IND-006 | **FAIL** | No `description`, `calculation_method`, `method`, or `formula` column in `oe_indicator_registry` or any indicator table |
| IND-007 | **FAIL** | `parameters='{}' for all 79 rows` (SQL confirmed); `register_indicator()` always called with empty dict; no `required_inputs` column |
| IND-008 | **FAIL** | No `output_fields`, `outputs`, or `produced_outputs` column in `oe_indicator_registry`; `oe_indicator_snapshots` stores runtime values but registry declares no output schema |
| IND-009 | **PASS** | `registered_at` null=0/79; `captured_at` null=0/2529; range 2026-07-20→2026-07-20 |
| IND-010 | **PARTIAL** | `freshness_seconds` non-null=353/2529 (14%); `quality_status` (FRESH/STALE/MISSING) populated for all 2,529 rows |
| IND-011 | **PASS** | `quality_status` null=0/2529; distribution: FRESH=1652 MISSING=641 STALE=236; `q or ("MISSING" if raw is None else "FRESH")` at line 720 |
| IND-012 | **PARTIAL** | `oe_indicator_snapshots` has no error column; `quality_status='MISSING'` encodes failure without detail; `layer9_scores.error` column exists with 7 populated rows; exception logs to debug only |
| IND-013 | **PARTIAL** | `sha256` populated 79/79 (code-hash, not semantic version); no `version` column; polygon/layer9 have no version field |
| IND-014 | **FAIL** | `parameters='{}' for all 79 rows` (SQL confirmed); timeframe and parameters are schema columns that are structurally never populated |
| IND-015 | **FAIL** | `timeframe=NULL for all 79 rows` (SQL: `SELECT DISTINCT timeframe` → `[None]`); polygon/layer9 have no timeframe column |
| IND-016 | **PARTIAL** | No `market_regime` in registry; `oe_indicator_snapshots.regime_context` exists but null=2529/2529; `layer9_scores.regime` populated (trending/random_walk/unknown) at score level not indicator level |
| IND-017 | **FAIL** | No `asset_type`, `asset_class`, or `applicable_asset` column in any indicator table; `family` is domain grouping, not asset type |
| IND-018 | **PASS** | `oe_indicator_snapshots.raw_value` non-null=1814/2529; `raw_value_text` covers additional 362; `layer9_scores.hurst_raw` non-null=1587/1587, `vpin_raw` non-null=1587/1587 |
| IND-019 | **PARTIAL** | `normalized_value` non-null=576/2529; polygon/layer9 individual indicators lack normalization column; `layer9_scores.statistical_score` (0–100) is aggregate normalized score only |
| IND-020 | **FAIL** | `contribution_score` null=2529/2529; `weight` null=2529/2529; `supported_decision` null=2529/2529 — all three scoring-contribution columns exist in schema but are never written by `snap_indicator()` |
| IND-021 | **FAIL** | `oe_indicator_attribution` has 0 rows; schema is correct (lift, IC, brier_score_delta, p_value_corrected, is_significant) but attribution system is never populated |
| IND-022 | **NOT_IMPLEMENTED** | `aiem_specialist_council_runs` and `bull_bear_debates` exist; no per-indicator contribution stored; specialist council operates at strategy level not indicator level |
| IND-023 | **PARTIAL** | `trace_id` architectural linkage `oe_indicator_snapshots → oe_decision_records` exists; `oe_decision_records` has 0 rows (never populated); `supported_decision` null for all snapshots |
| IND-024 | **PASS** | `grep -n` line 720: `q or ("MISSING" if raw is None else "FRESH")` — raw=None forces `quality_status=MISSING`, not neutral/zero; 641 MISSING rows in production confirm gate fires |
| IND-025 | **PARTIAL** | `REGISTRY_MISSING_INDICATOR` and `REGISTRY_STALE_DATA` strings appended to `_reg_gate_failures` and recorded in `verify_result["gate_failures"]`; gate is non-fatal (line 702 comment: "non-fatal — never block pipeline") |
| IND-026 | **PARTIAL** | STALE classification applied (`_pmd_q = "STALE" if _pmd_age > _pmd_stale_thresh`); 236 STALE rows in production; `REGISTRY_STALE_DATA` gate recorded but non-fatal |
| IND-027 | **NOT_IMPLEMENTED** | grep for `oe_indicator_snapshots` in `main.py` → no matches; no API route exposes per-indicator snapshot data to dashboard |
| IND-028 | **NOT_IMPLEMENTED** | Cannot reconcile API vs stored evidence without an indicator-serving API endpoint (IND-027 finding); `mkt_layer9_score` tool is AIEM-internal only |
| IND-029 | **FAIL** | POLY_CLOSE_PRICE: stored vs `polygon_market_daily.close_price` — all 5 rows fail <0.01 threshold (e.g., ADSK 2026-07-20: stored=218.35 polygon_EOD=217.80; MESO: stored=17.32 polygon_EOD=16.63); POLY_CLOSE_STRENGTH: all 3 rows fail <0.001 threshold. Root cause: options pipeline captures prices intraday (at pipeline runtime), `polygon_market_daily` stores EOD close; the two are legitimately different data points, not the same measurement |
| IND-030 | **PARTIAL** | NC-1: 236 STALE rows classified (not defaulted to FRESH); NC-2: 641 MISSING rows with raw_value=NULL (not zero-filled); NC-3: deliberate test row `VERIFY_FRESHNESS_TEST_PHASE3P1_001` with freshness_seconds=999999 present and classified STALE; NC-4: gate code confirmed active at lines 1561/1578/1579; gate is non-fatal so hard-reject never fires |

---

## Summary Totals

| Verdict | Count | Items |
|---|---|---|
| PASS | 6 | IND-002, IND-003, IND-009, IND-011, IND-018, IND-024 |
| FAIL | 9 | IND-006, IND-007, IND-008, IND-014, IND-015, IND-017, IND-020, IND-021, IND-029 |
| PARTIAL | 12 | IND-001, IND-004, IND-005, IND-010, IND-012, IND-013, IND-016, IND-019, IND-023, IND-025, IND-026, IND-030 |
| NOT_IMPLEMENTED | 3 | IND-022, IND-027, IND-028 |
| **Total** | **30** | |

---

## Key Findings

**1. Registry exists but covers only one indicator family (options engine).** The `oe_indicator_registry` with 79 rows is a real, populated, deduplicated registry — but it exclusively covers the options pipeline. The polygon technical indicators (19 production columns), layer9 statistical indicators (14 fields), and conviction stack (~39 indicators) have no registry entry of any kind.

**2. Metadata columns are structurally empty.** `parameters`, `timeframe`, `description`, `required_inputs`, `output_fields`, and `asset_type` are either absent from the schema or present but always null/empty (`{}` for all 79 parameters rows, NULL for all 79 timeframe rows). These were never populated by `register_indicator()` callers.

**3. Scoring contribution fields are designed but never written.** `contribution_score`, `weight`, and `supported_decision` in `oe_indicator_snapshots` are all NULL for all 2,529 rows. `oe_indicator_attribution` has 0 rows despite a complete schema with lift, IC, Brier score delta, and significance columns. The attribution and scoring-contribution subsystems are implemented at the schema level but never activated.

**4. Degradation classification works; hard gating does not.** STALE (236 rows) and MISSING (641 rows) are correctly classified and never silently substituted with neutral values (IND-024 PASS). The `REGISTRY_MISSING_INDICATOR` / `REGISTRY_STALE_DATA` gate strings fire correctly. However, the registry block is explicitly non-fatal ("never block pipeline"), so IND-025/026 are PARTIAL rather than PASS.

**5. IND-029 independent recomputation fails due to intraday vs EOD data mismatch.** `POLY_CLOSE_PRICE` in `oe_indicator_snapshots` is the price captured during pipeline execution (market hours); `polygon_market_daily.close_price` is the EOD close. These are different measurements of the same underlying stock — the mismatch is structural, not a bug. However, per the spec, independent recomputation that does not match is a FAIL.

**6. PHASE 9 GATES PHASE 10.** All 30 items have verdicts. Phase 10 may begin.

---

## Sealed Evidence File

```
artifacts/stock-scanner-api/tools/logs/verified_run_100.log
  archive_sha256: 6a6943d9e3bac1c746a6d7fd1cda5973f96c98c498890ac192ec9096b33f96cc
```

Verifier: `artifacts/stock-scanner-api/verify_phase9_ind.py`
sha256: `d702812f8ccc02b4159c10ab5377978a66306065823d4b28e8f033ed7031bd4b`
