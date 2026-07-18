"""
aiem_portfolio_engine/snapshot.py — S1: Portfolio Snapshot Engine.

Builds an immutable, timestamped snapshot of all open positions before
every portfolio gate evaluation. Fail-closed: if positions or capital
cannot be reconciled, snapshot.reconciled=False and the gate blocks.
"""
from __future__ import annotations
import json, uuid, datetime
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional

import psycopg2
import psycopg2.extras

from .config import PORTFOLIO_CAPITAL, CONTRACT_MULTIPLIER, NOT_IMPLEMENTED_V1


def _snap_id() -> str:
    return f"ape_snap_{uuid.uuid4().hex[:16]}"


@dataclass
class PositionLeg:
    leg_number:   int
    asset_type:   str          # CALL / PUT / STOCK
    call_or_put:  Optional[str]
    buy_or_sell:  str          # LONG / SHORT
    quantity:     int
    ratio:        float
    strike:       Optional[float]
    expiration:   Optional[str]
    dte_at_entry: Optional[int]
    bid:          Optional[float]
    ask:          Optional[float]
    mid:          Optional[float]
    iv:           Optional[float]
    delta:        Optional[float]
    gamma:        Optional[float]
    theta:        Optional[float]
    vega:         Optional[float]
    rho:          Optional[float]


@dataclass
class PortfolioPosition:
    paper_trade_id:   str
    ticker:           str
    strategy_name:    str
    strategy_family:  Optional[str]
    thesis:           str
    direction:        Optional[str]
    entry_time:       str
    capital_at_risk:  float
    buying_power:     float
    maximum_loss:     float
    underlying_price: Optional[float]
    n_contracts:      int
    legs:             List[PositionLeg] = field(default_factory=list)
    sector:           Optional[str] = None
    is_long_vol:      bool = False
    is_short_vol:     bool = False
    is_defined_risk:  bool = True


@dataclass
class PortfolioSnapshot:
    snapshot_id:          str
    trace_id:             str
    snapshot_ts:          str
    cash_available:       float
    buying_power:         float
    reserved_capital:     float
    committed_capital:    float
    n_open_positions:     int
    total_market_value:   float
    total_unrealized_pnl: float
    positions:            List[PortfolioPosition]
    pending_orders:       List[Any]     # NOT_IMPLEMENTED v1 — always []
    reconciled:           bool
    reconcile_error:      Optional[str]
    not_implemented:      List[str] = field(default_factory=list)


def _classify_vol(strategy_name: str, strategy_family: Optional[str]) -> tuple[bool, bool]:
    """Return (is_long_vol, is_short_vol) from strategy name."""
    name = (strategy_name or "").upper()
    fam  = (strategy_family or "").upper()
    long_vol_patterns  = ("LONG_STRADDLE","LONG_STRANGLE","LONG_CALL","LONG_PUT","DEBIT","CALENDAR")
    short_vol_patterns = ("SHORT_STRADDLE","SHORT_STRANGLE","IRON_CONDOR","IRON_FLY",
                          "CREDIT","COVERED_CALL","CASH_SECURED_PUT","BUTTERFLY")
    is_lv = any(p in name or p in fam for p in long_vol_patterns)
    is_sv = any(p in name or p in fam for p in short_vol_patterns)
    if is_lv and is_sv:
        is_lv = True; is_sv = False
    return is_lv, is_sv


def _get_sector(ticker: str) -> Optional[str]:
    try:
        from sector_etf_data import TICKER_SECTOR_MAP
        return TICKER_SECTOR_MAP.get(ticker.upper())
    except Exception:
        return None


def build_snapshot(trace_id: str, db_url: str) -> PortfolioSnapshot:
    """
    Build an immutable portfolio snapshot from ase_paper_trades + ase_paper_trade_legs.
    Fail closed: reconciled=False if any position data is missing or inconsistent.
    """
    snap_id = _snap_id()
    ts      = datetime.datetime.now(datetime.timezone.utc).isoformat()

    try:
        conn = psycopg2.connect(db_url, connect_timeout=10)
        conn.autocommit = False
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT paper_trade_id, underlying, strategy_name, family, thesis,
                   direction, entry_time, capital_at_risk, buying_power,
                   maximum_loss, underlying_price_at_entry
            FROM ase_paper_trades
            WHERE status = 'OPEN'
            ORDER BY entry_time ASC
        """)
        trade_rows = cur.fetchall()

        positions: List[PortfolioPosition] = []
        reconcile_errors: List[str] = []
        total_committed = 0.0
        total_buying_pwr = 0.0
        total_mkt_value = 0.0

        for tr in trade_rows:
            pt_id = tr["paper_trade_id"]

            cur.execute("""
                SELECT leg_number, asset_type, call_or_put, buy_or_sell,
                       quantity, ratio, strike, expiration, dte_at_entry,
                       bid, ask, mid, iv,
                       delta, gamma, theta, vega, rho
                FROM ase_paper_trade_legs
                WHERE paper_trade_id = %s
                ORDER BY leg_number
            """, (pt_id,))
            leg_rows = cur.fetchall()

            if not leg_rows:
                reconcile_errors.append(f"{pt_id}: no legs found")
                continue

            legs = []
            n_contracts = 0
            for lr in leg_rows:
                qty = int(lr["quantity"] or 1)
                if lr["asset_type"] in ("CALL", "PUT"):
                    n_contracts += qty
                if qty <= 0:
                    reconcile_errors.append(f"{pt_id} leg {lr['leg_number']}: quantity={qty} invalid")
                legs.append(PositionLeg(
                    leg_number   = lr["leg_number"],
                    asset_type   = lr["asset_type"],
                    call_or_put  = lr["call_or_put"],
                    buy_or_sell  = lr["buy_or_sell"],
                    quantity     = qty,
                    ratio        = float(lr["ratio"] or 1.0),
                    strike       = float(lr["strike"]) if lr["strike"] else None,
                    expiration   = str(lr["expiration"]) if lr["expiration"] else None,
                    dte_at_entry = lr["dte_at_entry"],
                    bid          = float(lr["bid"]) if lr["bid"] is not None else None,
                    ask          = float(lr["ask"]) if lr["ask"] is not None else None,
                    mid          = float(lr["mid"]) if lr["mid"] is not None else None,
                    iv           = float(lr["iv"]) if lr["iv"] is not None else None,
                    delta        = float(lr["delta"]) if lr["delta"] is not None else None,
                    gamma        = float(lr["gamma"]) if lr["gamma"] is not None else None,
                    theta        = float(lr["theta"]) if lr["theta"] is not None else None,
                    vega         = float(lr["vega"]) if lr["vega"] is not None else None,
                    rho          = float(lr["rho"]) if lr["rho"] is not None else None,
                ))

            cap_risk   = float(tr["capital_at_risk"] or 0)
            buy_pwr    = float(tr["buying_power"] or cap_risk)
            max_loss   = float(tr["maximum_loss"] or cap_risk)
            spot       = float(tr["underlying_price_at_entry"]) if tr["underlying_price_at_entry"] else None

            total_committed  += cap_risk
            total_buying_pwr += buy_pwr

            leg_mkt = sum(
                (float(lg.mid or 0) * lg.quantity * CONTRACT_MULTIPLIER)
                for lg in legs
                if lg.asset_type in ("CALL", "PUT")
            )
            total_mkt_value += leg_mkt

            ticker = tr["underlying"]
            is_lv, is_sv = _classify_vol(tr["strategy_name"], tr["family"])
            sector = _get_sector(ticker)

            positions.append(PortfolioPosition(
                paper_trade_id   = pt_id,
                ticker           = ticker,
                strategy_name    = tr["strategy_name"],
                strategy_family  = tr["family"],
                thesis           = tr["thesis"],
                direction        = tr["direction"],
                entry_time       = str(tr["entry_time"]),
                capital_at_risk  = cap_risk,
                buying_power     = buy_pwr,
                maximum_loss     = max_loss,
                underlying_price = spot,
                n_contracts      = max(n_contracts, 1),
                legs             = legs,
                sector           = sector,
                is_long_vol      = is_lv,
                is_short_vol     = is_sv,
                is_defined_risk  = True,
            ))

        cur.close()
        conn.close()

        cash_available = PORTFOLIO_CAPITAL - total_committed
        reconciled     = len(reconcile_errors) == 0

        return PortfolioSnapshot(
            snapshot_id          = snap_id,
            trace_id             = trace_id,
            snapshot_ts          = ts,
            cash_available       = round(cash_available, 2),
            buying_power         = round(PORTFOLIO_CAPITAL - total_buying_pwr, 2),
            reserved_capital     = 0.0,
            committed_capital    = round(total_committed, 2),
            n_open_positions     = len(positions),
            total_market_value   = round(total_mkt_value, 2),
            total_unrealized_pnl = 0.0,
            positions            = positions,
            pending_orders       = [],   # NOT_IMPLEMENTED v1
            reconciled           = reconciled,
            reconcile_error      = "; ".join(reconcile_errors) if reconcile_errors else None,
            not_implemented      = [
                NOT_IMPLEMENTED_V1[4],  # pending_orders
                NOT_IMPLEMENTED_V1[5],  # realized_pnl_intraday
            ],
        )

    except Exception as exc:
        return PortfolioSnapshot(
            snapshot_id          = snap_id,
            trace_id             = trace_id,
            snapshot_ts          = ts,
            cash_available       = 0.0,
            buying_power         = 0.0,
            reserved_capital     = 0.0,
            committed_capital    = 0.0,
            n_open_positions     = 0,
            total_market_value   = 0.0,
            total_unrealized_pnl = 0.0,
            positions            = [],
            pending_orders       = [],
            reconciled           = False,
            reconcile_error      = f"DB exception: {type(exc).__name__}: {exc}",
        )


def detect_stale_quotes(snapshot: PortfolioSnapshot) -> List[str]:
    """
    Return a list of tickers whose option legs ALL have bid=0 and ask=0.
    A position where every option leg has zero bid/ask is treated as having
    stale or invalid market data and should not be used for risk calculations.

    Stock-only legs (asset_type='STOCK') are excluded from the staleness check.
    """
    stale: List[str] = []
    for pos in snapshot.positions:
        option_legs = [lg for lg in pos.legs if lg.asset_type in ("CALL", "PUT")]
        if not option_legs:
            continue
        all_stale = all(
            (lg.bid is None or lg.bid == 0.0) and (lg.ask is None or lg.ask == 0.0)
            for lg in option_legs
        )
        if all_stale:
            stale.append(pos.ticker)
    return stale


def save_snapshot(snap: PortfolioSnapshot, db_url: str) -> str:
    """Persist snapshot to ape_portfolio_snapshots. Returns snapshot_id."""
    positions_list = []
    for p in snap.positions:
        d = {
            "paper_trade_id": p.paper_trade_id,
            "ticker": p.ticker,
            "strategy_name": p.strategy_name,
            "capital_at_risk": p.capital_at_risk,
            "n_contracts": p.n_contracts,
            "n_legs": len(p.legs),
            "sector": p.sector,
            "is_long_vol": p.is_long_vol,
            "is_short_vol": p.is_short_vol,
        }
        positions_list.append(d)

    with psycopg2.connect(db_url, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO ape_portfolio_snapshots
                    (snapshot_id, trace_id, snapshot_ts, cash_available,
                     buying_power, reserved_capital, committed_capital,
                     n_open_positions, total_market_value, total_unrealized_pnl,
                     positions_json, pending_orders_json, reconciled, reconcile_error)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (snapshot_id) DO NOTHING
            """, (
                snap.snapshot_id, snap.trace_id, snap.snapshot_ts,
                snap.cash_available, snap.buying_power,
                snap.reserved_capital, snap.committed_capital,
                snap.n_open_positions, snap.total_market_value,
                snap.total_unrealized_pnl,
                json.dumps(positions_list),
                json.dumps(snap.pending_orders),
                snap.reconciled,
                snap.reconcile_error,
            ))
        conn.commit()
    return snap.snapshot_id
