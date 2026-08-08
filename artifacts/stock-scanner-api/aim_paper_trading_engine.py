import logging
import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class PatternLedger:
    """
    One independent paper-trading ledger for a single pattern (Gap Fill or ORB).
    Own balance, own position, own trade history, own stats — starts at $10,000
    and sizes every trade at 1.5% of its own net liquidation value.
    """

    def __init__(self, pattern_name: str, initial_capital_usd: float = 10000.0,
                 target_risk_pct: float = 0.015, expected_slippage_usd: float = 0.04):
        self.pattern_name = pattern_name
        self._starting_capital = initial_capital_usd
        self.account_balance_usd = initial_capital_usd
        self.net_liquidation_usd = initial_capital_usd
        self.active_position = None  # {"symbol","shares","side","entry","stop","target"}
        self.risk_pct = target_risk_pct
        self.slippage_usd = expected_slippage_usd
        self.order_inflight = False

        self.trade_log = []  # list of dicts: {symbol, side, entry, exit, shares, pnl_usd, result}
        self.wins = 0
        self.losses = 0

    # ---- stats ----
    @property
    def total_trades(self) -> int:
        return self.wins + self.losses

    @property
    def win_rate_pct(self) -> float:
        return (self.wins / self.total_trades * 100.0) if self.total_trades else 0.0

    @property
    def profit_rate_pct(self) -> float:
        """Cumulative return on starting capital, i.e. account growth %."""
        return ((self.account_balance_usd - self._starting_capital) / self._starting_capital * 100.0) if self._starting_capital else 0.0

    def snapshot(self) -> dict:
        return {
            "pattern": self.pattern_name,
            "account_balance_usd": round(self.account_balance_usd, 2),
            "net_liquidation_usd": round(self.net_liquidation_usd, 2),
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate_pct": round(self.win_rate_pct, 2),
            "profit_rate_pct": round(self.profit_rate_pct, 2),
            "active_position": self.active_position,
        }

    # ---- valuation ----
    def update_valuation(self, current_price: float):
        pos = self.active_position
        if pos:
            if pos["side"] == "SHORT":
                floating = (pos["entry"] - current_price) * pos["shares"]
            else:
                floating = (current_price - pos["entry"]) * pos["shares"]
            self.net_liquidation_usd = self.account_balance_usd + floating
        else:
            self.net_liquidation_usd = self.account_balance_usd

    # ---- exits ----
    def _close(self, exit_price: float, pnl_usd: float, reason: str):
        pos = self.active_position
        self.account_balance_usd += pnl_usd
        result = "WIN" if pnl_usd > 0 else "LOSS"
        if result == "WIN":
            self.wins += 1
        else:
            self.losses += 1
        self.trade_log.append({
            "symbol": pos["symbol"], "side": pos["side"], "entry": pos["entry"],
            "exit": exit_price, "shares": pos["shares"], "pnl_usd": round(pnl_usd, 2),
            "result": result, "reason": reason,
        })
        logging.info(f"[{self.pattern_name}] {result} ({reason}): {pos['side']} {pos['shares']}sh "
                     f"@ {pos['entry']:.2f} -> {exit_price:.2f} | P&L ${pnl_usd:.2f} | "
                     f"Balance ${self.account_balance_usd:.2f}")
        self.active_position = None
        self.order_inflight = False

    def check_exits(self, current_bar: pd.Series):
        pos = self.active_position
        if not pos:
            return

        high, low, close = current_bar['high'], current_bar['low'], current_bar['close']
        bar_time = current_bar.name.strftime('%H:%M') if hasattr(current_bar.name, 'strftime') else None

        if pos["side"] == "LONG":
            if low <= pos["stop"]:
                self._close(pos["stop"], (pos["stop"] - pos["entry"]) * pos["shares"], "STOP")
            elif high >= pos["target"]:
                self._close(pos["target"], (pos["target"] - pos["entry"]) * pos["shares"], "TARGET")
        elif pos["side"] == "SHORT":
            if high >= pos["stop"]:
                self._close(pos["stop"], (pos["entry"] - pos["stop"]) * pos["shares"], "STOP")
            elif low <= pos["target"]:
                self._close(pos["target"], (pos["entry"] - pos["target"]) * pos["shares"], "TARGET")

        # end-of-day flatten, still open at 15:55
        if self.active_position and bar_time and bar_time >= "15:55":
            pos = self.active_position
            pnl = (close - pos["entry"]) * pos["shares"] if pos["side"] == "LONG" else (pos["entry"] - close) * pos["shares"]
            self._close(close, pnl, "EOD_FLATTEN")

        self.update_valuation(close)

    # ---- entries ----
    def enter(self, symbol: str, entry: float, stop: float, target: float, side: str):
        if self.active_position or self.order_inflight:
            return  # one position at a time per pattern

        self.order_inflight = True

        if side == "LONG":
            p_entry, p_stop, p_target = entry + self.slippage_usd / 2, stop - self.slippage_usd / 2, target - self.slippage_usd / 2
        else:
            p_entry, p_stop, p_target = entry - self.slippage_usd / 2, stop + self.slippage_usd / 2, target + self.slippage_usd / 2

        risk_per_share = abs(p_entry - p_stop)
        if risk_per_share <= 0:
            self.order_inflight = False
            return

        allowed_risk_usd = self.net_liquidation_usd * self.risk_pct  # 1.5% compounding sizing
        shares = int(allowed_risk_usd / risk_per_share)
        if shares <= 0:
            self.order_inflight = False
            return

        self.active_position = {"symbol": symbol, "shares": shares, "side": side,
                                 "entry": p_entry, "stop": p_stop, "target": p_target}
        self.order_inflight = False

        logging.info(f"[{self.pattern_name}] ENTRY: {side} {shares}sh {symbol} @ {p_entry:.2f} "
                     f"| stop {p_stop:.2f} | target {p_target:.2f} | risking ${allowed_risk_usd:.2f}")


class AIMPaperTradingEngine:
    """
    SKU-scoped paper engine (same reserved VM OK — separate in-memory books).

    sku='aiem' → AIEM Pattern Lab (Gap Fill + ORB + F3 + asym packages)
    sku='oe'   → OE Strategies (F3 + same asym packages; no equity patterns)

    Same option patterns on both SKUs; independent capital / fills / persist.
    """

    def __init__(
        self,
        symbol: str = "SPY",
        initial_capital_usd: float = 10000.0,
        sku: str = "aiem",
        include_equity_patterns: bool | None = None,
    ):
        self.symbol = symbol
        self.sku = (sku or "aiem").strip().lower()
        if self.sku not in ("aiem", "oe"):
            self.sku = "aiem"
        self.buffer_pct = 0.0005
        self.min_gap_rr = 1.2

        if include_equity_patterns is None:
            include_equity_patterns = self.sku == "aiem"
        self.include_equity_patterns = bool(include_equity_patterns)

        self.gap_fill = (
            PatternLedger("GAP_FILL", initial_capital_usd)
            if self.include_equity_patterns
            else None
        )
        self.orb = (
            PatternLedger("ORB", initial_capital_usd)
            if self.include_equity_patterns
            else None
        )
        try:
            from aim_f3_spy_0dte import F3OptionsLedger as _F3
            self.f3 = _F3(underlying=symbol, starting_capital_usd=initial_capital_usd)
        except Exception as _f3e:
            logging.warning("F3 ledger unavailable: %s", _f3e)
            self.f3 = None

        self.asym = {}
        try:
            from aim_asym_paper_strategies import build_default_asym_ledgers as _build_asym
            self.asym = _build_asym(
                underlying=symbol,
                capital=initial_capital_usd,
                sku=self.sku,
            )
        except Exception as _asyme:
            logging.warning("Asym paper ledgers unavailable: %s", _asyme)
            self.asym = {}

    def _evaluate_options_ledgers(self, df: pd.DataFrame):
        if self.f3 is not None:
            try:
                self.f3.evaluate(df)
            except Exception as _f3ev:
                logging.warning("[F3] evaluate error: %s", _f3ev)
        for _key, ledger in (self.asym or {}).items():
            try:
                ledger.evaluate(df)
            except Exception as _asym_ev:
                logging.warning("[asym:%s:%s] evaluate error: %s", self.sku, _key, _asym_ev)

    def dashboard_snapshot(self) -> dict:
        """AIEM Pattern Lab full snapshot (equity + options)."""
        out = {
            "sku": self.sku,
            "product": "AIEM" if self.sku == "aiem" else "OE",
        }
        if self.gap_fill is not None:
            out["gap_fill"] = self.gap_fill.snapshot()
        if self.orb is not None:
            out["orb"] = self.orb.snapshot()
        if self.f3 is not None:
            out["f3"] = self.f3.snapshot()
        for key, ledger in (self.asym or {}).items():
            out[key] = ledger.snapshot()
        return out

    def options_snapshot(self) -> dict:
        """OE Strategies surface — F3 + asym only (same option keys as AIEM)."""
        out = {
            "sku": self.sku,
            "product": "AIEM" if self.sku == "aiem" else "OE",
        }
        if self.f3 is not None:
            out["f3"] = self.f3.snapshot()
        for key, ledger in (self.asym or {}).items():
            out[key] = ledger.snapshot()
        return out

    def evaluate_market_bars(self, prior_close: float, today_dataframe: pd.DataFrame):
        df = today_dataframe.sort_index()

        opening_bar = df.between_time('09:30', '09:30')
        opening_15min_window = df.between_time('09:30', '09:45')
        if (
            not self.include_equity_patterns
            or opening_bar.empty
            or len(opening_15min_window) < 15
        ):
            # F3 / asym can still evaluate without a full Gap Fill / ORB window
            self._evaluate_options_ledgers(df)
            return

        market_open_price = opening_bar.iloc[-1]['open']
        range_high = opening_15min_window['high'].max()
        range_low = opening_15min_window['low'].min()
        latest_bar = df.iloc[-1]

        # --- Gap Fill: manage exit if in a trade, else look for entry ---
        if self.gap_fill.active_position:
            self.gap_fill.check_exits(latest_bar)
        elif not self.gap_fill.order_inflight:
            gap_size_usd = market_open_price - prior_close
            gap_size_pct = abs(gap_size_usd) / prior_close if prior_close else 0
            if gap_size_pct <= 0.0050 and abs(gap_size_usd) >= 0.05:
                gf_bias = "SHORT" if gap_size_usd > 0 else "LONG"
                gf_entry = opening_15min_window.iloc[-1]['close']
                gf_stop = range_high * (1 + self.buffer_pct) if gf_bias == "SHORT" else range_low * (1 - self.buffer_pct)
                gf_target = prior_close
                if (abs(gf_entry - gf_target) / abs(gf_entry - gf_stop)) >= self.min_gap_rr:
                    self.gap_fill.enter(self.symbol, gf_entry, gf_stop, gf_target, gf_bias)

        # --- ORB: manage exit if in a trade, else look for entry ---
        if self.orb.active_position:
            self.orb.check_exits(latest_bar)
        elif not self.orb.order_inflight:
            post_range_session = df.between_time('09:46', '16:00')
            if not post_range_session.empty:
                if latest_bar['close'] > range_high:
                    orb_stop = range_low * (1 - self.buffer_pct)
                    # 3.0R take-profit (same OR stop) — Pattern Lab backtest IS/OOS edge vs 2.0R
                    orb_target = latest_bar['close'] + (abs(latest_bar['close'] - range_low) * 3.0)
                    self.orb.enter(self.symbol, latest_bar['close'], orb_stop, orb_target, "LONG")
                elif latest_bar['close'] < range_low:
                    orb_stop = range_high * (1 + self.buffer_pct)
                    orb_target = latest_bar['close'] - (abs(range_high - latest_bar['close']) * 3.0)
                    self.orb.enter(self.symbol, latest_bar['close'], orb_stop, orb_target, "SHORT")

        # --- F3 + asymmetric packages (debit + cash-secured credit) ---
        self._evaluate_options_ledgers(df)
