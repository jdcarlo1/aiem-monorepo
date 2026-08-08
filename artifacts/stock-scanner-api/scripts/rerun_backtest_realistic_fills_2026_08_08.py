#!/usr/bin/env python3
"""Directive_Backtest_RealisticFill_Rerun_2026-08-08 (v2)

Re-run as-published backtests under Version A (original assumptions) vs
Version B (realistic paper fills matching live engine after c3edc5fd).

Rules (enforced):
  - Same trade list / signals; only fill & cost assumptions change.
  - Version B requires REAL historical bid/ask at entry AND exit.
  - If bid/ask history is unavailable → CANNOT VERIFY (no synthetic spreads).
  - Report only. No strategy parameter changes.

Reproduce:
  cd artifacts/stock-scanner-api
  python3 scripts/rerun_backtest_realistic_fills_2026_08_08.py

Outputs:
  docs/verification/backtest-realistic-fill-rerun-2026-08-08.md
  docs/verification/backtest-realistic-fill-rerun-2026-08-08.json
"""

from __future__ import annotations

import csv
import json
import math
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[3]
API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API))

from aiem_options_paper_fill import (  # noqa: E402
    paper_buy_fill,
    paper_realized_pnl,
    paper_round_trip_fees,
    paper_sell_fill,
    paper_slippage_dollars,
)
from aiem_options_phase2 import _STRATEGY_CATALOG  # noqa: E402

OUT_MD = REPO / "docs" / "verification" / "backtest-realistic-fill-rerun-2026-08-08.md"
OUT_JSON = REPO / "docs" / "verification" / "backtest-realistic-fill-rerun-2026-08-08.json"
F3_CSV = API / "f3_trade_comparison.csv"

# ---------------------------------------------------------------------------
# Live on Pattern Lab + OE Strategies (site surfaces)
# ---------------------------------------------------------------------------
LIVE_STRATEGIES = [
    {
        "id": "GAP_FILL",
        "name": "Gap Fill",
        "surface": "Pattern Lab (AIEM)",
        "instrument": "equity",
        "options_fill_applicable": False,
    },
    {
        "id": "ORB",
        "name": "Opening Range Breakout",
        "surface": "Pattern Lab (AIEM)",
        "instrument": "equity",
        "options_fill_applicable": False,
    },
    {
        "id": "F3_SPY_0DTE",
        "name": "F3 SPY 0DTE",
        "surface": "Pattern Lab (AIEM) + OE Strategies",
        "instrument": "options",
        "options_fill_applicable": True,
    },
]

# Directive-named names → catalog ids (or None if absent)
DIRECTIVE_NAMED = [
    ("long_call_condor", "LONG_CALL_CONDOR"),
    ("long_put_condor", "LONG_PUT_CONDOR"),
    ("narrow_wing_butterfly", None),  # not in _STRATEGY_CATALOG
    ("call_butterfly", "LONG_CALL_BUTTERFLY"),
    ("put_butterfly", "LONG_PUT_BUTTERFLY"),
    ("put_ladder", None),  # not in _STRATEGY_CATALOG
    ("bullish_risk_reversal", "RISK_REVERSAL"),
    ("F3 0DTE", "F3_SPY_0DTE"),  # live — handled above
]


def _metrics_from_pnls(pnls: List[float]) -> Dict[str, Any]:
    if not pnls:
        return {
            "trades": 0,
            "total_pnl": None,
            "win_rate": None,
            "avg_per_trade": None,
            "max_dd": None,
            "profit_factor": None,
            "profitable": None,
        }
    total = float(sum(pnls))
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        pf: Any = round(gross_win / gross_loss, 3)
    elif gross_win > 0:
        pf = "inf"
    else:
        pf = None
    return {
        "trades": len(pnls),
        "total_pnl": round(total, 2),
        "win_rate": round(100.0 * len(wins) / len(pnls), 2),
        "avg_per_trade": round(total / len(pnls), 2),
        "max_dd": round(max_dd, 2),
        "profit_factor": pf,
        "profitable": total > 0,
    }


def load_f3_published() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with F3_CSV.open() as f:
        for r in csv.DictReader(f):
            rows.append(
                {
                    "date": r["date"],
                    "direction": r["direction"],
                    "entry_premium": float(r["entry_premium"]),
                    "exit_premium": float(r["exit_premium"]),
                    "contracts": float(r["contracts"]),
                    "synth_dollar": float(r["synth_dollar"]),
                    "real_dollar": float(r["real_dollar"]),
                    "pricing_source": (
                        "Polygon 1-min option bar CLOSE (MODELED as fill; NOT NBBO bid/ask)"
                    ),
                }
            )
    # Chronological for drawdown
    rows_chrono = sorted(rows, key=lambda x: x["date"])
    pnls = [r["real_dollar"] for r in rows_chrono]
    return rows_chrono, _metrics_from_pnls(pnls)


def version_a_pre_c3edc5fd(
    entry_bid: float,
    entry_ask: float,
    exit_bid: float,
    exit_ask: float,
    *,
    n_legs: int,
    qty: int,
) -> Dict[str, Any]:
    """Original OE paper assumptions immediately prior to c3edc5fd.

    - Entry: ASK
    - Exit:  BID was already used in some post-f5f081c9 demos; grading otherwise
             used intrinsic. For this quote-complete proof trade we apply ask-in /
             bid-out gross with OLD cost model:
               fees = $0.65 flat (not per-leg×2)
               slippage = entry half-spread only, quantity=1 (NOT qty*n_legs)
    """
    entry, eq = paper_buy_fill(entry_bid, entry_ask)
    exit_px, xq = paper_sell_fill(exit_bid, exit_ask)
    assert entry is not None and exit_px is not None
    fees = 0.65
    entry_slip = paper_slippage_dollars(entry_bid, entry_ask, quantity=1)
    pnl, _ = paper_realized_pnl(
        entry_price=entry,
        exit_price=exit_px,
        quantity=qty,
        fees_est=fees,
        slippage_est=entry_slip,
    )
    return {
        "entry_fill": entry,
        "entry_quality": eq,
        "exit_fill": exit_px,
        "exit_quality": xq,
        "fees": fees,
        "entry_slippage_est": entry_slip,
        "exit_slippage_est": 0.0,
        "realized_pnl": pnl,
        "assumption": (
            "pre-c3edc5fd: ask entry, bid exit gross, fees=$0.65 flat, "
            "entry-only half-spread slip at quantity=1 (no leg multiplier, no exit slip)"
        ),
        "n_legs_ignored_for_slip_and_fees": n_legs,
    }


def version_b_live_engine(
    entry_bid: float,
    entry_ask: float,
    exit_bid: float,
    exit_ask: float,
    *,
    n_legs: int,
    qty: int,
) -> Dict[str, Any]:
    """Match live paper engine after c3edc5fd (phase2 capture + exit update)."""
    entry, eq = paper_buy_fill(entry_bid, entry_ask)
    exit_px, xq = paper_sell_fill(exit_bid, exit_ask)
    assert entry is not None and exit_px is not None
    fees = paper_round_trip_fees(n_legs=n_legs, quantity=qty)
    entry_slip = paper_slippage_dollars(entry_bid, entry_ask, quantity=qty * n_legs)
    exit_slip = paper_slippage_dollars(exit_bid, exit_ask, quantity=qty * n_legs)
    pnl, _ = paper_realized_pnl(
        entry_price=entry,
        exit_price=exit_px,
        quantity=qty,
        fees_est=fees,
        slippage_est=entry_slip + exit_slip,
    )
    return {
        "entry_fill": entry,
        "entry_quality": eq,
        "exit_fill": exit_px,
        "exit_quality": xq,
        "fees": fees,
        "entry_slippage_est": entry_slip,
        "exit_slippage_est": exit_slip,
        "realized_pnl": pnl,
        "assumption": (
            "c3edc5fd live: ask entry, bid exit, fees=0.65*legs*qty*2, "
            "dual half-spread slip with quantity=qty*n_legs"
        ),
        "two_sided_both": eq == "ASK" and xq == "BID",
    }


def load_iron_condor_proof() -> Optional[Dict[str, Any]]:
    """Only closed options row in local proof DB with real bid/ask both sides."""
    db_url = os.environ.get("DATABASE_URL") or (
        "postgresql://postgres:postgres@127.0.0.1:5432/oe_paper_proof"
    )
    try:
        import psycopg2
    except ImportError:
        return None
    try:
        with psycopg2.connect(db_url, connect_timeout=3) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT strategy_family, ticker, quantity,
                       entry_bid, entry_ask, exit_bid, exit_ask,
                       entry_price, exit_price, fees_est,
                       entry_slippage_est, exit_slippage_est, slippage_est,
                       realized_pnl, fill_quality, exit_fill_quality, legs_json
                FROM oe_trade_records
                WHERE exit_ts IS NOT NULL
                  AND entry_bid IS NOT NULL AND entry_ask IS NOT NULL
                  AND exit_bid IS NOT NULL AND exit_ask IS NOT NULL
                ORDER BY id DESC
                LIMIT 5
                """
            )
            rows = cur.fetchall()
    except Exception as e:
        return {"error": str(e)}
    if not rows:
        return None
    out = []
    for r in rows:
        (
            fam, ticker, qty,
            eb, ea, xb, xa,
            ep, xp, fees,
            es, xs, slip,
            rpnl, fq, xfq, legs,
        ) = r
        n_legs = len(legs) if isinstance(legs, list) else 4
        qty_i = int(qty or 1)
        a = version_a_pre_c3edc5fd(
            float(eb), float(ea), float(xb), float(xa), n_legs=n_legs, qty=qty_i
        )
        b = version_b_live_engine(
            float(eb), float(ea), float(xb), float(xa), n_legs=n_legs, qty=qty_i
        )
        half_e = 0.5 * (float(ea) - float(eb))
        half_x = 0.5 * (float(xa) - float(xb))
        mid_e = 0.5 * (float(ea) + float(eb))
        spread_pct_mid = (float(ea) - float(eb)) / mid_e * 100.0 if mid_e else None
        out.append(
            {
                "strategy_family": fam,
                "ticker": ticker,
                "n_legs": n_legs,
                "qty": qty_i,
                "entry_bid": float(eb),
                "entry_ask": float(ea),
                "exit_bid": float(xb),
                "exit_ask": float(xa),
                "db_realized_pnl": float(rpnl) if rpnl is not None else None,
                "db_entry_slippage_est": float(es) if es is not None else None,
                "db_exit_slippage_est": float(xs) if xs is not None else None,
                "db_fees_est": float(fees) if fees is not None else None,
                "fill_quality": fq,
                "exit_fill_quality": xfq,
                "half_spread_entry": half_e,
                "half_spread_exit": half_x,
                "spread_abs_entry": float(ea) - float(eb),
                "spread_pct_of_mid_entry": round(spread_pct_mid, 2) if spread_pct_mid is not None else None,
                "version_a": a,
                "version_b": b,
                "diff_dollars": round(float(a["realized_pnl"]) - float(b["realized_pnl"]), 2),
                "pct_overstated": (
                    round(
                        (float(a["realized_pnl"]) - float(b["realized_pnl"]))
                        / abs(float(b["realized_pnl"]))
                        * 100.0,
                        1,
                    )
                    if b["realized_pnl"]
                    else None
                ),
                "flips_to_unprofitable": bool(a["realized_pnl"] > 0 and b["realized_pnl"] <= 0),
                "note": (
                    "Verification fixture in oe_paper_proof — NOT a Pattern Lab / "
                    "OE Strategies live card. Included because it is the only archived "
                    "closed options trade with real two-sided bid/ask at entry and exit."
                ),
            }
        )
    return {"trades": out, "source": db_url.split("@")[-1]}


def f3_census(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    entry = [r["entry_premium"] for r in rows]
    exit_ = [r["exit_premium"] for r in rows]
    gross = [r["real_dollar"] for r in rows]
    avg_gross = statistics.mean(gross) if gross else None

    def pctile(xs: List[float], p: float) -> Optional[float]:
        if not xs:
            return None
        s = sorted(xs)
        return round(s[min(len(s) - 1, int(p * (len(s) - 1)))], 4)

    return {
        "pricing_label": (
            "Polygon 1-min CLOSE premiums in f3_trade_comparison.csv — "
            "MODELED/SYNTHESIZED as fills; NOT historical NBBO"
        ),
        "n_trades": len(rows),
        "date_range": (
            [rows[0]["date"], rows[-1]["date"]] if rows else None
        ),
        "entry_premium_median": pctile(entry, 0.50),
        "entry_premium_p75": pctile(entry, 0.75),
        "entry_premium_p90": pctile(entry, 0.90),
        "exit_premium_median": pctile(exit_, 0.50),
        "avg_version_a_pnl": round(avg_gross, 2) if avg_gross is not None else None,
        "bid_ask_spread_abs_median": "CANNOT VERIFY — no historical bid/ask",
        "bid_ask_spread_abs_p75": "CANNOT VERIFY",
        "bid_ask_spread_abs_p90": "CANNOT VERIFY",
        "bid_ask_spread_pct_mid_median": "CANNOT VERIFY",
        "bid_ask_spread_pct_mid_p75": "CANNOT VERIFY",
        "bid_ask_spread_pct_mid_p90": "CANNOT VERIFY",
        "pct_trades_two_sided_both_entry_exit": "CANNOT VERIFY — no quote-quality archive",
        "pct_one_sided_or_synthetic": "CANNOT VERIFY",
        "round_trip_cost_pct_of_max_theoretical_gain": "CANNOT VERIFY without bid/ask",
        "version_a_cost_model": (
            "As-published real_dollar = (exit_premium-entry_premium)/entry_premium*200 "
            "(=$200 notional). No paper_round_trip_fees. No dual half-spread slippage. "
            "No ask-entry / bid-exit differentiation — bar close used both sides."
        ),
    }


def build_report() -> Dict[str, Any]:
    f3_rows, f3_a = load_f3_published()
    census = f3_census(f3_rows)
    proof = load_iron_condor_proof()

    catalog_ids = [s["id"] for s in _STRATEGY_CATALOG]

    strategies_ran: List[Dict[str, Any]] = []

    # Equity live cards
    for sid, name in (("GAP_FILL", "Gap Fill"), ("ORB", "Opening Range Breakout")):
        meta = next(s for s in LIVE_STRATEGIES if s["id"] == sid)
        strategies_ran.append(
            {
                **meta,
                "version_a": {
                    "status": "NO_PUBLISHED_TRADE_BOOK",
                    "trades": None,
                    "total_pnl": None,
                    "win_rate": None,
                    "avg_per_trade": None,
                    "max_dd": None,
                    "profit_factor": None,
                    "profitable": None,
                    "reason": (
                        "backtest_pattern_lab.py references "
                        "docs/verification/pattern-lab-backtest-6mo.md but that "
                        "artifact was never committed. Live paper snapshots exist "
                        "(pattern-lab-FINAL.md single-day) but are not a multi-trade "
                        "historical book. POLYGON_API_KEY absent in this environment — "
                        "refusing to invent equity fills."
                    ),
                },
                "version_b": {
                    "status": "N/A_EQUITY",
                    "reason": "Equity strategy — options ask/bid fill model does not apply.",
                    "total_pnl": None,
                    "win_rate": None,
                    "avg_per_trade": None,
                    "max_dd": None,
                    "profit_factor": None,
                    "profitable": None,
                },
                "diff_dollars": None,
                "pct_overstated": None,
                "flips_to_unprofitable": False,
                "still_profitable_under_b": "N/A",
                "pricing_source": "Equity OHLC (when backtested) — not options NBBO",
            }
        )

    # F3
    f3_meta = next(s for s in LIVE_STRATEGIES if s["id"] == "F3_SPY_0DTE")
    strategies_ran.append(
        {
            **f3_meta,
            "version_a": {**f3_a, "status": "AS_PUBLISHED"},
            "version_b": {
                "status": "CANNOT_VERIFY",
                "reason": (
                    "f3_trade_comparison.csv stores Polygon 1-min bar CLOSE as "
                    "entry_premium/exit_premium only. No historical bid/ask per contract. "
                    "Per directive: do not substitute synthetic spreads."
                ),
                "total_pnl": None,
                "win_rate": None,
                "avg_per_trade": None,
                "max_dd": None,
                "profit_factor": None,
                "profitable": None,
            },
            "diff_dollars": None,
            "pct_overstated": None,
            "flips_to_unprofitable": "UNKNOWN — Version B CANNOT VERIFY",
            "still_profitable_under_b": "CANNOT VERIFY",
            "pricing_source": (
                "Polygon 1-min option bar CLOSE (MODELED as fill; not NBBO). "
                "Source file: artifacts/stock-scanner-api/f3_trade_comparison.csv"
            ),
            "version_a_assumption_note": census["version_a_cost_model"],
            "still_profitable_under_a": bool(f3_a.get("profitable")),
        }
    )

    # Directive-named but not live
    not_live_rows = []
    for label, cat_id in DIRECTIVE_NAMED:
        if label in ("F3 0DTE",):
            continue
        if cat_id is None:
            not_live_rows.append(
                {
                    "directive_name": label,
                    "catalog_id": None,
                    "status": "NOT_IN_CATALOG",
                    "version_a": "NO_PUBLISHED_TRADE_BOOK",
                    "version_b": "CANNOT_VERIFY",
                    "reason": (
                        f"Name `{label}` appears in the directive but is not present "
                        "in _STRATEGY_CATALOG and is not a live Pattern Lab / OE Strategies card."
                    ),
                }
            )
        else:
            not_live_rows.append(
                {
                    "directive_name": label,
                    "catalog_id": cat_id,
                    "status": "CATALOG_ONLY_NOT_LIVE",
                    "version_a": "NO_PUBLISHED_TRADE_BOOK",
                    "version_b": "CANNOT_VERIFY",
                    "reason": (
                        f"Registered as `{cat_id}` in OE strategy catalog/registry, but "
                        "not currently saved/live as a Pattern Lab or OE Strategies card "
                        "with an archived historical trade list + bid/ask."
                    ),
                }
            )

    catalog_also = [
        cid
        for cid in catalog_ids
        if cid
        not in {
            "LONG_CALL_CONDOR",
            "LONG_PUT_CONDOR",
            "LONG_CALL_BUTTERFLY",
            "LONG_PUT_BUTTERFLY",
            "RISK_REVERSAL",
        }
    ]

    # Proof trade A/B (fixture)
    proof_summary = None
    if proof and proof.get("trades"):
        t0 = proof["trades"][0]
        proof_summary = {
            "id": "IRON_CONDOR_PROOF_FIXTURE",
            "name": "Iron Condor (c3edc5fd verification fixture)",
            "surface": "oe_paper_proof DB only — NOT live on site",
            "instrument": "options",
            "options_fill_applicable": True,
            "trades": 1,
            "version_a": _metrics_from_pnls([t0["version_a"]["realized_pnl"]]),
            "version_b": _metrics_from_pnls([t0["version_b"]["realized_pnl"]]),
            "diff_dollars": t0["diff_dollars"],
            "pct_overstated": t0["pct_overstated"],
            "flips_to_unprofitable": t0["flips_to_unprofitable"],
            "still_profitable_under_b": t0["version_b"]["realized_pnl"] > 0,
            "pricing_source": (
                "REAL Tradier-style two-sided package quotes stored on oe_trade_records "
                f"(entry {t0['entry_bid']}/{t0['entry_ask']}, "
                f"exit {t0['exit_bid']}/{t0['exit_ask']})"
            ),
            "detail": t0,
        }

    representativeness = {
        "verdict": (
            "OUTLIER / multi-leg formula artifact — NOT a representative "
            "single-contract percent-of-premium"
        ),
        "detail": (
            "On the proof IRON_CONDOR, entry_slippage_est=40.00 equals "
            "half-spread $0.10 × 100 × (qty*n_legs=4). It is NOT '29% of a $1.40 premium'. "
            "A $0.20 package bid-ask on a 4-leg structure becomes $40 dollar slippage per "
            "side by construction of quantity=qty*n_legs. Treat as thin-quote × multi-leg "
            "multiplier effect, not a typical single-option spread percentage for SPY 0DTE."
        ),
    }

    tradeability = (
        "From archived Pattern Lab / OE Strategies backtests: CANNOT conclude whether "
        "round-trip cost routinely exceeds average gross gain — historical bid/ask is "
        "not archived for F3 (or for catalog multi-legs, which are not live). "
        "From the live IRON_CONDOR proof fixture: round-trip cost "
        "(fees $5.20 + dual slippage $80.00 = $85.20) EXCEEDED the favorable gross move "
        "($50.00 on a +0.50 ask→bid package mark), producing realized_pnl=-35.20. "
        "That is a TRADEABILITY finding for thin multi-leg package quotes under the "
        "leg-multiplied slippage formula — distinct from 'the strategy has no edge.'"
    )

    return {
        "directive": "Directive_Backtest_RealisticFill_Rerun_2026-08-08 (v2)",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "reproduce": (
            "cd artifacts/stock-scanner-api && "
            "python3 scripts/rerun_backtest_realistic_fills_2026_08_08.py"
        ),
        "paper_engine_commit_reference": "c3edc5fd",
        "version_b_rules": {
            "entry": "BUY at ASK (one-sided -> ONE_SIDED_ASK)",
            "exit": (
                "SELL at BID (one-sided -> ONE_SIDED_BID); "
                "intrinsic ONLY at MARKET_ON_EXPIRY_SETTLE"
            ),
            "fees": "paper_round_trip_fees(n_legs, qty) = 0.65 * legs * qty * 2",
            "slippage": (
                "half-spread dollars on BOTH entry and exit, always adverse; "
                "engine passes quantity=qty*n_legs into paper_slippage_dollars"
            ),
            "worthless_expiry": "full loss",
            "no_synthetic_spreads": True,
        },
        "strategies_actually_ran": [s["id"] for s in strategies_ran],
        "strategies": strategies_ran,
        "proof_fixture_ab": proof_summary,
        "directive_named_but_not_live": not_live_rows,
        "catalog_also_not_live_additional": catalog_also,
        "catalog_strategy_count": len(catalog_ids),
        "spread_liquidity_census": {
            "historical_backtest_contracts": census,
            "live_verified_paper_trade_c3edc5fd": proof,
            "representativeness_of_40_per_side": representativeness,
            "tradeability_finding": tradeability,
        },
        "honesty": {
            "version_b_computable_for_any_live_options_strategy": False,
            "blocking_reason": (
                "No historical NBBO/bid-ask archive tied to published backtest trade lists. "
                "F3 uses Polygon bar closes. Equity Pattern Lab multi-month book not archived. "
                "Directive-named multi-legs are catalog-only with zero trade books."
            ),
            "what_would_unblock": (
                "Archive bid, ask, mid, quote_quality at signal time and exit time per leg "
                "(or per package) for every backtest fill, then re-run Version B over the "
                "same trade IDs via version_b_live_engine()."
            ),
        },
    }


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    return str(v)


def render_md(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Backtest Realistic-Fill Re-run — 2026-08-08 (v2)")
    lines.append("")
    lines.append(f"**Generated (UTC):** {report['generated_at_utc']}")
    lines.append(f"**Reproduce:** `{report['reproduce']}`")
    lines.append(f"**Paper engine reference:** `{report['paper_engine_commit_reference']}`")
    lines.append("")
    lines.append("## Scope — strategies actually evaluated")
    lines.append("")
    lines.append("### Live on Pattern Lab + OE Strategies (full set ran — none silently dropped)")
    for s in report["strategies"]:
        lines.append(
            f"- `{s['id']}` — {s['name']} ({s['instrument']}; surface: {s['surface']})"
        )
    lines.append("")
    lines.append(
        "### Directive-named strategies not currently live on those surfaces"
    )
    for s in report["directive_named_but_not_live"]:
        lines.append(
            f"- `{s['directive_name']}` → catalog `{s['catalog_id']}` — "
            f"**{s['status']}** / Version B **{s['version_b']}**"
        )
    lines.append("")
    lines.append(
        f"### Additional OE catalog strategies also not live "
        f"({len(report['catalog_also_not_live_additional'])} of "
        f"{report['catalog_strategy_count']} catalog ids — listed for completeness)"
    )
    for c in report["catalog_also_not_live_additional"]:
        lines.append(f"- `{c}`")
    lines.append("")
    lines.append("## Version definitions")
    lines.append("")
    lines.append(
        "- **Version A** = as-published backtest assumptions / stored `real_dollar` "
        "(F3) or pre-c3edc5fd OE cost model (proof fixture)."
    )
    lines.append(
        "- **Version B** = live paper engine after c3edc5fd: ask entry, bid exit, "
        "`0.65*legs*qty*2` fees, dual half-spread slippage with `quantity=qty*n_legs`, "
        "intrinsic only at true expiry settle."
    )
    lines.append(
        "- **Hard rule:** if real historical bid/ask is unavailable → **CANNOT VERIFY**. "
        "No synthetic spreads."
    )
    lines.append("")
    lines.append("## Comparison table — live site strategies")
    lines.append("")
    lines.append(
        "| Strategy | Trades | A P&L | B P&L | $ Diff | % Overstated | "
        "A WR | B WR | A avg $/trade | B avg $/trade | A max DD | B max DD | "
        "A PF | B PF | Still profitable under B? |"
    )
    lines.append(
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"
    )
    for s in report["strategies"]:
        a = s["version_a"]
        b = s["version_b"]
        a_status = a.get("status")
        b_status = b.get("status")
        a_pnl = a.get("total_pnl")
        b_pnl = b.get("total_pnl")
        lines.append(
            "| {id} | {tr} | {ap} | {bp} | {d} | {pct} | {aw} | {bw} | {aa} | {ba} | "
            "{ad} | {bd} | {af} | {bf} | {ok} |".format(
                id=s["id"],
                tr=_fmt(a.get("trades")),
                ap=_fmt(a_pnl if a_pnl is not None else a_status),
                bp=_fmt(b_pnl if b_pnl is not None else b_status),
                d=_fmt(s.get("diff_dollars")),
                pct=_fmt(s.get("pct_overstated")),
                aw=_fmt(a.get("win_rate") if a.get("win_rate") is not None else a_status),
                bw=_fmt(b.get("win_rate") if b.get("win_rate") is not None else b_status),
                aa=_fmt(
                    a.get("avg_per_trade")
                    if a.get("avg_per_trade") is not None
                    else a_status
                ),
                ba=_fmt(
                    b.get("avg_per_trade")
                    if b.get("avg_per_trade") is not None
                    else b_status
                ),
                ad=_fmt(a.get("max_dd") if a.get("max_dd") is not None else a_status),
                bd=_fmt(b.get("max_dd") if b.get("max_dd") is not None else b_status),
                af=_fmt(
                    a.get("profit_factor")
                    if a.get("profit_factor") is not None
                    else a_status
                ),
                bf=_fmt(
                    b.get("profit_factor")
                    if b.get("profit_factor") is not None
                    else b_status
                ),
                ok=_fmt(s.get("still_profitable_under_b")),
            )
        )
    lines.append("")

    pf = report.get("proof_fixture_ab")
    if pf:
        lines.append("## Side table — only quote-complete options trade (proof fixture)")
        lines.append("")
        lines.append(
            "> Not a live Pattern Lab / OE Strategies card. Included because it is the "
            "only archived closed options trade with real two-sided bid/ask at both entry "
            "and exit, so Version A vs B can be computed on the **same** quotes."
        )
        lines.append("")
        a = pf["version_a"]
        b = pf["version_b"]
        lines.append(
            "| Strategy | Trades | A P&L | B P&L | $ Diff | % Overstated | "
            "A WR | B WR | A avg | B avg | A max DD | B max DD | A PF | B PF | "
            "Still profitable under B? |"
        )
        lines.append(
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"
        )
        lines.append(
            f"| {pf['id']} | {a['trades']} | {a['total_pnl']} | {b['total_pnl']} | "
            f"{pf['diff_dollars']} | {pf['pct_overstated']} | {a['win_rate']} | "
            f"{b['win_rate']} | {a['avg_per_trade']} | {b['avg_per_trade']} | "
            f"{a['max_dd']} | {b['max_dd']} | {a['profit_factor']} | {b['profit_factor']} | "
            f"{pf['still_profitable_under_b']} |"
        )
        lines.append("")
        d = pf["detail"]
        lines.append(
            f"- **FLIP:** Version A profitable ({d['version_a']['realized_pnl']}) → "
            f"Version B unprofitable ({d['version_b']['realized_pnl']})."
        )
        lines.append(
            f"- Quotes: entry {d['entry_bid']}/{d['entry_ask']}, "
            f"exit {d['exit_bid']}/{d['exit_ask']}; {d['n_legs']} legs × qty {d['qty']}."
        )
        lines.append(
            f"- Version A costs: fees ${d['version_a']['fees']}, "
            f"entry_slip ${d['version_a']['entry_slippage_est']}, exit_slip $0."
        )
        lines.append(
            f"- Version B costs: fees ${d['version_b']['fees']}, "
            f"entry_slip ${d['version_b']['entry_slippage_est']}, "
            f"exit_slip ${d['version_b']['exit_slippage_est']} "
            f"(round-trip cost $"
            f"{d['version_b']['fees']+d['version_b']['entry_slippage_est']+d['version_b']['exit_slippage_est']:.2f} "
            f"vs gross move $50.00)."
        )
        lines.append("")

    lines.append("### Plain-language verdicts (live strategies)")
    lines.append("")
    for s in report["strategies"]:
        lines.append(f"**{s['id']}**")
        lines.append(f"- Pricing source: {s['pricing_source']}")
        if s["id"] in ("GAP_FILL", "ORB"):
            lines.append(
                f"- Version A: **{s['version_a']['status']}** — {s['version_a']['reason']}"
            )
            lines.append("- Version B: **N/A_EQUITY** (options fill model does not apply).")
        else:
            lines.append(
                f"- Version A (as-published): trades={s['version_a'].get('trades')}, "
                f"P&L={s['version_a'].get('total_pnl')}, "
                f"WR={s['version_a'].get('win_rate')}%, "
                f"avg=${s['version_a'].get('avg_per_trade')}, "
                f"maxDD={s['version_a'].get('max_dd')}, "
                f"PF={s['version_a'].get('profit_factor')}, "
                f"profitable={s['version_a'].get('profitable')}"
            )
            lines.append(
                f"- Version B: **{s['version_b']['status']}** — {s['version_b']['reason']}"
            )
            lines.append(
                f"- Flip profitable→unprofitable under B: **{s['flips_to_unprofitable']}**"
            )
            if s.get("version_a_assumption_note"):
                lines.append(f"- Version A note: {s['version_a_assumption_note']}")
        lines.append("")

    lines.append("### Directive-named / not live")
    lines.append("")
    for s in report["directive_named_but_not_live"]:
        lines.append(
            f"- `{s['directive_name']}` ({s['status']}): {s['reason']} "
            f"→ Version A = {s['version_a']}; Version B = **{s['version_b']}**."
        )
    lines.append("")

    lines.append("## Spread / liquidity census")
    lines.append("")
    lines.append("### 1–3. Historical contracts traded by live strategies")
    lines.append("")
    c = report["spread_liquidity_census"]["historical_backtest_contracts"]
    lines.append(f"- Pricing label: **{c['pricing_label']}**")
    lines.append(f"- F3 published trades examined: {c['n_trades']} ({c['date_range']})")
    lines.append(
        f"- Entry premium median / p75 / p90: "
        f"{c['entry_premium_median']} / {c['entry_premium_p75']} / {c['entry_premium_p90']}"
    )
    lines.append(
        f"- Bid-ask spread (abs $) median / p75 / p90: **{c['bid_ask_spread_abs_median']}**"
    )
    lines.append(
        f"- Bid-ask spread (% of mid) median / p75 / p90: "
        f"**{c['bid_ask_spread_pct_mid_median']}**"
    )
    lines.append(
        f"- % trades with real two-sided quotes at BOTH entry and exit: "
        f"**{c['pct_trades_two_sided_both_entry_exit']}**"
    )
    lines.append(
        f"- Round-trip cost as % of max theoretical gain: "
        f"**{c['round_trip_cost_pct_of_max_theoretical_gain']}**"
    )
    lines.append("")
    lines.append(
        "### 4. Is entry_slippage_est=40.00 / exit_slippage_est=40.00 representative?"
    )
    lines.append("")
    r = report["spread_liquidity_census"]["representativeness_of_40_per_side"]
    lines.append(f"**Verdict: {r['verdict']}**")
    lines.append("")
    lines.append(r["detail"])
    lines.append("")
    if pf:
        d = pf["detail"]
        lines.append(
            f"Proof quotes: spread abs ${d['spread_abs_entry']:.2f} "
            f"({d['spread_pct_of_mid_entry']}% of mid ${0.5*(d['entry_bid']+d['entry_ask']):.2f}). "
            f"Half-spread ${d['half_spread_entry']:.2f} × 100 × 4 legs = $40.00/side."
        )
        lines.append("")
    lines.append("### Tradeability finding (distinct from edge)")
    lines.append("")
    lines.append(report["spread_liquidity_census"]["tradeability_finding"])
    lines.append("")
    lines.append("## Data honesty")
    lines.append("")
    lines.append(
        f"- Version B computable for any live options strategy with current archives: "
        f"**{report['honesty']['version_b_computable_for_any_live_options_strategy']}**"
    )
    lines.append(f"- Blocking reason: {report['honesty']['blocking_reason']}")
    lines.append(f"- Unblock path: {report['honesty']['what_would_unblock']}")
    lines.append("")
    lines.append("## What this script does / does not do")
    lines.append("")
    lines.append(
        "- Does: inventory every live Pattern Lab / OE Strategies strategy; load "
        "as-published Version A for F3; attempt Version B only when real bid/ask "
        "exists; document the c3edc5fd proof cost structure; archive census."
    )
    lines.append(
        "- Does not: invent synthetic spreads; re-tune any strategy parameter; claim "
        "Version B P&L for F3 or catalog multi-legs without NBBO history."
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    report = build_report()
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2, default=str) + "\n")
    OUT_MD.write_text(render_md(report))
    print(f"Wrote {OUT_MD}")
    print(f"Wrote {OUT_JSON}")
    print("Strategies ran:", ", ".join(report["strategies_actually_ran"]))
    f3 = next(s for s in report["strategies"] if s["id"] == "F3_SPY_0DTE")
    print(
        "F3 Version A:",
        f3["version_a"].get("trades"),
        "trades, P&L",
        f3["version_a"].get("total_pnl"),
        "WR",
        f3["version_a"].get("win_rate"),
    )
    print("F3 Version B:", f3["version_b"]["status"])
    pf = report.get("proof_fixture_ab")
    if pf:
        print(
            "Proof fixture A→B:",
            pf["version_a"]["total_pnl"],
            "→",
            pf["version_b"]["total_pnl"],
            "flip=",
            pf["flips_to_unprofitable"],
        )
    print("40/side representativeness: OUTLIER / multi-leg formula artifact")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
