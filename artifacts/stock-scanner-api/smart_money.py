import math
import numpy as np
import pandas as pd
import ta
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

DEFAULT_LEADERBOARD = [
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "AMD",
    "NFLX", "PLTR", "COIN", "SOFI", "MARA", "RBLX", "UBER", "SMCI",
    "ARM", "INTC", "MU", "AI", "SPY", "QQQ", "JPM", "V", "PYPL",
]


def _f(v, default=0.0):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


# ─────────────────────────────────────────────────────────────────────────────
# REAL OPTIONS CHAIN FETCH (yfinance)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_options_data(ticker: str) -> dict:
    """
    Pull real options chain from yfinance for the expiry nearest to 30 days out.
    Returns a dict of computed metrics, or empty dict if data is unavailable.

    Key metrics:
      call_vol_oi    – call volume / call OI  (>0.5 unusual, >1.0 = sweep territory)
      put_vol_oi     – put volume  / put OI
      call_put_ratio – today's call vol / put vol
      cp_oi_ratio    – call OI / put OI (accumulated positioning skew)
      otm_call_vol   – volume in OTM calls 3-20% above current price
      atm_iv         – avg implied volatility of near-ATM calls
      total_call_vol / total_put_vol / total_call_oi / total_put_oi
    """
    try:
        import yfinance as yf

        tkr  = yf.Ticker(ticker)
        exps = tkr.options          # tuple of expiry date strings
        if not exps:
            return {}

        today      = datetime.now().date()
        target     = today + timedelta(days=30)

        # Nearest expiry to 30 days out
        exp = min(exps, key=lambda e: abs(
            (datetime.strptime(e, "%Y-%m-%d").date() - target).days
        ))

        chain = tkr.option_chain(exp)
        calls = chain.calls.copy()
        puts  = chain.puts.copy()

        for col in ["volume", "openInterest", "impliedVolatility"]:
            for df_ in (calls, puts):
                if col in df_.columns:
                    df_[col] = pd.to_numeric(df_[col], errors="coerce").fillna(0)

        total_call_vol = _f(calls["volume"].sum())
        total_call_oi  = max(_f(calls["openInterest"].sum()), 1.0)
        total_put_vol  = _f(puts["volume"].sum())
        total_put_oi   = max(_f(puts["openInterest"].sum()), 1.0)

        call_vol_oi    = total_call_vol / total_call_oi
        put_vol_oi     = total_put_vol  / total_put_oi
        call_put_ratio = total_call_vol / max(total_put_vol, 1.0)
        cp_oi_ratio    = total_call_oi  / total_put_oi

        # Near-ATM IV (strikes within 7% of last price)
        cur_price = _f(getattr(tkr.fast_info, "last_price", 0) or 0)
        atm_iv    = None
        otm_call_vol = 0.0

        if cur_price > 0 and "strike" in calls.columns:
            atm_mask = (calls["strike"] - cur_price).abs() / cur_price < 0.07
            atm_df   = calls[atm_mask]
            if not atm_df.empty and "impliedVolatility" in atm_df.columns:
                atm_iv = _f(atm_df["impliedVolatility"].mean())

            # OTM calls 3-20% above spot (near-term bullish positioning)
            otm_mask     = (calls["strike"] > cur_price * 1.03) & (calls["strike"] < cur_price * 1.20)
            otm_call_vol = _f(calls.loc[otm_mask, "volume"].sum())

        # ── Top-volume expiry: scan up to 4 nearest expiries, pick highest call vol ──
        top_vol_expiry    = exp
        top_vol_strike    = None
        top_vol_contracts = 0
        top_prem_expiry   = exp
        top_prem_strike   = None
        top_prem_contracts = 0
        top_prem_value    = 0.0   # ask × volume (dollar premium traded)

        scan_exps = [e for e in exps if datetime.strptime(e, "%Y-%m-%d").date() >= today][:4]
        best_exp_vol = total_call_vol  # start with what we already have

        for se in scan_exps:
            try:
                sc = tkr.option_chain(se).calls.copy()
                for col in ["volume", "openInterest", "ask"]:
                    if col in sc.columns:
                        sc[col] = pd.to_numeric(sc[col], errors="coerce").fillna(0)

                se_total_vol = _f(sc["volume"].sum())

                # Highest-volume expiry
                if se_total_vol > best_exp_vol:
                    best_exp_vol   = se_total_vol
                    top_vol_expiry = se

                # Highest-volume strike — OTM/near-ATM only (strike 80%–150% of spot)
                # Excludes deep-ITM strikes (e.g. LEAPS) which skew the signal
                if not sc.empty and "volume" in sc.columns and "strike" in sc.columns and cur_price > 0:
                    atm_otm = sc[
                        (sc["strike"] >= cur_price * 0.80) &
                        (sc["strike"] <= cur_price * 1.50)
                    ]
                    if not atm_otm.empty:
                        best_row = atm_otm.loc[atm_otm["volume"].idxmax()]
                        sv = _f(best_row["volume"])
                        if sv > top_vol_contracts:
                            top_vol_contracts = int(sv)
                            top_vol_strike    = float(best_row["strike"])
                            top_vol_expiry    = se

                # Highest-premium strike — OTM/near-ATM only (strike 80%–150% of spot)
                # Excludes deep-ITM LEAPS from distorting the dollar-premium signal
                if "ask" in sc.columns and "volume" in sc.columns and "strike" in sc.columns and cur_price > 0:
                    atm_otm_p = sc[
                        (sc["strike"] >= cur_price * 0.80) &
                        (sc["strike"] <= cur_price * 1.50)
                    ].copy()
                    if not atm_otm_p.empty:
                        atm_otm_p["_prem"] = atm_otm_p["ask"] * atm_otm_p["volume"] * 100
                        best_prem_row = atm_otm_p.loc[atm_otm_p["_prem"].idxmax()]
                        pv = _f(best_prem_row["_prem"])
                        if pv > top_prem_value:
                            top_prem_value     = pv
                            top_prem_strike    = float(best_prem_row["strike"])
                            top_prem_contracts = int(_f(best_prem_row["volume"]))
                            top_prem_expiry    = se
            except Exception:
                continue

        return {
            "call_vol_oi":         _f(call_vol_oi),
            "put_vol_oi":          _f(put_vol_oi),
            "call_put_ratio":      _f(call_put_ratio),
            "cp_oi_ratio":         _f(cp_oi_ratio),
            "total_call_vol":      total_call_vol,
            "total_put_vol":       total_put_vol,
            "total_call_oi":       total_call_oi,
            "total_put_oi":        total_put_oi,
            "atm_iv":              atm_iv,
            "otm_call_vol":        otm_call_vol,
            "expiry":              exp,
            # Top-volume contract
            "top_vol_strike":      top_vol_strike,
            "top_vol_expiry":      top_vol_expiry,
            "top_vol_contracts":   top_vol_contracts,
            # Top-premium contract (most dollar value traded)
            "top_prem_strike":     top_prem_strike,
            "top_prem_expiry":     top_prem_expiry,
            "top_prem_contracts":  top_prem_contracts,
            "top_prem_value":      round(top_prem_value / 1_000, 1),  # in $K
        }
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# SCORE ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def compute_smart_money(ticker: str, df, spy_ret_1m: float = 0.0,
                        opts: dict | None = None) -> dict | None:
    """
    Compute Smart Money Score (0-100).

    When `opts` is provided (real options chain data), Call Sweep and
    Volume/OI use actual market data.  Otherwise falls back to price/volume proxies.
    """
    try:
        close  = df["Close"].squeeze().astype(float).dropna()
        volume = df["Volume"].squeeze().astype(float).fillna(0)
        high   = df["High"].squeeze().astype(float)
        low    = df["Low"].squeeze().astype(float)

        if len(close) < 60:
            return None

        price = _f(close.iloc[-1])
        if price <= 0:
            return None

        opts = opts or {}

        # ── Stock volume metrics ────────────────────────────────────────────
        avg30  = max(_f(volume.rolling(30).mean().iloc[-1]), 1.0)
        avg10  = max(_f(volume.rolling(10).mean().iloc[-1]), 1.0)
        cur_v  = _f(volume.iloc[-1], avg30)
        rvol   = max(0.0, cur_v / avg30)
        rvol10 = max(0.0, cur_v / avg10)

        chg1d = _f(
            (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2]
            if len(close) > 1 else 0
        )

        sma20  = _f(close.rolling(20).mean().iloc[-1])
        sma50  = _f(close.rolling(50).mean().iloc[-1])
        sma200 = _f(close.rolling(200).mean().iloc[-1])

        try:
            rsi = _f(ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1], 50.0)
        except Exception:
            rsi = 50.0

        try:
            atr = _f(ta.volatility.AverageTrueRange(high, low, close, window=14)
                     .average_true_range().iloc[-1])
        except Exception:
            atr = price * 0.02
        atr = max(atr, price * 0.005)

        ret1m = _f(
            (close.iloc[-1] - close.iloc[-21]) / close.iloc[-21]
            if len(close) > 21 else 0
        )

        # ── Real options metrics ────────────────────────────────────────────
        has_opts        = bool(opts)
        call_vol_oi     = _f(opts.get("call_vol_oi",    0.0))
        put_vol_oi      = _f(opts.get("put_vol_oi",     0.0))
        call_put_ratio  = _f(opts.get("call_put_ratio", 1.0))
        cp_oi_ratio     = _f(opts.get("cp_oi_ratio",    1.0))
        otm_call_vol    = _f(opts.get("otm_call_vol",   0.0))
        total_call_oi   = _f(opts.get("total_call_oi",  0.0))
        total_call_vol  = _f(opts.get("total_call_vol", 0.0))
        total_put_vol   = _f(opts.get("total_put_vol",  0.0))
        total_put_oi    = _f(opts.get("total_put_oi",   0.0))
        atm_iv_raw      = opts.get("atm_iv")
        atm_iv          = _f(atm_iv_raw) if atm_iv_raw is not None else None
        expiry          = opts.get("expiry", "N/A")

        # ────────────────────────────────────────────────────────────────────
        # 1. Call Sweep (0–25)
        # ────────────────────────────────────────────────────────────────────
        if has_opts:
            # Vol/OI > 1.0 means today's volume exceeded all existing open contracts
            # → classic sweep signature.  Typical retail: 0.05-0.20.  Sweep: 0.5+
            s_call_voi  = min(18.0, call_vol_oi * 9.0)          # 2.0 ratio → 18 pts
            s_call_cpr  = min(7.0,  max(0.0, (call_put_ratio - 1.0) * 3.5))  # >3x → 7 pts
            s_call      = min(25.0, s_call_voi + s_call_cpr)
        else:
            s_call = min(25.0, max(0.0, (rvol - 1.0) * 6.0 + max(0.0, chg1d * 180.0)))

        # ────────────────────────────────────────────────────────────────────
        # 2. Volume / OI (0–20)
        # ────────────────────────────────────────────────────────────────────
        if has_opts:
            # OTM call buying: ratio of OTM call vol to total call OI
            otm_ratio  = otm_call_vol / max(total_call_oi, 1.0)
            s_otm      = min(8.0,  otm_ratio * 16.0)            # 0.5 → 8 pts
            # Accumulated OI positioning: call OI >> put OI = smart money loaded
            s_cp_oi    = min(6.0,  max(0.0, (cp_oi_ratio - 1.0) * 3.0))
            # Stock volume reinforcement
            s_vol_stk  = min(6.0,  max(0.0, (rvol - 1.0) * 3.0))
            s_vol      = min(20.0, s_otm + s_cp_oi + s_vol_stk)
        else:
            s_vol = min(20.0, max(0.0, (rvol - 1.0) * 4.5 + max(0.0, (rvol10 - 1.0) * 3.0)))

        # ────────────────────────────────────────────────────────────────────
        # 3. Ask-Side Aggression (0–15) — close-in-range (no options needed)
        # ────────────────────────────────────────────────────────────────────
        recent  = df.tail(20).copy()
        rng_    = (recent["High"].astype(float) - recent["Low"].astype(float))
        cls_up  = (recent["Close"].astype(float) - recent["Low"].astype(float))
        pct_upper = _f((cls_up / rng_.replace(0, np.nan)).fillna(0.5).mean(), 0.5)
        s_ask   = min(15.0, pct_upper * 15.0)

        # ────────────────────────────────────────────────────────────────────
        # 4. Dark Pool Proxy (0–15)
        #    SMA alignment + IV context (high call IV = smart money paying premium)
        # ────────────────────────────────────────────────────────────────────
        s_dp = 0.0
        if sma50  > 0 and price > sma50:   s_dp += 5.0
        if sma200 > 0 and price > sma200:  s_dp += 5.0
        if sma20  > 0 and sma50 > 0 and sma20 > sma50: s_dp += 3.0
        # High ATM IV while price is rising = institutions paying up → accumulation
        if atm_iv is not None and atm_iv > 0.30 and chg1d > 0:
            s_dp += min(2.0, (atm_iv - 0.30) * 10.0)
        s_dp = min(15.0, s_dp)

        # ────────────────────────────────────────────────────────────────────
        # 5. Sector Strength (0–10)
        # ────────────────────────────────────────────────────────────────────
        rel      = ret1m - spy_ret_1m
        s_sector = min(10.0, max(0.0, 5.0 + rel * 100.0))

        # ────────────────────────────────────────────────────────────────────
        # 6. Historical Similarity (0–15)
        # ────────────────────────────────────────────────────────────────────
        wins, total_occ, ret_sum = 0, 0, 0.0
        vol_roll = volume.rolling(30).mean()
        for i in range(30, len(close) - 5):
            av = _f(vol_roll.iloc[i], 1.0)
            if av <= 0:
                continue
            pr = _f(volume.iloc[i]) / av
            pc = _f(
                (close.iloc[i] - close.iloc[i - 1]) / close.iloc[i - 1]
                if close.iloc[i - 1] > 0 else 0
            )
            if pr > 1.3 and pc > 0:
                fwd = _f(
                    (close.iloc[i + 5] - close.iloc[i]) / close.iloc[i]
                    if close.iloc[i] > 0 else 0
                )
                if fwd > 0:
                    wins += 1
                total_occ += 1
                ret_sum   += fwd

        win_rate    = round(wins / total_occ * 100.0, 1) if total_occ > 0 else 50.0
        avg_5d      = round(ret_sum / total_occ * 100.0, 2) if total_occ > 0 else 0.0
        occurrences = total_occ
        s_hist      = min(15.0, max(0.0, (win_rate - 50.0) / 50.0 * 15.0 + 7.5))

        total_score = round(min(100, max(0, s_call + s_vol + s_ask + s_dp + s_sector + s_hist)))

        # ── Signal labels ────────────────────────────────────────────────────
        if total_score >= 80:
            signal, direction, confidence = "Strong Bullish Institutional Accumulation", "Bullish", "Very High"
        elif total_score >= 65:
            signal, direction, confidence = "Bullish Options Flow Detected", "Bullish", "High"
        elif total_score >= 50:
            signal, direction, confidence = "Moderate Institutional Interest", "Bullish", "Moderate"
        elif total_score >= 35:
            signal, direction, confidence = "Neutral Market Activity", "Neutral", "Low"
        elif total_score >= 20:
            signal, direction, confidence = "Bearish Pressure Detected", "Bearish", "Moderate"
        else:
            signal, direction, confidence = "Strong Bearish Institutional Activity", "Bearish", "High"

        # ── Risk ─────────────────────────────────────────────────────────────
        vol_daily = _f(close.pct_change().rolling(20).std().iloc[-1], 0.02) * 100.0
        risk = (
            "Low"      if vol_daily < 1.5
            else "Moderate" if vol_daily < 2.5
            else "High"     if vol_daily < 4.0
            else "Very High"
        )

        # ── Expected Move ─────────────────────────────────────────────────────
        # If we have real ATM IV, use options-implied expected move instead of ATR
        if atm_iv is not None and atm_iv > 0:
            # 1-day ATM IV → annualised → 5-day window
            em_pct_1d = atm_iv / math.sqrt(252) * 100.0
            em_low    = round(em_pct_1d * math.sqrt(5), 1)
            em_high   = round(em_pct_1d * math.sqrt(5) * 2.0, 1)
        else:
            em_low  = round(atr / price * 100.0, 1)
            em_high = round(atr / price * 300.0, 1)

        # ── AI Trade Thesis ───────────────────────────────────────────────────
        parts = []

        # Options-data sentences (only when real data available)
        if has_opts:
            if call_vol_oi >= 1.0:
                parts.append(
                    f"REAL OPTIONS DATA: Call volume ({int(total_call_vol):,} contracts) "
                    f"exceeded open interest ({int(total_call_oi):,}) — "
                    f"a {call_vol_oi:.1f}x Vol/OI ratio strongly indicates institutional sweep activity."
                )
            elif call_vol_oi >= 0.5:
                parts.append(
                    f"REAL OPTIONS DATA: Call Vol/OI ratio of {call_vol_oi:.2f} on the "
                    f"{expiry} expiry signals elevated institutional options participation."
                )
            else:
                parts.append(
                    f"REAL OPTIONS DATA: Call Vol/OI ratio is {call_vol_oi:.2f} (normal < 0.3) "
                    f"on the {expiry} expiry — no significant sweep activity detected."
                )

            if call_put_ratio >= 2.0:
                parts.append(
                    f"Bullish call/put volume ratio of {call_put_ratio:.1f}x — "
                    f"calls dominate today's options flow, signaling directional conviction."
                )
            elif call_put_ratio >= 1.3:
                parts.append(
                    f"Call volume running {call_put_ratio:.1f}x put volume — mild bullish options bias."
                )
            elif call_put_ratio < 0.8:
                parts.append(
                    f"Put volume exceeding calls ({1/call_put_ratio:.1f}x) — defensive positioning detected."
                )

            if cp_oi_ratio >= 1.5:
                parts.append(
                    f"Accumulated call OI is {cp_oi_ratio:.1f}x put OI — "
                    f"smart money has been building bullish positions over multiple sessions."
                )

            if otm_call_vol > 0 and total_call_vol > 0:
                otm_pct = otm_call_vol / total_call_vol * 100
                if otm_pct >= 25:
                    parts.append(
                        f"{otm_pct:.0f}% of call volume is in OTM strikes 3-20% above spot — "
                        f"aggressive speculative or insider-style positioning."
                    )

            if atm_iv is not None:
                iv_pct = atm_iv * 100
                if iv_pct > 60:
                    parts.append(f"ATM implied volatility is elevated at {iv_pct:.0f}% — market pricing in a significant move.")
                elif iv_pct > 35:
                    parts.append(f"ATM IV at {iv_pct:.0f}% reflects moderate uncertainty; expected 5-day move: ±{em_low}–{em_high}%.")

        # Stock volume & price sentences
        if rvol >= 2.0:
            parts.append(f"Equity volume running {rvol:.1f}x above 30-day average — institutional participation confirmed.")
        elif rvol >= 1.3:
            parts.append(f"Stock volume {rvol:.1f}x normal, reinforcing the options flow signal.")

        if s_dp >= 10:
            parts.append("Price is trading above both the 50-day and 200-day moving averages — sustained dark pool accumulation pattern.")
        elif s_dp >= 5:
            parts.append("Partial institutional accumulation pattern detected via moving average structure.")

        if chg1d > 0.02:
            parts.append(f"Strong ask-side aggression: +{chg1d * 100:.1f}% intraday — buyers actively lifting the offer.")
        elif chg1d > 0:
            parts.append(f"Positive intraday momentum (+{chg1d * 100:.1f}%) supports near-term bullish bias.")

        if rel > 0.03:
            parts.append(f"Outperforming SPY by {rel * 100:.1f}% over the past month — top-tier sector rotation.")
        elif rel < -0.03:
            parts.append(f"Underperforming SPY by {abs(rel) * 100:.1f}% — weak relative strength.")

        if occurrences > 5:
            pfx = "+" if avg_5d > 0 else ""
            parts.append(
                f"Historical setups with similar volume/momentum produced a {win_rate:.0f}% win rate "
                f"over {occurrences} occurrences (avg 5-day return: {pfx}{avg_5d:.1f}%)."
            )

        if not parts:
            parts.append(
                f"Smart Money Score: {total_score}/100 with {confidence.lower()} confidence "
                f"in the {direction.lower()} direction."
            )

        thesis = " ".join(parts)

        # ── Options summary for UI ────────────────────────────────────────────
        options_summary = None
        if has_opts:
            options_summary = {
                "expiry":              expiry,
                "call_vol_oi":         round(call_vol_oi, 3),
                "put_vol_oi":          round(put_vol_oi, 3),
                "call_put_ratio":      round(call_put_ratio, 2),
                "cp_oi_ratio":         round(cp_oi_ratio, 2),
                "total_call_vol":      int(total_call_vol),
                "total_put_vol":       int(total_put_vol),
                "total_call_oi":       int(total_call_oi),
                "total_put_oi":        int(total_put_oi),
                "otm_call_vol":        int(otm_call_vol),
                "atm_iv":              round(atm_iv * 100, 1) if atm_iv is not None else None,
                "data_source":         "yfinance (15-min delayed)",
                # Top-volume contract (most active strike)
                "top_vol_strike":      opts.get("top_vol_strike"),
                "top_vol_expiry":      opts.get("top_vol_expiry"),
                "top_vol_contracts":   opts.get("top_vol_contracts"),
                # Top-premium contract (most dollar value traded)
                "top_prem_strike":     opts.get("top_prem_strike"),
                "top_prem_expiry":     opts.get("top_prem_expiry"),
                "top_prem_contracts":  opts.get("top_prem_contracts"),
                "top_prem_value_k":    opts.get("top_prem_value"),  # $K
            }

        return {
            "ticker":            ticker,
            "price":             round(price, 2),
            "smart_money_score": total_score,
            "confidence":        confidence,
            "signal":            signal,
            "direction":         direction,
            "risk_rating":       risk,
            "win_rate":          win_rate,
            "avg_5d_return":     avg_5d,
            "occurrences":       occurrences,
            "expected_move_low":  em_low,
            "expected_move_high": em_high,
            "rvol":              round(rvol, 2),
            "thesis":            thesis,
            "options_summary":   options_summary,
            "score_breakdown": {
                "call_sweep":      round(s_call),
                "volume_oi":       round(s_vol),
                "ask_aggression":  round(s_ask),
                "dark_pool":       round(s_dp),
                "sector_strength": round(s_sector),
                "historical":      round(s_hist),
            },
        }
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# SCAN  (parallel options fetch + price data fetch)
# ─────────────────────────────────────────────────────────────────────────────

def scan_smart_money(tickers: list) -> dict:
    from scanner import fetch_stock_data

    # SPY 1-month return for relative strength
    spy_ret = 0.0
    try:
        spy_df = fetch_stock_data("SPY", period="35d")
        if spy_df is not None and len(spy_df) > 21:
            sc      = spy_df["Close"].squeeze().astype(float).dropna()
            spy_ret = _f((sc.iloc[-1] - sc.iloc[-21]) / sc.iloc[-21])
    except Exception:
        pass

    # Parallel options + price fetch
    price_data:   dict[str, object] = {}
    options_data: dict[str, dict]   = {}

    def _fetch_ticker(t):
        df   = None
        opts = {}
        try:
            df = fetch_stock_data(t, period="1y")
        except Exception:
            pass
        try:
            opts = fetch_options_data(t)
        except Exception:
            pass
        return t, df, opts

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_fetch_ticker, t): t for t in tickers}
        for fut in as_completed(futures):
            try:
                t, df, opts = fut.result()
                price_data[t]   = df
                options_data[t] = opts
            except Exception:
                pass

    results = []
    for ticker in tickers:
        try:
            df   = price_data.get(ticker)
            opts = options_data.get(ticker, {})
            if df is None or df.empty:
                continue
            result = compute_smart_money(ticker, df, spy_ret, opts)
            if result:
                results.append(result)
        except Exception:
            continue

    results.sort(key=lambda x: x["smart_money_score"], reverse=True)
    return {
        "leaderboard": results,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "data_source": "yfinance real options chain (15-min delayed)",
    }
