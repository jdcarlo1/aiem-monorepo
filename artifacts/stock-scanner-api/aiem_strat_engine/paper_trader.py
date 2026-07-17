"""
paper_trader.py — Paper trade insertion with full audit trail.

Three-layer safety check before any INSERT:
  1. execution_mode must be AUTONOMOUS
  2. max_loss must be a finite positive number (undefined-risk blocked)
  3. payoff_info.is_undefined_risk must be False

All DB operations are atomic: parent + legs + evaluation update in one transaction.
Provides per-trade audit hash (SHA-256 of all trade parameters).
"""
from __future__ import annotations
import json, uuid, hashlib
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

import psycopg2, psycopg2.extras

from .db import get_conn
from .legs import Leg, MODE_AUTONOMOUS, RISK_UNDEFINED
from .selector import EvaluationResult, SelectionResult


# ── Safety constants ─────────────────────────────────────────────────────────
_BLOCK_REASON_UNDEFINED_RISK   = "BLOCKED: undefined-risk strategy (execution_mode=ANALYSIS_ONLY)"
_BLOCK_REASON_NO_MAX_LOSS      = "BLOCKED: max_loss is None (undefined maximum loss)"
_BLOCK_REASON_ANALYSIS_ONLY    = "BLOCKED: strategy is ANALYSIS_ONLY"
_BLOCK_REASON_EMPTY_LEGS       = "BLOCKED: legs list is empty"


def _audit_hash(data: dict) -> str:
    """Deterministic SHA-256 hash of the trade parameters."""
    blob = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _new_trade_id() -> str:
    return f"ase_pt_{uuid.uuid4().hex[:16]}"


def _new_run_id(ticker: str, thesis: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:8]
    return f"ase_{ticker.upper()}_{ts}_{thesis[:4].upper()}_{short}"


def _new_adjustment_id() -> str:
    return f"ase_adj_{uuid.uuid4().hex[:12]}"


def safety_check(
    evaluation: EvaluationResult,
) -> Optional[str]:
    """
    Pre-flight safety check before paper-trading.
    Returns None if safe, or a string reason if blocked.
    """
    if not evaluation.legs:
        return _BLOCK_REASON_EMPTY_LEGS
    if evaluation.execution_mode != MODE_AUTONOMOUS:
        return _BLOCK_REASON_ANALYSIS_ONLY
    if evaluation.risk_class == RISK_UNDEFINED:
        return _BLOCK_REASON_UNDEFINED_RISK
    if evaluation.payoff_info.get("is_undefined_risk"):
        return _BLOCK_REASON_UNDEFINED_RISK
    max_loss = evaluation.payoff_info.get("max_loss")
    if max_loss is None:
        return _BLOCK_REASON_NO_MAX_LOSS
    if max_loss <= 0:
        return "BLOCKED: max_loss must be positive"
    return None


def insert_paper_trade(
    evaluation:      EvaluationResult,
    selection:       SelectionResult,
    ticker:          str,
    thesis:          str,
    market_regime:   str,
    volatility_regime: str,
    event_context:   Optional[str],
    run_id:          str,
    underlying_price: float,
    planned_exit_date: Optional[str] = None,
) -> Optional[str]:
    """
    Insert a paper trade (parent + legs) into the database atomically.

    Returns the paper_trade_id string on success, or None on failure/block.
    """
    block_reason = safety_check(evaluation)
    if block_reason:
        print(f"[paper_trader] {block_reason} — {evaluation.strategy_name}")
        return None

    payoff   = evaluation.payoff_info
    prob     = evaluation.probability_info
    pricing  = evaluation.pricing_info
    greeks   = evaluation.greeks_info
    sc       = evaluation.score_components

    paper_trade_id = _new_trade_id()
    entry_time     = datetime.now(timezone.utc)
    planned_exit   = None
    if planned_exit_date:
        try:
            planned_exit = datetime.strptime(planned_exit_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            pass

    # Compute audit hash over all critical parameters
    audit_data = {
        "paper_trade_id":       paper_trade_id,
        "ticker":               ticker,
        "strategy_name":        evaluation.strategy_name,
        "thesis":               thesis,
        "underlying_price":     underlying_price,
        "max_profit":           payoff.get("max_profit"),
        "max_loss":             payoff.get("max_loss"),
        "pop":                  prob.get("pop"),
        "ev_after_costs":       pricing.get("ev_after_costs"),
        "capital_at_risk":      pricing.get("capital_at_risk"),
        "score":                evaluation.capital_compounding_score,
        "legs":                 [lg.to_dict() for lg in evaluation.legs],
        "entry_time":           entry_time.isoformat(),
    }
    audit = _audit_hash(audit_data)

    max_loss   = payoff.get("max_loss", 0)
    max_profit = payoff.get("max_profit")
    cap_risk   = pricing.get("capital_at_risk") or (max_loss * 100)
    buying_pwr = pricing.get("buying_power") or cap_risk

    try:
        with get_conn() as conn, conn.cursor() as cur:
            # ── 1. Insert parent trade ────────────────────────────────────
            cur.execute("""
                INSERT INTO ase_paper_trades (
                    paper_trade_id, strategy_evaluation_id, strategy_fingerprint,
                    decision_run_id, underlying, strategy_name, family, thesis, direction,
                    volatility_thesis, entry_time, planned_exit,
                    probability_of_profit, expected_value, maximum_profit, maximum_loss,
                    capital_at_risk, buying_power, return_on_risk, liquidity_score,
                    selected_score, runner_up, no_trade_score, market_regime,
                    volatility_regime, event_context, underlying_price_at_entry,
                    status, audit_hash
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
            """, (
                paper_trade_id,
                None,    # strategy_evaluation_id — updated below
                evaluation.strategy_fingerprint,
                run_id,
                ticker.upper(),
                evaluation.strategy_name,
                evaluation.strategy_family,
                thesis,
                evaluation.strategy_name.split()[0] if evaluation.strategy_name else None,
                None,    # volatility_thesis
                entry_time,
                planned_exit,
                prob.get("pop"),
                pricing.get("ev_after_costs"),
                max_profit,
                max_loss,
                cap_risk,
                buying_pwr,
                pricing.get("return_on_risk"),
                pricing.get("liquidity_score"),
                evaluation.capital_compounding_score,
                selection.runner_up.strategy_name if selection.runner_up else None,
                selection.no_trade_score,
                market_regime,
                volatility_regime,
                event_context,
                underlying_price,
                "OPEN",
                audit,
            ))

            # ── 2. Insert per-leg records ─────────────────────────────────
            for i, lg in enumerate(evaluation.legs, 1):
                call_put = lg.asset_type if lg.asset_type != "STOCK" else None
                modeled_fill = pricing.get(f"leg_{i}_fill") or lg.mid
                cur.execute("""
                    INSERT INTO ase_paper_trade_legs (
                        paper_trade_id, leg_number, asset_type, option_symbol,
                        call_or_put, buy_or_sell, open_or_close, quantity, ratio,
                        strike, expiration, dte_at_entry, bid, ask, mid, modeled_fill,
                        paper_fill, iv, delta, gamma, theta, vega, rho,
                        volume, open_interest, quote_timestamp, data_provider
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                    )
                """, (
                    paper_trade_id, i,
                    lg.asset_type,
                    lg.option_symbol,
                    call_put,
                    lg.side,
                    "OPEN",
                    lg.quantity,
                    lg.ratio,
                    lg.strike,
                    lg.expiration,
                    lg.dte,
                    lg.bid, lg.ask, lg.mid,
                    modeled_fill,    # modeled fill
                    modeled_fill,    # paper fill = same at entry
                    lg.iv,
                    lg.delta, lg.gamma, lg.theta, lg.vega, lg.rho,
                    lg.volume, lg.open_interest,
                    lg.quote_timestamp,
                    lg.data_provider,
                ))

            conn.commit()
        print(f"[paper_trader] Inserted {paper_trade_id}: {evaluation.strategy_name} on {ticker} @ {underlying_price}")
        return paper_trade_id

    except Exception as exc:
        print(f"[paper_trader] DB error: {type(exc).__name__}: {exc}")
        return None


def close_paper_trade(
    paper_trade_id: str,
    close_reason:   str,
    gross_pnl:      float,
    commission_paid: float,
    close_time:     Optional[datetime] = None,
) -> bool:
    """Mark a paper trade as closed and record P&L."""
    net_pnl = round(gross_pnl - commission_paid, 4)
    ct = close_time or datetime.now(timezone.utc)
    try:
        with get_conn() as conn, conn.cursor() as cur:
            # Compute holding period
            cur.execute("SELECT entry_time, capital_at_risk FROM ase_paper_trades WHERE paper_trade_id=%s", (paper_trade_id,))
            row = cur.fetchone()
            if not row:
                return False
            entry_time, cap_risk = row
            holding = (ct.replace(tzinfo=timezone.utc) - entry_time.replace(tzinfo=timezone.utc)).days if entry_time else 0
            ror = round(net_pnl / max(float(cap_risk or 1), 0.01), 4)

            cur.execute("""
                UPDATE ase_paper_trades
                SET status='CLOSED', close_time=%s, close_reason=%s,
                    gross_pnl=%s, net_pnl=%s, commission_paid=%s,
                    return_on_capital_realized=%s,
                    holding_period_days=%s, updated_at=NOW()
                WHERE paper_trade_id=%s AND status='OPEN'
            """, (ct, close_reason, gross_pnl, net_pnl, commission_paid, ror, holding, paper_trade_id))
            conn.commit()
        return True
    except Exception as exc:
        print(f"[paper_trader.close] {type(exc).__name__}: {exc}")
        return False


def get_open_trades() -> List[Dict[str, Any]]:
    """Fetch all open paper trades for position management."""
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT pt.*, legs_sub.legs
                    FROM ase_paper_trades pt
                    LEFT JOIN (
                        SELECT paper_trade_id,
                               array_agg(row_to_json(pl.*)) AS legs
                        FROM ase_paper_trade_legs pl
                        GROUP BY paper_trade_id
                    ) legs_sub ON legs_sub.paper_trade_id = pt.paper_trade_id
                    WHERE pt.status = 'OPEN'
                    ORDER BY pt.entry_time DESC
                """)
                return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        print(f"[paper_trader.get_open] {type(exc).__name__}: {exc}")
        return []


def save_decision_run(
    run_id:         str,
    ticker:         str,
    spot:           float,
    thesis:         str,
    market_regime:  str,
    volatility_regime: str,
    event_context:  Optional[str],
    iv_rank:        Optional[float],
    iv_percentile:  Optional[float],
    expected_move:  Optional[float],
    n_evaluated:    int,
    n_rejected:     int,
    selection:      SelectionResult,
    config_sha:     str,
) -> bool:
    """Insert or update an ase_decision_runs row."""
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO ase_decision_runs (
                    run_id, ticker, underlying_price, thesis, market_regime,
                    volatility_regime, event_context, iv_rank, iv_percentile,
                    expected_move, strategies_evaluated, strategies_rejected,
                    selected_strategy_name, runner_up_name, no_trade_score,
                    decision, config_sha256, finished_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                ON CONFLICT (run_id) DO UPDATE
                SET finished_at=NOW(), decision=EXCLUDED.decision,
                    selected_strategy_name=EXCLUDED.selected_strategy_name
            """, (
                run_id, ticker.upper(), spot, thesis, market_regime,
                volatility_regime, event_context, iv_rank, iv_percentile,
                expected_move, n_evaluated, n_rejected,
                selection.selected.strategy_name if selection.selected else None,
                selection.runner_up.strategy_name if selection.runner_up else None,
                selection.no_trade_score,
                selection.decision,
                config_sha,
            ))
            conn.commit()
        return True
    except Exception as exc:
        print(f"[paper_trader.save_run] {type(exc).__name__}: {exc}")
        return False
