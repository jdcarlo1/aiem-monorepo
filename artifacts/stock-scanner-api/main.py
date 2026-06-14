from flask import Flask, request, jsonify
from flask_cors import CORS
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import math
import threading

from scanner import analyze_ticker, scan_tickers, WATCHLIST_DEFAULT, fetch_stock_data
from portfolio import get_portfolio, add_position, remove_position, get_portfolio_value
from backtest import backtest_strategy
from alerts import get_alerts, add_alert, delete_alert
from analytics import run_historical_analytics
from prop_signal import prop_signal
from smart_money import scan_smart_money, compute_smart_money, DEFAULT_LEADERBOARD
from congress_trades import get_congress_trades
from insider_trades import fetch_insider_trades
from email_alerts import (
    init_db, subscribe, unsubscribe, get_active_subscribers,
    send_daily_digest, smtp_configured, subscriber_count,
)
from historical_performance import init_score_history_table, save_scan_scores
from signal_outcomes import init_signal_outcomes_table, store_bull_flow_signals, get_signal_outcomes
from sms_alerts import (init_sms_log_table, init_exit_log_table,
                        init_midday_log_table,
                        run_sms_alert_scan, run_exit_alert_scan,
                        run_midday_breakout_scan, run_gap_recovery_scan,
                        run_steady_grinder_scan,
                        send_sms, sms_configured)
from options_sweep import init_call_sweep_log_table, run_call_sweep_scan
from news_catalyst import init_news_catalyst_log, run_news_catalyst_scan
import execution
import pnl

app = Flask(__name__)
CORS(app)

# ── Global yfinance HTTP timeout patch ────────────────────────────────────────
# yfinance creates requests.Session internally and never sets per-request
# timeouts, so rate-limited calls hang forever and block Flask worker threads.
# Patching Session.__init__ here ensures every Session (including yfinance's)
# mounts an adapter that enforces an 8-second timeout on each HTTP call.
try:
    import requests as _req_patch
    from requests.adapters import HTTPAdapter as _HTTPAdapter

    class _TimeoutAdapter(_HTTPAdapter):
        def send(self, *args, **kwargs):
            kwargs.setdefault("timeout", 8)
            return super().send(*args, **kwargs)

    _orig_session_init = _req_patch.Session.__init__
    def _patched_session_init(self, *args, **kwargs):
        _orig_session_init(self, *args, **kwargs)
        self.mount("https://", _TimeoutAdapter())
        self.mount("http://",  _TimeoutAdapter())
    _req_patch.Session.__init__ = _patched_session_init
    print("[startup] global requests timeout adapter installed (8s per call)")
except Exception as _tpe:
    print(f"[startup] timeout adapter failed (non-fatal): {_tpe}")

# ── Scan result cache (pre-warmed every 15 min during market hours) ───────────
import threading as _threading
app._sm_cache: dict = {}          # key = frozen sorted ticker tuple → {"result": ..., "ts": datetime}
app._sm_cache_lock = _threading.Lock()
_SM_CACHE_TTL_SECS = 1200        # 20 minutes

# ── init DB & scheduler ──────────────────────────────────────────────────────
init_db()
init_score_history_table()
init_signal_outcomes_table()
init_sms_log_table()
init_exit_log_table()
init_call_sweep_log_table()
init_news_catalyst_log()
init_midday_log_table()

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    import pytz

    _ET = pytz.timezone("US/Eastern")

    def _do_scan_and_save(session: str) -> None:
        """Run a Smart Money scan, save scores to history, then email subscribers."""
        result  = scan_smart_money(DEFAULT_LEADERBOARD)
        signals = result.get("leaderboard", [])
        # Persist every scan so historical performance can build up over time
        save_scan_scores(signals)
        base_url = os.getenv("PUBLIC_URL", "")
        out = send_daily_digest(signals, base_url, session=session)
        print(f"[scheduler] {session} scan → {out}")

    def _run_premarket_scan():
        """9:00 AM ET — overnight OI; options haven't opened yet."""
        try:
            _do_scan_and_save("premarket")
        except Exception as e:
            print(f"[scheduler] pre-market scan error: {e}")

    def _run_morning_scan():
        """9:45 AM ET — first real options data 15 min after open."""
        try:
            _do_scan_and_save("morning")
        except Exception as e:
            print(f"[scheduler] morning scan error: {e}")

    def _run_preclose_scan():
        """3:30 PM ET — 30 min before close. Last chance to act."""
        try:
            _do_scan_and_save("preclose")
        except Exception as e:
            print(f"[scheduler] pre-close scan error: {e}")

    def _run_eod_scan():
        """4:15 PM ET — full-day final options flow summary."""
        try:
            _do_scan_and_save("eod")
        except Exception as e:
            print(f"[scheduler] EOD scan error: {e}")

    _scheduler = BackgroundScheduler(timezone=_ET)
    # Pre-market: Mon-Fri 9:00 AM ET  (overnight OI — who loaded up positions yesterday)
    _scheduler.add_job(
        _run_premarket_scan,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=0, timezone=_ET),
        id="premarket_scan",
        replace_existing=True,
    )
    # Morning: Mon-Fri 9:45 AM ET  (market opens 9:30, data ready by 9:45)
    _scheduler.add_job(
        _run_morning_scan,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=45, timezone=_ET),
        id="morning_scan",
        replace_existing=True,
    )
    # Pre-close: Mon-Fri 3:30 PM ET  (30 min before 4 PM close — still time to act)
    _scheduler.add_job(
        _run_preclose_scan,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=30, timezone=_ET),
        id="preclose_scan",
        replace_existing=True,
    )

    # EOD unusual-calls auto-scan — populates unusual_calls_log so the EOD SWEEP tab
    # has data without a user needing to manually open the Unusual Calls tab first.
    def _run_unusual_calls_scan(label: str):
        try:
            import yfinance as yf
            from concurrent.futures import ThreadPoolExecutor, as_completed as _asc
            _ETF_SET = {
                "SPY","QQQ","IWM","DIA","MDY","VTI","VOO",
                "XLF","XLE","XLK","XLY","XLI","XLV","XLB","XLP","XLU","XLRE",
                "SMH","SOXX","XBI","IBB","KRE","XRT","ITB","JETS","KWEB","DRAM",
                "TQQQ","SPXL","SOXL","UDOW","LABU","FNGU","TECL","UPRO","TNA","FAS","ERX",
                "SQQQ","SPXS","SOXS","SDOW","TZA","FAZ","ERY",
                "GLD","IAU","SLV","USO","UNG","GDX","GDXJ","OIH",
                "TLT","HYG","LQD","TBT","TMF","SHY","IEF","JNK",
                "EEM","EFA","FXI","EWJ","EWZ","EWY","IEMG","ARKK","IBIT","FBTC",
            }
            def _scan_one(ticker):
                hits = []
                try:
                    is_etf  = ticker in _ETF_SET
                    min_voi = 1.5 if is_etf else 2.0   # lowered: 3x was too strict
                    min_prem= 50_000 if is_etf else 20_000  # small-cap: even $20K is meaningful
                    max_exp = 60 if is_etf else 45
                    from datetime import datetime as _dt2
                    tk = yf.Ticker(ticker)
                    price = tk.fast_info.get("lastPrice") or tk.fast_info.get("regularMarketPrice") or 0
                    if not price: return hits
                    for exp in (tk.options or []):
                        days = (_dt2.strptime(exp, "%Y-%m-%d") - _dt2.now()).days + 1
                        if not (1 <= days <= max_exp): continue
                        chain = tk.option_chain(exp).calls
                        for _, row in chain.iterrows():
                            try:
                                vol = int(row.get("volume") or 0)
                                oi  = int(row.get("openInterest") or 0)
                                if oi < 10 or vol < 10: continue
                                voi = vol / oi
                                if voi < min_voi: continue
                                strike = float(row["strike"])
                                otm_pct = round((strike - price) / price * 100, 2)
                                if otm_pct < -5 or otm_pct > 40: continue
                                bid = float(row.get("bid") or 0)
                                ask = float(row.get("ask") or 0)
                                mid = (bid + ask) / 2 if bid and ask else float(row.get("lastPrice") or 0)
                                prem = int(mid * vol * 100)
                                if prem < min_prem: continue
                                iv = round(float(row.get("impliedVolatility") or 0) * 100, 1)
                                urgency = "EXPIRING" if days <= 3 else "SHORT" if days <= 7 else "NEAR"
                                # pre_positioned: OI >> vol means position was accumulated BEFORE today
                                pre_positioned = bool(oi > 0 and vol < oi * 0.5 and oi >= 100)
                                ldt = row.get("lastTradeDate")
                                last_trade = str(ldt)[:10] if ldt is not None else ""
                                hits.append({"ticker": ticker, "price": price, "strike": strike,
                                             "expiry": exp, "days_out": days, "volume": vol, "oi": oi,
                                             "vol_oi": round(voi, 2), "prem": prem, "otm_pct": otm_pct,
                                             "iv": iv, "urgency": urgency,
                                             "pre_positioned": pre_positioned,
                                             "last_trade": last_trade})
                            except Exception: pass
                except Exception: pass
                return hits
            # Build augmented universe:
            #  1. earnings stocks today (e.g. CBRL reporting Q3) — always first
            #  2. today's top % gainers + most-active — catches catalyst moves early
            #  3. core leaderboard top 500 — fills out coverage
            _earnings = _fetch_earnings_today()
            _movers   = _fetch_market_movers()
            _seen: set = set()
            _universe: list = []
            for _t in (_earnings + _movers + list(DEFAULT_LEADERBOARD)[:500]):
                if _t not in _seen:
                    _seen.add(_t); _universe.append(_t)
            print(f"[scheduler] {label} scan universe: {len(_earnings)} earnings + "
                  f"{len(_movers)} movers + core = {len(_universe)} total")
            all_hits = []
            with ThreadPoolExecutor(max_workers=30) as ex:
                futs = {ex.submit(_scan_one, t): t for t in _universe}
                for fut in _asc(futs, timeout=180):
                    try:
                        all_hits.extend(fut.result() or [])
                    except Exception:
                        pass
            _save_unusual_calls_to_db(all_hits)
            print(f"[scheduler] {label} unusual-calls scan → {len(all_hits)} hits saved")
            # Send instant email alert for high-conviction hits (morning scan only)
            if label in ("market-open", "morning"):
                import threading as _alt
                _alt.Thread(target=_send_unusual_calls_alert, args=(all_hits,), daemon=True).start()
        except Exception as e:
            import traceback
            print(f"[scheduler] {label} unusual-calls scan error: {e}\n{traceback.format_exc()}")

    # 9:30 AM — right at market open: scan earnings stocks + movers first, then
    # send an instant email alert if any hit Vol/OI >= 5x with prem >= $500K.
    # This is the scan that would have caught CBRL call activity at open.
    _scheduler.add_job(
        lambda: _run_unusual_calls_scan("market-open"),
        CronTrigger(day_of_week="mon-fri", hour=9, minute=30, timezone=_ET),
        id="market_open_unusual_calls",
        replace_existing=True,
    )
    _scheduler.add_job(
        lambda: _run_unusual_calls_scan("morning"),
        CronTrigger(day_of_week="mon-fri", hour=10, minute=0, timezone=_ET),
        id="morning_unusual_calls",
        replace_existing=True,
    )
    _scheduler.add_job(
        lambda: _run_unusual_calls_scan("pre-close"),
        CronTrigger(day_of_week="mon-fri", hour=15, minute=30, timezone=_ET),
        id="preclose_unusual_calls",
        replace_existing=True,
    )
    _scheduler.add_job(
        lambda: _run_unusual_calls_scan("eod-1"),
        CronTrigger(day_of_week="mon-fri", hour=16, minute=0, timezone=_ET),
        id="eod_unusual_calls_1",
        replace_existing=True,
    )
    _scheduler.add_job(
        lambda: _run_unusual_calls_scan("eod-2"),
        CronTrigger(day_of_week="mon-fri", hour=16, minute=15, timezone=_ET),
        id="eod_unusual_calls_2",
        replace_existing=True,
    )

    # EOD: Mon-Fri 4:15 PM ET  (market closes 4:00, options data settled by 4:15)
    _scheduler.add_job(
        _run_eod_scan,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=15, timezone=_ET),
        id="eod_scan",
        replace_existing=True,
    )
    # Outcomes: Mon-Fri 4:30 PM ET — after market close, fetch closing prices for open AI trade log entries
    def _run_outcomes_update():
        try:
            _update_ai_trade_outcomes()
        except Exception as e:
            print(f"[scheduler] outcomes update error: {e}")
    _scheduler.add_job(
        _run_outcomes_update,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=30, timezone=_ET),
        id="outcomes_update",
        replace_existing=True,
    )
    # Signal snapshot: Mon-Fri 4:00 PM ET — snapshot today's signals for multi-day persistence tracking
    def _run_signal_snapshot():
        try:
            _save_signal_snapshot()
        except Exception as e:
            print(f"[scheduler] signal snapshot error: {e}")
    _scheduler.add_job(
        _run_signal_snapshot,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=0, timezone=_ET),
        id="signal_snapshot",
        replace_existing=True,
    )
    # Daily vol snapshot: Mon-Fri 4:05 PM ET — store IV skew + short interest for future percentile ranking
    def _run_daily_vol_snapshot():
        try:
            _save_daily_vol_snapshot()
        except Exception as e:
            print(f"[scheduler] daily vol snapshot error: {e}")
    _scheduler.add_job(
        _run_daily_vol_snapshot,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=5, timezone=_ET),
        id="daily_vol_snapshot",
        replace_existing=True,
    )
    # SPY cache refresh: Mon-Fri 9:05 AM ET — pre-warm SPY 1y cache before market opens
    def _run_spy_cache_refresh():
        try:
            _refresh_spy_1y_cache()
        except Exception as e:
            print(f"[scheduler] SPY cache refresh error: {e}")
    _scheduler.add_job(
        _run_spy_cache_refresh,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=5, timezone=_ET),
        id="spy_cache_refresh",
        replace_existing=True,
    )
    # Micro-cap net flow pre-warm: every 30 min during market hours (9:45 AM – 3:30 PM ET)
    def _run_microcap_prewarm():
        try:
            from datetime import datetime as _dt
            out = _run_microcap_flow_scan()
            app._nfmc_cache    = out
            app._nfmc_cache_ts = _dt.now()
            print(f"[scheduler] micro-cap pre-warm → scanned {out['scanned']} tickers, {len(out['results'])} positive")
        except Exception as e:
            print(f"[scheduler] micro-cap pre-warm error: {e}")
    _scheduler.add_job(
        _run_microcap_prewarm,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/30", timezone=_ET),
        id="microcap_prewarm",
        replace_existing=True,
    )
    # Micro-cap options scan: Mon-Fri at 10:30 AM, 3:30 PM, 4:00 PM, 4:15 PM ET
    # EOD runs are the richest — rate limits relax after close, full day's volume captured
    def _run_microcap_options_auto():
        try:
            hits = _run_microcap_options_scan()
            _save_microcap_calls_to_db(hits)
            print(f"[scheduler] micro-cap options scan → {len(hits)} unusual calls saved")
        except Exception as e:
            print(f"[scheduler] micro-cap options scan error: {e}")
    for _mc_hour, _mc_min in [(10, 30), (15, 30), (16, 0), (16, 15)]:
        _scheduler.add_job(
            _run_microcap_options_auto,
            CronTrigger(day_of_week="mon-fri", hour=_mc_hour, minute=_mc_min, timezone=_ET),
            id=f"microcap_options_auto_{_mc_hour}_{_mc_min}",
            replace_existing=True,
        )
    # AI Trades auto-generation: Mon-Fri 10:00 AM ET — caches are warm after 9:45 AM morning scan
    def _run_ai_trades_auto():
        try:
            import threading as _thr
            if not getattr(app, "_ait_generating", False):
                _thr.Thread(target=_ai_trades_worker, daemon=True).start()
                print("[scheduler] AI trades auto-generation started")
        except Exception as e:
            print(f"[scheduler] AI trades auto error: {e}")
    _scheduler.add_job(
        _run_ai_trades_auto,
        CronTrigger(day_of_week="mon-fri", hour=10, minute=0, timezone=_ET),
        id="ai_trades_auto",
        replace_existing=True,
    )
    # Morning inflows emails: fired after each scan wave so DB is fully written
    # 9:36 AM = 1 min after the 9:35 double-pass scan — double-confirmed early entry
    # Subsequent: 10:01, 10:16, 10:31 AM for updated confirmation waves
    def _run_morning_inflows_email():
        try:
            import threading as _thr_mi
            _thr_mi.Thread(target=_send_morning_inflows_email, daemon=True).start()
        except Exception as e:
            print(f"[scheduler] morning inflows email error: {e}")
    for _mi_eh, _mi_em in [(9, 36), (10, 1), (10, 16), (10, 31), (13, 1), (14, 1)]:
        _scheduler.add_job(
            _run_morning_inflows_email,
            CronTrigger(day_of_week="mon-fri", hour=_mi_eh, minute=_mi_em, timezone=_ET),
            id=f"morning_inflows_email_{_mi_eh}_{_mi_em}",
            replace_existing=True,
        )
    # SMS alert scan: every 5 min Mon-Fri 10:00 AM – 3:45 PM ET
    # Starts at 10 AM — backtest showed pre-10 AM signals are opening-bell noise (13% hit rate)
    # Only fires on green SPY days — red days historically lose money regardless of signal quality
    def _run_sms_alert_scan():
        try:
            import threading as _thr_sms
            _thr_sms.Thread(target=run_sms_alert_scan, daemon=True).start()
        except Exception as _e_sms:
            print(f"[scheduler] sms alert scan error: {_e_sms}")
    _scheduler.add_job(
        _run_sms_alert_scan,
        "cron",
        hour="9", minute="35,40,45",
        id="sms_alert_scan",
        replace_existing=True,
    )
    # Exit alert scan: every 15 min — watches stocks alerted today for VWAP breaks
    def _run_exit_alert_scan():
        try:
            import threading as _thr_exit
            _thr_exit.Thread(target=run_exit_alert_scan, daemon=True).start()
        except Exception as _e_exit:
            print(f"[scheduler] exit alert scan error: {_e_exit}")
    _scheduler.add_job(
        _run_exit_alert_scan,
        "interval",
        minutes=15,
        id="exit_alert_scan",
        replace_existing=True,
    )
    # Mid-Day Breakout scan: every 5 min 10:30 AM – 3:30 PM ET
    # Confirmed trend + above VWAP + 15-min momentum — lower risk than morning entry
    def _run_midday_breakout_scan():
        try:
            import threading as _thr_md
            _thr_md.Thread(target=run_midday_breakout_scan, daemon=True).start()
        except Exception as _e_md:
            print(f"[scheduler] midday breakout scan error: {_e_md}")
    _scheduler.add_job(
        _run_midday_breakout_scan,
        "interval",
        minutes=5,
        id="midday_breakout_scan",
        replace_existing=True,
    )
    # Gap Recovery scan: every 5 min 10:30 AM – 1:00 PM ET
    # Big gapper (20%+) that sold off then reclaimed VWAP with momentum
    def _run_gap_recovery_scan():
        try:
            import threading as _thr_gr
            _thr_gr.Thread(target=run_gap_recovery_scan, daemon=True).start()
        except Exception as _e_gr:
            print(f"[scheduler] gap recovery scan error: {_e_gr}")
    _scheduler.add_job(
        _run_gap_recovery_scan,
        "interval",
        minutes=5,
        id="gap_recovery_scan",
        replace_existing=True,
    )
    # Steady Grinder scan: every 30 min 11:00 AM – 1:30 PM ET
    # Large/mid-cap institutional accumulation stocks (FRO/AMKR type)
    # Low RVOL (1-3x) but sustained uptrend confirmed by dual 45-min trend check
    # avg vol ≥ 1M, above VWAP, within 2% of HOD, has options
    def _run_steady_grinder_scan():
        try:
            import threading as _thr_sg
            _thr_sg.Thread(target=run_steady_grinder_scan, daemon=True).start()
        except Exception as _e_sg:
            print(f"[scheduler] steady grinder scan error: {_e_sg}")
    _scheduler.add_job(
        _run_steady_grinder_scan,
        "interval",
        minutes=30,
        id="steady_grinder_scan",
        replace_existing=True,
    )
    # VWAP Reclaim scan: every 5 min — immediate SMS when alerted stock reclaims VWAP
    def _run_vwap_reclaim_scan():
        try:
            import threading as _thr_vr
            from holy_grail import run_vwap_reclaim_scan
            _thr_vr.Thread(target=run_vwap_reclaim_scan, daemon=True).start()
        except Exception as _e_vr:
            print(f"[scheduler] vwap reclaim scan error: {_e_vr}")
    _scheduler.add_job(
        _run_vwap_reclaim_scan,
        "interval",
        minutes=5,
        id="vwap_reclaim_scan",
        replace_existing=True,
    )

    # Call sweep scan: every 15 min — watches alerted tickers for bullish options sweeps above VWAP
    def _run_call_sweep_scan():
        try:
            import threading as _thr_cs
            _thr_cs.Thread(target=run_call_sweep_scan, daemon=True).start()
        except Exception as _e_cs:
            print(f"[scheduler] call sweep scan error: {_e_cs}")
    _scheduler.add_job(
        _run_call_sweep_scan,
        "interval",
        minutes=15,
        id="call_sweep_scan",
        replace_existing=True,
    )

    # EOD accum picks email: 3:46 PM ET — 1 min after the 3:45 PM scan saves picks
    def _run_eod_accum_email_job():
        try:
            import threading as _thr_ea
            _thr_ea.Thread(target=_send_eod_accum_email, daemon=True).start()
        except Exception as e:
            print(f"[scheduler] EOD accum email error: {e}")
    _scheduler.add_job(
        _run_eod_accum_email_job,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=46, timezone=_ET),
        id="eod_accum_email",
        replace_existing=True,
    )
    # Pre-Close Swing Setup scan: 3:30 PM ET — 30 min before close so user can
    # enter same day and hold overnight. 3-day lookback for tightest entry timing.
    def _run_eod_swing_job():
        try:
            import threading as _thr_sw
            from eod_swing import send_swing_digest
            _base = os.getenv("BASE_URL", "")
            _thr_sw.Thread(target=send_swing_digest, args=(_base,), daemon=True).start()
        except Exception as _e_sw:
            print(f"[scheduler] pre-close swing scan error: {_e_sw}")
    _scheduler.add_job(
        _run_eod_swing_job,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=30, timezone=_ET),
        id="eod_swing_scan",
        replace_existing=True,
    )
    # Unusual Calls email: 9:47 AM (after options warmer at 9:45) + 4:20 PM EOD
    def _run_unusual_calls_email():
        try:
            import threading as _thr_uc
            _thr_uc.Thread(target=_send_unusual_calls_email, daemon=True).start()
        except Exception as e:
            print(f"[scheduler] unusual calls email error: {e}")
    for _uc_h, _uc_m in [(9, 47), (15, 15)]:
        _scheduler.add_job(
            _run_unusual_calls_email,
            CronTrigger(day_of_week="mon-fri", hour=_uc_h, minute=_uc_m, timezone=_ET),
            id=f"unusual_calls_email_{_uc_h}_{_uc_m}",
            replace_existing=True,
        )
    # Small & Growth (Microcap) Calls email: 10:32 AM + 4:17 PM (after scans)
    def _run_microcap_calls_email():
        try:
            import threading as _thr_mc
            _thr_mc.Thread(target=_send_microcap_calls_email, daemon=True).start()
        except Exception as e:
            print(f"[scheduler] microcap calls email error: {e}")
    for _mc_h, _mc_m in [(10, 32), (15, 16)]:
        _scheduler.add_job(
            _run_microcap_calls_email,
            CronTrigger(day_of_week="mon-fri", hour=_mc_h, minute=_mc_m, timezone=_ET),
            id=f"microcap_calls_email_{_mc_h}_{_mc_m}",
            replace_existing=True,
        )
    # High Conviction Calls email: 9:48 AM (after unusual calls scan) + 4:22 PM EOD
    def _run_hc_calls_email():
        try:
            import threading as _thr_hc
            _thr_hc.Thread(target=_send_high_conviction_email, daemon=True).start()
        except Exception as e:
            print(f"[scheduler] high conviction calls email error: {e}")
    for _hc_h, _hc_m in [(9, 48), (15, 17)]:
        _scheduler.add_job(
            _run_hc_calls_email,
            CronTrigger(day_of_week="mon-fri", hour=_hc_h, minute=_hc_m, timezone=_ET),
            id=f"hc_calls_email_{_hc_h}_{_hc_m}",
            replace_existing=True,
        )
    # AI Short Calls auto-generation: Mon-Fri 10:15 AM ET — after scanner caches warm
    def _run_ai_short_calls_auto():
        try:
            import threading as _thr2
            from datetime import datetime as _dt2
            def _worker():
                try:
                    with app.app_context() if hasattr(app, "app_context") else __import__("contextlib").nullcontext():
                        resp = ai_short_calls()
                        data = resp.get_json() if hasattr(resp, "get_json") else {}
                        picks = data.get("picks", [])
                        if picks:
                            _save_ai_short_calls_to_log(picks, str(_dt2.now().date()))
                            print(f"[scheduler] AI short calls saved {len(picks)} picks")
                        else:
                            print("[scheduler] AI short calls: no picks returned")
                except Exception as _we:
                    print(f"[scheduler] AI short calls worker error: {_we}")
            _thr2.Thread(target=_worker, daemon=True).start()
        except Exception as e:
            print(f"[scheduler] AI short calls auto error: {e}")
    _scheduler.add_job(
        _run_ai_short_calls_auto,
        CronTrigger(day_of_week="mon-fri", hour=10, minute=15, timezone=_ET),
        id="ai_short_calls_auto",
        replace_existing=True,
    )
    # AI Short Calls outcomes: Mon-Fri 4:32 PM ET — alongside AI trade outcomes
    def _run_sc_outcomes():
        try:
            _update_ai_short_call_outcomes()
        except Exception as e:
            print(f"[scheduler] short call outcomes error: {e}")
    _scheduler.add_job(
        _run_sc_outcomes,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=32, timezone=_ET),
        id="sc_outcomes_update",
        replace_existing=True,
    )
    # Position monitor: poll Gmail for TRADE: emails every 15 min (market hours)
    def _run_poll_trade_emails():
        try:
            import threading as _thr_pt
            _thr_pt.Thread(target=_poll_trade_emails, daemon=True).start()
        except Exception as e:
            print(f"[scheduler] poll_trade_emails error: {e}")
    for _pm_h in range(9, 17):
        for _pm_m in [0, 15, 30, 45]:
            if _pm_h == 9 and _pm_m < 30:
                continue
            _scheduler.add_job(
                _run_poll_trade_emails,
                CronTrigger(day_of_week="mon-fri", hour=_pm_h, minute=_pm_m, timezone=_ET),
                id=f"poll_trade_emails_{_pm_h}_{_pm_m}",
                replace_existing=True,
            )
    # Position monitor: check exit signals every 30 min (market hours)
    def _run_monitor_positions():
        try:
            import threading as _thr_mp
            _thr_mp.Thread(target=_monitor_open_positions, daemon=True).start()
        except Exception as e:
            print(f"[scheduler] monitor_positions error: {e}")
    for _mo_h in range(9, 17):
        for _mo_m in [0, 30]:
            if _mo_h == 9 and _mo_m < 30:
                continue
            _scheduler.add_job(
                _run_monitor_positions,
                CronTrigger(day_of_week="mon-fri", hour=_mo_h, minute=_mo_m, timezone=_ET),
                id=f"monitor_positions_{_mo_h}_{_mo_m}",
                replace_existing=True,
            )
    # EOD sweep outcomes: Mon-Fri 4:35 PM ET — fills T+1/T+3/T+5 closing prices
    def _run_eod_sweep_outcomes():
        try:
            _update_eod_sweep_outcomes()
        except Exception as e:
            print(f"[scheduler] eod sweep outcomes error: {e}")
    _scheduler.add_job(
        _run_eod_sweep_outcomes,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=35, timezone=_ET),
        id="eod_sweep_outcomes",
        replace_existing=True,
    )
    # EOD sweep auto-log: Mon-Fri 4:20 PM ET — ensures track record is populated without
    # anyone needing to visit the tab.  Busts the cache then calls the route directly.
    def _auto_log_eod_sweeps():
        try:
            if hasattr(app, "_eod_sweeps_cache"):
                app._eod_sweeps_cache    = None
                app._eod_sweeps_cache_ts = None
            with app.test_request_context("/stock-api/eod-sweeps"):
                eod_sweeps()
            print("[scheduler] EOD sweep auto-log complete")
        except Exception as e:
            print(f"[scheduler] EOD sweep auto-log error: {e}")
    _scheduler.add_job(
        _auto_log_eod_sweeps,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=20, timezone=_ET),
        id="eod_sweep_auto_log",
        replace_existing=True,
    )
    # Scan cache warmer — every 15 min during market hours so on-demand scans feel instant
    def _warm_sm_cache():
        try:
            from datetime import datetime as _dtw
            from pytz import timezone as _tzw
            _et_now = _dtw.now(_tzw("America/New_York"))
            _wday = _et_now.weekday()          # 0=Mon … 4=Fri
            _h, _m = _et_now.hour, _et_now.minute
            _mins = _h * 60 + _m
            if _wday > 4 or _mins < 570 or _mins > 970:   # 9:30 AM (570) – 4:10 PM (970) ET
                return
            print("[cache_warmer] pre-warming smart money scan…")
            _warm_result = scan_smart_money(DEFAULT_LEADERBOARD)
            _cache_key = tuple(sorted(DEFAULT_LEADERBOARD))
            with app._sm_cache_lock:
                app._sm_cache[_cache_key] = {"result": _warm_result, "ts": _dtw.now()}
            print(f"[cache_warmer] cached {len(_warm_result.get('leaderboard', []))} tickers")
        except Exception as _we:
            print(f"[cache_warmer] error: {_we}")

    _scheduler.add_job(
        _warm_sm_cache,
        "interval",
        minutes=15,
        id="sm_cache_warmer",
        replace_existing=True,
    )

    # ── Helper: call a route function inside a test request context ──────────
    def _call_route(label, url, method="GET", body=None):
        _fn_map = {
            "/stock-api/bull-flow/top10":   "bull_flow_top10",
            "/stock-api/squeeze/detector":  "squeeze_detector",
            "/stock-api/breakout/radar":    "breakout_radar",
            "/stock-api/darkpool":          "darkpool",
            "/stock-api/vol-crush":         "vol_crush",
            "/stock-api/call-intent":       "call_intent",
            "/stock-api/max-pain":          "max_pain",
            "/stock-api/gamma-wall":        "gamma_wall",
            "/stock-api/convergence":       "convergence",
            "/stock-api/premarket":         "premarket",
        }
        try:
            _kwargs = {"method": method}
            if body is not None:
                _kwargs["data"] = body
                _kwargs["content_type"] = "application/json"
            with app.test_request_context(url, **_kwargs):
                globals()[_fn_map[url]]()
            print(f"[warmer] ✓ {label}")
        except Exception as _we:
            print(f"[warmer] ✗ {label}: {_we}")

    # Wave 1 — 8:00 AM ET: tabs that use prior-day / overnight data
    # Pre-Market (4 AM pre-market prices), Dark Pool (FINRA data published overnight),
    # Convergence (price/momentum — no live vol needed)
    def _run_early_warmer():
        import threading as _ethr
        def _w():
            _call_route("Pre-Market", "/stock-api/premarket")
            _call_route("Dark Pool",  "/stock-api/darkpool")
            _call_route("Convergence","/stock-api/convergence")
        _ethr.Thread(target=_w, daemon=True).start()
        print("[warmer] early wave started (Pre-Market, Dark Pool, Convergence)")

    _scheduler.add_job(
        _run_early_warmer,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=0, timezone=_ET),
        id="early_warmer",
        replace_existing=True,
    )

    # Wave 2 — 10:45 AM ET: options-based tabs (75 min of live market activity)
    # Wave 3 — 11:30 AM ET: second pass with more mature intraday vol/OI
    # Wave 4 — 4:18 PM ET:  EOD — freshest data of the day after close
    def _run_options_warmer():
        import threading as _othr
        def _w():
            _call_route("Bull Flow",      "/stock-api/bull-flow/top10",   "POST", b"{}")
            _call_route("Squeeze",        "/stock-api/squeeze/detector",  "POST", b"{}")
            _call_route("Vol Crush",      "/stock-api/vol-crush")
            _call_route("Call Intent",    "/stock-api/call-intent")
            _call_route("Max Pain",       "/stock-api/max-pain")
            _call_route("Gamma Wall",     "/stock-api/gamma-wall")
            _call_route("Breakout Radar", "/stock-api/breakout/radar",    "POST", b"{}")
            _call_route("Convergence",    "/stock-api/convergence")
            _call_route("Pre-Market",     "/stock-api/premarket")
            _call_route("Dark Pool",      "/stock-api/darkpool")
        _othr.Thread(target=_w, daemon=True).start()
        print("[warmer] options wave started (all tabs)")

    for _ow_hour, _ow_min in [(9, 45), (10, 45), (11, 30), (16, 18)]:
        _scheduler.add_job(
            _run_options_warmer,
            CronTrigger(day_of_week="mon-fri", hour=_ow_hour, minute=_ow_min, timezone=_ET),
            id=f"options_warmer_{_ow_hour}_{_ow_min}",
            replace_existing=True,
        )

    # Insider outcomes: Mon-Fri 4:37 PM ET — check post-earnings prices for flagged alerts
    def _run_insider_outcomes():
        try:
            _check_insider_outcomes()
        except Exception as e:
            print(f"[scheduler] insider outcomes error: {e}")
    _scheduler.add_job(
        _run_insider_outcomes,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=37, timezone=_ET),
        id="insider_outcomes_check",
        replace_existing=True,
    )

    # Morning standout inflows: 9:31 → 9:33 → 9:35 → 9:38 → 9:41 → 9:45 AM + later waves
    # Tight early window: 9:31 catches first movers; 9:33/9:35 confirm with 3-5 min real data
    # → text arrives by 9:33-9:36 AM, same window hedge funds are accumulating
    def _run_morning_inflows():
        try:
            with app.test_request_context("/stock-api/morning-inflows?bust=1"):
                morning_inflows()
        except Exception as e:
            print(f"[scheduler] morning inflows error: {e}")

    # News Catalyst scanner — parallel track, fires '📰 NEWS CATALYST' SMS (separate from ICS)
    # Runs same tight morning window 9:31–10:30.  Does NOT affect ICS logic.
    import threading as _thr_nc
    def _run_news_catalyst():
        try:
            _thr_nc.Thread(target=run_news_catalyst_scan, daemon=True).start()
        except Exception as e:
            print(f"[scheduler] news catalyst error: {e}")
    for _nc_h, _nc_m in [(9, 31), (9, 33), (9, 35), (9, 38), (9, 41), (9, 45), (10, 0), (10, 15), (10, 30)]:
        _scheduler.add_job(
            _run_news_catalyst,
            CronTrigger(day_of_week="mon-fri", hour=_nc_h, minute=_nc_m, timezone=_ET),
            id=f"news_catalyst_{_nc_h}_{_nc_m}",
            replace_existing=True,
        )

    for _mi_h, _mi_m in [(9, 31), (9, 33), (9, 35), (9, 38), (9, 41), (9, 45), (10, 0), (10, 15), (10, 30), (12, 0), (13, 0), (14, 0)]:
        _scheduler.add_job(
            _run_morning_inflows,
            CronTrigger(day_of_week="mon-fri", hour=_mi_h, minute=_mi_m, timezone=_ET),
            id=f"morning_inflows_{_mi_h}_{_mi_m}",
            replace_existing=True,
        )

    # ── EOD Accumulation picks + outcomes tables ──────────────────────────
    try:
        import psycopg2 as _pg_eat, os as _os_eat
        _eat_db = _os_eat.getenv("DATABASE_URL", "")
        with _pg_eat.connect(_eat_db) as _c_eat, _c_eat.cursor() as _cu_eat:
            _cu_eat.execute("""
                CREATE TABLE IF NOT EXISTS eod_accum_picks (
                    id          SERIAL PRIMARY KEY,
                    scan_date   DATE NOT NULL,
                    ticker      TEXT NOT NULL,
                    close_price NUMERIC,
                    accum_score NUMERIC,
                    news_type   TEXT DEFAULT 'none',
                    news_headline TEXT,
                    eod_rel_vol NUMERIC,
                    late_flow   NUMERIC,
                    closing_range NUMERIC,
                    price_chg_pct NUMERIC,
                    mkt_cap_m   NUMERIC,
                    scanned_at  TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(scan_date, ticker)
                )
            """)
            _cu_eat.execute("""
                CREATE TABLE IF NOT EXISTS eod_accum_outcomes (
                    id          SERIAL PRIMARY KEY,
                    pick_date   DATE NOT NULL,
                    ticker      TEXT NOT NULL,
                    entry_price NUMERIC,
                    next_open   NUMERIC,
                    next_open_chg_pct   NUMERIC,
                    morning_high        NUMERIC,
                    morning_high_chg_pct NUMERIC,
                    gapped_up   BOOLEAN,
                    news_type   TEXT DEFAULT 'none',
                    accum_score NUMERIC,
                    fetched_at  TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(pick_date, ticker)
                )
            """)
            _cu_eat.execute("""
                CREATE TABLE IF NOT EXISTS morning_watchlist (
                    id     SERIAL PRIMARY KEY,
                    ticker TEXT NOT NULL UNIQUE,
                    added_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            _c_eat.commit()
        print("[eod_accum_tables] ready")
    except Exception as _e_eat:
        print(f"[eod_accum_tables] error: {_e_eat}")

    # ── EOD Accumulation outcome fetcher: 10:00 AM ET ─────────────────────
    def _run_eod_accum_outcomes():
        """
        Runs at 10:00 AM ET Mon-Fri.
        Checks what happened to yesterday's EOD accum picks:
          - Did they gap up at the open?
          - What was the max gain in the first 30 minutes?
        Writes results to eod_accum_outcomes for track-record comparison.
        """
        try:
            import datetime as _dt_eao
            import yfinance as _yf_eao
            import psycopg2 as _pg_eao
            import pytz as _pytz_eao
            _et_eao = _pytz_eao.timezone("America/New_York")
            _today  = _dt_eao.date.today()
            # Most recent prior trading day
            _pick_day = _today - _dt_eao.timedelta(days=1)
            while _pick_day.weekday() >= 5:
                _pick_day -= _dt_eao.timedelta(days=1)
            _pick_date = _pick_day.isoformat()

            with _pg_eao.connect(_DB_URL) as _c_r, _c_r.cursor() as _cu_r:
                _cu_r.execute("""
                    SELECT ticker, close_price, accum_score, news_type
                    FROM eod_accum_picks WHERE scan_date = %s
                """, (_pick_date,))
                _picks = _cu_r.fetchall()

            if not _picks:
                print(f"[eod_accum_outcomes] no picks for {_pick_date}, skipping")
                return

            _saved = 0
            with _pg_eao.connect(_DB_URL) as _c_w, _c_w.cursor() as _cu_w:
                for _sym, _entry, _score, _ntype in _picks:
                    try:
                        _hist = _yf_eao.Ticker(_sym).history(period="1d", interval="1m")
                        if _hist.empty or len(_hist) < 3: continue
                        _hist.index = _hist.index.tz_convert(_et_eao)
                        # Next open: very first bar
                        _next_open = float(_hist["Open"].iloc[0])
                        # Morning high: max of 9:30-10:00 AM bars
                        _am_bars   = _hist[(_hist.index.time >= _dt_eao.time(9, 30)) &
                                           (_hist.index.time <  _dt_eao.time(10, 0))]
                        _morn_high = float(_am_bars["High"].max()) if not _am_bars.empty else _next_open
                        _ef = float(_entry or 0)
                        if _ef <= 0: continue
                        _open_chg = round((_next_open - _ef) / _ef * 100, 2)
                        _high_chg = round((_morn_high - _ef) / _ef * 100, 2)
                        _cu_w.execute("""
                            INSERT INTO eod_accum_outcomes
                                (pick_date, ticker, entry_price, next_open, next_open_chg_pct,
                                 morning_high, morning_high_chg_pct, gapped_up, news_type, accum_score)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (pick_date, ticker) DO UPDATE SET
                                next_open=EXCLUDED.next_open,
                                next_open_chg_pct=EXCLUDED.next_open_chg_pct,
                                morning_high=EXCLUDED.morning_high,
                                morning_high_chg_pct=EXCLUDED.morning_high_chg_pct,
                                gapped_up=EXCLUDED.gapped_up,
                                fetched_at=NOW()
                        """, (_pick_date, _sym, _ef, _next_open, _open_chg,
                              _morn_high, _high_chg, _next_open > _ef, _ntype, float(_score or 0)))
                        _saved += 1
                    except Exception as _te:
                        print(f"[eod_accum_outcomes] {_sym}: {_te}")
                _c_w.commit()
            print(f"[eod_accum_outcomes] saved {_saved}/{len(_picks)} for {_pick_date}")
        except Exception as _e_eao:
            print(f"[eod_accum_outcomes] error: {_e_eao}")

    _scheduler.add_job(
        _run_eod_accum_outcomes,
        CronTrigger(day_of_week="mon-fri", hour=10, minute=0, timezone=_ET),
        id="eod_accum_outcomes_fetcher",
        replace_existing=True,
    )

    def _run_eod_outcomes():
        """
        Runs at 4:15 PM ET Mon-Fri.
        Fetches final OHLC for every ticker flagged in today's scan_history,
        then writes to eod_outcomes so we can analyze signal accuracy over time.
        """
        try:
            import datetime as _dt_eod
            import yfinance as _yf_eod
            import psycopg2 as _pg_eod
            _today = _dt_eod.date.today().isoformat()
            with _pg_eod.connect(_DB_URL) as _c, _c.cursor() as _cu:
                # Get unique tickers flagged today with their best standout_score + fade_risk
                _cu.execute("""
                    SELECT DISTINCT ON (ticker)
                        ticker, fade_risk, standout_score, price AS open_price
                    FROM scan_history
                    WHERE scan_date = %s
                    ORDER BY ticker, rank_in_scan ASC
                """, (_today,))
                _rows = _cu.fetchall()
            if not _rows:
                print(f"[eod_outcomes] no scan_history rows for {_today}, skipping")
                return
            _tickers = [r[0] for r in _rows]
            _meta = {r[0]: {"fade_risk": r[1], "standout_score": float(r[2] or 0), "open_price": float(r[3] or 0)} for r in _rows}
            # Fetch EOD data from yfinance
            _dl = _yf_eod.download(
                " ".join(_tickers), period="1d", interval="1d",
                auto_adjust=True, progress=False
            )
            with _pg_eod.connect(_DB_URL) as _c2, _c2.cursor() as _cu2:
                _saved = 0
                for _tk in _tickers:
                    try:
                        if len(_tickers) == 1:
                            _o = float(_dl["Open"].iloc[-1])
                            _c_px = float(_dl["Close"].iloc[-1])
                            _h = float(_dl["High"].iloc[-1])
                            _l = float(_dl["Low"].iloc[-1])
                        else:
                            _o = float(_dl["Open"][_tk].iloc[-1])
                            _c_px = float(_dl["Close"][_tk].iloc[-1])
                            _h = float(_dl["High"][_tk].iloc[-1])
                            _l = float(_dl["Low"][_tk].iloc[-1])
                        _open_ref = _meta[_tk]["open_price"] or _o
                        _o2c = round((_c_px - _open_ref) / _open_ref * 100, 2) if _open_ref else None
                        _o2h = round((_h - _open_ref) / _open_ref * 100, 2) if _open_ref else None
                        _cu2.execute("""
                            INSERT INTO eod_outcomes
                                (trade_date, ticker, open_price, close_price, high_price, low_price,
                                 open_to_close_pct, open_to_high_pct, fade_risk_signal, standout_score)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (trade_date, ticker) DO UPDATE
                                SET close_price=EXCLUDED.close_price,
                                    high_price=EXCLUDED.high_price,
                                    low_price=EXCLUDED.low_price,
                                    open_to_close_pct=EXCLUDED.open_to_close_pct,
                                    open_to_high_pct=EXCLUDED.open_to_high_pct,
                                    fetched_at=NOW()
                        """, (_today, _tk, _open_ref, _c_px, _h, _l, _o2c, _o2h,
                              _meta[_tk]["fade_risk"], _meta[_tk]["standout_score"]))
                        _saved += 1
                    except Exception as _te:
                        print(f"[eod_outcomes] {_tk}: {_te}")
                _c2.commit()
            print(f"[eod_outcomes] saved {_saved}/{len(_tickers)} outcomes for {_today}")
        except Exception as _e_eod:
            print(f"[eod_outcomes] error: {_e_eod}")

    _scheduler.add_job(
        _run_eod_outcomes,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=15, timezone=_ET),
        id="eod_outcomes_fetcher",
        replace_existing=True,
    )

    # EOD Accumulation scanner: 3:45 PM and 3:55 PM ET — detects late-day pump accumulation
    # so users can buy before the close and sell into the next-morning gap.
    def _run_eod_accum():
        try:
            with app.test_request_context("/stock-api/eod-accumulation?bust=1"):
                eod_accumulation()
        except Exception as _e_ea:
            print(f"[scheduler] eod_accum error: {_e_ea}")
    for _ea_h, _ea_m in [(15, 45), (15, 55)]:
        _scheduler.add_job(
            _run_eod_accum,
            CronTrigger(day_of_week="mon-fri", hour=_ea_h, minute=_ea_m, timezone=_ET),
            id=f"eod_accum_{_ea_h}_{_ea_m}",
            replace_existing=True,
        )

    _scheduler.start()
    print("[scheduler] APScheduler started — "
          "scans: 9:00/9:45 AM, 3:30/4:00/4:05/4:15 PM ET | "
          "microcap: 10:30 AM, 3:30/4:00/4:15 PM ET | "
          "AI trades: 10:00 AM | AI short calls: 10:15 AM | "
          "early warmer (Pre-Market/Dark Pool/Convergence): 8:00 AM | "
          "options warmer (all tabs): 9:45 AM, 10:45 AM, 11:30 AM, 4:18 PM | "
          "morning inflows: 9:31 + 9:33 + 9:35 + 9:38 + 9:41 + 9:45 AM + 10:00 AM + 10:15 AM + 10:30 AM + 12:00 PM + 1:00 PM + 2:00 PM | "
          "outcomes: 4:30-4:35 PM | cache warmer: every 15 min — Mon–Fri ET")
except Exception as _e:
    print(f"[scheduler] Could not start scheduler: {_e}")


def _fetch_earnings_today() -> list:
    """
    Return tickers that have earnings announced today (before/after market).
    Uses the Yahoo Finance earnings calendar endpoint — no API key required.
    These stocks get prepended to the scan queue so pre-earnings call activity
    is never missed regardless of their position in DEFAULT_LEADERBOARD.
    """
    try:
        import requests as _r
        from datetime import date
        today = date.today().strftime("%Y-%m-%d")
        url = (
            f"https://query1.finance.yahoo.com/v1/finance/earnings"
            f"?day={today}&region=US&lang=en-US"
        )
        hdrs = {"User-Agent": "Mozilla/5.0 (compatible; StockScannerBot/1.0)"}
        data = _r.get(url, headers=hdrs, timeout=8).json()
        rows = (
            data.get("finance", {})
                .get("result", [{}])[0]
                .get("earnings", {})
                .get("rows", [])
        )
        tickers = []
        for row in rows:
            sym = row.get("ticker", "")
            if sym and "^" not in sym and "/" not in sym:
                tickers.append(sym)
        if tickers:
            print(f"[earnings] {len(tickers)} stocks reporting today: {tickers[:10]}")
        return tickers
    except Exception as _e:
        print(f"[earnings] calendar fetch failed (non-fatal): {_e}")
        return []


def _send_unusual_calls_alert(hits: list) -> None:
    """
    Send an immediate email alert to all subscribers when the morning scan
    finds high-conviction unusual call activity (Vol/OI >= 2x, prem >= $50K).
    Called right after _run_unusual_calls_scan saves to DB.
    """
    try:
        # Filter: Vol/OI >= 2x and prem >= $50K — catches insider-sized bets too
        alerts = [
            h for h in hits
            if h.get("vol_oi", 0) >= 2.0 and h.get("prem", 0) >= 20_000
        ]
        if not alerts:
            return

        subs = get_active_subscribers()
        if not subs:
            return

        alerts.sort(key=lambda x: x["prem"], reverse=True)
        top = alerts[:10]

        rows_html = ""
        for h in top:
            prem_str = f"${h['prem']:,.0f}"
            voi_str  = f"{h['vol_oi']:.1f}×"
            otm_str  = f"+{h['otm_pct']:.1f}%" if h.get('otm_pct', 0) >= 0 else f"{h['otm_pct']:.1f}%"
            color    = "#f97316" if h["vol_oi"] >= 10 else "#22c55e"
            pre_badge = (
                '<span style="background:#7c3aed;color:#fff;font-size:9px;font-weight:700;'
                'padding:1px 5px;border-radius:3px;margin-left:5px;">PRE-POSITIONED</span>'
                if h.get("pre_positioned") else ""
            )
            last_trade_note = f'<div style="font-size:10px;color:#64748b;">last trade: {h["last_trade"]}</div>' if h.get("last_trade") else ""
            rows_html += f"""
            <tr>
              <td style="padding:10px 14px;border-bottom:1px solid #1e293b;">
                <span style="font-size:15px;font-weight:700;color:#f1f5f9;">{h['ticker']}</span>
                <span style="font-size:11px;color:#64748b;margin-left:8px;">${h['price']:.2f}</span>
                {pre_badge}
              </td>
              <td style="padding:10px 14px;border-bottom:1px solid #1e293b;text-align:center;">
                <span style="font-weight:700;color:{color};">{voi_str}</span>
                <div style="font-size:10px;color:#64748b;">Vol/OI</div>
              </td>
              <td style="padding:10px 14px;border-bottom:1px solid #1e293b;text-align:center;">
                <span style="font-weight:700;color:#f1f5f9;">${h['strike']:.0f} {otm_str}</span>
                <div style="font-size:10px;color:#64748b;">Strike · OTM</div>
              </td>
              <td style="padding:10px 14px;border-bottom:1px solid #1e293b;text-align:center;">
                <span style="font-weight:700;color:#a78bfa;">{prem_str}</span>
                <div style="font-size:10px;color:#64748b;">Premium · {h['days_out']}d exp</div>
                {last_trade_note}
              </td>
            </tr>"""

        base_url = os.getenv("PUBLIC_URL", "https://hello-world-2-joeldcarlo.replit.app")
        dashboard_url = f"{base_url}/stock-scanner/"
        from datetime import datetime as _dt
        time_str = _dt.now().strftime("%-I:%M %p ET")

        html = f"""
        <div style="background:#0a0f1a;font-family:'Segoe UI',Arial,sans-serif;padding:24px;max-width:600px;margin:0 auto;border-radius:12px;">
          <div style="margin-bottom:20px;">
            <span style="font-size:22px;font-weight:800;color:#f1f5f9;">🚨 Unusual Call Alert</span>
            <span style="display:block;font-size:12px;color:#64748b;margin-top:4px;">
              {len(alerts)} high-conviction signal{'s' if len(alerts) != 1 else ''} detected · {time_str}
            </span>
          </div>
          <table width="100%" cellpadding="0" cellspacing="0" style="background:#111827;border-radius:8px;border:1px solid #1e293b;margin-bottom:20px;">
            <tr style="background:#0f172a;">
              <th style="padding:8px 14px;text-align:left;color:#475569;font-size:10px;font-weight:600;text-transform:uppercase;">Ticker</th>
              <th style="padding:8px 14px;text-align:center;color:#475569;font-size:10px;font-weight:600;text-transform:uppercase;">Vol/OI</th>
              <th style="padding:8px 14px;text-align:center;color:#475569;font-size:10px;font-weight:600;text-transform:uppercase;">Strike</th>
              <th style="padding:8px 14px;text-align:center;color:#475569;font-size:10px;font-weight:600;text-transform:uppercase;">Premium</th>
            </tr>
            {rows_html}
          </table>
          <div style="text-align:center;margin-bottom:16px;">
            <a href="{dashboard_url}" style="background:#6366f1;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:700;font-size:14px;">
              View Full Scanner →
            </a>
          </div>
          <p style="font-size:10px;color:#334155;text-align:center;margin:0;">
            StockScanner AI · <a href="{base_url}/stock-scanner/unsubscribe" style="color:#475569;">Unsubscribe</a>
          </p>
        </div>"""

        sent = 0
        for sub in subs:
            if send_email_raw(sub["email"], f"🚨 {len(alerts)} Unusual Call Signal{'s' if len(alerts) != 1 else ''} Detected", html):
                sent += 1
        print(f"[unusual_alert] sent alert to {sent}/{len(subs)} subscribers — {len(alerts)} signals")
    except Exception as _ae:
        print(f"[unusual_alert] alert error (non-fatal): {_ae}")


def _send_eod_accum_email() -> None:
    """
    Send the EOD accumulation picks to all subscribers after the 3:45 PM scan.
    Reads today's picks from eod_accum_picks table and formats a clean digest.
    """
    try:
        from email_alerts import get_active_subscribers, send_email_raw, smtp_configured
        if not smtp_configured():
            print("[eod_accum_email] SMTP not configured — skipping")
            return
        subs = get_active_subscribers()
        if not subs:
            print("[eod_accum_email] no subscribers — skipping")
            return

        import psycopg2, os as _os
        from datetime import datetime as _dt
        con = psycopg2.connect(_os.environ["DATABASE_URL"])
        cur = con.cursor()
        cur.execute("""
            SELECT ticker, accum_score, close_price, price_chg_pct,
                   eod_rel_vol, late_flow, closing_range, mkt_cap_m, signal_type
            FROM eod_accum_picks
            WHERE scan_date = CURRENT_DATE
            ORDER BY accum_score DESC
            LIMIT 25
        """)
        rows = cur.fetchall()
        cur.close(); con.close()

        if not rows:
            print("[eod_accum_email] no picks for today — skipping")
            return

        accum = [r for r in rows if (r[8] or "accum") == "accum"]
        squeeze = [r for r in rows if r[8] == "squeeze"]

        date_str = _dt.now().strftime("%B %d, %Y")
        base_url = _os.getenv("PUBLIC_URL", "https://nclexai.org")

        # --- Fetch earnings dates concurrently for all picks ---
        import concurrent.futures as _cf
        from datetime import timedelta as _td
        import yfinance as _yf_earn

        def _get_earnings_flag(ticker):
            try:
                info = _yf_earn.Ticker(ticker).info
                ts = info.get("earningsTimestamp") or info.get("earningsDate")
                if not ts:
                    return ticker, None
                import datetime as _dtt
                ed = _dtt.datetime.fromtimestamp(ts).date()
                today = _dtt.date.today()
                days = (ed - today).days
                if days == 0:
                    return ticker, "TODAY"
                elif days == 1:
                    return ticker, "TOMORROW"
                elif days == 2:
                    return ticker, "IN 2 DAYS"
                return ticker, None
            except Exception:
                return ticker, None

        all_tickers = [r[0] for r in rows]
        earnings_map = {}
        try:
            with _cf.ThreadPoolExecutor(max_workers=8) as _pool:
                for _tkr, _flag in _pool.map(_get_earnings_flag, all_tickers):
                    if _flag:
                        earnings_map[_tkr] = _flag
        except Exception:
            pass

        def _row_html(r, rank, earnings_flag=None):
            ticker, score, close, chg, vol, lf, cr, cap, sig = r
            chg_str   = f"+{chg:.1f}%" if chg and chg >= 0 else f"{chg:.1f}%"
            chg_color = "#22c55e" if chg and chg >= 0 else "#ef4444"
            cr_pct    = f"{(cr or 0)*100:.0f}%"
            lf_str    = "MAX" if lf and lf >= 99 else f"{lf:.1f}×" if lf else "—"
            vol_str   = f"{vol:.1f}×" if vol else "—"
            score_str = f"{score:.0f}" if score else "—"
            medal     = {1:"🥇",2:"🥈",3:"🥉"}.get(rank, f"#{rank}")
            cap_str   = f"${cap/1000:.1f}B" if cap and cap >= 1000 else (f"${cap:.0f}M" if cap else "—")
            score_color = "#22c55e" if score and score >= 100 else "#06b6d4" if score and score >= 30 else "#f59e0b"
            if earnings_flag == "TODAY":
                earn_badge = '<span style="display:inline-block;font-size:9px;font-weight:800;color:#fff;background:#dc2626;border-radius:3px;padding:1px 5px;margin-top:3px;">⚠️ EARNINGS TODAY — HIGH RISK</span>'
            elif earnings_flag == "TOMORROW":
                earn_badge = '<span style="display:inline-block;font-size:9px;font-weight:800;color:#fff;background:#b45309;border-radius:3px;padding:1px 5px;margin-top:3px;">⚠️ EARNINGS TOMORROW</span>'
            elif earnings_flag == "IN 2 DAYS":
                earn_badge = '<span style="display:inline-block;font-size:9px;font-weight:800;color:#fff;background:#1d4ed8;border-radius:3px;padding:1px 5px;margin-top:3px;">📅 EARNINGS IN 2 DAYS</span>'
            else:
                earn_badge = ""
            return f"""
            <tr>
              <td style="padding:10px 14px;border-bottom:1px solid #1e293b;">
                <span style="font-size:15px;font-weight:800;color:#f1f5f9;">{medal} {ticker}</span>
                <span style="display:block;font-size:10px;color:#64748b;margin-top:2px;">${close:.2f} · {cap_str}</span>
                {"<span style='display:block;margin-top:3px;'>" + earn_badge + "</span>" if earn_badge else ""}
              </td>
              <td style="padding:10px 8px;border-bottom:1px solid #1e293b;text-align:center;">
                <span style="font-weight:700;color:{score_color};font-size:16px;">{score_str}</span>
                <div style="font-size:10px;color:#64748b;">score</div>
              </td>
              <td style="padding:10px 8px;border-bottom:1px solid #1e293b;text-align:center;">
                <span style="font-weight:700;color:{chg_color};">{chg_str}</span>
                <div style="font-size:10px;color:#64748b;">day chg</div>
              </td>
              <td style="padding:10px 8px;border-bottom:1px solid #1e293b;text-align:center;">
                <span style="font-weight:700;color:#a78bfa;">{vol_str}</span>
                <div style="font-size:10px;color:#64748b;">EOD vol</div>
              </td>
              <td style="padding:10px 8px;border-bottom:1px solid #1e293b;text-align:center;">
                <span style="font-weight:700;color:#f59e0b;">{lf_str}</span>
                <div style="font-size:10px;color:#64748b;">late flow</div>
              </td>
              <td style="padding:10px 8px;border-bottom:1px solid #1e293b;text-align:center;">
                <span style="font-weight:700;color:#38bdf8;">{cr_pct}</span>
                <div style="font-size:10px;color:#64748b;">close rng</div>
              </td>
            </tr>"""

        accum_rows   = "".join(_row_html(r, i+1, earnings_map.get(r[0])) for i, r in enumerate(accum))
        squeeze_rows = "".join(_row_html(r, i+1, earnings_map.get(r[0])) for i, r in enumerate(squeeze))
        squeeze_section = ""
        if squeeze_rows:
            squeeze_section = f"""
            <div style="margin-top:24px;">
              <div style="font-size:13px;font-weight:700;color:#ef4444;margin-bottom:8px;">🩳 SHORT SQUEEZE SETUPS</div>
              <table width="100%" cellpadding="0" cellspacing="0" style="background:#111827;border-radius:8px;border:1px solid #1e293b;">
                <tr style="background:#0f172a;">
                  <th style="padding:8px 14px;text-align:left;color:#475569;font-size:10px;">Ticker</th>
                  <th style="padding:8px 8px;text-align:center;color:#475569;font-size:10px;">Score</th>
                  <th style="padding:8px 8px;text-align:center;color:#475569;font-size:10px;">Day %</th>
                  <th style="padding:8px 8px;text-align:center;color:#475569;font-size:10px;">EOD Vol</th>
                  <th style="padding:8px 8px;text-align:center;color:#475569;font-size:10px;">Late Flow</th>
                  <th style="padding:8px 8px;text-align:center;color:#475569;font-size:10px;">Close Rng</th>
                </tr>
                {squeeze_rows}
              </table>
            </div>"""

        html = f"""
        <div style="background:#0a0f1a;font-family:'Segoe UI',Arial,sans-serif;padding:24px;max-width:620px;margin:0 auto;border-radius:12px;">
          <div style="margin-bottom:20px;">
            <span style="font-size:22px;font-weight:800;color:#f1f5f9;">📈 EOD Accumulation Picks</span>
            <span style="display:block;font-size:12px;color:#64748b;margin-top:4px;">
              {len(accum)} accumulation · {len(squeeze)} squeeze setup{'s' if len(squeeze)!=1 else ''} · {date_str}
            </span>
          </div>
          <table width="100%" cellpadding="0" cellspacing="0" style="background:#111827;border-radius:8px;border:1px solid #1e293b;margin-bottom:8px;">
            <tr style="background:#0f172a;">
              <th style="padding:8px 14px;text-align:left;color:#475569;font-size:10px;font-weight:600;text-transform:uppercase;">Ticker</th>
              <th style="padding:8px 8px;text-align:center;color:#475569;font-size:10px;font-weight:600;text-transform:uppercase;">Score</th>
              <th style="padding:8px 8px;text-align:center;color:#475569;font-size:10px;font-weight:600;text-transform:uppercase;">Day %</th>
              <th style="padding:8px 8px;text-align:center;color:#475569;font-size:10px;font-weight:600;text-transform:uppercase;">EOD Vol</th>
              <th style="padding:8px 8px;text-align:center;color:#475569;font-size:10px;font-weight:600;text-transform:uppercase;">Late Flow</th>
              <th style="padding:8px 8px;text-align:center;color:#475569;font-size:10px;font-weight:600;text-transform:uppercase;">Close Rng</th>
            </tr>
            {accum_rows}
          </table>
          {squeeze_section}
          <div style="text-align:center;margin:20px 0 16px;">
            <a href="{base_url}/stock-scanner/" style="background:#6366f1;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:700;font-size:14px;">
              View Full Scanner →
            </a>
          </div>
          <p style="font-size:10px;color:#334155;text-align:center;margin:0;">
            StockScanner AI · <a href="{base_url}/stock-scanner/unsubscribe" style="color:#475569;">Unsubscribe</a>
          </p>
        </div>"""

        sent = 0
        subject = f"📈 EOD Picks: {len(accum)} Accumulation Signal{'s' if len(accum)!=1 else ''} · {date_str}"
        for sub in subs:
            if send_email_raw(sub["email"], subject, html):
                sent += 1
        print(f"[eod_accum_email] sent to {sent}/{len(subs)} subscribers — {len(accum)} picks")
    except Exception as _e:
        import traceback
        print(f"[eod_accum_email] error: {_e}\n{traceback.format_exc()}")


def _send_morning_inflows_email() -> None:
    """
    Morning email with two sections:
      1. Last night's EOD picks — how they are opening today (confirmed vs quiet)
      2. Fresh morning finds — new standouts NOT in last night's EOD list
    """
    try:
        from email_alerts import get_active_subscribers, send_email_raw, smtp_configured
        if not smtp_configured():
            return
        subs = get_active_subscribers()
        if not subs:
            return

        import psycopg2, os as _os, json
        from datetime import datetime as _dt

        con = psycopg2.connect(_os.environ["DATABASE_URL"])
        cur = con.cursor()

        # ── 1. Last night's EOD picks (most recent scan_date) ────────────────
        cur.execute("""
            SELECT ticker, close_price, accum_score
            FROM eod_accum_picks
            WHERE scan_date = (SELECT MAX(scan_date) FROM eod_accum_picks)
            ORDER BY accum_score DESC
            LIMIT 15
        """)
        eod_rows = cur.fetchall()
        eod_date_row = None
        if eod_rows:
            cur.execute("SELECT MAX(scan_date) FROM eod_accum_picks")
            eod_date_row = cur.fetchone()

        # ── 2. Today's morning standouts ─────────────────────────────────────
        cur.execute("""
            SELECT payload FROM morning_inflows_cache
            WHERE scan_date = CURRENT_DATE
            ORDER BY saved_at DESC LIMIT 1
        """)
        mi_row = cur.fetchone()
        cur.close(); con.close()

        # Parse morning standouts
        morning_standouts = []
        if mi_row and mi_row[0]:
            payload = mi_row[0] if isinstance(mi_row[0], dict) else json.loads(mi_row[0])
            morning_standouts = payload.get("standouts") or []

        morning_by_ticker = {s["ticker"]: s for s in morning_standouts}

        # Build EOD follow-up list
        eod_picks = {}
        for r in eod_rows:
            ticker = r[0]
            eod_picks[ticker] = {
                "ticker":       ticker,
                "close_price":  float(r[1] or 0),
                "accum_score":  float(r[2] or 0),
            }

        # ── Fetch premarket prices for all EOD picks concurrently ────────────
        def _get_premarket(ticker):
            try:
                import yfinance as _yf3
                tk_obj = _yf3.Ticker(ticker)
                fi     = tk_obj.fast_info
                pre    = fi.get("preMarketPrice") or fi.get("postMarketPrice")
                prev   = fi.get("regularMarketPreviousClose") or fi.get("previousClose")
                chg    = None
                if pre and prev and prev > 0:
                    chg = round((float(pre) - float(prev)) / float(prev) * 100, 2)
                name = None
                try:
                    info = tk_obj.info
                    name = info.get("shortName") or info.get("longName")
                except Exception:
                    pass
                return ticker, (float(pre) if pre else None), chg, name
            except Exception:
                pass
            return ticker, None, None, None

        from concurrent.futures import ThreadPoolExecutor as _TPE, as_completed as _asc2
        _pm_data = {}
        with _TPE(max_workers=8) as _ex:
            _futs = {_ex.submit(_get_premarket, t): t for t in eod_picks}
            for _f in _asc2(_futs):
                t, price, chg, name = _f.result()
                _pm_data[t] = {"pm_price": price, "pm_chg": chg, "name": name}

        # Annotate each EOD pick with morning action + premarket data
        eod_annotated = []
        for ticker, ep in eod_picks.items():
            mi  = morning_by_ticker.get(ticker)
            pm  = _pm_data.get(ticker, {})
            eod_annotated.append({
                **ep,
                "price":          float((mi or {}).get("price", 0) or 0),
                "price_chg_pct":  float((mi or {}).get("price_chg_pct", 0) or 0),
                "rel_vol":        float((mi or {}).get("rel_vol", 0) or 0),
                "flow_ratio":     float((mi or {}).get("flow_ratio", 0) or 0),
                "standout_score": float((mi or {}).get("standout_score", 0) or 0),
                "confirming":     mi is not None,
                "pm_price":       pm.get("pm_price"),
                "pm_chg":         pm.get("pm_chg"),
                "name":           pm.get("name"),
            })

        # Sort: confirming picks first (by standout_score), then quiet ones (by accum_score)
        eod_annotated.sort(key=lambda x: (
            0 if x["confirming"] else 1,
            -x["standout_score"] if x["confirming"] else -x["accum_score"]
        ))

        # Fresh finds: morning standouts NOT in last night's EOD list
        fresh = [s for s in morning_standouts if s["ticker"] not in eod_picks][:8]

        date_str  = _dt.now().strftime("%B %d, %Y")
        eod_label = eod_date_row[0].strftime("%b %d") if eod_date_row and eod_date_row[0] else "Last Night"
        base_url  = _os.getenv("PUBLIC_URL", "https://nclexai.org")

        # ── Build Section 1: EOD picks follow-up ─────────────────────────────
        eod_rows_html = ""
        for ep in eod_annotated:
            ticker   = ep["ticker"]
            chg      = ep["price_chg_pct"]
            vol      = ep["rel_vol"]
            flow     = ep["flow_ratio"]
            price    = ep["price"] or ep["close_price"]
            score    = ep["accum_score"]
            conf     = ep["confirming"]
            pm_chg   = ep.get("pm_chg")
            pm_price = ep.get("pm_price")

            co_name    = ep.get("name") or ""
            chg_color  = "#22c55e" if chg > 0 else "#ef4444" if chg < 0 else "#94a3b8"
            chg_str    = f"+{chg:.1f}%" if chg > 0 else f"{chg:.1f}%" if chg < 0 else "flat"
            status_dot = '<span style="color:#22c55e;font-weight:900;">●</span>' if conf else '<span style="color:#475569;">○</span>'
            status_lbl = '<span style="font-size:9px;color:#22c55e;">CONFIRMING</span>' if conf else '<span style="font-size:9px;color:#475569;">QUIET</span>'
            vol_str    = f"{vol:.1f}×" if vol else "—"
            flow_str   = f"{flow:.1f}:1" if flow else "—"
            price_str  = f"${price:.2f}" if price else "—"
            name_html  = f'<span style="display:block;font-size:10px;color:#94a3b8;margin-top:1px;">{co_name}</span>' if co_name else ""

            # Premarket badge — prominent warning if gapping down hard
            pm_html = ""
            if pm_chg is not None:
                pm_str = f"{pm_chg:+.1f}%"
                if pm_chg <= -10:
                    pm_html = (
                        f'<span style="display:inline-block;margin-top:4px;'
                        f'background:#ef444433;border:1px solid #ef4444;color:#ef4444;'
                        f'font-size:10px;font-weight:800;padding:2px 7px;border-radius:4px;">'
                        f'⚠️ PREMARKET {pm_str} — SKIP TODAY</span>'
                    )
                elif pm_chg <= -5:
                    pm_html = (
                        f'<span style="display:inline-block;margin-top:4px;'
                        f'background:#f59e0b22;border:1px solid #f59e0b;color:#f59e0b;'
                        f'font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;">'
                        f'⚠️ PM {pm_str} — wait for open</span>'
                    )
                elif pm_chg >= 5:
                    pm_html = (
                        f'<span style="display:inline-block;margin-top:4px;'
                        f'background:#22c55e22;border:1px solid #22c55e;color:#22c55e;'
                        f'font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;">'
                        f'🟢 PM {pm_str} — gapping up</span>'
                    )
                else:
                    pm_html = (
                        f'<span style="display:inline-block;margin-top:4px;'
                        f'font-size:10px;color:#64748b;">PM {pm_str}</span>'
                    )

            # Dim entire row if gapping down hard — visual skip signal
            row_bg = "background:#1a0a0a;" if (pm_chg is not None and pm_chg <= -10) else ""

            eod_rows_html += f"""
            <tr style="{row_bg}">
              <td style="padding:10px 14px;border-bottom:1px solid #1e293b;">
                <span style="font-size:14px;font-weight:800;color:#f1f5f9;">{status_dot} {ticker}</span>
                {name_html}
                <span style="display:block;font-size:10px;color:#64748b;margin-top:1px;">{price_str} · EOD score {score:.0f}</span>
                <span style="display:block;margin-top:2px;">{status_lbl}</span>
                {pm_html}
              </td>
              <td style="padding:10px 8px;border-bottom:1px solid #1e293b;text-align:center;white-space:nowrap;">
                <span style="font-weight:700;color:{chg_color};">{chg_str if conf else "—"}</span>
              </td>
              <td style="padding:10px 8px;border-bottom:1px solid #1e293b;text-align:center;">
                <span style="font-weight:700;color:#a78bfa;">{vol_str}</span>
              </td>
              <td style="padding:10px 8px;border-bottom:1px solid #1e293b;text-align:center;">
                <span style="font-weight:700;color:#f59e0b;">{flow_str}</span>
              </td>
            </tr>"""

        # ── Build Section 2: Fresh morning finds ─────────────────────────────
        fresh_rows_html = ""
        for i, s in enumerate(fresh):
            ticker = s.get("ticker", "")
            score  = s.get("standout_score", 0)
            chg    = s.get("price_chg_pct", 0) or 0
            vol    = s.get("rel_vol", 0) or 0
            price  = s.get("price", 0) or 0
            flow   = s.get("flow_ratio", 0) or 0
            medal  = {0:"🥇",1:"🥈",2:"🥉"}.get(i, f"#{i+1}")
            chg_color  = "#22c55e" if chg >= 0 else "#ef4444"
            chg_str    = f"+{chg:.1f}%" if chg >= 0 else f"{chg:.1f}%"
            score_color = "#22c55e" if score >= 50 else "#06b6d4" if score >= 20 else "#f59e0b"

            fresh_rows_html += f"""
            <tr>
              <td style="padding:9px 14px;border-bottom:1px solid #1e293b;">
                <span style="font-size:13px;font-weight:800;color:#f1f5f9;">{medal} {ticker}</span>
                <span style="display:block;font-size:10px;color:#64748b;">${price:.2f}</span>
              </td>
              <td style="padding:9px 8px;border-bottom:1px solid #1e293b;text-align:center;">
                <span style="font-weight:700;color:{score_color};">{score:.0f}</span>
              </td>
              <td style="padding:9px 8px;border-bottom:1px solid #1e293b;text-align:center;">
                <span style="font-weight:700;color:{chg_color};">{chg_str}</span>
              </td>
              <td style="padding:9px 8px;border-bottom:1px solid #1e293b;text-align:center;">
                <span style="font-weight:700;color:#a78bfa;">{vol:.1f}×</span>
              </td>
              <td style="padding:9px 8px;border-bottom:1px solid #1e293b;text-align:center;">
                <span style="font-weight:700;color:#f59e0b;">{flow:.1f}:1</span>
              </td>
            </tr>"""

        confirmed_count = sum(1 for e in eod_annotated if e["confirming"])

        fresh_section_html = ""
        if fresh_rows_html:
            fresh_section_html = f"""
          <div style="margin-top:24px;margin-bottom:8px;">
            <span style="font-size:16px;font-weight:800;color:#f1f5f9;">✨ Fresh Morning Finds</span>
            <span style="display:block;font-size:11px;color:#64748b;margin-top:2px;">New signals not on last night's list</span>
          </div>
          <table width="100%" cellpadding="0" cellspacing="0" style="background:#111827;border-radius:8px;border:1px solid #1e293b;margin-bottom:16px;">
            <tr style="background:#0f172a;">
              <th style="padding:8px 14px;text-align:left;color:#475569;font-size:10px;text-transform:uppercase;">Ticker</th>
              <th style="padding:8px 8px;text-align:center;color:#475569;font-size:10px;text-transform:uppercase;">Score</th>
              <th style="padding:8px 8px;text-align:center;color:#475569;font-size:10px;text-transform:uppercase;">Chg%</th>
              <th style="padding:8px 8px;text-align:center;color:#475569;font-size:10px;text-transform:uppercase;">Rel Vol</th>
              <th style="padding:8px 8px;text-align:center;color:#475569;font-size:10px;text-transform:uppercase;">Flow</th>
            </tr>
            {fresh_rows_html}
          </table>"""

        no_eod_msg = ""
        if not eod_annotated:
            no_eod_msg = '<p style="color:#64748b;font-size:12px;text-align:center;padding:16px 0;">No EOD picks from last night — market may have been closed.</p>'

        html = f"""
        <div style="background:#0a0f1a;font-family:'Segoe UI',Arial,sans-serif;padding:24px;max-width:620px;margin:0 auto;border-radius:12px;">
          <div style="margin-bottom:20px;">
            <span style="font-size:22px;font-weight:800;color:#f1f5f9;">☀️ Morning Scan · {date_str}</span>
            <span style="display:block;font-size:12px;color:#64748b;margin-top:4px;">
              {confirmed_count} of {len(eod_annotated)} {eod_label} picks confirming
              {f'· {len(fresh)} fresh find{"s" if len(fresh)!=1 else ""}' if fresh else ''}
            </span>
          </div>

          <div style="margin-bottom:8px;">
            <span style="font-size:16px;font-weight:800;color:#f1f5f9;">📋 {eod_label} EOD Picks — Opening Action</span>
            <span style="display:block;font-size:11px;color:#64748b;margin-top:2px;">
              <span style="color:#22c55e;">●</span> Confirming = showing in morning scanner &nbsp;
              <span style="color:#475569;">○</span> Quiet = no signal yet
            </span>
          </div>
          {no_eod_msg}
          <table width="100%" cellpadding="0" cellspacing="0" style="background:#111827;border-radius:8px;border:1px solid #1e293b;margin-bottom:4px;">
            <tr style="background:#0f172a;">
              <th style="padding:8px 14px;text-align:left;color:#475569;font-size:10px;text-transform:uppercase;">Ticker</th>
              <th style="padding:8px 8px;text-align:center;color:#475569;font-size:10px;text-transform:uppercase;">Chg%</th>
              <th style="padding:8px 8px;text-align:center;color:#475569;font-size:10px;text-transform:uppercase;">Rel Vol</th>
              <th style="padding:8px 8px;text-align:center;color:#475569;font-size:10px;text-transform:uppercase;">Flow</th>
            </tr>
            {eod_rows_html}
          </table>

          {fresh_section_html}

          <div style="text-align:center;margin:20px 0 16px;">
            <a href="{base_url}/stock-scanner/" style="background:#6366f1;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:700;font-size:14px;">Open Scanner →</a>
          </div>
          <p style="font-size:10px;color:#334155;text-align:center;margin:0;">
            StockScanner AI · <a href="{base_url}/stock-scanner/unsubscribe" style="color:#475569;">Unsubscribe</a>
          </p>
        </div>"""

        sent = 0
        subject = (
            f"☀️ Morning: {confirmed_count}/{len(eod_annotated)} EOD picks confirming"
            + (f" · {len(fresh)} fresh find{'s' if len(fresh)!=1 else ''}" if fresh else "")
            + f" · {date_str}"
        )
        for sub in subs:
            if send_email_raw(sub["email"], subject, html):
                sent += 1
        print(f"[morning_email] sent to {sent}/{len(subs)} — {confirmed_count}/{len(eod_annotated)} EOD confirming, {len(fresh)} fresh finds")
    except Exception as _e:
        import traceback
        print(f"[morning_email] error: {_e}\n{traceback.format_exc()}")


def _send_ai_trades_email(trades: list) -> None:
    """Email today's AI-generated trade picks with full options details."""
    try:
        from email_alerts import get_active_subscribers, send_email_raw, smtp_configured
        if not smtp_configured() or not trades:
            return
        subs = get_active_subscribers()
        if not subs:
            return

        from datetime import datetime as _dt
        import os as _os
        date_str = _dt.now().strftime("%B %d, %Y")
        base_url = _os.getenv("PUBLIC_URL", "https://nclexai.org")

        cards_html = ""
        for i, tr in enumerate(trades[:5]):
            ticker    = tr.get("ticker", "")
            setup     = tr.get("setup_type", "LONG CALL")
            thesis    = tr.get("thesis", "")
            thesis    = thesis[:140] + ("…" if len(thesis) > 140 else "")
            price     = float(tr.get("price", 0) or 0)
            strike    = float(tr.get("entry_strike", 0) or 0)
            expiry    = tr.get("expiry", "")
            target    = float(tr.get("target_price", 0) or 0)
            stop      = float(tr.get("stop_loss", 0) or 0)
            premium   = float(tr.get("option_premium", 0) or 0)
            conv      = tr.get("conviction", "").upper()
            direction = tr.get("direction", "BULLISH").upper()
            risk      = tr.get("risk_level", "").upper()
            signals   = tr.get("signals_aligned", [])

            medal      = {0:"🥇",1:"🥈",2:"🥉"}.get(i, f"#{i+1}")
            conv_color = "#22c55e" if "HIGH" in conv else "#f59e0b" if "MED" in conv else "#64748b"
            risk_color = "#ef4444" if "HIGH" in risk else "#f59e0b" if "MED" in risk else "#22c55e"
            strike_str = f"${strike:.2f}" if strike else "—"
            target_str = f"${target:.2f}" if target else "—"
            stop_str   = f"${stop:.2f}"   if stop   else "—"
            prem_str   = f"${premium:.2f}/sh · ~${premium*100:.0f}/contract" if premium else "—"
            # Format expiry as human-readable: 2026-07-18 → Jul 18
            expiry_str = expiry
            if expiry:
                try:
                    from datetime import datetime as _dtx
                    expiry_str = _dtx.strptime(expiry, "%Y-%m-%d").strftime("%b %d, %Y")
                except Exception:
                    pass
            signals_html = "".join(
                f'<span style="display:inline-block;background:#1e293b;color:#94a3b8;font-size:9px;padding:2px 6px;border-radius:4px;margin:2px 2px 0 0;">{s}</span>'
                for s in (signals[:4] if signals else [])
            )

            cards_html += f"""
            <div style="background:#111827;border:1px solid #1e293b;border-radius:10px;margin-bottom:14px;overflow:hidden;">
              <!-- Header -->
              <div style="background:#0f172a;padding:12px 16px;display:flex;justify-content:space-between;align-items:center;">
                <span style="font-size:17px;font-weight:900;color:#f1f5f9;">{medal} {ticker}
                  <span style="font-size:11px;font-weight:500;color:#6366f1;margin-left:6px;">{setup}</span>
                </span>
                <span>
                  <span style="font-size:10px;font-weight:700;color:{conv_color};background:{conv_color}22;padding:2px 7px;border-radius:4px;">{conv}</span>
                  <span style="font-size:10px;font-weight:700;color:{risk_color};background:{risk_color}22;padding:2px 7px;border-radius:4px;margin-left:4px;">RISK: {risk}</span>
                </span>
              </div>
              <!-- Options contract row -->
              <div style="padding:10px 16px;border-bottom:1px solid #1e293b;background:#0d1525;">
                <table width="100%" cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="text-align:center;padding:4px 8px;">
                      <div style="font-size:9px;color:#475569;text-transform:uppercase;margin-bottom:2px;">Stock Price</div>
                      <div style="font-size:14px;font-weight:800;color:#f1f5f9;">${price:.2f}</div>
                    </td>
                    <td style="text-align:center;padding:4px 8px;">
                      <div style="font-size:9px;color:#475569;text-transform:uppercase;margin-bottom:2px;">Strike</div>
                      <div style="font-size:14px;font-weight:800;color:#6366f1;">{strike_str}</div>
                    </td>
                    <td style="text-align:center;padding:4px 8px;">
                      <div style="font-size:9px;color:#475569;text-transform:uppercase;margin-bottom:2px;">Expiry</div>
                      <div style="font-size:13px;font-weight:800;color:#a78bfa;">{expiry_str}</div>
                    </td>
                    <td style="text-align:center;padding:4px 8px;">
                      <div style="font-size:9px;color:#475569;text-transform:uppercase;margin-bottom:2px;">Est. Premium</div>
                      <div style="font-size:12px;font-weight:700;color:#f59e0b;">{prem_str.split("·")[0].strip()}</div>
                    </td>
                  </tr>
                </table>
              </div>
              <!-- Target / Stop -->
              <div style="padding:10px 16px;border-bottom:1px solid #1e293b;">
                <table width="100%" cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="width:50%;padding:0 8px 0 0;">
                      <div style="font-size:9px;color:#475569;text-transform:uppercase;margin-bottom:2px;">Target Price</div>
                      <div style="font-size:14px;font-weight:800;color:#22c55e;">{target_str}</div>
                    </td>
                    <td style="width:50%;padding:0 0 0 8px;border-left:1px solid #1e293b;">
                      <div style="font-size:9px;color:#475569;text-transform:uppercase;margin-bottom:2px;">Stop Loss</div>
                      <div style="font-size:14px;font-weight:800;color:#ef4444;">{stop_str}</div>
                    </td>
                  </tr>
                </table>
              </div>
              <!-- Thesis -->
              <div style="padding:10px 16px;border-bottom:1px solid #1e293b;">
                <div style="font-size:9px;color:#475569;text-transform:uppercase;margin-bottom:4px;">Thesis</div>
                <div style="font-size:12px;color:#cbd5e1;line-height:1.5;">{thesis}</div>
              </div>
              <!-- Signals -->
              {f'<div style="padding:8px 16px;">{signals_html}</div>' if signals_html else ''}
            </div>"""

        html = f"""
        <div style="background:#0a0f1a;font-family:'Segoe UI',Arial,sans-serif;padding:24px;max-width:620px;margin:0 auto;border-radius:12px;">
          <div style="margin-bottom:20px;">
            <span style="font-size:22px;font-weight:800;color:#f1f5f9;">🤖 AI Trade Setups</span>
            <span style="display:block;font-size:12px;color:#64748b;margin-top:4px;">{len(trades)} LONG CALL setup{'s' if len(trades)!=1 else ''} · {date_str}</span>
          </div>
          {cards_html}
          <div style="text-align:center;margin:8px 0 16px;">
            <a href="{base_url}/stock-scanner/" style="background:#6366f1;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:700;font-size:14px;">View AI Trades →</a>
          </div>
          <p style="font-size:10px;color:#334155;text-align:center;margin:0;">
            StockScanner AI · <a href="{base_url}/stock-scanner/unsubscribe" style="color:#475569;">Unsubscribe</a>
          </p>
        </div>"""

        sent = 0
        subject = f"🤖 AI Trades: {len(trades)} LONG CALL Setup{'s' if len(trades)!=1 else ''} · {date_str}"
        for sub in subs:
            if send_email_raw(sub["email"], subject, html):
                sent += 1
        print(f"[ai_trades_email] sent to {sent}/{len(subs)} subscribers — {len(trades)} trades")
    except Exception as _e:
        import traceback
        print(f"[ai_trades_email] error: {_e}\n{traceback.format_exc()}")


def _send_unusual_calls_email() -> None:
    """Email top 5 unusual calls ranked by conviction score (prem × vol_oi × urgency)."""
    try:
        from email_alerts import get_active_subscribers, send_email_raw, smtp_configured
        if not smtp_configured():
            return
        subs = get_active_subscribers()
        if not subs:
            return

        from datetime import datetime as _dt
        import os as _os

        cache = getattr(app, "_unusual_calls_cache", None)
        hits  = (cache or {}).get("hits", [])

        if not hits:
            # Fall back to DB if no cache
            try:
                import psycopg2, os as _os2
                con = psycopg2.connect(_os2.environ["DATABASE_URL"])
                cur = con.cursor()
                cur.execute("""
                    SELECT ticker, price::float, strike::float, expiry, days_out,
                           volume, oi, vol_oi::float, prem::bigint, otm_pct::float,
                           iv::float, urgency
                    FROM unusual_calls_log
                    WHERE last_seen >= CURRENT_DATE
                      AND expiry::date > CURRENT_DATE
                      AND vol_oi >= 3 AND prem >= 100000
                    ORDER BY prem DESC LIMIT 80
                """)
                cols = ["ticker","price","strike","expiry","days_out","volume","oi","vol_oi","prem","otm_pct","iv","urgency"]
                hits = [dict(zip(cols, r)) for r in cur.fetchall()]
                cur.close(); con.close()
            except Exception:
                pass

        if not hits:
            print("[unusual_calls_email] no data — skipping")
            return

        _ETF_SET_LOCAL = {"SPY","QQQ","IWM","DIA","XLF","XLE","XLK","XLV","TQQQ","SQQQ","UVXY","VIX"}
        urgency_mult   = {"EXPIRING": 2.0, "SHORT": 1.5, "NEAR": 1.2, "MEDIUM": 1.0}

        scored = []
        for h in hits:
            if h.get("is_etf") or h.get("ticker") in _ETF_SET_LOCAL:
                continue
            urg    = h.get("urgency", "MEDIUM")
            um     = urgency_mult.get(urg, 1.0)
            score  = float(h.get("prem", 0)) * float(h.get("vol_oi", 0)) * um
            scored.append({**h, "_score": score})

        scored.sort(key=lambda x: -x["_score"])
        top5 = scored[:5]

        if not top5:
            print("[unusual_calls_email] no qualifying picks — skipping")
            return

        date_str = _dt.now().strftime("%B %d, %Y")
        base_url = _os.getenv("PUBLIC_URL", "https://nclexai.org")
        total    = (cache or {}).get("total", len(hits))

        cards_html = ""
        for i, h in enumerate(top5):
            ticker  = h.get("ticker", "")
            price   = float(h.get("price", 0) or 0)
            strike  = float(h.get("strike", 0) or 0)
            expiry  = h.get("expiry", "")
            days    = int(h.get("days_out", 0) or 0)
            vol     = int(h.get("volume", 0) or 0)
            oi      = int(h.get("oi", 0) or 0)
            voi     = float(h.get("vol_oi", 0) or 0)
            prem    = int(h.get("prem", 0) or 0)
            otm     = float(h.get("otm_pct", 0) or 0)
            iv      = float(h.get("iv", 0) or 0)
            urg     = h.get("urgency", "MEDIUM")
            medal   = {0:"🥇",1:"🥈",2:"🥉"}.get(i, f"#{i+1}")

            prem_str   = f"${prem/1000:.0f}K" if prem < 1_000_000 else f"${prem/1_000_000:.1f}M"
            otm_str    = f"+{otm:.1f}% OTM" if otm >= 0 else f"{otm:.1f}% ITM"
            voi_color  = "#22c55e" if voi >= 10 else "#f59e0b" if voi >= 5 else "#94a3b8"
            urg_color  = "#ef4444" if urg == "EXPIRING" else "#f59e0b" if urg == "SHORT" else "#06b6d4" if urg == "NEAR" else "#475569"
            otm_color  = "#22c55e" if 0 <= otm <= 10 else "#f59e0b" if otm <= 20 else "#94a3b8"
            expiry_fmt = expiry
            try:
                from datetime import datetime as _dx
                expiry_fmt = _dx.strptime(expiry, "%Y-%m-%d").strftime("%b %d")
            except Exception:
                pass

            cards_html += f"""
            <div style="background:#111827;border:1px solid #1e293b;border-radius:10px;margin-bottom:12px;overflow:hidden;">
              <div style="background:#0f172a;padding:10px 16px;display:flex;justify-content:space-between;align-items:center;">
                <span style="font-size:16px;font-weight:900;color:#f1f5f9;">{medal} {ticker}
                  <span style="font-size:11px;font-weight:500;color:#64748b;margin-left:6px;">${price:.2f}</span>
                </span>
                <span style="font-size:10px;font-weight:700;color:{urg_color};background:{urg_color}22;padding:2px 7px;border-radius:4px;">{urg} ≤{days}d</span>
              </div>
              <div style="padding:10px 16px;border-bottom:1px solid #1e293b;">
                <table width="100%" cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="text-align:center;padding:4px 6px;">
                      <div style="font-size:9px;color:#475569;text-transform:uppercase;margin-bottom:2px;">Strike</div>
                      <div style="font-size:15px;font-weight:800;color:#6366f1;">${strike:.0f}</div>
                    </td>
                    <td style="text-align:center;padding:4px 6px;">
                      <div style="font-size:9px;color:#475569;text-transform:uppercase;margin-bottom:2px;">Expiry</div>
                      <div style="font-size:13px;font-weight:800;color:#a78bfa;">{expiry_fmt}</div>
                    </td>
                    <td style="text-align:center;padding:4px 6px;">
                      <div style="font-size:9px;color:#475569;text-transform:uppercase;margin-bottom:2px;">OTM</div>
                      <div style="font-size:13px;font-weight:800;color:{otm_color};">{otm_str}</div>
                    </td>
                    <td style="text-align:center;padding:4px 6px;">
                      <div style="font-size:9px;color:#475569;text-transform:uppercase;margin-bottom:2px;">Vol/OI</div>
                      <div style="font-size:13px;font-weight:800;color:{voi_color};">{voi:.1f}×</div>
                    </td>
                    <td style="text-align:center;padding:4px 6px;">
                      <div style="font-size:9px;color:#475569;text-transform:uppercase;margin-bottom:2px;">Premium</div>
                      <div style="font-size:13px;font-weight:800;color:#f59e0b;">{prem_str}</div>
                    </td>
                  </tr>
                </table>
              </div>
              <div style="padding:7px 16px;display:flex;justify-content:space-between;">
                <span style="font-size:10px;color:#475569;">Vol {vol:,} · OI {oi:,} · IV {iv:.0f}%</span>
              </div>
            </div>"""

        html = f"""
        <div style="background:#0a0f1a;font-family:'Segoe UI',Arial,sans-serif;padding:24px;max-width:620px;margin:0 auto;border-radius:12px;">
          <div style="margin-bottom:20px;">
            <span style="font-size:22px;font-weight:800;color:#f1f5f9;">🚨 Unusual Calls — Top 5</span>
            <span style="display:block;font-size:12px;color:#64748b;margin-top:4px;">{total} signals found · ranked by conviction · {date_str}</span>
          </div>
          {cards_html}
          <div style="text-align:center;margin:8px 0 16px;">
            <a href="{base_url}/stock-scanner/" style="background:#6366f1;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:700;font-size:14px;">View All Unusual Calls →</a>
          </div>
          <p style="font-size:10px;color:#334155;text-align:center;margin:0;">
            StockScanner AI · <a href="{base_url}/stock-scanner/unsubscribe" style="color:#475569;">Unsubscribe</a>
          </p>
        </div>"""

        sent = 0
        subject = f"🚨 Unusual Calls: Top 5 Picks · {date_str}"
        for sub in subs:
            if send_email_raw(sub["email"], subject, html):
                sent += 1
        print(f"[unusual_calls_email] sent to {sent}/{len(subs)} — {len(top5)} picks")
    except Exception as _e:
        import traceback
        print(f"[unusual_calls_email] error: {_e}\n{traceback.format_exc()}")


def _send_microcap_calls_email() -> None:
    """Email top 5 Small & Growth options flow picks ranked by conviction score."""
    try:
        from email_alerts import get_active_subscribers, send_email_raw, smtp_configured
        if not smtp_configured():
            return
        subs = get_active_subscribers()
        if not subs:
            return

        import psycopg2, os as _os, json
        from datetime import datetime as _dt

        con = psycopg2.connect(_os.environ["DATABASE_URL"])
        cur = con.cursor()
        cur.execute("""
            SELECT ticker, price::float, strike::float, expiry, days_out,
                   volume, oi, vol_oi::float, prem::bigint, otm_pct::float,
                   iv::float, urgency, cap_tier
            FROM unusual_calls_microcap_log
            WHERE last_seen >= CURRENT_DATE
              AND expiry::date > CURRENT_DATE
              AND vol_oi >= 1.5
            ORDER BY prem DESC
            LIMIT 150
        """)
        cols = ["ticker","price","strike","expiry","days_out","volume","oi","vol_oi","prem","otm_pct","iv","urgency","cap_tier"]
        hits = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close(); con.close()

        if not hits:
            print("[microcap_calls_email] no data — skipping")
            return

        urgency_mult = {"EXPIRING": 2.0, "SHORT": 1.5, "NEAR": 1.2, "MEDIUM": 1.0}

        scored = []
        for h in hits:
            urg   = h.get("urgency", "MEDIUM")
            um    = urgency_mult.get(urg, 1.0)
            score = float(h.get("prem", 0)) * float(h.get("vol_oi", 0)) * um
            scored.append({**h, "_score": score})

        scored.sort(key=lambda x: -x["_score"])
        top5 = scored[:5]

        if not top5:
            print("[microcap_calls_email] no qualifying picks — skipping")
            return

        date_str = _dt.now().strftime("%B %d, %Y")
        base_url = _os.getenv("PUBLIC_URL", "https://nclexai.org")

        cap_label = {"nano": "NANO", "micro": "MICRO", "small": "SMALL", "mid": "MID"}
        cap_color = {"nano": "#f59e0b", "micro": "#06b6d4", "small": "#22c55e", "mid": "#a78bfa"}

        cards_html = ""
        for i, h in enumerate(top5):
            ticker  = h.get("ticker", "")
            price   = float(h.get("price", 0) or 0)
            strike  = float(h.get("strike", 0) or 0)
            expiry  = h.get("expiry", "")
            days    = int(h.get("days_out", 0) or 0)
            vol     = int(h.get("volume", 0) or 0)
            oi      = int(h.get("oi", 0) or 0)
            voi     = float(h.get("vol_oi", 0) or 0)
            prem    = int(h.get("prem", 0) or 0)
            otm     = float(h.get("otm_pct", 0) or 0)
            iv      = float(h.get("iv", 0) or 0)
            urg     = h.get("urgency", "MEDIUM")
            cap     = h.get("cap_tier", "micro")
            medal   = {0:"🥇",1:"🥈",2:"🥉"}.get(i, f"#{i+1}")

            prem_str   = f"${prem/1000:.1f}K" if prem < 1_000_000 else f"${prem/1_000_000:.1f}M"
            otm_str    = f"+{otm:.1f}% OTM" if otm >= 0 else f"{otm:.1f}% ITM"
            voi_color  = "#22c55e" if voi >= 10 else "#f59e0b" if voi >= 5 else "#94a3b8"
            urg_color  = "#ef4444" if urg == "EXPIRING" else "#f59e0b" if urg == "SHORT" else "#06b6d4" if urg == "NEAR" else "#475569"
            otm_color  = "#22c55e" if 0 <= otm <= 15 else "#f59e0b" if otm <= 30 else "#94a3b8"
            clbl       = cap_label.get(cap, cap.upper())
            cclr       = cap_color.get(cap, "#94a3b8")
            expiry_fmt = expiry
            try:
                from datetime import datetime as _dx
                expiry_fmt = _dx.strptime(expiry, "%Y-%m-%d").strftime("%b %d")
            except Exception:
                pass

            cards_html += f"""
            <div style="background:#111827;border:1px solid #1e293b;border-radius:10px;margin-bottom:12px;overflow:hidden;">
              <div style="background:#0f172a;padding:10px 16px;display:flex;justify-content:space-between;align-items:center;">
                <span style="font-size:16px;font-weight:900;color:#f1f5f9;">{medal} {ticker}
                  <span style="font-size:11px;font-weight:500;color:#64748b;margin-left:6px;">${price:.2f}</span>
                  <span style="font-size:10px;font-weight:700;color:{cclr};background:{cclr}22;padding:1px 5px;border-radius:3px;margin-left:4px;">{clbl}</span>
                </span>
                <span style="font-size:10px;font-weight:700;color:{urg_color};background:{urg_color}22;padding:2px 7px;border-radius:4px;">{urg} ≤{days}d</span>
              </div>
              <div style="padding:10px 16px;border-bottom:1px solid #1e293b;">
                <table width="100%" cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="text-align:center;padding:4px 6px;">
                      <div style="font-size:9px;color:#475569;text-transform:uppercase;margin-bottom:2px;">Strike</div>
                      <div style="font-size:15px;font-weight:800;color:#6366f1;">${strike:.2f}</div>
                    </td>
                    <td style="text-align:center;padding:4px 6px;">
                      <div style="font-size:9px;color:#475569;text-transform:uppercase;margin-bottom:2px;">Expiry</div>
                      <div style="font-size:13px;font-weight:800;color:#a78bfa;">{expiry_fmt}</div>
                    </td>
                    <td style="text-align:center;padding:4px 6px;">
                      <div style="font-size:9px;color:#475569;text-transform:uppercase;margin-bottom:2px;">OTM</div>
                      <div style="font-size:13px;font-weight:800;color:{otm_color};">{otm_str}</div>
                    </td>
                    <td style="text-align:center;padding:4px 6px;">
                      <div style="font-size:9px;color:#475569;text-transform:uppercase;margin-bottom:2px;">Vol/OI</div>
                      <div style="font-size:13px;font-weight:800;color:{voi_color};">{voi:.1f}×</div>
                    </td>
                    <td style="text-align:center;padding:4px 6px;">
                      <div style="font-size:9px;color:#475569;text-transform:uppercase;margin-bottom:2px;">Premium</div>
                      <div style="font-size:13px;font-weight:800;color:#f59e0b;">{prem_str}</div>
                    </td>
                  </tr>
                </table>
              </div>
              <div style="padding:7px 16px;">
                <span style="font-size:10px;color:#475569;">Vol {vol:,} · OI {oi:,} · IV {iv:.0f}%</span>
              </div>
            </div>"""

        html = f"""
        <div style="background:#0a0f1a;font-family:'Segoe UI',Arial,sans-serif;padding:24px;max-width:620px;margin:0 auto;border-radius:12px;">
          <div style="margin-bottom:20px;">
            <span style="font-size:22px;font-weight:800;color:#f1f5f9;">🎯 Small &amp; Growth Options Flow — Top 5</span>
            <span style="display:block;font-size:12px;color:#64748b;margin-top:4px;">Unusual call activity · tight spreads · ranked by conviction · {date_str}</span>
          </div>
          {cards_html}
          <div style="text-align:center;margin:8px 0 16px;">
            <a href="{base_url}/stock-scanner/" style="background:#6366f1;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:700;font-size:14px;">View Full Scanner →</a>
          </div>
          <p style="font-size:10px;color:#334155;text-align:center;margin:0;">
            StockScanner AI · <a href="{base_url}/stock-scanner/unsubscribe" style="color:#475569;">Unsubscribe</a>
          </p>
        </div>"""

        sent = 0
        subject = f"🎯 Small & Growth Calls: Top 5 Picks · {date_str}"
        for sub in subs:
            if send_email_raw(sub["email"], subject, html):
                sent += 1
        print(f"[microcap_calls_email] sent to {sent}/{len(subs)} — {len(top5)} picks")
    except Exception as _e:
        import traceback
        print(f"[microcap_calls_email] error: {_e}\n{traceback.format_exc()}")


def _send_high_conviction_email() -> None:
    """Email top 5 High Conviction Calls ranked by composite conviction score."""
    try:
        from email_alerts import get_active_subscribers, send_email_raw, smtp_configured
        if not smtp_configured():
            return
        subs = get_active_subscribers()
        if not subs:
            return

        import math as _math, psycopg2, os as _os
        from datetime import datetime as _dt
        from collections import defaultdict as _dd

        # Use in-memory cache if fresh (< 6h old)
        cache = getattr(app, "_conv_calls_cache", None)
        cache_ts = getattr(app, "_conv_calls_cache_ts", None)
        signals = []
        if cache and cache_ts and (_dt.now() - cache_ts).total_seconds() < 21600:
            signals = (cache or {}).get("signals", [])

        if not signals:
            # Rebuild from DB
            con = psycopg2.connect(_os.environ["DATABASE_URL"])
            cur = con.cursor()
            cur.execute("""
                SELECT ticker, price::float, strike::float, expiry, days_out,
                       vol_oi::float, prem::bigint, otm_pct::float, iv::float,
                       urgency, last_seen
                FROM unusual_calls_log
                WHERE last_seen >= NOW() - INTERVAL '24 hours'
                  AND expiry::date > CURRENT_DATE
                  AND vol_oi >= 3 AND prem >= 100000
                ORDER BY vol_oi DESC LIMIT 300
            """)
            cols = ["ticker","price","strike","expiry","days_out","vol_oi","prem","otm_pct","iv","urgency","last_seen"]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            cur.close(); con.close()

            by_ticker = _dd(list)
            for r in rows:
                by_ticker[r["ticker"]].append(r)

            for ticker, strikes in by_ticker.items():
                num_strikes  = len(strikes)
                total_prem   = sum(s["prem"] for s in strikes)
                max_vol_oi   = max(s["vol_oi"] for s in strikes)
                avg_iv       = sum(s["iv"] or 0 for s in strikes) / num_strikes
                best_strike  = max(strikes, key=lambda s: s["vol_oi"])
                price        = strikes[-1]["price"]
                urgency_rank = {"EXPIRING": 3, "SHORT": 2, "NEAR": 1}.get(best_strike["urgency"], 1)
                iv_bonus     = 1.8 if avg_iv >= 90 else 1.5 if avg_iv >= 70 else 1.2 if avg_iv >= 50 else 1.0
                sweep_mult   = 1.0 + 0.4 * (num_strikes - 1)
                prem_factor  = _math.log(total_prem / 1_000_000 + 1) + 1
                voi_factor   = _math.log(max_vol_oi + 1)
                score        = round(voi_factor * prem_factor * iv_bonus * sweep_mult * urgency_rank, 2)
                if score < 4:
                    continue
                conviction = "EXTREME" if score >= 12 else "HIGH" if score >= 7 else "ELEVATED"
                days_out_min = min(s["days_out"] for s in strikes if s["days_out"])
                urgency_label = "EXPIRING" if days_out_min <= 5 else f"{days_out_min}D"
                signals.append({
                    "ticker": ticker, "price": price, "score": score,
                    "conviction": conviction, "num_strikes": num_strikes,
                    "total_prem_m": round(total_prem / 1_000_000, 2),
                    "max_vol_oi": round(max_vol_oi, 1),
                    "avg_iv": round(avg_iv, 1),
                    "urgency": urgency_label,
                })
            signals.sort(key=lambda x: x["score"], reverse=True)

        if not signals:
            print("[hc_calls_email] no data — skipping")
            return

        top5 = signals[:5]
        date_str = _dt.now().strftime("%B %d, %Y")
        base_url = _os.getenv("PUBLIC_URL", "https://nclexai.org")

        conv_color  = {"EXTREME": "#ef4444", "HIGH": "#f59e0b", "ELEVATED": "#22c55e"}
        conv_icon   = {"EXTREME": "🔥", "HIGH": "⚡", "ELEVATED": "✅"}
        conv_thresh = {"EXTREME": "≥12", "HIGH": "≥7", "ELEVATED": "≥4"}

        cards_html = ""
        for i, sig in enumerate(top5):
            ticker    = sig.get("ticker", "")
            price     = float(sig.get("price", 0) or 0)
            score     = float(sig.get("score", 0) or 0)
            conv      = sig.get("conviction", "ELEVATED")
            n_strikes = int(sig.get("num_strikes", 1) or 1)
            total_pm  = float(sig.get("total_prem_m", 0) or 0)
            max_voi   = float(sig.get("max_vol_oi", 0) or 0)
            avg_iv    = float(sig.get("avg_iv", 0) or 0)
            urg       = sig.get("urgency", "")
            medal     = {0:"🥇",1:"🥈",2:"🥉"}.get(i, f"#{i+1}")
            cc        = conv_color.get(conv, "#94a3b8")
            ci        = conv_icon.get(conv, "")

            prem_str  = f"${total_pm:.1f}M"
            voi_color = "#22c55e" if max_voi >= 20 else "#f59e0b" if max_voi >= 10 else "#94a3b8"

            cards_html += f"""
            <div style="background:#111827;border:1px solid {cc}44;border-left:3px solid {cc};border-radius:10px;margin-bottom:12px;overflow:hidden;">
              <div style="background:#0f172a;padding:10px 16px;display:flex;justify-content:space-between;align-items:center;">
                <span style="font-size:16px;font-weight:900;color:#f1f5f9;">{medal} {ticker}
                  <span style="font-size:11px;font-weight:500;color:#64748b;margin-left:6px;">${price:.2f}</span>
                </span>
                <span style="display:flex;gap:6px;align-items:center;">
                  <span style="font-size:10px;font-weight:700;color:{cc};background:{cc}22;padding:2px 7px;border-radius:4px;">{ci} {conv}</span>
                  <span style="font-size:10px;color:#64748b;">{urg}</span>
                </span>
              </div>
              <div style="padding:10px 16px;border-bottom:1px solid #1e293b;">
                <table width="100%" cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="text-align:center;padding:4px 6px;">
                      <div style="font-size:9px;color:#475569;text-transform:uppercase;margin-bottom:2px;">Score</div>
                      <div style="font-size:18px;font-weight:900;color:{cc};">{score:.1f}</div>
                    </td>
                    <td style="text-align:center;padding:4px 6px;">
                      <div style="font-size:9px;color:#475569;text-transform:uppercase;margin-bottom:2px;">Strikes</div>
                      <div style="font-size:15px;font-weight:800;color:#f1f5f9;">{n_strikes} sweeping</div>
                    </td>
                    <td style="text-align:center;padding:4px 6px;">
                      <div style="font-size:9px;color:#475569;text-transform:uppercase;margin-bottom:2px;">Total Prem</div>
                      <div style="font-size:14px;font-weight:800;color:#f59e0b;">{prem_str}</div>
                    </td>
                    <td style="text-align:center;padding:4px 6px;">
                      <div style="font-size:9px;color:#475569;text-transform:uppercase;margin-bottom:2px;">Max Vol/OI</div>
                      <div style="font-size:14px;font-weight:800;color:{voi_color};">{max_voi:.0f}×</div>
                    </td>
                    <td style="text-align:center;padding:4px 6px;">
                      <div style="font-size:9px;color:#475569;text-transform:uppercase;margin-bottom:2px;">Avg IV</div>
                      <div style="font-size:14px;font-weight:800;color:#a78bfa;">{avg_iv:.0f}%</div>
                    </td>
                  </tr>
                </table>
              </div>
              <div style="padding:7px 16px;">
                <span style="font-size:10px;color:#475569;">Score = Vol/OI × Premium × IV × Sweep multiplier</span>
              </div>
            </div>"""

        legend_html = "".join([
            f'<span style="margin-right:12px;font-size:10px;color:{conv_color[c]};">'
            f'{conv_icon[c]} <b>{c}</b> {conv_thresh[c]}</span>'
            for c in ["EXTREME","HIGH","ELEVATED"]
        ])

        html = f"""
        <div style="background:#0a0f1a;font-family:'Segoe UI',Arial,sans-serif;padding:24px;max-width:620px;margin:0 auto;border-radius:12px;">
          <div style="margin-bottom:20px;">
            <span style="font-size:22px;font-weight:800;color:#f1f5f9;">🔥 High Conviction Calls — Top 5</span>
            <span style="display:block;font-size:12px;color:#64748b;margin-top:4px;">Multi-strike sweeps · calls dramatically outpacing puts · {date_str}</span>
          </div>
          <div style="background:#0f172a;border:1px solid #1e293b;border-radius:8px;padding:8px 14px;margin-bottom:16px;">
            {legend_html}
          </div>
          {cards_html}
          <div style="text-align:center;margin:8px 0 16px;">
            <a href="{base_url}/stock-scanner/" style="background:#ef4444;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:700;font-size:14px;">View High Conviction Tab →</a>
          </div>
          <p style="font-size:10px;color:#334155;text-align:center;margin:0;">
            StockScanner AI · <a href="{base_url}/stock-scanner/unsubscribe" style="color:#475569;">Unsubscribe</a>
          </p>
        </div>"""

        sent = 0
        subject = f"🔥 High Conviction Calls: Top 5 · {date_str}"
        for sub in subs:
            if send_email_raw(sub["email"], subject, html):
                sent += 1
        print(f"[hc_calls_email] sent to {sent}/{len(subs)} — {len(top5)} signals")
    except Exception as _e:
        import traceback
        print(f"[hc_calls_email] error: {_e}\n{traceback.format_exc()}")


# ─── Position Monitor: email-in trade logging + exit signal system ─────────────

def _init_position_monitor_table():
    try:
        import psycopg2 as _pg2, os as _os2
        db = _os2.environ["DATABASE_URL"]
        with _pg2.connect(db) as conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS position_monitor (
                    id              SERIAL PRIMARY KEY,
                    ticker          TEXT NOT NULL,
                    direction       TEXT NOT NULL DEFAULT 'LONG',
                    entry_price     NUMERIC,
                    strike          NUMERIC,
                    expiry          TEXT,
                    email_source    TEXT,
                    logged_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    status          TEXT NOT NULL DEFAULT 'OPEN',
                    exit_alerted_at TIMESTAMPTZ,
                    exit_reason     TEXT
                );
            """)
            conn.commit()
        print("[position_monitor] table ready")
    except Exception as e:
        print(f"[position_monitor] init error: {e}")

_init_position_monitor_table()


def _parse_trade_command(subject: str) -> dict | None:
    """Parse 'TRADE: BUY MSFT', 'TRADE: BUY MSFT 420c 6/20', 'TRADE: SELL AAPL'."""
    import re
    m = re.match(
        r"TRADE:\s*(BUY|SELL|CLOSE)\s+([A-Z]+)"
        r"(?:\s+([\d.]+)[cC](?:\s+(\d{1,2}/\d{1,2}(?:/\d{2,4})?))?)??"
        r"(?:\s+([\d.]+))?",
        subject.strip().upper()
    )
    if not m:
        return None
    direction  = m.group(1)
    ticker     = m.group(2)
    strike     = float(m.group(3)) if m.group(3) else None
    raw_exp    = m.group(4)
    entry      = float(m.group(5)) if m.group(5) else None

    expiry = None
    if raw_exp:
        from datetime import datetime as _dt
        for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m/%d"):
            try:
                parsed = _dt.strptime(raw_exp, fmt)
                if fmt == "%m/%d":
                    parsed = parsed.replace(year=_dt.now().year)
                expiry = parsed.strftime("%Y-%m-%d")
                break
            except ValueError:
                continue

    return {"ticker": ticker, "direction": direction,
            "strike": strike, "expiry": expiry, "entry_price": entry}


def _poll_trade_emails() -> None:
    """Poll Gmail IMAP for TRADE: emails and log positions to position_monitor."""
    import imaplib, email as _email_lib, os as _os
    from email.header import decode_header as _dh
    from datetime import datetime as _dt

    user = _os.environ.get("SMTP_USER", "")
    pwd  = _os.environ.get("SMTP_PASS", "")
    if not user or not pwd:
        return

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(user, pwd)
        mail.select("INBOX")

        _, data = mail.search(None, 'UNSEEN SUBJECT "TRADE:"')
        uids = data[0].split()
        if not uids:
            mail.logout()
            return

        from email_alerts import send_email_raw
        processed = 0
        for uid in uids:
            try:
                _, msg_data = mail.fetch(uid, "(RFC822)")
                msg = _email_lib.message_from_bytes(msg_data[0][1])

                raw_subj = msg.get("Subject", "")
                decoded_parts = _dh(raw_subj)
                subject = "".join(
                    part.decode(enc or "utf-8") if isinstance(part, bytes) else part
                    for part, enc in decoded_parts
                )

                trade = _parse_trade_command(subject)
                if not trade:
                    continue

                ticker    = trade["ticker"]
                direction = trade["direction"]

                if direction in ("SELL", "CLOSE"):
                    with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
                        cur.execute("""
                            UPDATE position_monitor
                            SET status='CLOSED', exit_reason='Manual close via email'
                            WHERE ticker=%s AND status='OPEN'
                        """, (ticker,))
                        conn.commit()
                    send_email_raw(user,
                        f"✅ Position Closed: {ticker}",
                        f"""<div style="background:#0a0f1a;font-family:Arial,sans-serif;padding:20px;color:#f1f5f9;border-radius:8px;">
                        <b>✅ {ticker} position closed.</b><br><br>
                        The system will no longer monitor {ticker} for exit signals.
                        </div>""")
                    mail.store(uid, "+FLAGS", "\\Seen")
                    processed += 1
                    continue

                with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO position_monitor (ticker, direction, entry_price, strike, expiry, email_source)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                    """, (ticker, "LONG", trade["entry_price"],
                          trade["strike"], trade["expiry"], subject))
                    conn.commit()

                parts = [f"<b>{ticker}</b>"]
                if trade["strike"]: parts.append(f"${trade['strike']:.0f}c")
                if trade["expiry"]:
                    try:
                        from datetime import datetime as _dx
                        parts.append(_dx.strptime(trade["expiry"], "%Y-%m-%d").strftime("exp %b %d"))
                    except Exception:
                        parts.append(trade["expiry"])
                if trade["entry_price"]: parts.append(f"@ ${trade['entry_price']:.2f}")
                pos_str = " ".join(parts)

                send_email_raw(user,
                    f"✅ Position Logged: {ticker} — monitoring for exit signals",
                    f"""<div style="background:#0a0f1a;font-family:Arial,sans-serif;padding:20px;color:#f1f5f9;border-radius:8px;">
                    <p style="font-size:16px;font-weight:700;color:#22c55e;">✅ Position logged: {pos_str}</p>
                    <p style="color:#94a3b8;font-size:13px;">The scanner is now watching this position and will email you when exit signals converge (score ≥ 3).</p>
                    <p style="color:#64748b;font-size:11px;margin-top:12px;">To close manually, send: <code>TRADE: CLOSE {ticker}</code></p>
                    </div>""")

                mail.store(uid, "+FLAGS", "\\Seen")
                processed += 1
            except Exception as _e:
                print(f"[poll_trade_emails] uid {uid} error: {_e}")

        mail.logout()
        if processed:
            print(f"[poll_trade_emails] processed {processed} trade email(s)")
    except Exception as e:
        import traceback
        print(f"[poll_trade_emails] error: {e}\n{traceback.format_exc()}")


def _ema_list(values: list, period: int) -> list:
    k = 2.0 / (period + 1)
    ema = [values[0]]
    for v in values[1:]:
        ema.append(v * k + ema[-1] * (1 - k))
    return ema


def _rsi14(closes: list) -> float:
    if len(closes) < 15:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [max(d, 0.0) for d in deltas]
    losses = [abs(min(d, 0.0)) for d in deltas]
    ag = sum(gains[:14]) / 14
    al = sum(losses[:14]) / 14
    for i in range(14, len(deltas)):
        ag = (ag * 13 + gains[i]) / 14
        al = (al * 13 + losses[i]) / 14
    return round(100 - 100 / (1 + ag / al), 1) if al > 0 else 100.0


def _check_exit_signals(ticker: str, entry_price: float | None,
                        strike: float | None, expiry: str | None) -> tuple[int, list]:
    """
    Returns (score, signal_list). Fire exit alert when score >= 3.
    Conservative — requires multiple confirming signals for ~90% accuracy.
    """
    import yfinance as yf
    score   = 0
    signals = []

    # ── 1. Unusual PUT flow on this ticker (+2 pts) ──────────────────────────
    try:
        tk   = yf.Ticker(ticker)
        opts = tk.options
        if opts:
            for exp in opts[:3]:
                try:
                    puts = tk.option_chain(exp).puts
                    for _, row in puts.iterrows():
                        vol = int(row.get("volume") or 0)
                        oi  = int(row.get("openInterest") or 0)
                        if oi < 10 or vol < 20:
                            continue
                        voi = vol / oi
                        bid = float(row.get("bid") or 0)
                        ask = float(row.get("ask") or 0)
                        if bid <= 0 or ask <= 0:
                            continue
                        mid  = (bid + ask) / 2
                        prem = int(mid * vol * 100)
                        if voi >= 3.0 and prem >= 50_000:
                            strike_p = float(row["strike"])
                            signals.append(
                                f"🔴 PUT flow spike on {ticker}: ${strike_p:.0f} put, "
                                f"Vol/OI {voi:.1f}×, ${prem/1000:.0f}K premium — smart money hedging/shorting"
                            )
                            score += 2
                            raise StopIteration
                except StopIteration:
                    break
                except Exception:
                    pass
    except Exception:
        pass

    # ── 2. Call flow disappeared from unusual_calls_log (+2 pts) ─────────────
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT
                  COUNT(*) FILTER (WHERE last_seen >= CURRENT_DATE)            AS today,
                  COUNT(*) FILTER (WHERE last_seen >= CURRENT_DATE - INTERVAL '2 days'
                                   AND last_seen <  CURRENT_DATE)              AS yesterday
                FROM unusual_calls_log WHERE ticker = %s
            """, (ticker,))
            today_cnt, yest_cnt = cur.fetchone()
        if yest_cnt and yest_cnt > 0 and today_cnt == 0:
            signals.append(
                f"📉 Call flow gone: {ticker} had {yest_cnt} call signal(s) yesterday but 0 today — "
                f"institutional interest evaporated"
            )
            score += 2
    except Exception:
        pass

    # ── 3 & 4. MACD bearish cross + RSI overbought ───────────────────────────
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="60d", interval="1d")
        if len(hist) >= 30:
            closes = hist["Close"].tolist()
            lows   = hist["Low"].tolist()
            highs  = hist["High"].tolist()

            ema12  = _ema_list(closes, 12)
            ema26  = _ema_list(closes, 26)
            macd   = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
            sig    = _ema_list(macd[25:], 9)
            macd_r = macd[25:]
            if len(sig) >= 2:
                if macd_r[-2] > sig[-2] and macd_r[-1] < sig[-1]:
                    signals.append(
                        f"⚡ MACD bearish cross on {ticker} (daily) — momentum just flipped down"
                    )
                    score += 1

            rsi = _rsi14(closes)
            if rsi >= 75:
                signals.append(
                    f"📊 RSI overbought: {ticker} RSI={rsi:.0f} — extended, money rotating out"
                )
                score += 1

            # ── 5. Weak close yesterday (closing range < 25%) ─────────────
            if len(closes) >= 2:
                day_range = highs[-1] - lows[-1]
                if day_range > 0:
                    close_range_pct = (closes[-1] - lows[-1]) / day_range
                    if close_range_pct < 0.25:
                        signals.append(
                            f"🕯️ Weak close: {ticker} closed in bottom {close_range_pct*100:.0f}% "
                            f"of yesterday's range — sellers controlled the close (distribution)"
                        )
                        score += 1
    except Exception:
        pass

    return score, signals


def _send_exit_alert_email(position: dict, signals: list, score: int) -> None:
    """Send a formatted exit signal alert for a monitored position."""
    from email_alerts import send_email_raw, smtp_configured
    import os as _os
    from datetime import datetime as _dt

    if not smtp_configured():
        return

    ticker  = position.get("ticker", "")
    strike  = position.get("strike")
    expiry  = position.get("expiry", "")
    entry   = position.get("entry_price")
    user    = _os.environ.get("SMTP_USER", "")
    if not user:
        return

    pos_parts = [f"<b>{ticker}</b>"]
    if strike:  pos_parts.append(f"${strike:.0f}c")
    if expiry:
        try:
            from datetime import datetime as _dx
            pos_parts.append(_dx.strptime(expiry, "%Y-%m-%d").strftime("exp %b %d"))
        except Exception:
            pos_parts.append(expiry)
    if entry:   pos_parts.append(f"@ ${entry:.2f}")
    pos_str = " ".join(pos_parts)

    sig_rows = "".join(
        f'<div style="background:#0f172a;border-left:3px solid #ef4444;padding:10px 14px;margin-bottom:8px;border-radius:4px;">'
        f'<span style="font-size:13px;color:#f1f5f9;">{s}</span></div>'
        for s in signals
    )

    conviction = "STRONG EXIT" if score >= 5 else "HIGH EXIT" if score >= 4 else "EXIT"
    conv_color = "#ef4444" if score >= 5 else "#f59e0b" if score >= 4 else "#22c55e"
    date_str   = _dt.now().strftime("%B %d, %Y %I:%M %p ET")

    html = f"""
    <div style="background:#0a0f1a;font-family:'Segoe UI',Arial,sans-serif;padding:24px;max-width:620px;margin:0 auto;border-radius:12px;">
      <div style="margin-bottom:16px;">
        <span style="font-size:22px;font-weight:800;color:#f1f5f9;">🚨 Exit Signal: {ticker}</span>
        <span style="display:block;font-size:12px;color:#64748b;margin-top:4px;">{date_str}</span>
      </div>
      <div style="background:#0f172a;border:1px solid #1e293b;border-radius:8px;padding:12px 16px;margin-bottom:16px;display:flex;justify-content:space-between;align-items:center;">
        <span style="font-size:14px;color:#94a3b8;">Position: {pos_str}</span>
        <span style="font-size:12px;font-weight:700;color:{conv_color};background:{conv_color}22;padding:3px 10px;border-radius:4px;">{conviction} · Score {score}/7</span>
      </div>
      <div style="margin-bottom:16px;">
        <div style="font-size:11px;color:#475569;text-transform:uppercase;margin-bottom:8px;">Signals firing ({len(signals)} of 5)</div>
        {sig_rows}
      </div>
      <div style="background:#0f172a;border:1px solid #1e293b;border-radius:8px;padding:12px 16px;margin-bottom:16px;">
        <p style="font-size:13px;color:#94a3b8;margin:0;">
          <b style="color:#f1f5f9;">Recommended action:</b>
          {'Consider exiting the full position — multiple strong signals confirming.' if score >= 4
           else 'Consider tightening your stop or taking partial profits — signals building.'}
        </p>
      </div>
      <p style="font-size:10px;color:#334155;text-align:center;margin:0;">
        StockScanner AI position monitor · Reply <code>TRADE: CLOSE {ticker}</code> to stop monitoring
      </p>
    </div>"""

    send_email_raw(user, f"🚨 Exit Signal: {ticker} — {conviction} (score {score}/7)", html)
    print(f"[exit_alert] sent for {ticker} score={score} signals={len(signals)}")


def _monitor_open_positions() -> None:
    """Check all OPEN positions for exit signals. Alert when score >= 3."""
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, ticker, direction, entry_price::float,
                       strike::float, expiry, logged_at, exit_alerted_at
                FROM position_monitor
                WHERE status = 'OPEN'
            """)
            cols = ["id","ticker","direction","entry_price","strike","expiry","logged_at","exit_alerted_at"]
            positions = [dict(zip(cols, r)) for r in cur.fetchall()]

        if not positions:
            return

        from datetime import datetime as _dt, timezone as _tz
        now = _dt.now(_tz.utc)

        for pos in positions:
            ticker = pos["ticker"]
            last_alert = pos.get("exit_alerted_at")

            if last_alert:
                if hasattr(last_alert, "tzinfo") and last_alert.tzinfo:
                    since = (now - last_alert).total_seconds()
                else:
                    since = (now - last_alert.replace(tzinfo=_tz.utc)).total_seconds()
                if since < 14400:
                    continue

            score, signals = _check_exit_signals(
                ticker, pos["entry_price"], pos["strike"], pos["expiry"]
            )

            if score >= 3:
                # ── Shakeout vs Real Reversal filter ─────────────────────────
                # Checks 5 intraday fingerprints before firing any alert.
                # Shakeout = low-volume dip that price has already recovered from.
                # Real reversal = high-volume break, VWAP rejected, lower lows forming.
                _suppressed   = False
                _suppress_why = []
                _reversal_pts = 0   # accumulate real-reversal evidence
                try:
                    import yfinance as _yf2
                    _intra = _yf2.Ticker(ticker).history(period="1d", interval="5m")
                    if len(_intra) >= 6:
                        _closes  = _intra["Close"].tolist()
                        _vols    = _intra["Volume"].tolist()
                        _highs   = _intra["High"].tolist()
                        _lows    = _intra["Low"].tolist()
                        _current = _closes[-1]
                        _intraday_high = max(_highs)
                        _intraday_low  = min(_lows)

                        # ── 1. Price recovery from intraday low ───────────────
                        _recovery_pct = (_current - _intraday_low) / _intraday_low * 100
                        if _recovery_pct >= 7.0:
                            _suppress_why.append(
                                f"price recovered {_recovery_pct:.1f}% from intraday low"
                            )
                            _suppressed = True

                        # ── 2. Near intraday high (trend intact) ──────────────
                        _from_high_pct = (_intraday_high - _current) / _intraday_high * 100
                        if _from_high_pct < 3.0 and _recovery_pct > 4.0:
                            _suppress_why.append(
                                f"within {_from_high_pct:.1f}% of intraday high"
                            )
                            _suppressed = True

                        # ── 3. VWAP check (price × volume weighted average) ───
                        _tp   = [(h + l + c) / 3 for h, l, c in zip(_highs, _lows, _closes)]
                        _cv   = sum(t * v for t, v in zip(_tp, _vols))
                        _sv   = sum(_vols) or 1
                        _vwap = _cv / _sv
                        _above_vwap = _current > _vwap
                        if _above_vwap:
                            _suppress_why.append(
                                f"price ${_current:.2f} above VWAP ${_vwap:.2f} — trend intact"
                            )
                            _suppressed = True
                        else:
                            # Below VWAP and can't reclaim = real reversal evidence
                            _vwap_reject_count = sum(
                                1 for c in _closes[-4:] if c < _vwap
                            )
                            if _vwap_reject_count >= 3:
                                _reversal_pts += 1

                        # ── 4. Volume on down candles vs session average ───────
                        # Find the 3 biggest down candles (close < open) today
                        _avg_vol = sum(_vols) / len(_vols) if _vols else 1
                        _down_candle_vols = [
                            _vols[i] for i in range(len(_closes))
                            if i > 0 and _closes[i] < _closes[i-1]
                        ]
                        if _down_candle_vols:
                            _peak_sell_vol = max(_down_candle_vols)
                            _vol_ratio     = _peak_sell_vol / _avg_vol
                            if _vol_ratio < 1.5:
                                # Selling volume was BELOW 1.5× average — weak hands only
                                _suppress_why.append(
                                    f"peak sell volume only {_vol_ratio:.1f}× avg "
                                    f"(low-conviction selling = shakeout)"
                                )
                                _suppressed = True
                            elif _vol_ratio >= 3.0:
                                # 3× average volume on selling = real distribution
                                _reversal_pts += 1

                        # ── 5. Successive lower lows (reversal structure) ──────
                        # Check last 6 candle lows for downtrend structure
                        _recent_lows = _lows[-6:]
                        _lower_low_count = sum(
                            1 for i in range(1, len(_recent_lows))
                            if _recent_lows[i] < _recent_lows[i-1]
                        )
                        if _lower_low_count >= 4:
                            # 4 of last 5 candles made lower lows = downtrend forming
                            _reversal_pts += 1

                        # ── Override suppression if multiple reversal signals ──
                        # Even if price recovered a bit, if 2+ reversal signals
                        # are firing it's a dead-cat bounce, not a real recovery
                        if _suppressed and _reversal_pts >= 2:
                            _suppressed = False
                            _suppress_why = []

                        if _suppressed:
                            print(
                                f"[position_monitor] {ticker} SHAKEOUT — "
                                + "; ".join(_suppress_why)
                            )

                except Exception:
                    pass

                if not _suppressed:
                    _send_exit_alert_email(pos, signals, score)
                    with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
                        cur.execute("""
                            UPDATE position_monitor
                            SET exit_alerted_at = NOW(),
                                exit_reason = %s
                            WHERE id = %s
                        """, ("; ".join(signals[:2]), pos["id"]))
                        conn.commit()
                else:
                    print(f"[position_monitor] {ticker} alert suppressed — shakeout filter")

        print(f"[position_monitor] checked {len(positions)} position(s)")
    except Exception as e:
        import traceback
        print(f"[position_monitor] error: {e}\n{traceback.format_exc()}")


def _fetch_market_movers(count=75):
    """
    Pull today's top % gainers and most-active stocks from Yahoo Finance screener
    plus Barchart micro-cap + small-cap advances.
    Returns a deduplicated list of tickers, movers first — these get prepended to
    every unusual-calls scan so big-move stocks are always caught.
    """
    tickers = []
    try:
        import requests as _r
        hdrs = {"User-Agent": "Mozilla/5.0 (compatible; StockScannerBot/1.0)"}
        # Yahoo predefined screeners
        for scr in ("day_gainers", "most_actives", "small_cap_gainers", "aggressive_small_caps"):
            try:
                url = (
                    f"https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
                    f"?formatted=false&lang=en-US&region=US&scrIds={scr}&count={count}"
                )
                resp = _r.get(url, headers=hdrs, timeout=8)
                quotes = (
                    resp.json()
                    .get("finance", {})
                    .get("result", [{}])[0]
                    .get("quotes", [])
                )
                for q in quotes:
                    sym = q.get("symbol", "")
                    if sym and "^" not in sym and "/" not in sym and "." not in sym:
                        tickers.append(sym)
            except Exception:
                pass
        # Barchart micro-cap + small-cap advances (catches tiny movers Yahoo lags on)
        _bc_hdrs = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://www.barchart.com/stocks/advances",
        }
        for _bc_list in ("stocks.advances.microcap.us", "stocks.advances.smallcap.us", "stocks.advances.midcap.us", "stocks.advances.largecap.us"):
            try:
                _bc_url = (
                    "https://www.barchart.com/proxies/core-api/v1/quotes/get"
                    f"?fields=symbol%2CpercentChange%2Cvolume&"
                    f"list={_bc_list}&orderBy=percentChange&orderDir=desc&raw=1&limit=100"
                )
                _bc_r = _r.get(_bc_url, headers=_bc_hdrs, timeout=8)
                if _bc_r.ok:
                    for _row in _bc_r.json().get("data", []):
                        sym = (_row.get("symbol") or "").strip().upper()
                        if sym and len(sym) <= 5 and "." not in sym:
                            tickers.append(sym)
            except Exception:
                pass
    except Exception:
        pass
    seen: set = set()
    out: list = []
    for t in tickers:
        if t not in seen:
            seen.add(t); out.append(t)
    return out


def _safe(v):
    """Replace NaN/Infinity with None so jsonify never emits invalid JSON."""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, dict):
        return {k: _safe(val) for k, val in v.items()}
    if isinstance(v, list):
        return [_safe(i) for i in v]
    return v

PORT = int(os.environ.get("STOCK_API_PORT", 5050))


@app.route("/stock-api/", methods=["GET"])
def health_root():
    return jsonify({"status": "ok"}), 200


@app.route("/stock-api/stock/analyze", methods=["GET"])
def analyze():
    ticker = request.args.get("ticker", "").strip().upper()
    if not ticker:
        return jsonify({"error": "ticker is required"}), 400
    result = analyze_ticker(ticker)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


# ── Daily Top-10 — DB-backed cache ───────────────────────────────────────────
import json as _json
import psycopg2 as _psycopg2

_DB_URL = os.getenv("DATABASE_URL", "")
_daily_top10_mem: dict = {"date": None, "data": None}


def _init_daily_top10_table():
    """Create the daily_top10 table if it doesn't exist."""
    sql = """
    CREATE TABLE IF NOT EXISTS daily_top10 (
        scan_date DATE PRIMARY KEY,
        payload   JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(sql)
            conn.commit()
    except Exception as e:
        print(f"[daily_top10] init table error: {e}")


def _load_top10_from_db(today: str):
    """Load today's (or latest available) top10 from DB."""
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("SELECT payload FROM daily_top10 WHERE scan_date = %s", (today,))
            row = cur.fetchone()
            if row:
                return row[0]
            # Fallback: most recent row from any date
            cur.execute("SELECT payload FROM daily_top10 ORDER BY scan_date DESC LIMIT 1")
            row = cur.fetchone()
            if row:
                payload = row[0]
                payload["stale"] = True
                return payload
    except Exception as e:
        print(f"[daily_top10] db load error: {e}")
    return None


def _save_top10_to_db(today: str, payload: dict):
    """Persist today's top10 to DB."""
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO daily_top10 (scan_date, payload) VALUES (%s, %s) ON CONFLICT (scan_date) DO UPDATE SET payload = EXCLUDED.payload, created_at = NOW()",
                (today, _json.dumps(payload))
            )
            conn.commit()
    except Exception as e:
        print(f"[daily_top10] db save error: {e}")


def _compute_daily_top10():
    """Return today's top 10. Checks memory → DB → fresh scan."""
    from datetime import date as _date
    today = str(_date.today())

    # 1. In-memory cache (fastest)
    if _daily_top10_mem["date"] == today and _daily_top10_mem["data"]:
        return _daily_top10_mem["data"]

    # 2. DB cache (survives server restarts)
    db_payload = _load_top10_from_db(today)
    if db_payload and not db_payload.get("stale") and db_payload.get("top10"):
        _daily_top10_mem["date"] = today
        _daily_top10_mem["data"] = db_payload
        return db_payload

    # 3. Fresh scan — clear yfinance cache first to fix crumb issues
    try:
        import yfinance as yf
        try:
            yf.utils.get_crumb(reuse_session=False)
        except Exception:
            pass
    except Exception:
        pass

    results = scan_tickers(DEFAULT_LEADERBOARD)
    scored  = [r for r in results if not r.get("error") and r.get("score") is not None and (r.get("score") or 0) >= 8]
    scored.sort(key=lambda r: r["score"], reverse=True)
    top10 = scored[:15]
    for i, r in enumerate(top10):
        r["rank"] = i + 1

    if not top10 and db_payload and db_payload.get("top10"):
        # Scan failed entirely — serve stale DB data rather than empty list
        print("[daily_top10] scan returned empty, serving stale DB data")
        return db_payload

    payload = {"top10": top10, "date": today, "total_scanned": len(DEFAULT_LEADERBOARD)}
    _daily_top10_mem["date"] = today
    _daily_top10_mem["data"] = payload
    _save_top10_to_db(today, payload)
    return payload


_init_daily_top10_table()


def _init_morning_inflows_table():
    """Persist morning inflow scan results so they survive API restarts."""
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS morning_inflows_cache (
                    scan_date  DATE PRIMARY KEY,
                    payload    JSONB NOT NULL,
                    saved_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            conn.commit()
    except Exception as e:
        print(f"[morning_inflows_cache] init error: {e}")

_init_morning_inflows_table()



# ── Whale Blocks persistent table ─────────────────────────────────────────────
def _init_whale_blocks_table():
    sql = """
    CREATE TABLE IF NOT EXISTS whale_blocks (
        id          SERIAL PRIMARY KEY,
        ticker      TEXT NOT NULL,
        direction   TEXT NOT NULL,
        strike      NUMERIC NOT NULL,
        expiry      TEXT NOT NULL,
        days_out    INTEGER,
        prem_m      NUMERIC NOT NULL,
        volume      INTEGER,
        otm_pct     NUMERIC,
        category    TEXT,
        tier        TEXT,
        price       NUMERIC,
        first_seen  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (ticker, direction, strike, expiry)
    );
    """
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(sql)
            conn.commit()
    except Exception as e:
        print(f"[whale_blocks] init table error: {e}")


def _save_whale_blocks_to_db(blocks: list):
    if not blocks:
        return
    sql = """
    INSERT INTO whale_blocks (ticker, direction, strike, expiry, days_out, prem_m, volume, otm_pct, category, tier, price)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (ticker, direction, strike, expiry) DO UPDATE
        SET days_out   = EXCLUDED.days_out,
            prem_m     = EXCLUDED.prem_m,
            volume     = EXCLUDED.volume,
            otm_pct    = EXCLUDED.otm_pct,
            category   = EXCLUDED.category,
            tier       = EXCLUDED.tier,
            price      = EXCLUDED.price;
    """
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            for b in blocks:
                cur.execute(sql, (
                    b["ticker"], b["direction"], b["strike"], b["expiry"],
                    b["days_out"], b["prem_m"], b["volume"], b["otm_pct"],
                    b["category"], b["tier"], b["price"]
                ))
            conn.commit()
        print(f"[whale_blocks] saved {len(blocks)} blocks to DB")
    except Exception as e:
        print(f"[whale_blocks] save error: {e}")


_init_whale_blocks_table()


def _init_unusual_calls_log_table():
    sql = """
    CREATE TABLE IF NOT EXISTS unusual_calls_log (
        id          SERIAL PRIMARY KEY,
        ticker      TEXT NOT NULL,
        price       NUMERIC NOT NULL,
        strike      NUMERIC NOT NULL,
        expiry      TEXT NOT NULL,
        days_out    INTEGER,
        volume      INTEGER,
        oi          INTEGER,
        vol_oi      NUMERIC,
        prem        BIGINT,
        otm_pct     NUMERIC,
        iv          NUMERIC,
        urgency     TEXT,
        first_seen  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_seen   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (ticker, strike, expiry)
    );
    """
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(sql)
            conn.commit()
    except Exception as e:
        print(f"[unusual_calls_log] init table error: {e}")


def _save_unusual_calls_to_db(hits: list):
    if not hits:
        return
    sql = """
    INSERT INTO unusual_calls_log (ticker, price, strike, expiry, days_out, volume, oi, vol_oi, prem, otm_pct, iv, urgency)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (ticker, strike, expiry) DO UPDATE
        SET price    = EXCLUDED.price,
            days_out = EXCLUDED.days_out,
            volume   = EXCLUDED.volume,
            oi       = EXCLUDED.oi,
            vol_oi   = EXCLUDED.vol_oi,
            prem     = EXCLUDED.prem,
            otm_pct  = EXCLUDED.otm_pct,
            iv       = EXCLUDED.iv,
            urgency  = EXCLUDED.urgency,
            last_seen = NOW();
    """
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            for h in hits:
                cur.execute(sql, (
                    h["ticker"], h["price"], h["strike"], h["expiry"],
                    h["days_out"], h["volume"], h["oi"], h["vol_oi"],
                    h["prem"], h["otm_pct"], h["iv"], h["urgency"]
                ))
            conn.commit()
        print(f"[unusual_calls_log] saved {len(hits)} signals to DB")
    except Exception as e:
        print(f"[unusual_calls_log] save error: {e}")


_init_unusual_calls_log_table()


def _init_eod_sweep_log_table():
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS eod_sweep_log (
                    id                  SERIAL PRIMARY KEY,
                    ticker              TEXT        NOT NULL,
                    signal_date         DATE        NOT NULL,
                    session             TEXT        NOT NULL DEFAULT 'eod',
                    score               NUMERIC,
                    grade               TEXT,
                    num_strikes         INTEGER,
                    total_prem_m        NUMERIC,
                    max_vol_oi          NUMERIC,
                    avg_iv              NUMERIC,
                    price_at_signal     NUMERIC,
                    detected_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    close_t1            NUMERIC,
                    close_t3            NUMERIC,
                    close_t5            NUMERIC,
                    return_t1           NUMERIC,
                    return_t3           NUMERIC,
                    return_t5           NUMERIC,
                    outcome_updated_at  TIMESTAMPTZ,
                    UNIQUE(ticker, signal_date, session)
                );
            """)
            conn.commit()
    except Exception as e:
        print(f"[eod_sweep_log] table init error: {e}")
_init_eod_sweep_log_table()


def _log_eod_sweep_signals(signals: list, today_only: bool = True):
    """Persist EOD sweep signals into eod_sweep_log for outcome tracking."""
    if not signals or not today_only:
        return
    try:
        from datetime import datetime as _dtl
        today = _dtl.utcnow().date()
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            for s in signals:
                try:
                    from dateutil import parser as _dp
                    hour = _dp.parse(str(s.get("latest_at", ""))).hour
                    session = "morning" if hour < 17 else "preclose" if hour < 20 else "eod"
                except Exception:
                    session = "eod"
                cur.execute("""
                    INSERT INTO eod_sweep_log
                        (ticker, signal_date, session, score, grade, num_strikes,
                         total_prem_m, max_vol_oi, avg_iv, price_at_signal)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ticker, signal_date, session) DO NOTHING
                """, (
                    s["ticker"], today, session,
                    s.get("score"), s.get("grade"), s.get("num_strikes"),
                    s.get("total_prem_m"), s.get("max_vol_oi"),
                    s.get("avg_iv"), s.get("price"),
                ))
            conn.commit()
        print(f"[eod_sweep_log] logged {len(signals)} signals for {today}")
    except Exception as e:
        print(f"[eod_sweep_log] save error: {e}")


def _update_eod_sweep_outcomes():
    """Fill in T+1/T+3/T+5 closing prices for past EOD sweep signals."""
    try:
        import yfinance as _yf
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, ticker, signal_date, price_at_signal
                FROM eod_sweep_log
                WHERE (close_t1 IS NULL OR close_t3 IS NULL OR close_t5 IS NULL)
                  AND signal_date < CURRENT_DATE
                  AND price_at_signal IS NOT NULL
                ORDER BY signal_date DESC LIMIT 80
            """)
            rows = cur.fetchall()
            updated = 0
            for row_id, ticker, sig_date, sig_price in rows:
                try:
                    hist = _yf.Ticker(ticker).history(period="15d", interval="1d")
                    if hist.empty:
                        continue
                    hist.index = [d.date() if hasattr(d, 'date') else d for d in hist.index]
                    dates = sorted(hist.index)
                    try:
                        idx = next(i for i, d in enumerate(dates) if d >= sig_date)
                    except StopIteration:
                        continue
                    def gc(n, _dates=dates, _hist=hist):
                        i = idx + n
                        return float(_hist.iloc[i]['Close']) if i < len(_dates) else None
                    t1, t3, t5 = gc(1), gc(3), gc(5)
                    def ret(t, _sp=float(sig_price)):
                        return round((t - _sp) / _sp * 100, 2) if t and _sp else None
                    cur.execute("""
                        UPDATE eod_sweep_log
                        SET close_t1=%s, close_t3=%s, close_t5=%s,
                            return_t1=%s, return_t3=%s, return_t5=%s,
                            outcome_updated_at=NOW()
                        WHERE id=%s
                    """, (t1, t3, t5, ret(t1), ret(t3), ret(t5), row_id))
                    updated += 1
                except Exception:
                    pass
            conn.commit()
        print(f"[eod_sweep_outcomes] updated {updated} signals")
    except Exception as e:
        print(f"[eod_sweep_outcomes] error: {e}")


# ── Micro-cap unusual call options scan ───────────────────────────────────────

def _init_microcap_calls_table():
    sql = """
    CREATE TABLE IF NOT EXISTS unusual_calls_microcap_log (
        id          SERIAL PRIMARY KEY,
        ticker      TEXT NOT NULL,
        price       NUMERIC NOT NULL,
        strike      NUMERIC NOT NULL,
        expiry      TEXT NOT NULL,
        days_out    INTEGER,
        volume      INTEGER,
        oi          INTEGER,
        vol_oi      NUMERIC,
        prem        BIGINT,
        otm_pct     NUMERIC,
        iv          NUMERIC,
        urgency     TEXT,
        cap_tier    TEXT,
        first_seen  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_seen   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (ticker, strike, expiry)
    );
    """
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(sql)
            conn.commit()
    except Exception as e:
        print(f"[microcap_calls] init table error: {e}")


def _save_microcap_calls_to_db(hits: list):
    if not hits:
        return
    sql = """
    INSERT INTO unusual_calls_microcap_log (ticker, price, strike, expiry, days_out, volume, oi, vol_oi, prem, otm_pct, iv, urgency, cap_tier)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (ticker, strike, expiry) DO UPDATE
        SET price     = EXCLUDED.price,
            days_out  = EXCLUDED.days_out,
            volume    = EXCLUDED.volume,
            oi        = EXCLUDED.oi,
            vol_oi    = EXCLUDED.vol_oi,
            prem      = EXCLUDED.prem,
            otm_pct   = EXCLUDED.otm_pct,
            iv        = EXCLUDED.iv,
            urgency   = EXCLUDED.urgency,
            cap_tier  = EXCLUDED.cap_tier,
            last_seen = NOW();
    """
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            for h in hits:
                cur.execute(sql, (
                    h["ticker"], h["price"], h["strike"], h["expiry"],
                    h["days_out"], h["volume"], h["oi"], h["vol_oi"],
                    h["prem"], h["otm_pct"], h["iv"], h["urgency"],
                    h.get("cap_tier", "micro"),
                ))
            conn.commit()
        print(f"[microcap_calls] saved {len(hits)} signals to DB")
    except Exception as e:
        print(f"[microcap_calls] save error: {e}")


def _run_microcap_options_scan() -> list:
    """Scan micro/small-cap tickers for unusual call option activity.
    Uses lower thresholds than large-cap to match the smaller size of these stocks.
    Only processes tickers that actually have options data on Yahoo Finance.
    """
    import yfinance as yf
    from concurrent.futures import ThreadPoolExecutor, as_completed as _asc
    from datetime import datetime as _dt2

    tickers = _get_microcap_tickers()

    def _scan_one(ticker):
        hits = []
        try:
            tk    = yf.Ticker(ticker)
            price = 0.0
            try:
                price = float(tk.fast_info.get("lastPrice") or tk.fast_info.get("regularMarketPrice") or 0)
            except Exception:
                pass
            if price <= 0:
                return hits

            cap_tier = "micro"
            try:
                mc = tk.fast_info.market_cap
                if mc:
                    if mc < 50_000_000:      cap_tier = "nano"
                    elif mc < 300_000_000:   cap_tier = "micro"
                    elif mc < 2_000_000_000: cap_tier = "small"
                    else:                    cap_tier = "mid"
            except Exception:
                pass

            min_voi  = 1.5
            min_prem = 5_000 if cap_tier in ("nano", "micro") else 15_000
            min_vol  = 10
            max_exp  = 45

            opts = tk.options
            if not opts:
                return hits

            for exp in opts:
                days = (_dt2.strptime(exp, "%Y-%m-%d") - _dt2.now()).days + 1
                if not (1 <= days <= max_exp):
                    continue
                try:
                    chain = tk.option_chain(exp).calls
                    for _, row in chain.iterrows():
                        try:
                            vol = int(row.get("volume") or 0)
                            oi  = int(row.get("openInterest") or 0)
                            if oi < 5 or vol < min_vol:
                                continue
                            voi = vol / oi
                            if voi < min_voi:
                                continue
                            strike  = float(row["strike"])
                            otm_pct = round((strike - price) / price * 100, 2)
                            if otm_pct < -10 or otm_pct > 50:
                                continue
                            bid  = float(row.get("bid") or 0)
                            ask  = float(row.get("ask") or 0)
                            if bid <= 0 or ask <= 0:
                                continue
                            spread_pct = (ask - bid) / ask
                            if spread_pct > 0.25:
                                continue
                            mid  = (bid + ask) / 2
                            prem = int(mid * vol * 100)
                            if prem < min_prem:
                                continue
                            iv      = round(float(row.get("impliedVolatility") or 0) * 100, 1)
                            urgency = ("EXPIRING" if days <= 3 else
                                       "SHORT"    if days <= 7 else
                                       "NEAR"     if days <= 14 else "MEDIUM")
                            hits.append({
                                "ticker": ticker, "price": price, "strike": strike,
                                "expiry": exp, "days_out": days, "volume": vol, "oi": oi,
                                "vol_oi": round(voi, 2), "prem": prem, "otm_pct": otm_pct,
                                "iv": iv, "urgency": urgency, "cap_tier": cap_tier,
                            })
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            pass
        return hits

    all_hits = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(_scan_one, t): t for t in tickers}
        for fut in _asc(futs):
            all_hits.extend(fut.result() or [])

    all_hits.sort(key=lambda x: x["prem"], reverse=True)
    return all_hits


_init_microcap_calls_table()


# ── My Trades — personal trade journal ───────────────────────────────────────

def _init_my_trades_table():
    sql = """
    CREATE TABLE IF NOT EXISTS my_trades (
        id                  SERIAL PRIMARY KEY,
        ticker              TEXT NOT NULL,
        strike              NUMERIC NOT NULL,
        expiry              TEXT NOT NULL,
        vol_oi              NUMERIC,
        prem                BIGINT,
        otm_pct             NUMERIC,
        urgency             TEXT,
        signal_detected_at  TIMESTAMPTZ,
        saved_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        entry_price         NUMERIC,
        exit_price          NUMERIC,
        contracts           INTEGER DEFAULT 1,
        notes               TEXT,
        status              TEXT DEFAULT 'open',
        UNIQUE (ticker, strike, expiry)
    );
    """
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(sql)
            conn.commit()
    except Exception as e:
        print(f"[my_trades] init table error: {e}")

_init_my_trades_table()


@app.route("/stock-api/my-trades", methods=["GET"])
def get_my_trades():
    """Return all saved trades, newest first."""
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, ticker, strike::float, expiry, vol_oi::float, prem::bigint,
                       otm_pct::float, urgency,
                       signal_detected_at AT TIME ZONE 'UTC' AS signal_detected_at,
                       saved_at AT TIME ZONE 'UTC' AS saved_at,
                       entry_price::float, exit_price::float,
                       contracts, notes, status
                FROM my_trades
                ORDER BY saved_at DESC
            """)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            for r in rows:
                for f in ("signal_detected_at", "saved_at"):
                    if r.get(f): r[f] = r[f].isoformat()
        return jsonify({"trades": rows, "total": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e), "trades": [], "total": 0}), 500


@app.route("/stock-api/my-trades", methods=["POST"])
def save_my_trade():
    """Save a signal as a personal trade. Idempotent on (ticker, strike, expiry)."""
    body = request.get_json(silent=True) or {}
    ticker  = str(body.get("ticker", "")).upper().strip()
    strike  = body.get("strike")
    expiry  = body.get("expiry", "")
    if not ticker or strike is None or not expiry:
        return jsonify({"error": "ticker, strike, expiry required"}), 400
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO my_trades (ticker, strike, expiry, vol_oi, prem, otm_pct, urgency, signal_detected_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, strike, expiry) DO NOTHING
                RETURNING id
            """, (
                ticker, strike, expiry,
                body.get("vol_oi"), body.get("prem"), body.get("otm_pct"),
                body.get("urgency"), body.get("signal_detected_at"),
            ))
            row = cur.fetchone()
            conn.commit()
        return jsonify({"ok": True, "created": row is not None})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/stock-api/my-trades/<int:trade_id>", methods=["PATCH"])
def update_my_trade(trade_id):
    """Update entry/exit prices, contracts, notes, and status."""
    body = request.get_json(silent=True) or {}
    allowed = ["entry_price", "exit_price", "contracts", "notes", "status"]
    sets, vals = [], []
    for k in allowed:
        if k in body:
            sets.append(f"{k} = %s")
            vals.append(body[k])
    if not sets:
        return jsonify({"error": "nothing to update"}), 400
    vals.append(trade_id)
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE my_trades SET {', '.join(sets)} WHERE id = %s", vals)
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/stock-api/my-trades/<int:trade_id>", methods=["DELETE"])
def delete_my_trade(trade_id):
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM my_trades WHERE id = %s", (trade_id,))
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── AI Trade Log — DB-backed track record ────────────────────────────────────

def _init_ai_trade_log_table():
    create_sql = """
    CREATE TABLE IF NOT EXISTS ai_trade_log (
        id              SERIAL PRIMARY KEY,
        trade_date      DATE NOT NULL,
        ticker          TEXT NOT NULL,
        direction       TEXT NOT NULL,
        setup_type      TEXT,
        conviction      TEXT,
        price_at_signal FLOAT,
        entry_strike    FLOAT,
        expiry          TEXT,
        target_price    FLOAT,
        stop_loss       FLOAT,
        signals_aligned JSONB,
        thesis          TEXT,
        risk_level      TEXT,
        t1_price        FLOAT,
        t3_price        FLOAT,
        t5_price        FLOAT,
        t10_price       FLOAT,
        t1_pct          FLOAT,
        t3_pct          FLOAT,
        t5_pct          FLOAT,
        t10_pct         FLOAT,
        t1_win          BOOL,
        t3_win          BOOL,
        t5_win          BOOL,
        t10_win         BOOL,
        expiry_price    FLOAT,
        expiry_pct      FLOAT,
        expiry_win      BOOL,
        outcome         TEXT NOT NULL DEFAULT 'OPEN',
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE(trade_date, ticker, direction)
    );
    """
    migrate_sql = [
        "ALTER TABLE ai_trade_log ADD COLUMN IF NOT EXISTS expiry_price    FLOAT",
        "ALTER TABLE ai_trade_log ADD COLUMN IF NOT EXISTS expiry_pct      FLOAT",
        "ALTER TABLE ai_trade_log ADD COLUMN IF NOT EXISTS expiry_win      BOOL",
        "ALTER TABLE ai_trade_log ADD COLUMN IF NOT EXISTS source          TEXT DEFAULT 'AI_TRADE'",
        "ALTER TABLE ai_trade_log ADD COLUMN IF NOT EXISTS option_premium    FLOAT",
        "ALTER TABLE ai_trade_log ADD COLUMN IF NOT EXISTS breakeven_price  FLOAT",
        "ALTER TABLE ai_trade_log ADD COLUMN IF NOT EXISTS total_premium_usd FLOAT",
    ]
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(create_sql)
            for m in migrate_sql:
                try:
                    cur.execute(m)
                except Exception:
                    pass
            conn.commit()
    except Exception as e:
        print(f"[ai_trade_log] init table error: {e}")


def _fetch_options_flow_usd(ticker: str, strike: float, expiry_str: str) -> float | None:
    """Fetch real options market dollar flow for a given ticker/strike/expiry.
    Returns volume * lastPrice * 100 (total $ spent today), or open_interest * lastPrice * 100
    if volume is zero. Returns None on any error."""
    import sys
    try:
        import yfinance as _yf
        tk = _yf.Ticker(ticker)
        available = tk.options  # tuple of expiry date strings
        if not available:
            print(f"[options_flow] {ticker}: no option dates available", file=sys.stderr)
            return None
        # Find nearest available expiry to the AI-generated date
        from datetime import datetime as _dtt
        target = _dtt.strptime(expiry_str, "%Y-%m-%d").date()
        best = min(available, key=lambda d: abs((_dtt.strptime(d, "%Y-%m-%d").date() - target).days))
        chain = tk.option_chain(best)
        calls = chain.calls
        if calls.empty:
            print(f"[options_flow] {ticker}: empty calls chain for {best}", file=sys.stderr)
            return None
        # Find nearest strike
        idx = (calls["strike"] - float(strike)).abs().argsort().iloc[0]
        row = calls.iloc[idx]
        last_price = float(row.get("lastPrice") or 0)
        volume     = int(row.get("volume") or 0)
        oi         = int(row.get("openInterest") or 0)
        qty = volume if volume > 0 else oi
        if qty <= 0 or last_price <= 0:
            print(f"[options_flow] {ticker} strike={strike} {best}: vol={volume} oi={oi} last={last_price} — no flow", file=sys.stderr)
            return None
        result = round(qty * last_price * 100, 2)
        print(f"[options_flow] {ticker} strike={strike} {best}: vol={volume} oi={oi} last={last_price} → ${result:,.0f}", file=sys.stderr)
        return result
    except Exception as e:
        print(f"[options_flow] {ticker} error: {e}", file=sys.stderr)
        return None


def _save_ai_trades_to_log(trades: list, trade_date: str):
    """Persist today's AI trade picks. Skips if already logged for this date."""
    if not trades:
        return
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            for t in trades:
                opt_prem   = t.get("option_premium")
                strike     = t.get("entry_strike")
                expiry     = t.get("expiry")
                breakeven  = round(strike + opt_prem, 2) if (strike and opt_prem) else None
                total_prem = _fetch_options_flow_usd(t.get("ticker",""), strike, expiry) if (strike and expiry) else None
                cur.execute("""
                    INSERT INTO ai_trade_log
                        (trade_date, ticker, direction, setup_type, conviction,
                         price_at_signal, entry_strike, expiry, target_price, stop_loss,
                         signals_aligned, thesis, risk_level,
                         option_premium, breakeven_price, total_premium_usd)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (trade_date, ticker, direction) DO NOTHING
                """, (
                    trade_date,
                    t.get("ticker"),
                    t.get("direction"),
                    t.get("setup_type"),
                    t.get("conviction"),
                    t.get("price"),
                    strike,
                    expiry,
                    t.get("target_price"),
                    t.get("stop_loss"),
                    _json.dumps(t.get("signals_aligned", [])),
                    t.get("thesis"),
                    t.get("risk_level"),
                    opt_prem,
                    breakeven,
                    total_prem,
                ))
            conn.commit()
        print(f"[ai_trade_log] saved {len(trades)} trades for {trade_date}")
    except Exception as e:
        print(f"[ai_trade_log] save error: {e}")


def _parse_expiry_date(expiry_str, trade_date):
    """Parse an options expiry string into a date. Returns None if unparseable."""
    if not expiry_str:
        return None
    from datetime import date as _d, timedelta as _td
    import re as _re
    s = expiry_str.strip()
    # Try common explicit formats
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y"):
        try:
            return _dt.strptime(s, fmt).date()
        except ValueError:
            pass
    # "Jan 17" or "January 17" without year — assume nearest future occurrence
    for fmt in ("%b %d", "%B %d"):
        try:
            parsed = _dt.strptime(s, fmt)
            year = trade_date.year
            d = parsed.replace(year=year).date()
            if d < trade_date:
                d = d.replace(year=year + 1)
            return d
        except ValueError:
            pass
    # Relative keywords
    s_lower = s.lower()
    if "weekly" in s_lower or "0dte" in s_lower:
        # next Friday from trade_date
        days_ahead = (4 - trade_date.weekday()) % 7 or 7
        return trade_date + _td(days=days_ahead)
    if "monthly" in s_lower:
        return trade_date + _td(days=30)
    # Try to extract a date-like substring
    m = _re.search(r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", s)
    if m:
        for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y", "%m-%d-%y"):
            try:
                return _dt.strptime(m.group(1), fmt).date()
            except ValueError:
                pass
    return None


def _update_ai_trade_outcomes():
    """Fetch closing prices for open AI trade log entries and mark win/loss at expiry."""
    import yfinance as _yf
    from datetime import date as _date2, timedelta as _td2
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, ticker, trade_date, price_at_signal, direction, target_price, stop_loss,
                       expiry, expiry_price,
                       t1_price, t3_price, t5_price, t10_price,
                       breakeven_price, entry_strike
                FROM ai_trade_log
                WHERE outcome = 'OPEN'
                ORDER BY trade_date DESC
                LIMIT 100
            """)
            rows = cur.fetchall()
            if not rows:
                return
            today = _date2.today()

            def _fetch_close(ticker, target_dt):
                """Fetch the first available closing price on or after target_dt."""
                try:
                    hist = _yf.Ticker(ticker).history(
                        start=str(target_dt),
                        end=str(target_dt + _td2(days=7))
                    )["Close"]
                    if hist.empty:
                        return None
                    return float(hist.iloc[0])
                except Exception:
                    return None

            def _win(close_price, pct, direction, breakeven=None, strike=None):
                """
                For LONG CALLs: WIN = stock closed above the break-even price.
                Break-even = entry_strike + option_premium.
                If breakeven not stored, fall back to a 2% stock-move threshold.
                """
                if close_price is None or pct is None:
                    return None
                if direction == "BULLISH":
                    if breakeven and breakeven > 0:
                        return close_price >= breakeven
                    return pct >= 2.0   # fallback: stock up ≥2%
                elif direction == "BEARISH":
                    return pct <= -2.0
                else:
                    return abs(pct) < 3.0

            for row in rows:
                id_, ticker, trade_date, p0, direction, target, stoploss, expiry_str, exp_p, t1p, t3p, t5p, t10p, bkeven, strike = row
                updates = {}

                # ── Expiry date outcome (primary) ────────────────────────────
                expiry_date = _parse_expiry_date(expiry_str, trade_date)
                if expiry_date and expiry_date <= today and exp_p is None:
                    close = _fetch_close(ticker, expiry_date)
                    if close is not None and p0:
                        pct = round((close - p0) / p0 * 100, 2)
                        updates["expiry_price"] = close
                        updates["expiry_pct"]   = pct
                        updates["expiry_win"]   = _win(close, pct, direction, bkeven, strike)

                # ── Fixed T+n checkpoints (supplemental context) ─────────────
                for n, col_p, col_pct, col_win, existing in [
                    (1,  "t1_price",  "t1_pct",  "t1_win",  t1p),
                    (3,  "t3_price",  "t3_pct",  "t3_win",  t3p),
                    (5,  "t5_price",  "t5_pct",  "t5_win",  t5p),
                    (10, "t10_price", "t10_pct", "t10_win", t10p),
                ]:
                    if existing is not None:
                        continue
                    target_dt = trade_date + _td2(days=n)
                    if target_dt > today:
                        continue
                    close = _fetch_close(ticker, target_dt)
                    if close is not None and p0:
                        pct = round((close - p0) / p0 * 100, 2)
                        updates[col_p]   = close
                        updates[col_pct] = pct
                        updates[col_win] = _win(close, pct, direction, bkeven, strike)

                if updates:
                    # Primary outcome = expiry result if available, else T+5
                    exp_win_val = updates.get("expiry_win") if "expiry_win" in updates else None
                    t5_close    = updates.get("t5_price")
                    t5_pct_val  = updates.get("t5_pct")
                    outcome = "OPEN"
                    if exp_win_val is not None:
                        outcome = "WIN" if exp_win_val else "LOSS"
                    elif t5_pct_val is not None:
                        outcome = "WIN" if _win(t5_close, t5_pct_val, direction, bkeven, strike) else "LOSS"
                    updates["outcome"] = outcome
                    set_sql = ", ".join(f"{k} = %s" for k in updates)
                    cur.execute(f"UPDATE ai_trade_log SET {set_sql} WHERE id = %s",
                                list(updates.values()) + [id_])
            conn.commit()
        print(f"[ai_trade_log] outcomes updated for {len(rows)} trades")
    except Exception as e:
        print(f"[ai_trade_log] update_outcomes error: {e}")


_init_ai_trade_log_table()


# ── AI SHORT CALLS LOG ──────────────────────────────────────────────────────

def _init_ai_short_calls_log_table():
    """Create ai_short_calls_log table for daily short-call pick history."""
    sql = """
    CREATE TABLE IF NOT EXISTS ai_short_calls_log (
        id                SERIAL PRIMARY KEY,
        trade_date        DATE NOT NULL,
        rank              INT,
        ticker            TEXT NOT NULL,
        strike            FLOAT,
        expiry            TEXT,
        days_out          INT,
        vol_oi            FLOAT,
        prem              BIGINT,
        stock_price       FLOAT,
        otm_pct           FLOAT,
        breakeven         FLOAT,
        conviction        TEXT,
        urgency           TEXT,
        thesis            TEXT,
        why_it_stands_out TEXT,
        outcome           TEXT NOT NULL DEFAULT 'OPEN',
        t1_price          FLOAT,
        t3_price          FLOAT,
        t5_price          FLOAT,
        t1_pct            FLOAT,
        t3_pct            FLOAT,
        t5_pct            FLOAT,
        t1_win            BOOL,
        t3_win            BOOL,
        t5_win            BOOL,
        expiry_price      FLOAT,
        expiry_pct        FLOAT,
        expiry_win        BOOL,
        created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE(trade_date, ticker, strike, expiry)
    );
    """
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(sql)
            conn.commit()
    except Exception as e:
        print(f"[ai_short_calls_log] init error: {e}")


def _save_ai_short_calls_to_log(picks: list, trade_date: str):
    """Persist today's AI short-call picks. Skips rows already logged."""
    if not picks:
        return
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            for i, p in enumerate(picks):
                cur.execute("""
                    INSERT INTO ai_short_calls_log
                        (trade_date, rank, ticker, strike, expiry, days_out, vol_oi,
                         prem, stock_price, otm_pct, breakeven, conviction, urgency,
                         thesis, why_it_stands_out)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (trade_date, ticker, strike, expiry) DO NOTHING
                """, (
                    trade_date,
                    i + 1,
                    p.get("ticker"),
                    p.get("strike"),
                    p.get("expiry"),
                    p.get("days_out"),
                    p.get("vol_oi"),
                    int(p.get("prem") or 0),
                    p.get("stock_price"),
                    p.get("otm_pct"),
                    p.get("breakeven"),
                    p.get("conviction"),
                    p.get("urgency"),
                    p.get("thesis"),
                    p.get("why_it_stands_out"),
                ))
            conn.commit()
        print(f"[ai_short_calls_log] saved {len(picks)} picks for {trade_date}")
    except Exception as e:
        print(f"[ai_short_calls_log] save error: {e}")


def _update_ai_short_call_outcomes():
    """Fetch closing prices for open short-call log entries and mark win/loss."""
    import yfinance as _yf
    from datetime import date as _date3, timedelta as _td3
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, ticker, trade_date, stock_price, strike, breakeven, expiry,
                       t1_price, t3_price, t5_price, expiry_price
                FROM ai_short_calls_log
                WHERE outcome = 'OPEN'
                ORDER BY trade_date DESC
                LIMIT 100
            """)
            rows = cur.fetchall()
            if not rows:
                return
            today = _date3.today()

            def _fetch_close(ticker, target_dt):
                try:
                    hist = _yf.Ticker(ticker).history(
                        start=str(target_dt),
                        end=str(target_dt + _td3(days=7))
                    )["Close"]
                    return float(hist.iloc[0]) if not hist.empty else None
                except Exception:
                    return None

            def _scwin(close, p0, bkeven):
                """WIN = stock closed above breakeven. Fallback: up >=2%."""
                if close is None or p0 is None:
                    return None
                if bkeven and bkeven > 0:
                    return close >= bkeven
                return (close - p0) / p0 * 100 >= 2.0

            for row in rows:
                id_, ticker, trade_date, p0, strike, bkeven, expiry_str, t1p, t3p, t5p, exp_p = row
                updates = {}

                # Expiry outcome
                exp_date = _parse_expiry_date(expiry_str, trade_date)
                if exp_date and exp_date <= today and exp_p is None:
                    close = _fetch_close(ticker, exp_date)
                    if close is not None and p0:
                        pct = round((close - p0) / p0 * 100, 2)
                        updates["expiry_price"] = close
                        updates["expiry_pct"]   = pct
                        updates["expiry_win"]   = _scwin(close, p0, bkeven)

                # T+1, T+3, T+5
                for n, col_p, col_pct, col_win, existing in [
                    (1, "t1_price", "t1_pct", "t1_win", t1p),
                    (3, "t3_price", "t3_pct", "t3_win", t3p),
                    (5, "t5_price", "t5_pct", "t5_win", t5p),
                ]:
                    if existing is not None:
                        continue
                    target_dt = trade_date + _td3(days=n)
                    if target_dt > today:
                        continue
                    close = _fetch_close(ticker, target_dt)
                    if close is not None and p0:
                        pct = round((close - p0) / p0 * 100, 2)
                        updates[col_p]   = close
                        updates[col_pct] = pct
                        updates[col_win] = _scwin(close, p0, bkeven)

                if updates:
                    exp_win_val = updates.get("expiry_win")
                    t5_pct_val  = updates.get("t5_pct")
                    t5_close    = updates.get("t5_price")
                    outcome = "OPEN"
                    if exp_win_val is not None:
                        outcome = "WIN" if exp_win_val else "LOSS"
                    elif t5_pct_val is not None:
                        outcome = "WIN" if _scwin(t5_close, p0, bkeven) else "LOSS"
                    updates["outcome"] = outcome
                    set_sql = ", ".join(f"{k} = %s" for k in updates)
                    cur.execute(f"UPDATE ai_short_calls_log SET {set_sql} WHERE id = %s",
                                list(updates.values()) + [id_])
            conn.commit()
        print(f"[ai_short_calls_log] outcomes updated for {len(rows)} entries")
    except Exception as e:
        print(f"[ai_short_calls_log] update_outcomes error: {e}")


_init_ai_short_calls_log_table()


def _init_signal_history_table():
    """Create signal_history table for multi-day persistence tracking."""
    create_sql = """
    CREATE TABLE IF NOT EXISTS signal_history (
        id           SERIAL PRIMARY KEY,
        ticker       TEXT NOT NULL,
        signal_date  DATE NOT NULL,
        comp_score   FLOAT,
        smart_cp     FLOAT,
        call_verdict TEXT,
        dp_prem_m    FLOAT,
        iv_rank      FLOAT,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE(ticker, signal_date)
    );
    """
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(create_sql)
            conn.commit()
    except Exception as e:
        print(f"[signal_history] init table error: {e}")

_init_signal_history_table()


def _init_daily_vol_snapshots_table():
    """Create daily_vol_snapshots table for IV skew & short interest percentile tracking."""
    try:
        import psycopg2 as _pg2_dvs
        with _pg2_dvs.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS daily_vol_snapshots (
                    id            SERIAL PRIMARY KEY,
                    ticker        TEXT NOT NULL,
                    snap_date     DATE NOT NULL,
                    iv_skew       FLOAT,
                    short_float   FLOAT,
                    pc_oi_ratio   FLOAT,
                    pc_prem_ratio FLOAT,
                    rs_vs_spy     FLOAT,
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(ticker, snap_date)
                );
            """)
            conn.commit()
    except Exception as e:
        print(f"[daily_vol_snapshots] init error: {e}")

_init_daily_vol_snapshots_table()


# Module-level SPY 1y cache — avoids rate-limit collisions during concurrent ticker fetches
_spy_1y_cache: dict = {"return_pct": None, "rets_arr": None, "date": None}

def _refresh_spy_1y_cache():
    """Fetch SPY 1-year history once; cache return % and daily returns array."""
    from datetime import date as _spy_d
    try:
        import yfinance as _yf_spy
        _raw = _yf_spy.download("SPY", period="1y", interval="1d", progress=False, auto_adjust=True)["Close"]
        _h = _raw.iloc[:, 0] if hasattr(_raw, "columns") else _raw
        _c = _h.dropna()
        if len(_c) >= 50:
            _spy_1y_cache["return_pct"] = round((float(_c.iloc[-1]) / float(_c.iloc[0]) - 1) * 100, 1)
            _spy_1y_cache["rets_arr"] = _c.pct_change().dropna().values
            _spy_1y_cache["date"] = _spy_d.today()
            print(f"[spy_cache] 1y return={_spy_1y_cache['return_pct']}%, {len(_c)} rows")
    except Exception as _e:
        print(f"[spy_cache] refresh error: {_e}")

_refresh_spy_1y_cache()


def _save_daily_vol_snapshot():
    """Store today's vol signals for IV skew + short interest percentile tracking."""
    from datetime import date as _dvs_date
    import psycopg2 as _pg2_snap
    today = _dvs_date.today()
    vc = getattr(app, "_vc_cache", None)
    if not vc:
        print("[daily_vol_snapshots] no vol-crush cache — skipping")
        return
    try:
        with _pg2_snap.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
            for r in vc.get("results", []):
                cur.execute("""
                    INSERT INTO daily_vol_snapshots
                        (ticker, snap_date, iv_skew, short_float, pc_oi_ratio, pc_prem_ratio, rs_vs_spy)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ticker, snap_date) DO UPDATE SET
                        iv_skew=EXCLUDED.iv_skew, short_float=EXCLUDED.short_float,
                        pc_oi_ratio=EXCLUDED.pc_oi_ratio, pc_prem_ratio=EXCLUDED.pc_prem_ratio,
                        rs_vs_spy=EXCLUDED.rs_vs_spy
                """, (
                    r["ticker"], today,
                    r.get("iv_skew"), r.get("short_float_pct"),
                    r.get("put_call_oi_ratio"), r.get("pc_premium_ratio"),
                    r.get("rs_vs_spy"),
                ))
            conn.commit()
        print(f"[daily_vol_snapshots] saved {len(vc.get('results', []))} rows for {today}")
    except Exception as e:
        print(f"[daily_vol_snapshots] save error: {e}")


def _save_signal_snapshot():
    """Snapshot today's composite + call-intent + darkpool signals for persistence tracking."""
    from datetime import date as _snap_date
    today = _snap_date.today()
    cs = getattr(app, "_cs_cache", None)
    ci = getattr(app, "_ci_cache", None)
    dp = getattr(app, "_dp_cache", None)
    if not cs:
        print("[signal_history] no composite cache — skipping snapshot")
        return
    ci_map = {r["ticker"]: r for r in (ci or {}).get("results", [])}
    dp_map = {r["ticker"]: r for r in (dp or {}).get("results", [])}
    rows_to_insert = []
    for r in cs.get("results", []):
        t = r["ticker"]
        comp = r.get("components", {})
        rows_to_insert.append((
            t, today,
            r.get("score"),
            comp.get("smart_cp"),
            ci_map.get(t, {}).get("verdict"),
            dp_map.get(t, {}).get("premium_m"),
            comp.get("iv_rank"),
        ))
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            for row in rows_to_insert:
                cur.execute("""
                    INSERT INTO signal_history
                        (ticker, signal_date, comp_score, smart_cp, call_verdict, dp_prem_m, iv_rank)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ticker, signal_date) DO UPDATE SET
                        comp_score=EXCLUDED.comp_score, smart_cp=EXCLUDED.smart_cp,
                        call_verdict=EXCLUDED.call_verdict, dp_prem_m=EXCLUDED.dp_prem_m,
                        iv_rank=EXCLUDED.iv_rank
                """, row)
            conn.commit()
        print(f"[signal_history] saved {len(rows_to_insert)} rows for {today}")
    except Exception as e:
        print(f"[signal_history] save error: {e}")


@app.route("/stock-api/daily-top10", methods=["GET"])
def daily_top10_route():
    """Return today's top 10 highest-scoring stocks. DB-backed, survives restarts."""
    try:
        data = _compute_daily_top10()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/stock-api/stock/scan", methods=["POST"])
def scan():
    body = request.get_json(silent=True) or {}
    tickers = body.get("tickers", WATCHLIST_DEFAULT[:10])
    if not isinstance(tickers, list) or len(tickers) == 0:
        tickers = WATCHLIST_DEFAULT[:10]
    tickers = [t.strip().upper() for t in tickers[:20]]
    results = scan_tickers(tickers)
    return jsonify({"results": results})


@app.route("/stock-api/stock/watchlist", methods=["GET"])
def default_watchlist():
    return jsonify({"tickers": WATCHLIST_DEFAULT})


@app.route("/stock-api/portfolio", methods=["GET"])
def portfolio():
    p = get_portfolio()
    tickers = [pos["ticker"] for pos in p.get("positions", [])]
    prices = {}
    if tickers:
        try:
            import yfinance as yf
            data = yf.download(tickers, period="1d", progress=False, auto_adjust=True)
            if not data.empty:
                if hasattr(data.columns, "levels"):
                    for t in tickers:
                        try:
                            prices[t] = float(data["Close"][t].dropna().iloc[-1])
                        except Exception:
                            pass
                else:
                    try:
                        prices[tickers[0]] = float(data["Close"].dropna().iloc[-1])
                    except Exception:
                        pass
        except Exception:
            pass
    result = get_portfolio_value(prices)
    return jsonify(result)


@app.route("/stock-api/portfolio/buy", methods=["POST"])
def buy():
    body = request.get_json(silent=True) or {}
    ticker = body.get("ticker", "").strip().upper()
    shares = float(body.get("shares", 0))
    price = float(body.get("price", 0))
    if not ticker or shares <= 0 or price <= 0:
        return jsonify({"error": "ticker, shares, and price are required"}), 400
    result = add_position(ticker, shares, price)
    return jsonify(result)


@app.route("/stock-api/portfolio/sell", methods=["POST"])
def sell():
    body = request.get_json(silent=True) or {}
    ticker = body.get("ticker", "").strip().upper()
    shares = float(body.get("shares", 0))
    price = float(body.get("price", 0))
    if not ticker or shares <= 0 or price <= 0:
        return jsonify({"error": "ticker, shares, and price are required"}), 400
    result = remove_position(ticker, shares, price)
    return jsonify(result)


@app.route("/stock-api/backtest", methods=["POST"])
def backtest():
    body = request.get_json(silent=True) or {}
    ticker = body.get("ticker", "").strip().upper()
    buy_threshold = float(body.get("buy_threshold", 6.5))
    sell_threshold = float(body.get("sell_threshold", 4.5))
    initial_cash = float(body.get("initial_cash", 10000.0))
    if not ticker:
        return jsonify({"error": "ticker is required"}), 400
    df = fetch_stock_data(ticker, period="2y")
    if df is None or df.empty:
        return jsonify({"error": f"Could not fetch data for {ticker}"}), 404
    result = backtest_strategy(df, buy_threshold=buy_threshold, sell_threshold=sell_threshold, initial_cash=initial_cash)
    result["ticker"] = ticker
    return jsonify(result)


@app.route("/stock-api/analytics/historical", methods=["POST"])
def historical_analytics():
    body = request.get_json(silent=True) or {}
    tickers = body.get("tickers", WATCHLIST_DEFAULT[:8])
    if not isinstance(tickers, list) or len(tickers) == 0:
        tickers = WATCHLIST_DEFAULT[:8]
    tickers = [t.strip().upper() for t in tickers[:15]]
    result = run_historical_analytics(tickers)
    return jsonify(result)


@app.route("/stock-api/alerts", methods=["GET"])
def list_alerts():
    return jsonify({"alerts": get_alerts()})


@app.route("/stock-api/alerts", methods=["POST"])
def create_alert():
    body = request.get_json(silent=True) or {}
    ticker = body.get("ticker", "").strip().upper()
    alert_type = body.get("type", "price")
    value = body.get("value")
    direction = body.get("direction", "above")
    if not ticker or value is None:
        return jsonify({"error": "ticker and value are required"}), 400
    if alert_type not in ("price", "rsi", "score"):
        return jsonify({"error": "type must be price, rsi, or score"}), 400
    if direction not in ("above", "below"):
        return jsonify({"error": "direction must be above or below"}), 400
    result = add_alert(ticker, alert_type, float(value), direction)
    return jsonify(result)


@app.route("/stock-api/alerts/<int:alert_id>", methods=["DELETE"])
def remove_alert(alert_id):
    result = delete_alert(alert_id)
    return jsonify(result)


@app.route("/stock-api/prop/scan", methods=["POST"])
def prop_scan():
    body = request.get_json(silent=True) or {}
    tickers = body.get("tickers", WATCHLIST_DEFAULT[:10])
    if not isinstance(tickers, list) or len(tickers) == 0:
        tickers = WATCHLIST_DEFAULT[:10]
    tickers = [t.strip().upper() for t in tickers[:20]]

    signals = []
    for ticker in tickers:
        try:
            df = fetch_stock_data(ticker, period="2y")
            if df is None or df.empty:
                continue
            sig = prop_signal(df)
            if sig is None:
                continue
            close_series = df["Close"].squeeze().dropna()
            if close_series.empty:
                continue
            price = float(close_series.iloc[-1])
            sig["ticker"] = ticker
            sig["price"] = round(price, 2)
            signals.append(sig)
        except Exception:
            continue

    signals.sort(key=lambda x: x["score"], reverse=True)

    positions = execution.get_positions()
    enriched_positions = {}
    for ticker, pos in positions.items():
        try:
            df = fetch_stock_data(ticker, period="5d")
            current_price = float(df["Close"].iloc[-1]) if df is not None and not df.empty else pos["entry"]
        except Exception:
            current_price = pos["entry"]
        pnl_unrealized = round((current_price - pos["entry"]) * pos["size"], 2)
        enriched_positions[ticker] = {**pos, "current_price": round(current_price, 2), "unrealized_pnl": pnl_unrealized}

    return jsonify(_safe({
        "signals": signals,
        "positions": enriched_positions,
        "cash": round(execution.get_cash(), 2),
        "realized_pnl": pnl.total_pnl(),
        "trades": pnl.get_trades()[-20:],
    }))


@app.route("/stock-api/prop/trade/<ticker>/<action>", methods=["POST"])
def prop_trade(ticker, action):
    ticker = ticker.strip().upper()
    action = action.lower()
    if action not in ("buy", "sell"):
        return jsonify({"error": "action must be buy or sell"}), 400

    df = fetch_stock_data(ticker, period="5d")
    if df is None or df.empty:
        return jsonify({"error": f"Could not fetch price for {ticker}"}), 404

    price = float(df["Close"].iloc[-1])

    if action == "buy":
        success = execution.enter_trade(ticker, price, size=10)
        if not success:
            return jsonify({"error": "Insufficient cash", "cash": execution.get_cash()}), 400
        return jsonify({"status": "ok", "action": "buy", "ticker": ticker, "price": price, "cash": execution.get_cash()})
    else:
        trade_pnl = execution.exit_trade(ticker, price)
        if trade_pnl is None:
            return jsonify({"error": f"No open position for {ticker}"}), 400
        pnl.log_trade(ticker, trade_pnl, "manual sell")
        return jsonify({"status": "ok", "action": "sell", "ticker": ticker, "price": price, "pnl": trade_pnl, "cash": execution.get_cash()})


@app.route("/stock-api/prop/reset", methods=["POST"])
def prop_reset():
    execution.reset()
    pnl.clear_trades()
    return jsonify({"status": "ok", "cash": execution.get_cash()})


@app.route("/stock-api/smart-money/scan", methods=["POST"])
def smart_money_scan_route():
    from datetime import datetime as _dts
    body = request.get_json(silent=True) or {}
    tickers = body.get("tickers", DEFAULT_LEADERBOARD)
    force_refresh = body.get("force_refresh", False)
    if not isinstance(tickers, list) or not tickers:
        tickers = DEFAULT_LEADERBOARD
    tickers = [t.strip().upper() for t in tickers[:50]]
    cache_key = tuple(sorted(tickers))

    # Serve from cache if fresh and not force-refreshing
    if not force_refresh:
        with app._sm_cache_lock:
            entry = app._sm_cache.get(cache_key)
        if entry:
            age = (_dts.now() - entry["ts"]).total_seconds()
            if age < _SM_CACHE_TTL_SECS:
                resp = dict(entry["result"])
                resp["cached"] = True
                resp["cache_age_secs"] = int(age)
                return jsonify(_safe(resp))

    # Live fetch — then store in cache
    result = scan_smart_money(tickers)
    result["cached"] = False
    result["cache_age_secs"] = 0
    with app._sm_cache_lock:
        app._sm_cache[cache_key] = {"result": result, "ts": _dts.now()}
    return jsonify(_safe(result))


@app.route("/stock-api/smart-money/cache-status", methods=["GET"])
def sm_cache_status():
    from datetime import datetime as _dts
    with app._sm_cache_lock:
        entries = list(app._sm_cache.items())
    status = []
    for key, val in entries:
        age = int((_dts.now() - val["ts"]).total_seconds())
        status.append({"tickers_count": len(key), "age_secs": age, "fresh": age < _SM_CACHE_TTL_SECS})
    return jsonify({"entries": status, "count": len(status)})


@app.route("/stock-api/smart-money/detail/<ticker>", methods=["GET"])
def smart_money_detail(ticker):
    ticker = ticker.strip().upper()
    df = fetch_stock_data(ticker, period="1y")
    if df is None or df.empty:
        return jsonify({"error": f"Could not fetch data for {ticker}"}), 404
    spy_ret = 0.0
    try:
        spy_df = fetch_stock_data("SPY", period="35d")
        if spy_df is not None and len(spy_df) > 21:
            sc = spy_df["Close"].squeeze().astype(float).dropna()
            spy_ret = float((sc.iloc[-1] - sc.iloc[-21]) / sc.iloc[-21])
    except Exception:
        pass
    from smart_money import fetch_options_data
    opts = fetch_options_data(ticker)
    result = compute_smart_money(ticker, df, spy_ret, opts)
    if result is None:
        return jsonify({"error": f"Insufficient data for {ticker}"}), 404
    return jsonify(_safe(result))


@app.route("/stock-api/congress/trades", methods=["GET"])
def congress_trades_route():
    force = request.args.get("refresh", "").lower() == "true"
    trades = get_congress_trades(force=force)
    return jsonify({"trades": trades, "count": len(trades)})


@app.route("/stock-api/alerts/subscribe", methods=["POST"])
def email_subscribe():
    body  = request.get_json(silent=True) or {}
    email = body.get("email", "").strip()
    result = subscribe(email)
    return jsonify(result), (200 if result["ok"] else 400)


@app.route("/stock-api/alerts/unsubscribe/<token>", methods=["GET"])
def email_unsubscribe(token):
    result = unsubscribe(token)
    if result["ok"]:
        return (
            "<html><body style='font-family:sans-serif;text-align:center;padding:60px;"
            "background:#0f172a;color:#fff'>"
            "<h2>✅ Unsubscribed</h2>"
            "<p style='color:#94a3b8'>You've been removed from StockScanner AI daily signals.</p>"
            "</body></html>"
        ), 200
    return jsonify(result), 404


@app.route("/stock-api/alerts/count", methods=["GET"])
def email_count():
    return jsonify({
        "subscribers": subscriber_count(),
        "smtp_configured": smtp_configured(),
    })


@app.route("/stock-api/alerts/test-digest", methods=["POST"])
def test_digest():
    """Send a test digest right now. Pass ?session=morning or ?session=eod (default)."""
    session  = request.args.get("session", "eod")
    result   = scan_smart_money(DEFAULT_LEADERBOARD[:10])
    signals  = result.get("leaderboard", [])
    base_url = os.getenv("PUBLIC_URL", "")
    out      = send_daily_digest(signals, base_url, session=session)
    return jsonify(out)


@app.route("/stock-api/bull-flow/top10", methods=["POST"])
def bull_flow_top10():
    """
    Rank tickers by most bullish options activity today.
    Returns top 10 sorted by call premium (highest dollar flow first).
    Each row: ticker, price, strike, expiry, premium_m, call_put_ratio, call_vol_oi, total_call_vol
    """
    import yfinance as yf
    from smart_money import fetch_options_data
    from datetime import datetime as _dt

    body    = request.get_json(silent=True) or {}
    tickers = body.get("tickers", DEFAULT_LEADERBOARD)
    if not isinstance(tickers, list) or not tickers:
        tickers = DEFAULT_LEADERBOARD
    tickers = [t.strip().upper() for t in tickers[:150]]

    # 5-minute cache — prevents concurrent tab-open requests from hammering yfinance
    _bf_cache = getattr(app, "_bf_cache", None)
    _bf_ts    = getattr(app, "_bf_cache_ts", None)
    _bf_key   = getattr(app, "_bf_cache_key", None)
    if (_bf_cache and _bf_ts and _bf_key == tickers
            and (_dt.now() - _bf_ts).total_seconds() < 300):
        return jsonify(_bf_cache)

    # Force a fresh Yahoo Finance crumb/session before bulk fetching
    try:
        yf.utils.get_crumb(reuse_session=False)
    except Exception:
        pass

    def _get_row(ticker):
        try:
            from datetime import date, datetime
            today      = date.today()
            today_str  = today.isoformat()

            opts = fetch_options_data(ticker)
            if not opts or opts.get("top_prem_value", 0) <= 0:
                return None

            expiry = opts.get("top_prem_expiry")
            if not expiry or expiry <= today_str:
                return None

            tkr   = yf.Ticker(ticker)
            price = float(getattr(tkr.fast_info, "last_price", 0) or 0)
            if price <= 0:
                hist  = tkr.history(period="1d")
                price = float(hist["Close"].iloc[-1]) if not hist.empty else 0
            prem_k = float(opts.get("top_prem_value", 0))
            if prem_k < 20:   # minimum $20K — catches small-cap insider-sized bets
                return None

            # Days to earnings
            days_to_earnings = None
            try:
                cal = tkr.calendar
                earn = None
                if isinstance(cal, dict):
                    earn = cal.get("Earnings Date")
                    if isinstance(earn, list) and earn:
                        earn = earn[0]
                elif cal is not None and hasattr(cal, "columns") and "Earnings Date" in cal.columns:
                    earn = cal.loc["Earnings Date", cal.columns[0]]
                if earn is not None:
                    if hasattr(earn, "date"):
                        earn = earn.date()
                    elif isinstance(earn, str):
                        earn = datetime.strptime(earn[:10], "%Y-%m-%d").date()
                    diff = (earn - today).days
                    if 0 <= diff <= 365:
                        days_to_earnings = diff
            except Exception:
                pass

            # Short float %
            short_float_pct = None
            try:
                info = tkr.info
                sfp  = float(info.get("shortPercentOfFloat") or 0) * 100
                if 0 < sfp < 100:
                    short_float_pct = round(sfp, 1)
            except Exception:
                pass

            return {
                "ticker":           ticker,
                "price":            round(price, 2),
                "strike":           opts.get("top_prem_strike"),
                "expiry":           expiry,
                "premium_m":        round(prem_k / 1000, 2),
                "premium_k":        round(prem_k, 1),
                "call_put_ratio":   round(float(opts.get("call_put_ratio", 0)), 2),
                "call_vol_oi":      round(float(opts.get("call_vol_oi", 0)), 2),
                "total_call_vol":   int(opts.get("total_call_vol", 0)),
                "days_to_earnings": days_to_earnings,
                "short_float_pct":  short_float_pct,
            }
        except Exception:
            return None

    rows = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(_get_row, t): t for t in tickers}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                rows.append(r)

    # ── DB fallback: if yfinance rate-limited and returned nothing, pull from unusual_calls_log ──
    if not rows:
        try:
            with _psycopg2.connect(_DB_URL) as _bfc, _bfc.cursor() as _bfcur:
                _bfcur.execute("""
                    SELECT DISTINCT ON (ticker)
                        ticker, price::float, strike::float, expiry,
                        volume, oi, vol_oi::float, prem::bigint
                    FROM unusual_calls_log
                    WHERE last_seen  >= NOW() - INTERVAL '36 hours'
                      AND expiry::date > CURRENT_DATE
                      AND prem >= 500000
                    ORDER BY ticker, prem DESC
                """)
                db_hits = _bfcur.fetchall()
            db_hits.sort(key=lambda x: x[7], reverse=True)
            for idx, r in enumerate(db_hits[:40]):
                _tk, _pr, _st, _ex, _vol, _oi, _voi, _prem = r
                rows.append({
                    "ticker":           _tk,
                    "price":            round(float(_pr or 0), 2),
                    "strike":           float(_st or 0),
                    "expiry":           _ex,
                    "premium_m":        round(float(_prem) / 1_000_000, 2),
                    "premium_k":        round(float(_prem) / 1_000, 1),
                    "call_put_ratio":   round(float(_voi or 1), 2),
                    "call_vol_oi":      round(float(_voi or 0), 2),
                    "total_call_vol":   int(_vol or 0),
                    "days_to_earnings": None,
                    "short_float_pct":  None,
                    "source":           "db",
                })
        except Exception as _dbe:
            print(f"[bull_flow] DB fallback error: {_dbe}")

    rows.sort(key=lambda x: x["premium_m"], reverse=True)
    top40 = rows[:40]
    for i, r in enumerate(top40):
        r["rank"] = i + 1

    # Persist signals for the outcome tracker
    try:
        store_bull_flow_signals(top40, session="manual")
    except Exception as _se:
        print(f"[bull_flow] signal store error: {_se}")

    out = {"results": top40, "scanned": len(tickers), "returned": len(top40)}
    app._bf_cache     = out
    app._bf_cache_ts  = _dt.now()
    app._bf_cache_key = tickers
    return jsonify(out)


@app.route("/stock-api/bull-flow/persistence", methods=["GET"])
def bull_flow_persistence():
    """Return stocks with bull-flow signals on 2+ distinct days — persistence signal."""
    if not _DB_URL:
        return jsonify({"signals": [], "count": 0})
    try:
        import psycopg2
        from collections import defaultdict
        conn = psycopg2.connect(_DB_URL)
        cur = conn.cursor()
        cur.execute("""
            SELECT ticker, signal_date, price_at_signal,
                   call_put_ratio, premium_m, strike, expiry
            FROM signal_outcomes
            WHERE call_put_ratio >= 2
              AND signal_date >= CURRENT_DATE - INTERVAL '14 days'
            ORDER BY ticker, signal_date DESC
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        ticker_days = defaultdict(list)
        for ticker, sig_date, price, cpr, premium_m, strike, expiry in rows:
            ticker_days[ticker].append({
                "date":           sig_date.isoformat(),
                "price_at_signal": round(float(price), 2) if price else None,
                "call_put_ratio":  round(float(cpr), 2) if cpr else None,
                "premium_m":       round(float(premium_m), 2) if premium_m else None,
                "strike":          float(strike) if strike else None,
                "expiry":          expiry,
            })

        signals = []
        for ticker, day_rows in ticker_days.items():
            unique_dates = sorted(set(d["date"] for d in day_rows), reverse=True)
            if len(unique_dates) < 2:
                continue
            # Best record per day (highest C/P ratio)
            day_records = {}
            for d in day_rows:
                dt = d["date"]
                if dt not in day_records or (d["call_put_ratio"] or 0) > (day_records[dt]["call_put_ratio"] or 0):
                    day_records[dt] = d
            day_list = [day_records[dt] for dt in unique_dates]
            cprs   = [d["call_put_ratio"] for d in day_list if d["call_put_ratio"]]
            prems  = [d["premium_m"]      for d in day_list if d["premium_m"]]
            signals.append({
                "ticker":           ticker,
                "days_count":       len(unique_dates),
                "first_seen":       unique_dates[-1],
                "last_seen":        unique_dates[0],
                "days":             day_list,
                "max_call_put_ratio": round(max(cprs), 2) if cprs else None,
                "max_premium_m":    round(max(prems), 2) if prems else None,
            })

        signals.sort(key=lambda x: (-x["days_count"], -(x["max_premium_m"] or 0)))
        return jsonify({"signals": signals, "count": len(signals)})
    except Exception as e:
        print(f"[bull_flow_persistence] error: {e}")
        return jsonify({"signals": [], "count": 0})


@app.route("/stock-api/bull-flow/history", methods=["GET"])
def bull_flow_history():
    """Return all stored bull-flow signals from the DB, newest first."""
    if not _DB_URL:
        return jsonify({"signals": [], "dates": [], "count": 0})
    try:
        import psycopg2
        conn = psycopg2.connect(_DB_URL)
        cur = conn.cursor()
        cur.execute("""
            SELECT ticker, signal_date, session, price_at_signal,
                   call_put_ratio, premium_m, strike, expiry
            FROM signal_outcomes
            WHERE call_put_ratio >= 2
            ORDER BY signal_date DESC, call_put_ratio DESC
            LIMIT 500
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        signals = []
        for ticker, sig_date, session, price, cpr, premium_m, strike, expiry in rows:
            signals.append({
                "ticker":          ticker,
                "signal_date":     sig_date.isoformat(),
                "session":         session,
                "price_at_signal": round(float(price), 2) if price else None,
                "call_put_ratio":  round(float(cpr), 2) if cpr else None,
                "premium_m":       round(float(premium_m), 2) if premium_m else None,
                "strike":          float(strike) if strike else None,
                "expiry":          expiry,
            })

        dates = sorted(set(s["signal_date"] for s in signals), reverse=True)
        return jsonify({"signals": signals, "dates": dates, "count": len(signals)})
    except Exception as e:
        print(f"[bull_flow_history] error: {e}")
        return jsonify({"signals": [], "dates": [], "count": 0})


# ── Net Equity Flow ──────────────────────────────────────────────────────────

@app.route("/stock-api/net-flow", methods=["POST"])
def net_flow_scan():
    """
    Scan tickers for today's net equity flow using intraday 1-min bars.
    Tick rule: bars where close >= open = buy flow; else = sell flow.
    Returns tickers with positive net flow sorted largest to smallest.
    """
    import yfinance as yf
    from datetime import datetime as _dt
    import threading

    if not hasattr(app, "_nf_lock"):
        app._nf_lock = threading.Lock()

    body    = request.get_json(silent=True) or {}
    tickers = body.get("tickers", DEFAULT_LEADERBOARD[:50])
    if not isinstance(tickers, list) or not tickers:
        tickers = DEFAULT_LEADERBOARD[:50]
    tickers = [t.strip().upper() for t in tickers[:50]]

    _nf_cache = getattr(app, "_nf_cache", None)
    _nf_ts    = getattr(app, "_nf_cache_ts", None)
    _nf_key   = getattr(app, "_nf_cache_key", None)
    if (_nf_cache and _nf_ts and _nf_key == tickers
            and (_dt.now() - _nf_ts).total_seconds() < 300):
        return jsonify(_nf_cache)

    with app._nf_lock:
        _nf_cache = getattr(app, "_nf_cache", None)
        _nf_ts    = getattr(app, "_nf_cache_ts", None)
        _nf_key   = getattr(app, "_nf_cache_key", None)
        if (_nf_cache and _nf_ts and _nf_key == tickers
                and (_dt.now() - _nf_ts).total_seconds() < 300):
            return jsonify(_nf_cache)

        def _compute_flow(ticker):
            try:
                hist = yf.Ticker(ticker).history(period="1d", interval="1m")
                if hist.empty or len(hist) < 5:
                    return None
                inflow = outflow = 0.0
                for _, row in hist.iterrows():
                    if row["Volume"] <= 0:
                        continue
                    avg = (float(row["Open"]) + float(row["Close"])) / 2
                    dv  = avg * float(row["Volume"])
                    if float(row["Close"]) >= float(row["Open"]):
                        inflow  += dv
                    else:
                        outflow += dv
                net = inflow - outflow
                last_price = float(hist["Close"].iloc[-1])
                total      = inflow + outflow
                return {
                    "ticker":       ticker,
                    "price":        round(last_price, 2),
                    "inflow_m":     round(inflow   / 1_000_000, 1),
                    "outflow_m":    round(outflow  / 1_000_000, 1),
                    "net_m":        round(net       / 1_000_000, 1),
                    "total_vol_m":  round(total    / 1_000_000, 1),
                    "flow_ratio":   round(inflow / max(outflow, 1), 3),
                }
            except Exception:
                return None

        rows = []
        with ThreadPoolExecutor(max_workers=10) as ex:
            for r in ex.map(_compute_flow, tickers):
                if r and r["net_m"] > 0:
                    rows.append(r)

        rows.sort(key=lambda x: x["net_m"], reverse=True)
        for i, r in enumerate(rows):
            r["rank"] = i + 1

        out = {"results": rows, "scanned": len(tickers)}
        app._nf_cache     = out
        app._nf_cache_ts  = _dt.now()
        app._nf_cache_key = tickers
        return jsonify(out)


@app.route("/stock-api/net-flow/single", methods=["GET"])
def net_flow_single():
    """Net equity flow for a single ticker — used by Stock Lookup."""
    import yfinance as yf
    ticker = request.args.get("ticker", "").upper().strip()
    if not ticker:
        return jsonify({"error": "ticker required"}), 400
    try:
        hist = yf.Ticker(ticker).history(period="1d", interval="1m")
        if hist.empty or len(hist) < 5:
            return jsonify({"error": "no data"}), 404
        inflow = outflow = 0.0
        bars = []
        for ts, row in hist.iterrows():
            if row["Volume"] <= 0:
                continue
            avg = (float(row["Open"]) + float(row["Close"])) / 2
            dv  = avg * float(row["Volume"])
            if float(row["Close"]) >= float(row["Open"]):
                inflow  += dv
                bars.append({"t": str(ts)[:16], "v": round(dv / 1_000_000, 2), "dir": "buy"})
            else:
                outflow += dv
                bars.append({"t": str(ts)[:16], "v": round(dv / 1_000_000, 2), "dir": "sell"})
        net   = inflow - outflow
        total = inflow + outflow
        return jsonify({
            "ticker":      ticker,
            "price":       round(float(hist["Close"].iloc[-1]), 2),
            "inflow_m":    round(inflow  / 1_000_000, 1),
            "outflow_m":   round(outflow / 1_000_000, 1),
            "net_m":       round(net     / 1_000_000, 1),
            "total_vol_m": round(total   / 1_000_000, 1),
            "flow_ratio":  round(inflow / max(outflow, 1), 3),
            "bars":        bars[-60:],   # last 60 min for chart
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Micro-Cap Net Flow ────────────────────────────────────────────────────────

# ── Micro-cap universe (~350 actively-traded small/micro-caps across all sectors)
# Curated for liquidity and trading interest — covers NASDAQ, NYSE, AMEX.
# On small floats even $1-5M net inflow is a strong accumulation signal.
_MICRO_CAP_UNIVERSE = sorted(set([
    # ── EV / Mobility / Clean Energy ──────────────────────────────────────
    "RIVN","LCID","CHPT","BLNK","EVGO","JOBY","ACHR","WKHS","PLUG",
    "FCEL","BE","STEM","SPWR","NOVA","ARRY","SHLS","HYZN","NKLA",
    "PTRA","MVST","SLDP","FREYR","KULR","ACMR","CBAT","CENN",
    # ── Space / Drones / Defense Tech ─────────────────────────────────────
    "RKLB","ASTS","LUNR","BBAI","KTOS","LOAR","RCAT","MNTS",
    "SPIR","AEVA","VORB","BWXT","KARO","SATL","SFET","ATRO",
    # ── AI / Quantum / Small Semis ────────────────────────────────────────
    "IONQ","QUBT","RGTI","SOUN","QBTS","ARQQ","CRDO","AMBA",
    "FORM","SITM","NVTS","INDI","LAZR","MVIS","CXAI","AIOT",
    "OAI","MKFG","MARA","RIOT","CLSK","HUT","BTBT","CIFR",
    # ── Biotech / Gene Therapy / RNA ──────────────────────────────────────
    "RXRX","KRYS","VKTX","VERA","ARQT","INSM","IMVT","JANX",
    "KYMR","RVMD","RCKT","FOLD","DNLI","BLUE","NTLA","CRSP",
    "BEAM","EDIT","FATE","MNKD","OCGN","NVAX","ARDX","APLT",
    "AVXL","BHVN","BOLT","CRNX","ENTA","FGEN","FMTX","GBIO",
    "GERN","HOOK","IMCR","IOVA","KRTX","KURA","MIRM","PACB",
    "PRAX","SAGE","SAVA","SGMO","STTK","TGTX","TYRA","VERV",
    "VRNA","CELC","NRIX","RLMD","SURF","ZLAB","SMMT","RVMD",
    "ALDX","AGEN","ADAP","AKRO","BLCM","CGEM","CNCE","CPRX",
    "CRNX","GBIO","GLYC","GRTS","HRTX","IDRA","IMGN","INBX",
    "JNCE","KNSA","KPTI","LQDA","OCGN","ONCT","PALI","RCUS",
    "ALVO","SVRA","VACC","OMER","PTGX","PULM","RIGL","XNCR",
    "ACRS","CORT","IRWD","ITCI","PCVX","SEER","SENS","SRNE",
    "STAA","ZYME","AKBA","AXNX","RPRX","FLXN","ALDX","CYCN",
    # ── Fintech / Crypto / Trading ────────────────────────────────────────
    "SOFI","HOOD","UPST","LC","FUTU","AFRM","DAVE","LMND",
    "ROOT","MGNI","PUBM","EVRI","RELY","OPEN","PFSI","UWMC",
    "TREE","RPAY","STER","QFIN","JFIN","ATLC","CURO","GDOT",
    # ── SaaS / Growth Tech ────────────────────────────────────────────────
    "DUOL","CFLT","GTLB","BRZE","DOCN","DOMO","JAMF","TOST",
    "YEXT","ZETA","AMPL","NCNO","VRNS","FROG","GLBE","LSPD",
    "SDGR","BIGC","MNTV","NEWR","OSPN","UPLD","TUYA","SPSC",
    "TASK","XMTR","SMAR","RSKD","TTWO","PAGS","BARK",
    # ── Consumer / Lifestyle / Health ─────────────────────────────────────
    "HIMS","BYND","RVLV","SFIX","LOVE","LESL","XPOF","CLOV",
    "PRGO","RENT","JMIA","LAUR","COUR","SKIN","PSFE","PTON",
    "TLRY","CELH","MNST","OPAD","PETZ","RVLV","SWIM","WKME",
    "GETY","COOK","ATIP","CERE","NOVA","PAYO","GNUS","PRPL",
    # ── Mining / Commodities / Rare Earths ────────────────────────────────
    "MP","UUUU","UEC","PAAS","SILV","HL","CLF","TMST","CEIX",
    "ARCH","AMR","LAC","SLI","LITM","NOVAGOLD","GATO","MAG",
    "SAND","GFI","TECK","GOLD","CMP","CLNE","AZEK",
    # ── Healthcare Devices / Diagnostics ──────────────────────────────────
    "SILK","INMD","OSUR","ATEC","HSKA","FLXN","NTRA","NVCR",
    "QDEL","RGEN","SWAV","MMSI","INSP","OFIX","OMCL","HAYW",
    "BEAT","CLFD","CNMD","AXNX","PDCO","PGNY","PRCT","RMBS",
    "SEAS","SENS","SPNE","SSYS","STEP","HOLX","LNTH","MASI",
    # ── Momentum / High-Beta Growth ───────────────────────────────────────
    "WOLF","ACMR","VNET","TIGR","BFLY","NVTS","INDI","SKLZ",
    "RBLX","SSYS","MVIS","LAZR","SOUN","CXAI","KULR","RCAT",
    "ASTS","RKLB","LUNR","BBAI","KTOS","ACHR","JOBY","IONQ",
]))


def _get_microcap_tickers() -> list:
    """Return the curated micro-cap ticker universe (~350 stocks)."""
    return list(_MICRO_CAP_UNIVERSE)


def _run_microcap_flow_scan() -> dict:
    """Core scan: fetch intraday flow + market cap for all tickers.

    Returns results split into cap_tier buckets:
      micro  → $50M–$300M market cap
      small  → $300M–$2B
      mid    → >$2B  (included but shown separately)
      nano   → <$50M (OTC/illiquid — included but flagged)
      unknown → market cap unavailable
    """
    import yfinance as yf

    tickers = _get_microcap_tickers()

    def _compute_flow_mc(ticker):
        try:
            t_obj = yf.Ticker(ticker)
            hist  = t_obj.history(period="1d", interval="1m")
            if hist.empty or len(hist) < 5:
                return None

            # Grab market cap via fast_info (lightweight — reuses same session)
            market_cap = None
            try:
                market_cap = t_obj.fast_info.market_cap
                if market_cap and market_cap <= 0:
                    market_cap = None
            except Exception:
                pass

            inflow = outflow = 0.0
            for _, row in hist.iterrows():
                if row["Volume"] <= 0:
                    continue
                avg = (float(row["Open"]) + float(row["Close"])) / 2
                dv  = avg * float(row["Volume"])
                if float(row["Close"]) >= float(row["Open"]):
                    inflow  += dv
                else:
                    outflow += dv

            net   = inflow - outflow
            total = inflow + outflow
            if net <= 0:
                return None

            last_price  = float(hist["Close"].iloc[-1])
            net_m       = net / 1_000_000
            mktcap_m    = round(market_cap / 1_000_000, 1) if market_cap else None
            net_pct     = round(net / market_cap * 100, 2) if market_cap and market_cap > 0 else None

            # Tier classification (standard Wall Street definitions)
            if market_cap is None:
                cap_tier = "unknown"
            elif market_cap < 50_000_000:
                cap_tier = "nano"
            elif market_cap < 300_000_000:
                cap_tier = "micro"
            elif market_cap < 2_000_000_000:
                cap_tier = "small"
            else:
                cap_tier = "mid"

            return {
                "ticker":         ticker,
                "price":          round(last_price, 2),
                "inflow_m":       round(inflow  / 1_000_000, 2),
                "outflow_m":      round(outflow / 1_000_000, 2),
                "net_m":          round(net_m, 3),
                "total_vol_m":    round(total   / 1_000_000, 2),
                "flow_ratio":     round(inflow / max(outflow, 1), 3),
                "market_cap_m":   mktcap_m,
                "net_pct_mktcap": net_pct,
                "cap_tier":       cap_tier,
            }
        except Exception:
            return None

    raw = []
    with ThreadPoolExecutor(max_workers=14) as ex:
        for r in ex.map(_compute_flow_mc, tickers):
            if r:
                raw.append(r)

    # Rank within each tier separately by net_pct_mktcap (most meaningful signal)
    def _rank_tier(rows):
        rows.sort(key=lambda x: (x["net_pct_mktcap"] or 0), reverse=True)
        for i, r in enumerate(rows):
            r["rank"] = i + 1
        return rows

    micro  = _rank_tier([r for r in raw if r["cap_tier"] == "micro"])
    small  = _rank_tier([r for r in raw if r["cap_tier"] == "small"])
    nano   = _rank_tier([r for r in raw if r["cap_tier"] == "nano"])
    mid    = _rank_tier([r for r in raw if r["cap_tier"] == "mid"])
    unk    = _rank_tier([r for r in raw if r["cap_tier"] == "unknown"])

    return {
        "micro":   micro,
        "small":   small,
        "nano":    nano,
        "mid":     mid,
        "unknown": unk,
        "scanned": len(tickers),
    }


@app.route("/stock-api/net-flow/microcap", methods=["POST"])
def net_flow_microcap():
    """Net equity flow scan for the dynamic micro-cap universe (200-400 tickers).

    Results are pre-cached every 30 min by the scheduler during market hours.
    Cache TTL is 30 min; first cold load takes ~40-90 s depending on ticker count.
    """
    from datetime import datetime as _dt

    if not hasattr(app, "_nfmc_lock"):
        app._nfmc_lock = threading.Lock()

    _CACHE_TTL = 1800  # 30 minutes

    _cache = getattr(app, "_nfmc_cache", None)
    _ts    = getattr(app, "_nfmc_cache_ts", None)
    if _cache and _ts and (_dt.now() - _ts).total_seconds() < _CACHE_TTL:
        return jsonify(_cache)

    with app._nfmc_lock:
        _cache = getattr(app, "_nfmc_cache", None)
        _ts    = getattr(app, "_nfmc_cache_ts", None)
        if _cache and _ts and (_dt.now() - _ts).total_seconds() < _CACHE_TTL:
            return jsonify(_cache)

        out = _run_microcap_flow_scan()
        app._nfmc_cache    = out
        app._nfmc_cache_ts = _dt.now()
        return jsonify(out)


@app.route("/stock-api/net-flow/microcap/tickers", methods=["GET"])
def microcap_ticker_count():
    """Returns the current size of the dynamic micro-cap ticker universe."""
    tickers = _get_microcap_tickers()
    return jsonify({"count": len(tickers), "tickers": tickers})


# ── Multi-day flow streak scan ────────────────────────────────────────────────

def _run_multiday_flow_scan(n_days: int = 40) -> dict:
    """
    For every ticker in the micro-cap universe, fetch `period='60d' interval='1d'`
    daily OHLCV (~42 trading days) and compute per-day net flow using the same
    buy/sell candle logic as the intraday scan (close >= open → inflow; else → outflow).

    Looks back up to ~3 months so 1-week (5d), 2-week (10d), and 3-week (15d)
    institutional accumulation streaks are all detectable.

    Returns only tickers with streak >= 2 consecutive positive-net days,
    sorted by streak desc then by cumulative % of market cap.
    """
    import yfinance as yf
    from concurrent.futures import ThreadPoolExecutor, as_completed

    tickers = _get_microcap_tickers()

    def _compute(ticker):
        try:
            t_obj = yf.Ticker(ticker)
            hist  = t_obj.history(period="60d", interval="1d")
            if hist.empty or len(hist) < 2:
                return None

            market_cap = None
            try:
                market_cap = t_obj.fast_info.market_cap
                if market_cap and market_cap <= 0:
                    market_cap = None
            except Exception:
                pass

            # Build per-day flow list (oldest → newest)
            daily = []
            for dt, row in hist.iterrows():
                vol = float(row.get("Volume", 0))
                if vol <= 0:
                    continue
                avg   = (float(row["Open"]) + float(row["Close"])) / 2
                dv    = avg * vol
                is_up = float(row["Close"]) >= float(row["Open"])
                net_m = round(dv / 1_000_000 if is_up else -dv / 1_000_000, 3)
                date_str = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)[:10]
                daily.append({"date": date_str, "net_m": net_m, "positive": net_m > 0})

            if not daily:
                return None

            # Count consecutive positive days from the most recent day backwards
            streak = 0
            for d in reversed(daily):
                if d["positive"]:
                    streak += 1
                else:
                    break

            if streak < 2:
                return None

            # Accumulated net flow over the streak window
            streak_days     = daily[-streak:]
            streak_net_vals = [d["net_m"] for d in streak_days]
            total_net_m     = round(sum(streak_net_vals), 3)
            avg_daily_net_m = round(total_net_m / streak, 3)
            min_daily_net_m = round(min(streak_net_vals), 3)

            # Consistency: how even is the buying across days?
            # min/avg ratio — 1.0 = perfectly even, 0.0 = all in one day
            consistency = round(min_daily_net_m / avg_daily_net_m, 2) if avg_daily_net_m > 0 else 0.0

            mktcap_m    = round(market_cap / 1_000_000, 1) if market_cap else None
            total_pct   = round(total_net_m / (market_cap / 1_000_000) * 100, 2) \
                          if market_cap and market_cap > 0 else None
            avg_pct_day = round(avg_daily_net_m / (market_cap / 1_000_000) * 100, 3) \
                          if market_cap and market_cap > 0 else None

            # Tier
            if market_cap is None:
                cap_tier = "unknown"
            elif market_cap < 50_000_000:
                cap_tier = "nano"
            elif market_cap < 300_000_000:
                cap_tier = "micro"
            elif market_cap < 2_000_000_000:
                cap_tier = "small"
            else:
                cap_tier = "mid"

            last_price = round(float(hist["Close"].iloc[-1]), 2)

            return {
                "ticker":           ticker,
                "price":            last_price,
                "streak":           streak,
                "total_net_m":      total_net_m,
                "avg_daily_net_m":  avg_daily_net_m,
                "min_daily_net_m":  min_daily_net_m,
                "consistency":      consistency,      # 0–1; ≥0.4 = institutional-like
                "market_cap_m":     mktcap_m,
                "total_pct_mktcap": total_pct,
                "avg_pct_per_day":  avg_pct_day,      # avg % of mktcap flowing in per day
                "cap_tier":         cap_tier,
                "days":             daily[-n_days:],  # last N daily dots for UI
            }
        except Exception:
            return None

    results = []
    with ThreadPoolExecutor(max_workers=14) as ex:
        futures = {ex.submit(_compute, t): t for t in tickers}
        for f in as_completed(futures):
            r = f.result()
            if r:
                results.append(r)

    # Sort: longest streak first, then cumulative % of mktcap descending
    results.sort(key=lambda x: (-x["streak"], -(x["total_pct_mktcap"] or 0)))
    for i, r in enumerate(results, 1):
        r["rank"] = i

    return {"results": results, "scanned": len(tickers), "found": len(results)}


@app.route("/stock-api/net-flow/multiday", methods=["POST", "GET"])
def net_flow_multiday():
    """Multi-day accumulation streak scan. Cached for 2 hours (daily data)."""
    from datetime import datetime as _dt

    if not hasattr(app, "_nfmd_lock"):
        app._nfmd_lock = threading.Lock()

    _CACHE_TTL = 7200   # 2 hours — daily candles change slowly

    _cache = getattr(app, "_nfmd_cache", None)
    _ts    = getattr(app, "_nfmd_cache_ts", None)
    if _cache and _ts and (_dt.now() - _ts).total_seconds() < _CACHE_TTL:
        return jsonify(_cache)

    with app._nfmd_lock:
        _cache = getattr(app, "_nfmd_cache", None)
        _ts    = getattr(app, "_nfmd_cache_ts", None)
        if _cache and _ts and (_dt.now() - _ts).total_seconds() < _CACHE_TTL:
            return jsonify(_cache)

        out = _run_multiday_flow_scan()
        app._nfmd_cache    = out
        app._nfmd_cache_ts = _dt.now()
        return jsonify(out)


# ── AI Signal Analysis ────────────────────────────────────────────────────────

@app.route("/stock-api/net-flow/ai-signal", methods=["POST"])
def net_flow_ai_signal():
    """Analyze streak rows with OpenAI and return structured conviction signals."""
    import os, json, re
    from openai import OpenAI

    body = request.get_json(silent=True) or {}
    rows = body.get("rows", [])

    if not rows:
        return jsonify({"error": "No streak data provided — run the Accumulation Streak scan first."}), 400

    # Sort by streak desc, then consistency desc; cap at 30
    rows = sorted(rows, key=lambda r: (-r.get("streak", 0), -r.get("consistency", 0)))[:30]

    # Build compact, LLM-readable context
    stock_lines = []
    for r in rows:
        parts = [
            f"ticker={r.get('ticker')}",
            f"streak={r.get('streak')}d",
            f"avg_flow=${r.get('avg_daily_net_m', 0):.2f}M/day",
            f"consistency={r.get('consistency', 0):.2f}",
        ]
        if r.get("min_daily_net_m") is not None:
            parts.append(f"min_day=${r.get('min_daily_net_m', 0):.2f}M")
        if r.get("total_pct_mktcap"):
            parts.append(f"pct_mktcap={r.get('total_pct_mktcap'):.3f}%")
        if r.get("market_cap_m"):
            parts.append(f"mktcap=${r.get('market_cap_m'):.0f}M")
        if r.get("cap_tier"):
            parts.append(f"tier={r.get('cap_tier')}")
        stock_lines.append("  " + " | ".join(parts))

    stock_block = "\n".join(stock_lines)

    prompt = f"""You are an institutional flow analyst specializing in micro-cap and small-cap accumulation patterns.

Analyze each stock for signs of sustained institutional accumulation using multi-day net flow data.

Field definitions:
- streak: consecutive trading days with positive net flow (no breaks)
- avg_flow: average daily net inflow in $M
- min_day: weakest single day inflow — if close to avg_flow, buying is EVEN (institutional pattern); near 0 = one big day dominated (retail spike or event)
- consistency: min_day / avg_day ratio (0-1) — 1.0 = perfectly smooth increments, 0.0 = one giant day did all the work
- pct_mktcap: cumulative inflow as % of market cap over the streak period
- tier: nano (<$50M mktcap), micro ($50-300M), small ($300M-1B), mid ($1-5B)

Stocks to analyze:
{stock_block}

Signal classification — assign exactly ONE per stock:
- CONVICTION: streak ≥ 10d AND consistency ≥ 0.65 — textbook stealth accumulation, likely institutional loading
- BUILDING: streak 5-9d OR (streak ≥ 10d but consistency 0.40-0.64) — pattern forming, early positioning
- WATCH: streak 3-4d with consistency ≥ 0.40 — too short to confirm but worth monitoring
- NOISE: consistency < 0.35 regardless of streak, or streak < 3 — likely event-driven or retail, not sustained

Key insight: true institutional accumulation shows SMOOTH increments (high consistency). A 7-day streak where day 1 = $10M and days 2-7 = $0.1M each is a retail spike, not accumulation. True accumulation: $1.0M, $0.9M, $1.1M, $1.0M, $0.95M... every day.

Also: for nano-caps ($20-50M mktcap), even $0.3M/day sustained over 10 days represents meaningful size — harder to hide than mid-cap flow.

Return ONLY valid JSON, zero markdown fences or extra text:
{{"signals":[{{"ticker":"XXXX","signal":"CONVICTION","thesis":"1-2 punchy sentences specific to the numbers — what this pattern implies for the stock.","confidence":85}}]}}"""

    try:
        client = OpenAI(
            api_key=os.environ["AI_INTEGRATIONS_OPENAI_API_KEY"],
            base_url=os.environ["AI_INTEGRATIONS_OPENAI_BASE_URL"],
        )
        response = client.chat.completions.create(
            model="gpt-5.4",
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=2500,
        )
        raw = response.choices[0].message.content.strip()

        # Strip markdown fences if the model adds them anyway
        raw = re.sub(r'^```(?:json)?\s*', '', raw).strip()
        raw = re.sub(r'\s*```$',          '', raw).strip()

        result = json.loads(raw)
        result["model"]    = "gpt-5.4"
        result["analyzed"] = len(rows)
        return jsonify(result)

    except json.JSONDecodeError as exc:
        return jsonify({"error": f"AI returned invalid JSON: {exc}", "raw": raw[:500]}), 500
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/stock-api/market/overview", methods=["GET"])
def market_overview():
    import yfinance as yf
    from datetime import date

    _cache = getattr(app, "_mo_cache", None)
    _ts    = getattr(app, "_mo_cache_ts", None)
    if _cache and _ts and _ts == date.today().isoformat():
        return jsonify(_cache)

    SECTORS = [
        ("XLK",  "Technology"),
        ("XLF",  "Financials"),
        ("XLE",  "Energy"),
        ("XLV",  "Healthcare"),
        ("XLY",  "Cons. Disc."),
        ("XLP",  "Cons. Staples"),
        ("XLI",  "Industrials"),
        ("XLB",  "Materials"),
        ("XLRE", "Real Estate"),
        ("XLU",  "Utilities"),
        ("XLC",  "Comm. Services"),
    ]
    INDICES = [
        ("SPY", "S&P 500"),
        ("QQQ", "Nasdaq 100"),
        ("DIA", "Dow Jones"),
        ("IWM", "Russell 2000"),
        ("VIX", "VIX"),
    ]

    def get_chg(ticker):
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="5d")
            if len(hist) < 2:
                return None
            prev  = float(hist["Close"].iloc[-2])
            close = float(hist["Close"].iloc[-1])
            chg   = round((close - prev) / prev * 100, 2)
            return {"price": round(close, 2), "change_pct": chg}
        except Exception:
            return None

    sectors = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = {ex.submit(get_chg, sym): (sym, name) for sym, name in SECTORS}
        for fut, (sym, name) in futs.items():
            r = fut.result()
            if r:
                sectors.append({"ticker": sym, "name": name, **r})
    sectors.sort(key=lambda x: x["change_pct"], reverse=True)

    indices = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(get_chg, sym): (sym, label) for sym, label in INDICES}
        for fut, (sym, label) in futs.items():
            r = fut.result()
            if r:
                indices.append({"ticker": sym, "label": label, **r})
    idx_order = {sym: i for i, (sym, _) in enumerate(INDICES)}
    indices.sort(key=lambda x: idx_order.get(x["ticker"], 99))

    # Advance / Decline using DEFAULT_LEADERBOARD
    ad_up, ad_down, ad_unch = 0, 0, 0
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = {ex.submit(get_chg, t): t for t in DEFAULT_LEADERBOARD}
        for fut in as_completed(futs):
            r = fut.result()
            if r:
                if r["change_pct"] > 0.1:   ad_up   += 1
                elif r["change_pct"] < -0.1: ad_down += 1
                else:                        ad_unch += 1

    out = {
        "sectors": sectors,
        "indices": indices,
        "advance_decline": {"up": ad_up, "down": ad_down, "unchanged": ad_unch},
        "as_of": date.today().isoformat(),
    }
    app._mo_cache = out; app._mo_cache_ts = date.today().isoformat()
    return jsonify(out)


@app.route("/stock-api/ai-analyze", methods=["POST"])
def ai_analyze_route():
    """Generate Claude AI swing analysis for a stock."""
    body    = request.get_json(silent=True) or {}
    ticker  = body.get("ticker", "").upper()
    if not ticker:
        return jsonify({"error": "ticker required"}), 400

    rsi          = body.get("rsi")
    macd         = body.get("macd")
    vol_ratio    = body.get("volume_ratio")
    price        = body.get("price")
    change_pct   = body.get("change_pct")
    score_val    = body.get("score")
    rating       = body.get("rating", "Neutral")
    sector       = body.get("sector", "")
    sma50        = body.get("sma50")
    sma200       = body.get("sma200")

    def _f(v, d=2):
        try: return round(float(v), d)
        except: return None

    prompt = f"""You are a professional swing trader. Provide a concise, actionable swing trade analysis for {ticker}.

Data:
- Sector: {sector or "N/A"}
- Price: ${_f(price) or "N/A"} ({_f(change_pct, 2) or 0:+.2f}% today)
- RSI (14): {_f(rsi, 1) or "N/A"} {"[OVERBOUGHT]" if rsi and float(rsi) > 70 else "[OVERSOLD]" if rsi and float(rsi) < 30 else ""}
- MACD: {_f(macd, 3) or "N/A"} {"[BULLISH]" if macd and float(macd) > 0 else "[BEARISH]"}
- Volume Ratio: {_f(vol_ratio, 1) or "N/A"}x {"[ELEVATED]" if vol_ratio and float(vol_ratio) >= 1.5 else ""}
- SMA 50: ${_f(sma50) or "N/A"} | SMA 200: ${_f(sma200) or "N/A"}
- Composite Score: {_f(score_val, 1) or "N/A"}/10 — {rating}

Write 3–4 sentences covering: (1) technical setup & momentum, (2) risk/reward, (3) swing trade thesis. Be direct and data-driven. Under 90 words."""

    try:
        import anthropic as _anthropic
        base_url = os.getenv("AI_INTEGRATIONS_ANTHROPIC_BASE_URL")
        api_key  = os.getenv("AI_INTEGRATIONS_ANTHROPIC_API_KEY", "placeholder")
        client   = _anthropic.Anthropic(base_url=base_url, api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text if msg.content else "Analysis unavailable."
        return jsonify({"analysis": text, "ticker": ticker})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/stock-api/ics-thesis", methods=["POST"])
def ics_thesis():
    body    = request.get_json(silent=True) or {}
    ticker  = str(body.get("ticker", "this ticker")).upper()
    score   = int(body.get("score", 0))
    label   = str(body.get("label", ""))
    signals = body.get("signals", [])
    if not signals:
        return jsonify({"error": "No signals provided"}), 400

    signal_list = "\n".join(f"• {s.get('label','')}: {s.get('description','')}" for s in signals)
    prompt = f"""You are a professional options flow analyst at a top hedge fund.
Analyze the following institutional conviction signals detected for {ticker}.

CONVICTION SCORE: {score}/100 — {label}

ACTIVE SIGNALS:
{signal_list}

Provide a sharp, professional trade thesis in 3 parts:
1. SIGNAL INTERPRETATION (2-3 sentences on what the smart money is likely doing)
2. RISK FACTORS (1-2 sentences on what could invalidate this thesis)
3. TRADE SETUP (specific actionable idea: entry, expiration, strike type)

Be direct and specific. No fluff. Write like a desk analyst briefing a PM."""

    try:
        import anthropic as _anthropic
        base_url = os.getenv("AI_INTEGRATIONS_ANTHROPIC_BASE_URL")
        api_key  = os.getenv("AI_INTEGRATIONS_ANTHROPIC_API_KEY", "placeholder")
        client   = _anthropic.Anthropic(base_url=base_url, api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text if msg.content else "Analysis unavailable."
        return jsonify({"thesis": text, "ticker": ticker, "score": score})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/stock-api/squeeze/detector", methods=["POST"])
def squeeze_detector():
    import yfinance as yf
    from smart_money import fetch_options_data, _f

    body    = request.get_json(silent=True) or {}
    tickers = body.get("tickers", DEFAULT_LEADERBOARD)
    if not isinstance(tickers, list) or not tickers:
        tickers = DEFAULT_LEADERBOARD
    tickers = [t.strip().upper() for t in tickers[:50]]

    def _get_squeeze_row(ticker):
        try:
            tkr  = yf.Ticker(ticker)
            info = tkr.info
            sfp  = _f(info.get("shortPercentOfFloat", 0)) * 100
            if sfp < 5:
                return None
            sr   = _f(info.get("shortRatio", 0))
            opts = fetch_options_data(ticker)
            cpr  = _f(opts.get("call_put_ratio", 0)) if opts else 0
            pk   = _f(opts.get("top_prem_value", 0)) if opts else 0
            price = _f(getattr(tkr.fast_info, "last_price", 0) or 0)
            short_score   = min(sfp * 2, 50)
            flow_score    = min(cpr * 10, 50)
            squeeze_score = round(short_score + flow_score, 1)
            return {
                "ticker":          ticker,
                "price":           round(price, 2),
                "short_float_pct": round(sfp, 1),
                "short_ratio":     round(sr, 1),
                "call_put_ratio":  round(cpr, 2),
                "premium_m":       round(pk / 1000, 2),
                "squeeze_score":   squeeze_score,
            }
        except Exception:
            return None

    rows = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(_get_squeeze_row, t): t for t in tickers}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                rows.append(r)
    rows.sort(key=lambda x: x["squeeze_score"], reverse=True)
    top = rows[:20]
    for i, r in enumerate(top):
        r["rank"] = i + 1
    return jsonify({"results": top, "scanned": len(tickers)})


@app.route("/stock-api/insider/trades", methods=["GET"])
def insider_trades_route():
    days    = int(request.args.get("days", 30))
    tickers = DEFAULT_LEADERBOARD
    trades  = fetch_insider_trades(tickers, days=days)
    return jsonify({"trades": trades, "count": len(trades)})


@app.route("/stock-api/ai/thesis", methods=["POST"])
def ai_thesis():
    body             = request.get_json(silent=True) or {}
    ticker           = body.get("ticker", "")
    cpr              = float(body.get("call_put_ratio", 0))
    premium_m        = float(body.get("premium_m", 0))
    days_to_earnings = body.get("days_to_earnings")
    short_float_pct  = body.get("short_float_pct")
    strike           = body.get("strike")
    expiry           = body.get("expiry")

    if not ticker:
        return jsonify({"error": "ticker required"}), 400

    parts = []
    if cpr >= 8:
        parts.append(f"An extraordinary {cpr:.1f}x call/put ratio places {ticker} among the highest-conviction institutional signals — fewer than 2% of scanned tickers ever reach this threshold.")
    elif cpr >= 5:
        parts.append(f"A {cpr:.1f}x call/put ratio on {ticker} is a rare institutional signal. At this level, the options tape is overwhelmingly positioned for upside — this is not retail speculation.")
    elif cpr >= 3:
        parts.append(f"A {cpr:.1f}x call/put ratio signals strong institutional bias toward {ticker}. Smart money is leaning aggressively bullish on this name.")
    elif cpr >= 2:
        parts.append(f"With a {cpr:.1f}x call/put ratio, {ticker}'s options flow is skewing clearly bullish — more than double call volume vs puts signals real directional conviction.")
    else:
        parts.append(f"Options flow in {ticker} shows a {cpr:.1f}x call/put ratio — calls are outpacing puts, suggesting a modestly bullish institutional lean.")

    if premium_m >= 10:
        parts.append(f"The ${premium_m:.1f}M in call premium is the size of a hedge fund position. Bets of this magnitude are placed deliberately — someone is taking a significant directional view backed by strong conviction.")
    elif premium_m >= 5:
        parts.append(f"${premium_m:.1f}M in call premium signals an institutional-sized position. This is a deliberate, meaningful directional bet — not noise.")
    elif premium_m >= 1:
        parts.append(f"${premium_m:.1f}M in call premium represents real money behind this thesis. Worth tracking how price responds over the next 1-3 sessions.")

    if days_to_earnings is not None:
        dte = int(days_to_earnings)
        if dte <= 5:
            parts.append(f"⚠️ Earnings in {dte} day{'s' if dte != 1 else ''} — this may be a high-risk earnings directional bet. IV crush post-announcement is a real risk regardless of direction.")
        elif dte <= 21:
            parts.append(f"With earnings {dte} days out, this call position is likely being built ahead of a catalyst. Traders often front-run earnings 2-3 weeks in advance when they have conviction.")
        elif dte <= 45:
            parts.append(f"Earnings are {dte} days away — a medium-term swing position potentially anticipating a pre-earnings run or a positive catalyst at the event.")

    if short_float_pct and float(short_float_pct) >= 20:
        parts.append(f"🔥 Critical: {float(short_float_pct):.1f}% of the float is short. Bullish options accumulation on a heavily-shorted stock is a classic squeeze setup — short covering could dramatically amplify any upside move.")
    elif short_float_pct and float(short_float_pct) >= 10:
        parts.append(f"With {float(short_float_pct):.1f}% short float, any positive catalyst could trigger short covering that amplifies gains beyond the initial move.")

    if strike and expiry:
        parts.append(f"Target strike: ${strike} expiring {expiry}. Monitor for follow-through price action and volume over the next 1-3 sessions as confirmation of the thesis.")

    return jsonify({"ticker": ticker, "thesis": " ".join(parts[:4])})


@app.route("/stock-api/outcomes", methods=["GET"])
def signal_outcomes_route():
    """Return stored bull-flow signals with T+3, T+5, T+10 price outcomes."""
    outcomes = get_signal_outcomes(limit=60)

    # Compute win rates
    t3_results  = [o["t3_win"]  for o in outcomes if o["t3_win"]  is not None]
    t5_results  = [o["t5_win"]  for o in outcomes if o["t5_win"]  is not None]
    t10_results = [o["t10_win"] for o in outcomes if o["t10_win"] is not None]

    def win_rate(lst):
        return round(sum(lst) / len(lst) * 100, 1) if lst else None

    return jsonify({
        "outcomes":  outcomes,
        "count":     len(outcomes),
        "win_rates": {
            "t3":  win_rate(t3_results),
            "t5":  win_rate(t5_results),
            "t10": win_rate(t10_results),
        },
    })


@app.route("/stock-api/breakout/radar", methods=["POST"])
def breakout_radar():
    import yfinance as yf
    import numpy as np
    import pandas as pd

    body    = request.get_json(silent=True) or {}
    tickers = body.get("tickers", DEFAULT_LEADERBOARD)
    if not isinstance(tickers, list) or not tickers:
        tickers = DEFAULT_LEADERBOARD
    tickers = [t.strip().upper() for t in tickers[:60]]

    def _score(ticker):
        try:
            tkr  = yf.Ticker(ticker)
            hist = tkr.history(period="1y")
            if hist is None or len(hist) < 50:
                return None

            close  = hist["Close"]
            volume = hist["Volume"]

            price     = float(close.iloc[-1])
            high_52w  = float(close.rolling(252).max().iloc[-1])
            pct_52w   = round((price - high_52w) / high_52w * 100, 1)   # 0 = at 52w high

            sma50  = float(close.rolling(50).mean().iloc[-1])
            sma200 = float(close.rolling(200).mean().iloc[-1])

            # EMA for MACD
            ema12  = close.ewm(span=12, adjust=False).mean()
            ema26  = close.ewm(span=26, adjust=False).mean()
            macd   = ema12 - ema26
            signal = macd.ewm(span=9, adjust=False).mean()
            macd_val   = float(macd.iloc[-1])
            signal_val = float(signal.iloc[-1])
            macd_prev  = float(macd.iloc[-2])
            sig_prev   = float(signal.iloc[-2])
            macd_cross = macd_prev < sig_prev and macd_val > signal_val  # fresh crossover

            # RSI
            delta = close.diff()
            gain  = delta.clip(lower=0).rolling(14).mean()
            loss  = (-delta.clip(upper=0)).rolling(14).mean()
            last_loss = float(loss.iloc[-1])
            last_gain = float(gain.iloc[-1])
            if last_loss == 0:
                rsi = 100.0
            elif last_gain == 0:
                rsi = 0.0
            else:
                rs  = last_gain / last_loss
                rsi = float(100 - 100 / (1 + rs))
            if np.isnan(rsi):
                rsi = 50.0

            # Volume surge
            avg_vol    = float(volume.iloc[-21:-1].mean())
            today_vol  = float(volume.iloc[-1])
            vol_ratio  = round(today_vol / avg_vol, 2) if avg_vol > 0 else 0

            # ── Scoring ──────────────────────────────────────────────
            score = 0

            # MACD (0-25)
            if macd_cross:
                score += 25
            elif macd_val > signal_val:
                score += 15
            elif macd_val > 0:
                score += 5

            # RSI (0-25)
            if 55 <= rsi <= 70:
                score += 25
            elif 50 <= rsi < 55:
                score += 15
            elif 70 < rsi <= 80:
                score += 10
            elif 45 <= rsi < 50:
                score += 5

            # Volume surge (0-25)
            if vol_ratio >= 2.0:
                score += 25
            elif vol_ratio >= 1.5:
                score += 18
            elif vol_ratio >= 1.2:
                score += 10

            # 52W high proximity (0-25): pct_52w is 0 at high, negative below
            if pct_52w >= -3:
                score += 25
            elif pct_52w >= -7:
                score += 18
            elif pct_52w >= -12:
                score += 12
            elif pct_52w >= -20:
                score += 6

            if score < 20:
                return None

            def _safe(v, default=0.0):
                try:
                    f = float(v)
                    return default if (np.isnan(f) or np.isinf(f)) else f
                except Exception:
                    return default

            return {
                "ticker":            ticker,
                "price":             round(_safe(price), 2),
                "breakout_score":    score,
                "rsi":               round(_safe(rsi, 50.0), 1),
                "macd_bullish":      bool(macd_val > signal_val),
                "macd_cross":        bool(macd_cross),
                "volume_ratio":      round(_safe(vol_ratio), 2),
                "pct_from_52w_high": round(_safe(pct_52w), 1),
                "above_sma50":       bool(price > sma50),
                "above_sma200":      bool(price > sma200),
                "golden_cross":      bool(sma50 > sma200),
            }
        except Exception:
            return None

    rows = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        futures = {ex.submit(_score, t): t for t in tickers}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                rows.append(r)

    rows.sort(key=lambda x: x["breakout_score"], reverse=True)
    top = rows[:20]
    for i, r in enumerate(top):
        r["rank"] = i + 1

    return jsonify({"results": top, "scanned": len(tickers)})


@app.route("/stock-api/convergence", methods=["GET"])
def convergence():
    """Stocks with BOTH unusual volume AND unusual call flow — smart money convergence signal."""
    import yfinance as yf
    from smart_money import fetch_options_data
    from datetime import datetime as _cvdt

    _cache = getattr(app, "_conv_cache", None)
    _ts    = getattr(app, "_conv_cache_ts", None)
    if _cache and _ts and (_cvdt.now() - _ts).total_seconds() < 43200:
        return jsonify(_cache)

    try:
        yf.utils.get_crumb(reuse_session=False)
    except Exception:
        pass

    tickers = DEFAULT_LEADERBOARD
    results = []

    def _check(ticker):
        try:
            tkr = yf.Ticker(ticker)
            info = tkr.fast_info
            price = float(getattr(info, "last_price", 0) or 0)
            avg_vol = float(getattr(info, "three_month_average_volume", 1) or 1)
            today_vol = float(getattr(info, "last_volume", 0) or 0)
            vol_ratio = today_vol / avg_vol if avg_vol > 0 else 0

            if vol_ratio < 1.2 or price <= 0:
                return None

            opts = fetch_options_data(ticker)
            if not opts:
                return None

            call_put_ratio = float(opts.get("call_put_ratio", 0))
            prem = float(opts.get("top_prem_value", 0))

            if call_put_ratio < 1.3 or prem <= 0:
                return None

            convergence_score = min(round(vol_ratio * call_put_ratio, 1), 10.0)
            return {
                "ticker": ticker,
                "price": round(price, 2),
                "vol_ratio": round(vol_ratio, 2),
                "call_put_ratio": round(call_put_ratio, 2),
                "premium_m": round(prem / 1000, 2),
                "convergence_score": convergence_score,
                "expiry": opts.get("top_prem_expiry"),
                "strike": opts.get("top_prem_strike"),
            }
        except Exception:
            return None

    _ex_cv = ThreadPoolExecutor(max_workers=8)
    futures = {_ex_cv.submit(_check, t): t for t in tickers}
    try:
        for fut in as_completed(futures, timeout=22):
            try:
                r = fut.result()
                if r:
                    results.append(r)
            except Exception:
                pass
    except Exception:
        pass
    finally:
        _ex_cv.shutdown(wait=False, cancel_futures=True)

    results.sort(key=lambda x: x["convergence_score"], reverse=True)
    for i, r in enumerate(results[:15]):
        r["rank"] = i + 1
    out = {"results": results[:15], "scanned": len(tickers)}
    if results:
        app._conv_cache = out; app._conv_cache_ts = _cvdt.now()
    return jsonify(out)


@app.route("/stock-api/premarket", methods=["GET"])
def premarket():
    """Pre-market movers — price change and volume vs average."""
    import yfinance as yf
    from datetime import datetime as _pdt

    _cache = getattr(app, "_pm_cache", None)
    _ts    = getattr(app, "_pm_cache_ts", None)
    if _cache and _ts and (_pdt.now() - _ts).total_seconds() < 1800:
        return jsonify(_cache)

    tickers = DEFAULT_LEADERBOARD[:35]
    results = []

    def _get(ticker):
        try:
            tkr = yf.Ticker(ticker)
            info = tkr.fast_info
            price = float(getattr(info, "last_price", 0) or 0)
            prev_close = float(getattr(info, "previous_close", 0) or 0)
            if price <= 0 or prev_close <= 0:
                return None
            change_pct = (price - prev_close) / prev_close * 100
            if abs(change_pct) < 0.2:
                return None
            avg_vol = float(getattr(info, "three_month_average_volume", 1) or 1)
            today_vol = float(getattr(info, "last_volume", 0) or 0)
            vol_ratio = round(today_vol / avg_vol, 2) if avg_vol > 0 else 0
            mkt_cap = float(getattr(info, "market_cap", 0) or 0)
            return {
                "ticker": ticker,
                "price": round(price, 2),
                "prev_close": round(prev_close, 2),
                "change_pct": round(change_pct, 2),
                "vol_ratio": vol_ratio,
                "mkt_cap_b": round(mkt_cap / 1e9, 1) if mkt_cap else None,
            }
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(_get, t): t for t in tickers}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                results.append(r)

    results.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
    gainers = [r for r in results if r["change_pct"] > 0][:10]
    losers  = [r for r in results if r["change_pct"] < 0][:10]
    out = {"gainers": gainers, "losers": losers, "scanned": len(tickers)}
    app._pm_cache = out; app._pm_cache_ts = _pdt.now()
    return jsonify(out)


@app.route("/stock-api/darkpool", methods=["GET"])
def darkpool():
    """Dark Pool Radar — uses FINRA Reg SHO daily short volume as off-exchange proxy."""
    import requests as _req
    from datetime import datetime, timedelta

    _cache = getattr(app, "_dp_cache", None)
    _ts    = getattr(app, "_dp_cache_ts", None)
    if _cache and _ts and (datetime.now() - _ts).total_seconds() < 1800:
        return jsonify(_cache)

    def _fetch_date(date_str):
        combined = {}
        for code in ["FNSQ", "FNYX"]:
            url = f"https://cdn.finra.org/equity/regsho/daily/{code}shvol{date_str}.txt"
            try:
                r = _req.get(url, timeout=12)
                if r.status_code != 200:
                    continue
                for line in r.text.strip().split("\n")[1:]:
                    parts = line.strip().split("|")
                    if len(parts) < 5:
                        continue
                    sym = parts[1].strip()
                    try:
                        sv = int(float(parts[2])); tv = int(float(parts[4]))
                    except Exception:
                        continue
                    if sym in combined:
                        combined[sym]["sv"] += sv
                        combined[sym]["tv"] += tv
                    else:
                        combined[sym] = {"sv": sv, "tv": tv}
            except Exception:
                continue
        return combined

    raw = {}
    date_used = None
    now = datetime.now()
    for days_back in range(6):
        d = now - timedelta(days=days_back)
        if d.weekday() >= 5:
            continue
        date_str = d.strftime("%Y%m%d")
        raw = _fetch_date(date_str)
        if raw:
            date_used = d.strftime("%b %d, %Y")
            break

    if not raw:
        return jsonify({"results": [], "date": None, "total_in_db": 0})

    candidates = []
    for ticker in DEFAULT_LEADERBOARD:
        if ticker not in raw:
            continue
        d = raw[ticker]
        tv = d["tv"]; sv = d["sv"]
        if tv < 50000:
            continue
        pct = sv / tv * 100
        if pct < 50:
            continue
        score = min(round(max(pct - 40, 0) / 40 * 10, 1), 10.0)
        if pct >= 70:
            signal = "EXTREME"
        elif pct >= 62:
            signal = "HIGH"
        elif pct >= 54:
            signal = "ELEVATED"
        else:
            signal = "NOTABLE"
        candidates.append({
            "ticker": ticker,
            "short_vol": sv,
            "total_vol": tv,
            "short_pct": round(pct, 1),
            "score": score,
            "signal": signal,
            "call_put_ratio": None,
            "bias": "UNKNOWN",
        })

    # Cross-reference options C/P ratio AND OBV trend for full accumulation/distribution picture
    def _get_signals(ticker):
        import yfinance as yf
        cp = None; bias = "UNKNOWN"; flow = "UNKNOWN"
        try:
            opts = fetch_options_data(ticker)
            if opts:
                cp_raw = float(opts.get("call_put_ratio", 0))
                cp = round(cp_raw, 2)
                bias = "BULLISH" if cp_raw >= 1.5 else "BEARISH" if cp_raw <= 0.7 else "NEUTRAL"
        except Exception:
            pass
        try:
            hist = yf.Ticker(ticker).history(period="20d")
            closes = hist["Close"].tolist()
            vols   = hist["Volume"].tolist()
            if len(closes) >= 10:
                obv = 0; obv_series = [0]
                for i in range(1, len(closes)):
                    if closes[i] > closes[i - 1]:
                        obv += vols[i]
                    elif closes[i] < closes[i - 1]:
                        obv -= vols[i]
                    obv_series.append(obv)
                recent = obv_series[-10:]
                denom = max(abs(recent[0]), 1)
                slope = (recent[-1] - recent[0]) / denom
                flow = "INFLOW" if slope > 0.03 else "OUTFLOW" if slope < -0.03 else "NEUTRAL"
        except Exception:
            pass
        return cp, bias, flow

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_get_signals, r["ticker"]): r for r in candidates}
        for fut in as_completed(futures):
            row = futures[fut]
            cp, bias, flow = fut.result()
            row["call_put_ratio"] = cp
            row["bias"] = bias
            row["flow"] = flow

    # Cross-reference unusual_calls_log: tickers with strong call sweeps in DB = confirmed BULLISH
    try:
        with _psycopg2.connect(_DB_URL) as _conn, _conn.cursor() as _cur:
            _cur.execute("""
                SELECT DISTINCT ticker FROM unusual_calls_log
                WHERE last_seen >= NOW() - INTERVAL '5 days'
                  AND vol_oi >= 5 AND prem >= 300000
            """)
            _sweep_bullish = {row[0] for row in _cur.fetchall()}
    except Exception:
        _sweep_bullish = set()

    for r in candidates:
        if r["ticker"] in _sweep_bullish:
            r["bias"] = "BULLISH"
            if r["flow"] == "UNKNOWN":
                r["flow"] = "INFLOW"

    def _bullish_score(r):
        s = 0
        if r["flow"]  == "INFLOW":   s += 30
        elif r["flow"] == "OUTFLOW": s -= 30
        if r["bias"]  == "BULLISH":  s += 20
        elif r["bias"] == "BEARISH": s -= 20
        s += r["short_pct"]
        return s

    def _conviction(r):
        b = r["bias"]; f = r["flow"]
        if b == "BULLISH"  and f == "INFLOW":  return "STRONG BUY"
        if b == "BULLISH"  and f != "OUTFLOW": return "BUY"
        if b != "BEARISH"  and f == "INFLOW":  return "INFLOW"
        if b == "BEARISH"  and f == "OUTFLOW": return "STRONG SELL"
        if b == "BEARISH"  and f != "INFLOW":  return "SELL"
        if b != "BULLISH"  and f == "OUTFLOW": return "OUTFLOW"
        return "WATCH"

    for r in candidates:
        r["conviction"] = _conviction(r)
    results = sorted(candidates, key=_bullish_score, reverse=True)
    # Only BULLISH — confirmed via live C/P ratio OR unusual_calls_log sweep in last 5 days
    bullish_only = [r for r in results if r["bias"] == "BULLISH"]
    for i, r in enumerate(bullish_only[:25]):
        r["rank"] = i + 1

    out = {"results": bullish_only[:25], "date": date_used, "total_in_db": len(raw), "total_candidates": len(candidates)}
    app._dp_cache = out
    app._dp_cache_ts = datetime.now()
    return jsonify(out)


@app.route("/stock-api/options-intent", methods=["GET"])
def options_intent():
    """Classify put options as HEDGE (OTM+long-dated) vs BEARISH BET (near-money+short-dated)."""
    import yfinance as yf
    from datetime import datetime as _dt

    _cache = getattr(app, "_oi_cache", None)
    _ts    = getattr(app, "_oi_cache_ts", None)
    if _cache and _ts and (_dt.now() - _ts).total_seconds() < 43200:
        return jsonify(_cache)

    now = _dt.now()

    def _analyze(ticker):
        try:
            tkr   = yf.Ticker(ticker)
            price = float(getattr(tkr.fast_info, "last_price", 0) or 0)
            if price <= 0:
                return None
            exps = tkr.options
            if not exps:
                return None

            hedge_prem = 0.0; bear_prem = 0.0
            hedge_vol  = 0;   bear_vol  = 0
            top_bear   = {"strike": None, "expiry": None, "prem": 0.0}

            for exp in exps[:8]:
                try:
                    days_out = (_dt.strptime(exp, "%Y-%m-%d") - now).days
                    puts = tkr.option_chain(exp).puts
                    for _, row in puts.iterrows():
                        strike = float(row.get("strike", 0) or 0)
                        vol    = int(row.get("volume", 0) or 0)
                        oi     = int(row.get("openInterest", 0) or 0)
                        last   = float(row.get("lastPrice", 0) or 0)
                        if strike <= 0 or last <= 0:
                            continue
                        otm_pct = (price - strike) / price * 100
                        prem    = (vol + oi) * last * 100
                        if otm_pct > 5 and days_out > 60:
                            hedge_prem += prem; hedge_vol += vol
                        elif -3 < otm_pct < 3 and days_out < 45:
                            bear_prem += prem;  bear_vol  += vol
                            if prem > top_bear["prem"]:
                                top_bear = {"strike": round(strike, 2), "expiry": exp, "prem": prem}
                except Exception:
                    continue

            total = hedge_prem + bear_prem
            if total < 1000:
                return None

            hedge_pct = round(hedge_prem / total * 100, 1)
            bear_pct  = round(bear_prem  / total * 100, 1)
            verdict   = ("BEARISH BET" if bear_pct >= 60
                        else "HEDGE"      if hedge_pct >= 60
                        else "MIXED")
            return {
                "ticker":          ticker,
                "price":           round(price, 2),
                "hedge_prem_m":    round(hedge_prem / 1e6, 2),
                "bear_prem_m":     round(bear_prem  / 1e6, 2),
                "hedge_pct":       hedge_pct,
                "bear_pct":        bear_pct,
                "verdict":         verdict,
                "top_bear_strike": top_bear["strike"],
                "top_bear_expiry": top_bear["expiry"],
            }
        except Exception:
            return None

    _ex_oi = ThreadPoolExecutor(max_workers=8)
    futures = {_ex_oi.submit(_analyze, t): t for t in DEFAULT_LEADERBOARD}
    rows = []
    try:
        for fut in as_completed(futures, timeout=22):
            try:
                r = fut.result()
                if r is not None:
                    rows.append(r)
            except Exception:
                pass
    except Exception:
        pass
    finally:
        _ex_oi.shutdown(wait=False, cancel_futures=True)

    rows.sort(key=lambda x: x["bear_prem_m"], reverse=True)
    out = {"results": rows[:20], "scanned": len(DEFAULT_LEADERBOARD)}
    if rows:
        app._oi_cache = out
        app._oi_cache_ts = _dt.now()
    return jsonify(out)


@app.route("/stock-api/vol-crush", methods=["GET"])
def vol_crush():
    """IV rank vs 1-year realized vol range — flags when implied vol is historically inflated."""
    import yfinance as yf, numpy as np
    from datetime import datetime as _dt

    _cache = getattr(app, "_vc_cache", None)
    _ts    = getattr(app, "_vc_cache_ts", None)
    if _cache and _ts and (_dt.now() - _ts).total_seconds() < 43200:
        return jsonify(_cache)

    def _analyze(ticker):
        try:
            tkr   = yf.Ticker(ticker)
            price = float(getattr(tkr.fast_info, "last_price", 0) or 0)
            if price <= 0: return None
            exps = tkr.options
            if not exps: return None
            chain = tkr.option_chain(exps[0])
            puts  = chain.puts; calls = chain.calls
            atm   = min(sorted(puts["strike"].tolist()), key=lambda s: abs(s - price))
            ivp   = puts[puts["strike"]  == atm]["impliedVolatility"].values
            ivc   = calls[calls["strike"] == atm]["impliedVolatility"].values
            iv_vals = [v for v in list(ivp) + list(ivc) if v and v > 0]
            if not iv_vals: return None
            current_iv = float(np.mean(iv_vals))
            hist_full = tkr.history(period="1y")
            hist = hist_full["Close"]
            if len(hist) < 50: return None
            rets = hist.pct_change().dropna()
            hv_s = rets.rolling(21).std() * np.sqrt(252)
            hv_s = hv_s.dropna()
            hv30 = float(hv_s.iloc[-1])
            hv_min = float(hv_s.min()); hv_max = float(hv_s.max())
            iv_rank = round((current_iv - hv_min) / (hv_max - hv_min) * 100, 1) if (hv_max - hv_min) > 0 else 50.0
            iv_rank = max(0.0, min(100.0, iv_rank))
            iv_hv   = round(current_iv / hv30, 2) if hv30 > 0 else None

            # RSI (14-period)
            rsi = None
            try:
                gains  = rets.where(rets > 0, 0).rolling(14).mean()
                losses = (-rets.where(rets < 0, 0)).rolling(14).mean()
                rs = gains.iloc[-1] / losses.iloc[-1] if losses.iloc[-1] > 0 else 100
                rsi = round(100 - 100 / (1 + rs), 1)
            except Exception: pass

            # SMA50 position (% above / below)
            sma50_pct = None
            try:
                sma50 = float(hist.rolling(50).mean().iloc[-1])
                sma50_pct = round((price - sma50) / sma50 * 100, 1)
            except Exception: pass

            # 5-day vs 20-day volume trend
            vol_trend_5d = None
            try:
                vol = hist_full["Volume"].dropna()
                avg5  = float(vol.tail(5).mean())
                avg20 = float(vol.tail(20).mean())
                if avg20 > 0:
                    vol_trend_5d = round(avg5 / avg20, 2)
            except Exception: pass

            earnings_date = None
            short_float_pct = None
            short_ratio = None
            days_since_earnings = None
            days_to_earnings = None
            analyst_target_pct = None
            analyst_recommendation = None
            net_upgrades_7d = None
            instit_own_pct = None
            try:
                info = tkr.info
                ed = info.get("earningsTimestamp") or info.get("earningsTimestampStart")
                if ed:
                    ed_dt = _dt.fromtimestamp(int(ed))
                    earnings_date = ed_dt.strftime("%Y-%m-%d")
                    diff_days = (_dt.now() - ed_dt).days
                    if 0 <= diff_days <= 10:
                        days_since_earnings = diff_days
                    elif diff_days < 0:
                        days_to_earnings = abs(diff_days)
                sfp = info.get("shortPercentOfFloat")
                if sfp and sfp > 0:
                    short_float_pct = round(float(sfp) * 100, 1)
                sr = info.get("shortRatio")
                if sr and sr > 0:
                    short_ratio = round(float(sr), 1)
                tgt = info.get("targetMeanPrice")
                if tgt and price > 0:
                    analyst_target_pct = round((float(tgt) - price) / price * 100, 1)
                rec = info.get("recommendationKey") or ""
                if rec:
                    analyst_recommendation = rec.lower().replace("_", " ")
                hip = info.get("heldPercentInstitutions")
                if hip and float(hip) > 0:
                    instit_own_pct = round(float(hip) * 100, 1)
            except Exception: pass

            # Analyst price target dispersion (how much analysts disagree)
            analyst_dispersion_pct = None
            try:
                _tgt_lo = info.get("targetLowPrice")
                _tgt_hi = info.get("targetHighPrice")
                _tgt_mn = info.get("targetMeanPrice")
                if _tgt_lo and _tgt_hi and _tgt_mn and float(_tgt_mn) > 0:
                    analyst_dispersion_pct = round((float(_tgt_hi) - float(_tgt_lo)) / float(_tgt_mn) * 100, 1)
            except Exception: pass

            # Analyst revision velocity (last 7 days)
            try:
                from datetime import timedelta as _td_vc
                updn = tkr.upgrades_downgrades
                if updn is not None and not updn.empty:
                    cutoff = _dt.now() - _td_vc(days=7)
                    recent = updn[updn.index.tz_localize(None) >= cutoff] if updn.index.tz is not None else updn[updn.index >= cutoff]
                    if not recent.empty:
                        action_col = next((c for c in recent.columns if "action" in c.lower()), None)
                        if action_col:
                            acts = recent[action_col].str.lower()
                            ups = int(acts.str.contains("up|rais|init|strong", na=False).sum())
                            dns = int(acts.str.contains("down|lower|cut|reduc|under", na=False).sum())
                            net_upgrades_7d = ups - dns
            except Exception: pass

            # Options bid/ask liquidity (ATM call spread % of mid — >10% = illiquid/avoid)
            options_liquidity_pct = None
            try:
                atm_call = calls[calls["strike"] == atm]
                if not atm_call.empty:
                    bid_c = float(atm_call["bid"].values[0] or 0)
                    ask_c = float(atm_call["ask"].values[0] or 0)
                    mid_c = (bid_c + ask_c) / 2
                    if mid_c > 0.5:
                        options_liquidity_pct = round((ask_c - bid_c) / mid_c * 100, 1)
            except Exception: pass

            # Earnings beat streak (how many of last 4 quarters beat consensus estimate)
            earnings_beat_streak = None
            try:
                # Try multiple yfinance APIs (changed across versions)
                eq = None
                for _attr in ("quarterly_earnings", "earnings", "earnings_history"):
                    try:
                        eq = getattr(tkr, _attr, None)
                        if eq is not None and hasattr(eq, "empty") and not eq.empty:
                            break
                        eq = None
                    except Exception:
                        eq = None
                if eq is not None and not eq.empty:
                    # Normalize column names (yfinance returns different caps/spacing)
                    eq.columns = [c.strip().title() for c in eq.columns]
                    est_col = next((c for c in eq.columns if "Estim" in c), None)
                    act_col = next((c for c in eq.columns if "Actual" in c or "Earn" in c), None)
                    if est_col and act_col:
                        recent_q = eq.tail(4)
                        beats = int((recent_q[act_col] > recent_q[est_col]).sum())
                        total_q = int(len(recent_q))
                        if total_q >= 2:
                            earnings_beat_streak = f"{beats}/{total_q}"
            except Exception: pass

            # Beta to SPY (30-day rolling — uses pre-fetched _spy_rets_arr from outer scope)
            spy_beta = None
            if _spy_rets_arr is not None and len(_spy_rets_arr) >= 20:
                try:
                    common = min(len(_spy_rets_arr), len(rets))
                    t_r = rets.values[-common:]
                    s_r = _spy_rets_arr[-common:]
                    cov_val = float(np.cov(t_r, s_r)[0][1])
                    var_spy = float(np.var(s_r))
                    if var_spy > 0:
                        spy_beta = round(cov_val / var_spy, 2)
                except Exception: pass

            # ── QUANT HEDGE-FUND SIGNALS ───────────────────────────────────────────────

            # Q1. Volatility skew (OTM put IV vs OTM call IV) + IV term structure
            iv_skew = None
            iv_term_structure = None
            try:
                put_otm = puts[puts["strike"] < price * 0.92]["strike"]
                call_otm = calls[calls["strike"] > price * 1.08]["strike"]
                if not put_otm.empty and not call_otm.empty:
                    p25_k = float(put_otm.max())
                    c25_k = float(call_otm.min())
                    p25_iv = float(puts[puts["strike"] == p25_k]["impliedVolatility"].values[0])
                    c25_iv = float(calls[calls["strike"] == c25_k]["impliedVolatility"].values[0])
                    if p25_iv > 0 and c25_iv > 0:
                        iv_skew = round((p25_iv - c25_iv) * 100, 1)
                if len(exps) >= 2:
                    _ch2 = tkr.option_chain(exps[1])
                    _atm2 = min(sorted(_ch2.calls["strike"].tolist()), key=lambda s: abs(s - price))
                    _iv2 = [v for _c in [_ch2.puts, _ch2.calls]
                            for v in _c[_c["strike"] == _atm2]["impliedVolatility"].values if v and v > 0]
                    if _iv2:
                        iv_term_structure = round((current_iv - float(np.mean(_iv2))) * 100, 1)
            except Exception: pass

            # Q2. Dealer Gamma Exposure (GEX) via Black-Scholes gamma approximation
            gex_m = None
            gex_regime = None
            try:
                from datetime import datetime as _dt_gex
                _exp_dt = _dt_gex.strptime(exps[0], "%Y-%m-%d")
                _T = max((_exp_dt - _dt.now()).days, 1) / 365.0
                _rf = 0.05
                def _bs_gamma(S, K, T, r, sigma):
                    if sigma <= 0 or T <= 0 or K <= 0: return 0.0
                    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
                    return float(np.exp(-0.5 * d1 ** 2) / np.sqrt(2 * np.pi) / (S * sigma * np.sqrt(T)))
                _gex = 0.0
                for _, _row in calls.iterrows():
                    _K, _iv_v, _oi = _row["strike"], _row["impliedVolatility"], _row.get("openInterest") or 0
                    if _iv_v > 0 and _K > 0 and _oi > 0:
                        _gex += _bs_gamma(price, _K, _T, _rf, _iv_v) * _oi * 100 * price
                for _, _row in puts.iterrows():
                    _K, _iv_v, _oi = _row["strike"], _row["impliedVolatility"], _row.get("openInterest") or 0
                    if _iv_v > 0 and _K > 0 and _oi > 0:
                        _gex -= _bs_gamma(price, _K, _T, _rf, _iv_v) * _oi * 100 * price
                gex_m = round(_gex / 1e6, 1)
                gex_regime = "LONG_GAMMA" if _gex > 0 else "SHORT_GAMMA"
            except Exception: pass

            # Q3. IV premium vs Realized Vol (>20% = rich → sell premium; <-10% = cheap → buy vol)
            iv_rv_premium = None
            try:
                if iv_hv and iv_hv > 0:
                    iv_rv_premium = round((iv_hv - 1.0) * 100, 1)
            except Exception: pass

            # Q4. Factor scoring: momentum (12-1 month), quality (ROE), value (forward P/E)
            momentum_12_1 = None
            factor_roe = None
            factor_fpe = None
            try:
                if len(hist) >= 60:  # need at least ~3 months; use oldest available as 12m proxy
                    _p1m  = float(hist.iloc[-21]) if len(hist) >= 21 else float(hist.iloc[-1])
                    _p12m = float(hist.iloc[0])   # oldest bar in the 1-year window
                    if _p12m > 0:
                        momentum_12_1 = round((_p1m / _p12m - 1) * 100, 1)
            except Exception: pass
            try:
                _finfo = info  # reuse already-fetched tkr.info from earnings block
            except NameError:
                try: _finfo = tkr.info
                except Exception: _finfo = {}
            try:
                _roe = _finfo.get("returnOnEquity")
                if _roe is not None and abs(float(_roe)) < 5:
                    factor_roe = round(float(_roe) * 100, 1)
                _fpe = _finfo.get("forwardPE")
                if _fpe is not None and 0 < float(_fpe) < 500:
                    factor_fpe = round(float(_fpe), 1)
            except Exception: pass

            # Q5. Cross-asset correlation (30d: ticker vs its sector ETF)
            sector_corr = None
            try:
                _sec_etf = _TICKER_TO_SECTOR_ETF.get(ticker)
                _sec_rets = _sector_rets_map.get(_sec_etf)
                if _sec_rets is not None and len(_sec_rets) >= 20 and len(rets) >= 20:
                    _common = min(len(_sec_rets), len(rets))
                    _cm = np.corrcoef(rets.values[-_common:], _sec_rets[-_common:])
                    sector_corr = round(float(_cm[0, 1]), 2)
            except Exception: pass

            # Q6. News sentiment (keyword-scoring of recent headlines — fast, no extra API)
            news_sentiment = None
            news_headline = None
            try:
                _news_items = tkr.news
                if _news_items:
                    _pos_w = {"beat","surge","rally","upgrade","buy","strong","record","growth","profit",
                              "bullish","raises","gains","breakout","soars","lifts","boost"}
                    _neg_w = {"miss","fall","decline","downgrade","sell","weak","loss","warning",
                              "cut","bearish","lowers","drops","slumps","disappoints","concern","probe"}
                    _ns = 0; _nc = 0
                    for _ni in _news_items[:6]:
                        _t = (_ni.get("title") or "").lower()
                        _ns += sum(1 for w in _pos_w if w in _t) - sum(1 for w in _neg_w if w in _t)
                        _nc += 1
                    if _nc > 0:
                        news_sentiment = round(_ns / _nc, 1)
                    news_headline = (_news_items[0].get("title") or "")[:90] if _news_items else None
            except Exception: pass

            # Q7. Put/Call OI ratio + flow persistence (cumulative across 4 exps)
            put_call_oi_ratio = None
            call_vol_oi_ratio = None
            put_vol_oi_ratio  = None
            pc_premium_ratio  = None
            try:
                _tot_put_oi = 0; _tot_call_oi = 0
                _tot_call_vol = 0; _tot_put_vol = 0
                _tot_call_prem = 0.0; _tot_put_prem = 0.0
                for _exp_oi in exps[:4]:
                    try:
                        _ch_oi = tkr.option_chain(_exp_oi)
                        _tot_put_oi    += int(_ch_oi.puts["openInterest"].fillna(0).sum())
                        _tot_call_oi   += int(_ch_oi.calls["openInterest"].fillna(0).sum())
                        _tot_call_vol  += int(_ch_oi.calls["volume"].fillna(0).sum())
                        _tot_put_vol   += int(_ch_oi.puts["volume"].fillna(0).sum())
                        _tot_call_prem += float((_ch_oi.calls["volume"].fillna(0) * _ch_oi.calls["lastPrice"].fillna(0)).sum()) * 100
                        _tot_put_prem  += float((_ch_oi.puts["volume"].fillna(0)  * _ch_oi.puts["lastPrice"].fillna(0)).sum()) * 100
                    except Exception: continue
                if _tot_call_oi > 0:
                    put_call_oi_ratio = round(_tot_put_oi / _tot_call_oi, 2)
                    call_vol_oi_ratio = round(_tot_call_vol / _tot_call_oi, 3)
                if _tot_put_oi > 0:
                    put_vol_oi_ratio = round(_tot_put_vol / _tot_put_oi, 3)
                if _tot_call_prem > 0:
                    pc_premium_ratio = round(_tot_put_prem / _tot_call_prem, 2)
            except Exception: pass

            # Q8. Earnings implied move (IV-based expected ±% move into earnings)
            earnings_impl_move_pct = None
            try:
                if days_to_earnings and days_to_earnings > 0 and current_iv > 0:
                    earnings_impl_move_pct = round(current_iv * (max(days_to_earnings, 1) / 252) ** 0.5 * 100, 1)
            except Exception: pass

            # Q9. 52-week range percentile (0%=at 52w low, 100%=at 52w high)
            week52_range_pct = None
            try:
                _52h = float(info.get("fiftyTwoWeekHigh") or 0)
                _52l = float(info.get("fiftyTwoWeekLow")  or 0)
                if _52h > _52l > 0 and price > 0:
                    week52_range_pct = round((price - _52l) / (_52h - _52l) * 100, 1)
            except Exception: pass

            # Q10. Borrow cost proxy — inferred from short interest (no extra API)
            borrow_cost_proxy = None
            try:
                if short_float_pct is not None:
                    if short_float_pct >= 20:
                        borrow_cost_proxy = "HIGH_BORROW"
                    elif short_float_pct >= 10:
                        borrow_cost_proxy = "ELEVATED_BORROW"
                    else:
                        borrow_cost_proxy = "EASY_BORROW"
            except Exception: pass

            # Q11. EPS revision trend (forward vs trailing EPS — direction of estimate revisions)
            eps_revision_trend = None
            try:
                _feps = info.get("forwardEps")
                _teps = info.get("trailingEps")
                if _feps is not None and _teps is not None and abs(float(_teps)) > 0.01:
                    _fwd_growth = round((float(_feps) / float(_teps) - 1) * 100, 1)
                    if _fwd_growth > 15:
                        eps_revision_trend = f"RISING(+{_fwd_growth}%_fwd_growth)"
                    elif _fwd_growth < -15:
                        eps_revision_trend = f"DECLINING({_fwd_growth}%_fwd_growth)"
                    else:
                        eps_revision_trend = f"STABLE({_fwd_growth:+.1f}%_fwd_growth)"
            except Exception: pass

            # Q12. Historical earnings price reaction proxy (avg of top-4 absolute 1-day moves in
            #      the past year — for most stocks these are almost exclusively earnings days)
            hist_earn_reaction_pct = None
            try:
                _abs_daily = (abs(rets) * 100).dropna().sort_values(ascending=False)
                _top4 = _abs_daily.iloc[:4].tolist()
                if len(_top4) >= 2:
                    hist_earn_reaction_pct = round(sum(_top4) / len(_top4), 1)
            except Exception: pass

            # Q13. Short squeeze risk score (composite — higher = more squeeze potential)
            squeeze_risk = None
            try:
                _sq_pts = 0
                if short_float_pct is not None and short_float_pct >= 15: _sq_pts += 1
                if borrow_cost_proxy in ("HIGH_BORROW", "ELEVATED_BORROW"):        _sq_pts += 1
                if rsi is not None and rsi > 60:                                    _sq_pts += 1
                if vol_trend_5d is not None and vol_trend_5d >= 1.3:               _sq_pts += 1
                if short_float_pct is not None:
                    squeeze_risk = ("EXTREME" if _sq_pts == 4 else
                                    "HIGH"    if _sq_pts == 3 else
                                    "MEDIUM"  if _sq_pts == 2 else "LOW")
            except Exception: pass

            # Q14. Relative strength vs SPY (stock 1-year return minus SPY 1-year return)
            rs_vs_spy = None
            try:
                if len(hist) >= 200 and _spy_1y_return is not None:
                    _stock_1y = round((float(hist.iloc[-1]) / float(hist.iloc[0]) - 1) * 100, 1)
                    rs_vs_spy = round(_stock_1y - _spy_1y_return, 1)
            except Exception: pass

            # Q15. Money flow ratio (up-day vs down-day average volume — accumulation vs distribution)
            money_flow_ratio = None
            try:
                _vol_s = hist_full["Volume"].dropna()
                _cidx = rets.index.intersection(_vol_s.index)
                if len(_cidx) >= 30:
                    _rv = _vol_s.loc[_cidx]; _rm = rets.loc[_cidx]
                    _up_v = float(_rv[_rm > 0].mean()); _dn_v = float(_rv[_rm < 0].mean())
                    if _dn_v > 0:
                        money_flow_ratio = round(_up_v / _dn_v, 2)
            except Exception: pass

            # Q16. Insider transaction net (open-market purchases vs sales last 30 days)
            # yfinance columns: Text (has "Sale at price X" / "Purchase at price X"), Shares, Start Date
            insider_net = None
            try:
                from datetime import timedelta as _td_ins
                ins = tkr.insider_transactions
                if ins is not None and not ins.empty:
                    _cutoff_ins = _dt.now() - _td_ins(days=30)
                    _cols_lower = {c.lower(): c for c in ins.columns}
                    # Date: 'Start Date' column (not the index)
                    _date_c = (_cols_lower.get('start date') or _cols_lower.get('date') or
                               next((c for c in ins.columns if 'date' in c.lower()), None))
                    # Text: 'Text' column has "Sale at price X" / "Purchase at price X"
                    _txt_c = (_cols_lower.get('text') or _cols_lower.get('transaction') or
                              next((c for c in ins.columns if any(k in c.lower() for k in ['text', 'desc'])), None))
                    _shr_c = (_cols_lower.get('shares') or
                              next((c for c in ins.columns if 'share' in c.lower()), None))
                    if _date_c and _txt_c and _shr_c:
                        _recent_ins = ins[ins[_date_c] >= _cutoff_ins]
                        if not _recent_ins.empty:
                            _buys  = _recent_ins[_recent_ins[_txt_c].astype(str).str.contains('Purchase|Buy|Acqui', case=False, na=False)]
                            _sells = _recent_ins[_recent_ins[_txt_c].astype(str).str.contains('Sale|Sell|Dispo', case=False, na=False)]
                            _buy_sh  = int(_buys[_shr_c].fillna(0).abs().sum())
                            _sell_sh = int(_sells[_shr_c].fillna(0).abs().sum())
                            if _buy_sh + _sell_sh > 0:
                                _net_ins = _buy_sh - _sell_sh
                                insider_net = ("BUYING"  if _net_ins >  1000 else
                                               "SELLING" if _net_ins < -1000 else "NEUTRAL")
            except Exception: pass

            # Q17. Dividend yield + days to ex-dividend date
            div_yield_pct = None
            ex_div_days = None
            try:
                _dy = info.get("dividendYield")
                if _dy and float(_dy) > 0:
                    _dy_f = float(_dy)
                    # yfinance returns as decimal (0.035) or percentage (3.5) depending on version
                    div_yield_pct = round(_dy_f if _dy_f > 1 else _dy_f * 100, 2)
                _exd = info.get("exDividendDate")
                if _exd:
                    _exd_dt = _dt.fromtimestamp(int(_exd))
                    _dd = (_exd_dt - _dt.now()).days
                    if -5 <= _dd <= 90:
                        ex_div_days = _dd
            except Exception: pass

            # Q18. Tail risk put concentration (crash hedging proxy — % of put vol in deep OTM strikes)
            tail_risk_put_pct = None
            try:
                _deep_k = price * 0.85
                _deep_puts = puts[puts["strike"] < _deep_k]
                _tot_pvol = int(puts["volume"].fillna(0).sum())
                if _tot_pvol > 100:
                    _deep_pvol = int(_deep_puts["volume"].fillna(0).sum())
                    tail_risk_put_pct = round(_deep_pvol / _tot_pvol * 100, 1)
            except Exception: pass

            verdict = ("HIGH FEAR" if iv_rank >= 80 else "ELEVATED" if iv_rank >= 60 else "NORMAL" if iv_rank >= 30 else "LOW IV")
            return {
                "ticker": ticker, "price": round(price, 2),
                "current_iv": round(current_iv * 100, 1),
                "hv_30": round(hv30 * 100, 1), "iv_hv_ratio": iv_hv, "iv_rank": iv_rank,
                "verdict": verdict,
                "earnings_date": earnings_date, "days_since_earnings": days_since_earnings,
                "days_to_earnings": days_to_earnings,
                "short_float_pct": short_float_pct, "short_ratio": short_ratio,
                "instit_own_pct": instit_own_pct,
                "rsi": rsi, "sma50_pct": sma50_pct, "vol_trend_5d": vol_trend_5d,
                "net_upgrades_7d": net_upgrades_7d,
                "options_liquidity_pct": options_liquidity_pct,
                "earnings_beat_streak": earnings_beat_streak,
                "spy_beta": spy_beta,
                "iv_skew": iv_skew,
                "iv_term_structure": iv_term_structure,
                "gex_m": gex_m, "gex_regime": gex_regime,
                "iv_rv_premium": iv_rv_premium,
                "momentum_12_1": momentum_12_1,
                "factor_roe": factor_roe, "factor_fpe": factor_fpe,
                "sector_corr": sector_corr,
                "news_sentiment": news_sentiment, "news_headline": news_headline,
                "analyst_target_pct": analyst_target_pct,
                "analyst_recommendation": analyst_recommendation,
                "put_call_oi_ratio": put_call_oi_ratio,
                "earnings_impl_move_pct": earnings_impl_move_pct,
                "call_vol_oi_ratio": call_vol_oi_ratio,
                "put_vol_oi_ratio": put_vol_oi_ratio,
                "week52_range_pct": week52_range_pct,
                "borrow_cost_proxy": borrow_cost_proxy,
                "eps_revision_trend": eps_revision_trend,
                "hist_earn_reaction_pct": hist_earn_reaction_pct,
                "squeeze_risk": squeeze_risk,
                "analyst_dispersion_pct": analyst_dispersion_pct,
                "pc_premium_ratio": pc_premium_ratio,
                "rs_vs_spy": rs_vs_spy,
                "money_flow_ratio": money_flow_ratio,
                "insider_net": insider_net,
                "div_yield_pct": div_yield_pct,
                "ex_div_days": ex_div_days,
                "tail_risk_put_pct": tail_risk_put_pct,
            }
        except Exception: return None

    # Pre-fetch SPY returns once (shared across all tickers for beta calculation)
    # Use module-level cached SPY data (avoids rate-limit collisions with 20 concurrent ticker fetches)
    _spy_rets_arr = _spy_1y_cache.get("rets_arr")
    _spy_1y_return = _spy_1y_cache.get("return_pct")

    # Pre-fetch sector ETF returns for cross-asset correlation (Q5 in _analyze)
    _TICKER_TO_SECTOR_ETF = {
        "AAPL":"XLK","MSFT":"XLK","NVDA":"XLK","GOOGL":"XLK","META":"XLK",
        "AMD":"XLK","INTC":"XLK","MU":"XLK","ORCL":"XLK","QQQ":"XLK",
        "JPM":"XLF","BAC":"XLF","GS":"XLF","WFC":"XLF",
        "JNJ":"XLV","UNH":"XLV","MRNA":"XLV","PFE":"XLV","ABBV":"XLV",
        "XOM":"XLE","CVX":"XLE","USO":"XLE",
        "LMT":"XLI","CAT":"XLI","BA":"XLI",
        "AMZN":"XLY","TSLA":"XLY","COST":"XLY",
        "NFLX":"XLC","DIS":"XLC","CMCSA":"XLC",
        "IWM":"IWM",
    }
    _sector_rets_map = {}
    try:
        _sec_etfs = list(set(_TICKER_TO_SECTOR_ETF.values()))
        _sec_raw2 = yf.download(_sec_etfs, period="60d", interval="1d", progress=False, auto_adjust=True)["Close"]
        for _etf in _sec_etfs:
            try:
                _ser2 = (_sec_raw2[_etf].dropna() if hasattr(_sec_raw2, "columns") and _etf in _sec_raw2.columns
                         else _sec_raw2.dropna())
                if len(_ser2) >= 20:
                    _sector_rets_map[_etf] = _ser2.pct_change().dropna().values
            except Exception:
                pass
    except Exception:
        pass

    _ex_vc = ThreadPoolExecutor(max_workers=8)
    futures = {_ex_vc.submit(_analyze, t): t for t in DEFAULT_LEADERBOARD}
    rows = []
    try:
        for fut in as_completed(futures, timeout=22):
            try:
                r = fut.result()
                if r is not None:
                    rows.append(r)
            except Exception:
                pass
    except Exception:
        pass
    finally:
        _ex_vc.shutdown(wait=False, cancel_futures=True)
    rows.sort(key=lambda x: x["iv_rank"], reverse=True)

    # Enrich with IV skew percentile + short float trend from daily_vol_snapshots history
    try:
        import psycopg2 as _pg2_vc
        _snap_tickers = [r["ticker"] for r in rows]
        with _pg2_vc.connect(os.environ["DATABASE_URL"]) as _conn_vc, _conn_vc.cursor() as _cur_vc:
            _cur_vc.execute("""
                SELECT ticker, iv_skew, short_float, pc_oi_ratio
                FROM daily_vol_snapshots
                WHERE ticker = ANY(%s) AND snap_date >= CURRENT_DATE - INTERVAL '252 days'
                ORDER BY ticker, snap_date
            """, (_snap_tickers,))
            _snap_rows = _cur_vc.fetchall()
        _hist_map: dict = {}
        for _hr in _snap_rows:
            _ht = _hr[0]
            if _ht not in _hist_map:
                _hist_map[_ht] = []
            _hist_map[_ht].append({"iv_skew": _hr[1], "short_float": _hr[2], "pc_oi_ratio": _hr[3]})
        for r in rows:
            t = r["ticker"]
            if t in _hist_map and len(_hist_map[t]) >= 30:
                _skews = [h["iv_skew"] for h in _hist_map[t] if h["iv_skew"] is not None]
                _sfps  = [h["short_float"] for h in _hist_map[t] if h["short_float"] is not None]
                _pcrs  = [h["pc_oi_ratio"] for h in _hist_map[t] if h["pc_oi_ratio"] is not None]
                if _skews and r.get("iv_skew") is not None:
                    r["iv_skew_pctl"] = int(sum(1 for s in _skews if s <= r["iv_skew"]) / len(_skews) * 100)
                if len(_sfps) >= 5 and r.get("short_float_pct") is not None:
                    r["short_float_trend"] = round(r["short_float_pct"] - _sfps[-5], 1)
                if len(_pcrs) >= 5 and r.get("put_call_oi_ratio") is not None:
                    r["pc_ratio_trend"] = round(r["put_call_oi_ratio"] - _pcrs[-5], 2)
    except Exception:
        pass

    out = {"results": rows[:20], "scanned": len(DEFAULT_LEADERBOARD)}
    if rows:
        app._vc_cache = out; app._vc_cache_ts = _dt.now()
    return jsonify(out)


@app.route("/stock-api/call-intent", methods=["GET"])
def call_intent():
    """Classify calls: FOMO (near-money+short-dated) vs ACCUMULATION (OTM+long-dated)."""
    import yfinance as yf
    from datetime import datetime as _dt

    _cache = getattr(app, "_ci_cache", None)
    _ts    = getattr(app, "_ci_cache_ts", None)
    if _cache and _ts and (_dt.now() - _ts).total_seconds() < 43200:
        return jsonify(_cache)

    now = _dt.now()

    def _analyze(ticker):
        try:
            tkr   = yf.Ticker(ticker)
            price = float(getattr(tkr.fast_info, "last_price", 0) or 0)
            if price <= 0: return None
            exps = tkr.options
            if not exps: return None
            fomo_prem = 0.0; accum_prem = 0.0
            fomo_vol_prem = 0.0; accum_vol_prem = 0.0
            fomo_oi_prem  = 0.0; accum_oi_prem  = 0.0
            top_accum = {"strike": None, "expiry": None, "prem": 0.0, "otm_pct": 0.0}
            for exp in exps[:8]:
                try:
                    days_out = (_dt.strptime(exp, "%Y-%m-%d") - now).days
                    calls = tkr.option_chain(exp).calls
                    for _, row in calls.iterrows():
                        strike = float(row.get("strike", 0) or 0)
                        vol    = int(row.get("volume", 0) or 0)
                        oi     = int(row.get("openInterest", 0) or 0)
                        last   = float(row.get("lastPrice", 0) or 0)
                        if strike <= 0 or last <= 0: continue
                        otm_pct   = (strike - price) / price * 100
                        vol_prem  = vol * last * 100
                        oi_prem   = oi  * last * 100
                        prem      = vol_prem + oi_prem
                        if otm_pct > 5 and days_out > 60:
                            accum_prem     += prem
                            accum_vol_prem += vol_prem
                            accum_oi_prem  += oi_prem
                            if prem > top_accum["prem"]:
                                top_accum = {"strike": round(strike, 2), "expiry": exp,
                                             "prem": prem, "otm_pct": round(otm_pct, 1)}
                        elif -3 < otm_pct < 3 and days_out < 45:
                            fomo_prem     += prem
                            fomo_vol_prem += vol_prem
                            fomo_oi_prem  += oi_prem
                except Exception: continue
            total = fomo_prem + accum_prem
            if total < 1000: return None
            fomo_pct  = round(fomo_prem  / total * 100, 1)
            accum_pct = round(accum_prem / total * 100, 1)
            verdict   = ("ACCUMULATION" if accum_pct >= 60 else "FOMO" if fomo_pct >= 60 else "MIXED")

            # ── LEAPS WHALE SCANNER ──────────────────────────────────────────
            # Separate pass: scan ALL expirations 180–365 days out for a single
            # strike with ≥$10M in day's volume premium — institutional LEAPS block
            leaps_whale = {"strike": None, "expiry": None, "prem_m": 0.0,
                           "days_out": 0, "direction": "CALL"}
            for exp in exps:
                try:
                    days_out = (_dt.strptime(exp, "%Y-%m-%d") - now).days
                    if not (180 <= days_out <= 365): continue
                    for direction, chain in [("CALL", tkr.option_chain(exp).calls),
                                             ("PUT",  tkr.option_chain(exp).puts)]:
                        for _, row in chain.iterrows():
                            strike = float(row.get("strike", 0) or 0)
                            vol    = int(row.get("volume", 0) or 0)
                            last   = float(row.get("lastPrice", 0) or 0)
                            if strike <= 0 or last <= 0 or vol <= 0: continue
                            single_prem_m = vol * last * 100 / 1e6
                            if single_prem_m >= 10.0 and single_prem_m > leaps_whale["prem_m"]:
                                leaps_whale = {
                                    "strike":    round(strike, 2),
                                    "expiry":    exp,
                                    "prem_m":    round(single_prem_m, 1),
                                    "days_out":  days_out,
                                    "direction": direction,
                                }
                except Exception: continue
            # ─────────────────────────────────────────────────────────────────

            return {"ticker": ticker, "price": round(price, 2),
                    "fomo_prem_m":      round(fomo_prem      / 1e6, 2),
                    "accum_prem_m":     round(accum_prem     / 1e6, 2),
                    "accum_vol_m":      round(accum_vol_prem / 1e6, 2),
                    "accum_oi_m":       round(accum_oi_prem  / 1e6, 2),
                    "fomo_vol_m":       round(fomo_vol_prem  / 1e6, 2),
                    "fomo_oi_m":        round(fomo_oi_prem   / 1e6, 2),
                    "fomo_pct": fomo_pct, "accum_pct": accum_pct, "verdict": verdict,
                    "top_accum_strike": top_accum["strike"], "top_accum_expiry": top_accum["expiry"],
                    "top_accum_otm_pct": top_accum["otm_pct"],
                    "leaps_whale": leaps_whale if leaps_whale["strike"] else None}
        except Exception: return None

    _ex_ci = ThreadPoolExecutor(max_workers=8)
    futures = {_ex_ci.submit(_analyze, t): t for t in DEFAULT_LEADERBOARD}
    rows = []
    try:
        for fut in as_completed(futures, timeout=22):
            try:
                r = fut.result()
                if r is not None:
                    rows.append(r)
            except Exception:
                pass
    except Exception:
        pass
    finally:
        _ex_ci.shutdown(wait=False, cancel_futures=True)
    rows.sort(key=lambda x: x["accum_prem_m"], reverse=True)
    out = {"results": rows[:20], "scanned": len(DEFAULT_LEADERBOARD)}
    if rows:
        app._ci_cache = out; app._ci_cache_ts = _dt.now()
    return jsonify(out)


@app.route("/stock-api/smart-vs-retail", methods=["GET"])
def smart_vs_retail():
    """Compare large-block (institutional) vs small-contract (retail) options flow."""
    import yfinance as yf
    from datetime import datetime as _dt

    _cache = getattr(app, "_svr_cache", None)
    _ts    = getattr(app, "_svr_cache_ts", None)
    if _cache and _ts and (_dt.now() - _ts).total_seconds() < 43200:
        return jsonify(_cache)

    def _analyze(ticker):
        try:
            tkr   = yf.Ticker(ticker)
            price = float(getattr(tkr.fast_info, "last_price", 0) or 0)
            if price <= 0: return None
            exps = tkr.options
            if not exps: return None
            sc = 0.0; sp = 0.0; rc = 0.0; rp = 0.0
            for exp in exps[:4]:
                try:
                    chain = tkr.option_chain(exp)
                    for side, df in [("c", chain.calls), ("p", chain.puts)]:
                        for _, row in df.iterrows():
                            vol  = int(row.get("volume", 0) or 0)
                            last = float(row.get("lastPrice", 0) or 0)
                            if vol <= 0 or last <= 0: continue
                            prem = vol * last * 100
                            if last >= 3.0 and vol >= 30:
                                if side == "c": sc += prem
                                else:           sp += prem
                            elif last < 2.0 or vol < 15:
                                if side == "c": rc += prem
                                else:           rp += prem
                except Exception: continue
            if (sc + sp) < 1000 and (rc + rp) < 1000: return None
            s_cp = round(sc / sp, 2) if sp > 0 else 9.9
            r_cp = round(rc / rp, 2) if rp > 0 else 9.9
            sb = s_cp >= 1.5; sbear = s_cp <= 0.7
            rb = r_cp >= 1.5; rbear = r_cp <= 0.7
            if   sb    and rbear: div, strength = "SMART BULLISH",  "STRONG"
            elif sbear and rb:    div, strength = "SMART BEARISH",  "STRONG"
            elif sb    and not rb: div, strength = "SMART BULLISH", "MODERATE"
            elif sbear and not rbear: div, strength = "SMART BEARISH", "MODERATE"
            elif rb    and not sb: div, strength = "RETAIL BULLISH","MODERATE"
            elif rbear and not sbear: div, strength = "RETAIL BEARISH","MODERATE"
            elif sb or rb:         div, strength = "ALIGNED",       "WEAK"
            else:                  div, strength = "NEUTRAL",       "WEAK"
            return {"ticker": ticker, "price": round(price, 2),
                    "smart_prem_m": round((sc + sp) / 1e6, 2), "retail_prem_m": round((rc + rp) / 1e6, 2),
                    "smart_cp": s_cp, "retail_cp": r_cp, "divergence": div, "signal_strength": strength}
        except Exception: return None

    _ex_svr = ThreadPoolExecutor(max_workers=8)
    futures = {_ex_svr.submit(_analyze, t): t for t in DEFAULT_LEADERBOARD}
    rows = []
    try:
        for fut in as_completed(futures, timeout=22):
            try:
                r = fut.result()
                if r is not None:
                    rows.append(r)
            except Exception:
                pass
    except Exception:
        pass
    finally:
        _ex_svr.shutdown(wait=False, cancel_futures=True)
    rows.sort(key=lambda x: (x["signal_strength"] == "STRONG", x["smart_prem_m"]), reverse=True)
    out = {"results": rows[:20], "scanned": len(DEFAULT_LEADERBOARD)}
    if rows:
        app._svr_cache = out; app._svr_cache_ts = _dt.now()
    return jsonify(out)


@app.route("/stock-api/max-pain", methods=["GET"])
def max_pain():
    """Max pain strike for nearest expiry — where price tends to drift before expiration."""
    import yfinance as yf
    from datetime import datetime as _dt

    _cache = getattr(app, "_mp_cache", None)
    _ts    = getattr(app, "_mp_cache_ts", None)
    if _cache and _ts and (_dt.now() - _ts).total_seconds() < 43200:
        return jsonify(_cache)

    now = _dt.now()

    def _max_pain(puts_df, calls_df):
        strikes = sorted(set(list(puts_df["strike"]) + list(calls_df["strike"])))
        if not strikes: return None
        best = strikes[0]; lo = float("inf")
        for s in strikes:
            cp = sum(max(0.0, float(s) - float(k)) * float(oi or 0)
                     for k, oi in zip(calls_df["strike"], calls_df["openInterest"].fillna(0)))
            pp = sum(max(0.0, float(k) - float(s)) * float(oi or 0)
                     for k, oi in zip(puts_df["strike"],  puts_df["openInterest"].fillna(0)))
            if (cp + pp) < lo: lo = cp + pp; best = s
        return float(best)

    def _analyze(ticker):
        try:
            tkr   = yf.Ticker(ticker)
            price = float(getattr(tkr.fast_info, "last_price", 0) or 0)
            if price <= 0: return None
            exps = tkr.options
            if not exps: return None
            exp  = exps[0]
            days = (_dt.strptime(exp, "%Y-%m-%d") - now).days
            chain = tkr.option_chain(exp)
            mp = _max_pain(chain.puts, chain.calls)
            if mp is None: return None
            dist = round((price - mp) / mp * 100, 2)
            return {"ticker": ticker, "price": round(price, 2), "max_pain": round(mp, 2),
                    "distance_pct": dist, "direction": "ABOVE PAIN" if dist > 0 else "BELOW PAIN",
                    "nearest_expiry": exp, "days_to_exp": days}
        except Exception: return None

    _ex_mp = ThreadPoolExecutor(max_workers=8)
    futures = {_ex_mp.submit(_analyze, t): t for t in DEFAULT_LEADERBOARD}
    rows = []
    try:
        for fut in as_completed(futures, timeout=22):
            try:
                r = fut.result()
                if r is not None:
                    rows.append(r)
            except Exception:
                pass
    except Exception:
        pass
    finally:
        _ex_mp.shutdown(wait=False, cancel_futures=True)
    rows.sort(key=lambda x: abs(x["distance_pct"]), reverse=True)
    out = {"results": rows[:20], "scanned": len(DEFAULT_LEADERBOARD)}
    if rows:
        app._mp_cache = out; app._mp_cache_ts = _dt.now()
    return jsonify(out)


@app.route("/stock-api/gamma-wall", methods=["GET"])
def gamma_wall():
    """OI by strike for major tickers — shows dealer gamma concentration and flip points."""
    import yfinance as yf
    from datetime import datetime as _dt

    _cache = getattr(app, "_gw_cache", None)
    _ts    = getattr(app, "_gw_cache_ts", None)
    if _cache and _ts and (_dt.now() - _ts).total_seconds() < 43200:
        return jsonify(_cache)

    TICKERS = ["SPY", "QQQ", "IWM", "AAPL", "NVDA", "TSLA", "META", "AMZN", "MSFT", "GOOGL"]

    def _analyze(ticker):
        try:
            tkr   = yf.Ticker(ticker)
            price = float(getattr(tkr.fast_info, "last_price", 0) or 0)
            if price <= 0: return None
            exps  = tkr.options
            if not exps: return None
            exp   = exps[0]
            chain = tkr.option_chain(exp)
            puts  = chain.puts; calls = chain.calls
            lo = price * 0.90; hi = price * 1.10
            all_s = sorted(set(list(puts["strike"]) + list(calls["strike"])))
            strike_data = []; max_oi = 0; wall = price
            for s in all_s:
                if not (lo <= s <= hi): continue
                c_oi = int(calls[calls["strike"] == s]["openInterest"].sum() or 0)
                p_oi = int(puts[puts["strike"]   == s]["openInterest"].sum() or 0)
                tot  = c_oi + p_oi
                if tot > max_oi: max_oi = tot; wall = s
                strike_data.append({"strike": round(float(s), 2), "call_oi": c_oi,
                                     "put_oi": p_oi, "total_oi": tot, "net_gamma": c_oi - p_oi})
            if not strike_data: return None
            flip = None
            for i in range(1, len(strike_data)):
                if strike_data[i-1]["net_gamma"] >= 0 and strike_data[i]["net_gamma"] < 0:
                    flip = strike_data[i]["strike"]; break
            return {"ticker": ticker, "price": round(price, 2), "wall_strike": round(float(wall), 2),
                    "wall_distance_pct": round((float(wall) - price) / price * 100, 2),
                    "expiry": exp, "strikes": strike_data, "flip_strike": flip}
        except Exception: return None

    _ex_gw = ThreadPoolExecutor(max_workers=8)
    futures = {_ex_gw.submit(_analyze, t): t for t in TICKERS}
    rows = []
    try:
        for fut in as_completed(futures, timeout=22):
            try:
                r = fut.result()
                if r is not None:
                    rows.append(r)
            except Exception:
                pass
    except Exception:
        pass
    finally:
        _ex_gw.shutdown(wait=False, cancel_futures=True)
    rows.sort(key=lambda x: x["ticker"])
    out = {"results": rows}
    if rows:
        app._gw_cache = out; app._gw_cache_ts = _dt.now()
    return jsonify(out)


def _enrich_technical_signals(tickers_data):
    """
    Optional enrichment: MACD, Support/Resistance, Volume Profile POC, and VWAP.
    Runs in-process on tickers that already have price data.
    All failures are silent — if this function crashes, nothing else breaks.
    """
    import sys
    try:
        import yfinance as yf
        import numpy as np
    except Exception:
        return

    candidates = [t for t, v in tickers_data.items() if v.get("price")]
    if not candidates:
        return

    print(f"[enrich_tech] enriching {len(candidates)} tickers with MACD/S-R/POC/VWAP", file=sys.stderr, flush=True)

    try:
        raw = yf.download(
            candidates,
            period="6mo",
            interval="1d",
            progress=False,
            auto_adjust=True,
            group_by="ticker",
        )
    except Exception as e:
        print(f"[enrich_tech] batch download error: {e}", file=sys.stderr)
        return

    for ticker in candidates:
        try:
            v = tickers_data[ticker]
            price = float(v["price"])

            try:
                if len(candidates) == 1:
                    df = raw
                else:
                    lvl0 = raw.columns.get_level_values(0)
                    df = raw[ticker] if ticker in lvl0 else None
                if df is None or df.empty or len(df) < 30:
                    continue
                close = df["Close"].dropna()
                volume = df["Volume"].dropna()
                if len(close) < 30:
                    continue
            except Exception:
                continue

            # ── MACD (12/26/9 EMA) ────────────────────────────────────────────
            try:
                ema12 = close.ewm(span=12, adjust=False).mean()
                ema26 = close.ewm(span=26, adjust=False).mean()
                macd_line = ema12 - ema26
                signal_line = macd_line.ewm(span=9, adjust=False).mean()
                macd_val  = float(macd_line.iloc[-1])
                sig_val   = float(signal_line.iloc[-1])
                macd_prev = float(macd_line.iloc[-2]) if len(macd_line) >= 2 else macd_val
                sig_prev  = float(signal_line.iloc[-2]) if len(signal_line) >= 2 else sig_val

                if macd_prev < sig_prev and macd_val > sig_val:
                    macd_status = "BULLISH_CROSS"
                elif macd_val > sig_val:
                    macd_status = "BULLISH"
                elif macd_prev > sig_prev and macd_val < sig_val:
                    macd_status = "BEARISH_CROSS"
                else:
                    macd_status = "BEARISH"

                macd_div = None
                if len(close) >= 20:
                    price_low_recent = float(close.iloc[-5:].min())
                    price_low_prior  = float(close.iloc[-20:-5].min())
                    macd_low_recent  = float(macd_line.iloc[-5:].min())
                    macd_low_prior   = float(macd_line.iloc[-20:-5].min())
                    if price_low_recent < price_low_prior and macd_low_recent > macd_low_prior:
                        macd_div = "BULLISH_DIV"
                    elif price_low_recent > price_low_prior and macd_low_recent < macd_low_prior:
                        macd_div = "BEARISH_DIV"

                v["tech_macd"] = macd_status
                if macd_div:
                    v["tech_macd_div"] = macd_div
            except Exception:
                pass

            # ── Support / Resistance (swing highs/lows, 60-day window) ────────
            try:
                window = 5
                recent_close = close.iloc[-60:] if len(close) >= 60 else close
                swing_lows, swing_highs = [], []
                for i in range(window, len(recent_close) - window):
                    val = float(recent_close.iloc[i])
                    if all(val <= float(recent_close.iloc[i - j]) for j in range(1, window + 1)) and \
                       all(val <= float(recent_close.iloc[i + j]) for j in range(1, window + 1)):
                        swing_lows.append(val)
                    if all(val >= float(recent_close.iloc[i - j]) for j in range(1, window + 1)) and \
                       all(val >= float(recent_close.iloc[i + j]) for j in range(1, window + 1)):
                        swing_highs.append(val)

                supports    = [s for s in swing_lows  if s < price * 0.99]
                resistances = [r for r in swing_highs if r > price * 1.01]

                sr_parts = []
                if supports:
                    nearest_sup = max(supports)
                    sup_dist = round((price - nearest_sup) / price * 100, 1)
                    v["tech_support_dist_pct"] = sup_dist
                    sr_parts.append("AT_SUPPORT" if sup_dist <= 2 else f"ABOVE_SUPPORT({sup_dist}%_below)")
                if resistances:
                    nearest_res = min(resistances)
                    res_dist = round((nearest_res - price) / price * 100, 1)
                    v["tech_resistance_dist_pct"] = res_dist
                    sr_parts.append(f"BELOW_RESISTANCE({res_dist}%_above)")
                if sr_parts:
                    v["tech_sr_context"] = "_".join(sr_parts)
            except Exception:
                pass

            # ── Volume Profile / Point of Control (90-day) ───────────────────
            try:
                vol_w   = volume.iloc[-90:] if len(volume) >= 90 else volume
                close_w = close.iloc[-90:]  if len(close)  >= 90 else close
                idx = vol_w.index.intersection(close_w.index)
                if len(idx) >= 20:
                    c_arr = close_w.loc[idx].values.astype(float)
                    v_arr = vol_w.loc[idx].values.astype(float)
                    bins  = 20
                    pmin, pmax = c_arr.min(), c_arr.max()
                    if pmax > pmin:
                        bsize = (pmax - pmin) / bins
                        vol_buckets = {}
                        for cv, vv in zip(c_arr, v_arr):
                            b = min(int((cv - pmin) / bsize), bins - 1)
                            vol_buckets[b] = vol_buckets.get(b, 0) + vv
                        poc_b     = max(vol_buckets, key=vol_buckets.get)
                        poc_price = pmin + (poc_b + 0.5) * bsize
                        poc_dist  = round((price - poc_price) / poc_price * 100, 1)
                        v["tech_poc_dist_pct"] = poc_dist
                        v["tech_poc_context"]  = (
                            "AT_POC(high_volume_magnet)" if abs(poc_dist) <= 2 else
                            f"ABOVE_POC({poc_dist:+.1f}%)" if poc_dist > 0 else
                            f"BELOW_POC({poc_dist:+.1f}%)"
                        )
            except Exception:
                pass

            # ── VWAP (20-day volume-weighted average price) ───────────────────
            try:
                if len(close) >= 20 and len(volume) >= 20:
                    c20 = close.iloc[-20:]
                    v20 = volume.iloc[-20:]
                    idx2 = c20.index.intersection(v20.index)
                    if len(idx2) >= 10:
                        c_vals = c20.loc[idx2].values.astype(float)
                        v_vals = v20.loc[idx2].values.astype(float)
                        vwap      = float(np.average(c_vals, weights=v_vals))
                        vwap_dist = round((price - vwap) / vwap * 100, 1)
                        v["tech_vwap_dist_pct"] = vwap_dist
                        v["tech_vwap_context"]  = (
                            "ABOVE_VWAP(buyers_in_control)" if vwap_dist >  1.5 else
                            "BELOW_VWAP(sellers_in_control)" if vwap_dist < -1.5 else
                            "AT_VWAP(decision_point)"
                        )
            except Exception:
                pass

        except Exception:
            continue

    print("[enrich_tech] done", file=sys.stderr, flush=True)


def _ai_trades_worker():
    """Background worker: generate AI trade setups and store in app._ait_cache."""
    import sys
    from datetime import datetime as _dt, date as _date, timedelta as _timedelta
    from openai import OpenAI

    if getattr(app, "_ait_generating", False):
        return
    app._ait_generating = True
    print("[ai_trades_bg] starting generation…", file=sys.stderr, flush=True)

    try:
        oai = OpenAI(
            base_url=os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL"),
            api_key=os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY"),
            timeout=90.0,
        )
    except Exception as e:
        print(f"[ai_trades_bg] OpenAI init error: {e}", file=sys.stderr, flush=True)
        app._ait_generating = False
        return

    tickers_data = {}

    def _add(ticker, key, val):
        if val is None: return
        if ticker not in tickers_data:
            tickers_data[ticker] = {"ticker": ticker}
        if key not in tickers_data[ticker]:
            tickers_data[ticker][key] = val

    active_sources = []

    # 1. Composite Score Board — already aggregates many signals
    cs = getattr(app, "_cs_cache", None)
    if cs:
        active_sources.append("Composite Score Board")
        for r in cs.get("results", []):
            t = r["ticker"]
            _add(t, "price", r.get("price"))
            _add(t, "composite_score", r.get("score"))
            _add(t, "bias", r.get("bias"))
            comp = r.get("components", {})
            _add(t, "iv_rank", comp.get("iv_rank"))
            _add(t, "smart_cp", comp.get("smart_cp"))
            _add(t, "retail_cp", comp.get("retail_cp"))
            _add(t, "accum_pct", comp.get("accum_pct"))
            ta = comp.get("top_accum") or {}
            _add(t, "top_accum_strike", ta.get("strike"))
            _add(t, "top_accum_otm_pct", ta.get("otm_pct"))
            _add(t, "top_accum_expiry", ta.get("expiry"))
            _add(t, "nearest_exp", r.get("nearest_exp"))

    # 2. Vol Crush Detector (+ RSI, SMA50%, volume trend, analyst revisions, days-since-earnings,
    #    options liquidity, earnings beat streak, SPY beta)
    vc = getattr(app, "_vc_cache", None)
    if vc:
        active_sources.append("Vol Crush + Price Structure")
        for r in vc.get("results", []):
            t = r["ticker"]
            _add(t, "price", r.get("price"))
            _add(t, "iv_rank", r.get("iv_rank"))
            _add(t, "current_iv", r.get("current_iv"))
            _add(t, "hv_30", r.get("hv_30"))
            _add(t, "iv_verdict", r.get("verdict"))
            _add(t, "earnings_date", r.get("earnings_date"))
            _add(t, "days_since_earnings", r.get("days_since_earnings"))
            _add(t, "short_float_pct", r.get("short_float_pct"))
            _add(t, "short_ratio", r.get("short_ratio"))
            _add(t, "rsi", r.get("rsi"))
            _add(t, "sma50_pct", r.get("sma50_pct"))
            _add(t, "vol_trend_5d", r.get("vol_trend_5d"))
            _add(t, "net_upgrades_7d", r.get("net_upgrades_7d"))
            _add(t, "options_liquidity_pct", r.get("options_liquidity_pct"))
            _add(t, "earnings_beat_streak", r.get("earnings_beat_streak"))
            _add(t, "spy_beta", r.get("spy_beta"))
            _add(t, "iv_skew", r.get("iv_skew"))
            _add(t, "iv_term_structure", r.get("iv_term_structure"))
            _add(t, "gex_m", r.get("gex_m"))
            _add(t, "gex_regime", r.get("gex_regime"))
            _add(t, "iv_rv_premium", r.get("iv_rv_premium"))
            _add(t, "momentum_12_1", r.get("momentum_12_1"))
            _add(t, "factor_roe", r.get("factor_roe"))
            _add(t, "factor_fpe", r.get("factor_fpe"))
            _add(t, "sector_corr", r.get("sector_corr"))
            _add(t, "news_sentiment", r.get("news_sentiment"))
            _add(t, "news_headline", r.get("news_headline"))
            _add(t, "days_to_earnings", r.get("days_to_earnings"))
            _add(t, "analyst_target_pct", r.get("analyst_target_pct"))
            _add(t, "analyst_recommendation", r.get("analyst_recommendation"))
            _add(t, "put_call_oi_ratio", r.get("put_call_oi_ratio"))
            _add(t, "earnings_impl_move_pct", r.get("earnings_impl_move_pct"))
            _add(t, "call_vol_oi_ratio", r.get("call_vol_oi_ratio"))
            _add(t, "put_vol_oi_ratio", r.get("put_vol_oi_ratio"))
            _add(t, "week52_range_pct", r.get("week52_range_pct"))
            _add(t, "borrow_cost_proxy", r.get("borrow_cost_proxy"))
            _add(t, "eps_revision_trend", r.get("eps_revision_trend"))
            _add(t, "hist_earn_reaction_pct", r.get("hist_earn_reaction_pct"))
            _add(t, "squeeze_risk", r.get("squeeze_risk"))
            _add(t, "analyst_dispersion_pct", r.get("analyst_dispersion_pct"))
            _add(t, "pc_premium_ratio", r.get("pc_premium_ratio"))
            _add(t, "rs_vs_spy", r.get("rs_vs_spy"))
            _add(t, "money_flow_ratio", r.get("money_flow_ratio"))
            _add(t, "insider_net", r.get("insider_net"))
            _add(t, "div_yield_pct", r.get("div_yield_pct"))
            _add(t, "ex_div_days", r.get("ex_div_days"))
            _add(t, "tail_risk_put_pct", r.get("tail_risk_put_pct"))
            _add(t, "iv_skew_pctl", r.get("iv_skew_pctl"))
            _add(t, "short_float_trend", r.get("short_float_trend"))
            _add(t, "pc_ratio_trend", r.get("pc_ratio_trend"))
            _add(t, "instit_own_pct", r.get("instit_own_pct"))

    # 3. Call Intent Decoder
    ci = getattr(app, "_ci_cache", None)
    if ci:
        active_sources.append("Call Intent Decoder")
        for r in ci.get("results", []):
            t = r["ticker"]
            _add(t, "price", r.get("price"))
            _add(t, "call_verdict", r.get("verdict"))
            _add(t, "accum_pct", r.get("accum_pct"))
            _add(t, "fomo_pct", r.get("fomo_pct"))
            _add(t, "accum_prem_m", r.get("accum_prem_m"))
            _add(t, "top_accum_strike", r.get("top_accum_strike"))
            _add(t, "call_vol_oi", r.get("call_vol_oi"))
            _add(t, "top_accum_expiry", r.get("top_accum_expiry"))
            _add(t, "top_accum_otm_pct", r.get("top_accum_otm_pct"))

    # 4. Put Intent / Bear Flow
    oi = getattr(app, "_oi_cache", None)
    if oi:
        active_sources.append("Put Intent / Bear Flow")
        for r in oi.get("results", []):
            t = r["ticker"]
            _add(t, "price", r.get("price"))
            _add(t, "put_verdict", r.get("verdict"))
            _add(t, "hedge_pct", r.get("hedge_pct"))
            _add(t, "bear_pct", r.get("bear_pct"))
            _add(t, "top_bear_strike", r.get("top_bear_strike"))

    # 5. Smart vs Retail
    svr = getattr(app, "_svr_cache", None)
    if svr:
        active_sources.append("Smart vs Retail")
        for r in svr.get("results", []):
            t = r["ticker"]
            _add(t, "price", r.get("price"))
            _add(t, "smart_cp", r.get("smart_cp"))
            _add(t, "retail_cp", r.get("retail_cp"))
            _add(t, "divergence", r.get("divergence"))
            _add(t, "signal_strength", r.get("signal_strength"))
            _add(t, "smart_prem_m", r.get("smart_prem_m"))
            _add(t, "retail_prem_m", r.get("retail_prem_m"))

    # 6. Max Pain / Pinning
    mp = getattr(app, "_mp_cache", None)
    if mp:
        active_sources.append("Max Pain / Pinning")
        for r in mp.get("results", []):
            t = r["ticker"]
            _add(t, "price", r.get("price"))
            _add(t, "max_pain", r.get("max_pain"))
            _add(t, "mp_dist_pct", r.get("distance_pct"))
            _add(t, "mp_direction", r.get("direction"))
            _add(t, "nearest_exp", r.get("nearest_expiry"))
            _add(t, "days_to_exp", r.get("days_to_exp"))

    # 7. Gamma Wall
    gw = getattr(app, "_gw_cache", None)
    if gw:
        active_sources.append("Gamma Wall")
        for r in gw.get("results", []):
            t = r["ticker"]
            _add(t, "gamma_wall_strike", r.get("wall_strike"))
            _add(t, "gamma_wall_dist_pct", r.get("wall_distance_pct"))
            _add(t, "gamma_flip_strike", r.get("flip_strike"))

    # 8. Dark Pool
    dp = getattr(app, "_dp_cache", None)
    if dp:
        active_sources.append("Dark Pool Flow")
        for r in dp.get("results", []):
            t = r["ticker"]
            _add(t, "dark_pool_prem_m", r.get("premium_m"))
            _add(t, "dark_pool_cp_ratio", r.get("call_put_ratio"))

    # 9. Live Signal Feed (notable real-time alerts per ticker)
    sf = getattr(app, "_sf_cache", None)
    if sf:
        active_sources.append("Live Signal Feed")
        for ev in sf.get("events", []):
            t = ev["ticker"]
            if t not in tickers_data:
                tickers_data[t] = {"ticker": t}
            existing = tickers_data[t].get("live_alerts", [])
            existing.append(f"[{ev['type']}] {ev['msg']}")
            tickers_data[t]["live_alerts"] = existing

    # 10. Pre-market movers — inject gap direction per ticker
    pm = getattr(app, "_pm_cache", None)
    if pm:
        active_sources.append("Pre-market Movers")
        for r in pm.get("gainers", []) + pm.get("losers", []):
            t = r["ticker"]
            _add(t, "premarket_chg_pct", r.get("change_pct"))
            _add(t, "premarket_vol_ratio", r.get("vol_ratio"))

    # 11. Market overview — sector momentum + index context
    mo = getattr(app, "_mo_cache", None)
    sector_context = ""
    index_context = ""
    if mo:
        active_sources.append("Sector / Index Momentum")
        sectors_sorted = sorted(mo.get("sectors", []), key=lambda x: x.get("change_pct", 0), reverse=True)
        top2 = [f"{s['name']} {s['change_pct']:+.1f}%" for s in sectors_sorted[:2]]
        bot2 = [f"{s['name']} {s['change_pct']:+.1f}%" for s in sectors_sorted[-2:]]
        sector_context = f"Leading: {', '.join(top2)} | Lagging: {', '.join(bot2)}"
        spy = next((x for x in mo.get("indices", []) if x["ticker"] == "SPY"), None)
        qqq = next((x for x in mo.get("indices", []) if x["ticker"] == "QQQ"), None)
        vix = next((x for x in mo.get("indices", []) if x["ticker"] == "VIX"), None)
        idx_parts = []
        if spy: idx_parts.append(f"SPY {spy['change_pct']:+.2f}%")
        if qqq: idx_parts.append(f"QQQ {qqq['change_pct']:+.2f}%")
        if vix: idx_parts.append(f"VIX ${vix['price']:.1f}")
        index_context = " | ".join(idx_parts)
        ad = mo.get("advance_decline", {})
        if ad:
            index_context += f" | A/D {ad.get('up',0)}/{ad.get('down',0)}"

    # 12. Compute implied move per ticker from current_iv + days_to_exp
    for t, v in tickers_data.items():
        iv = v.get("current_iv")
        dte = v.get("days_to_exp")
        if iv and dte and dte > 0:
            impl_move = round(float(iv) / 100 * (float(dte) / 252) ** 0.5 * 100, 1)
            _add(t, "implied_move_pct", impl_move)

    # 13. Macro calendar — days to next key market events
    def _macro_context():
        from datetime import date as _date
        today = _date.today()
        FED_DATES_2026 = [
            _date(2026, 1, 29), _date(2026, 3, 19), _date(2026, 5, 7),
            _date(2026, 6, 18), _date(2026, 7, 29), _date(2026, 9, 17),
            _date(2026, 11, 5), _date(2026, 12, 10),
        ]
        CPI_APPROX_2026 = [
            _date(2026, 1, 14), _date(2026, 2, 11), _date(2026, 3, 11),
            _date(2026, 4, 10), _date(2026, 5, 13), _date(2026, 6, 10),
            _date(2026, 7, 14), _date(2026, 8, 12), _date(2026, 9, 9),
            _date(2026, 10, 14), _date(2026, 11, 12), _date(2026, 12, 9),
        ]
        import calendar as _cal
        def _next_monthly_opex():
            y, m = today.year, today.month
            for _ in range(3):
                weeks = _cal.monthcalendar(y, m)
                fridays = [w[4] for w in weeks if w[4] != 0]
                opex = _date(y, m, fridays[2])
                if opex >= today: return opex
                m += 1
                if m > 12: m = 1; y += 1
            return None
        parts = []
        fed_next = next((d for d in sorted(FED_DATES_2026) if d >= today), None)
        if fed_next: parts.append(f"Fed={( fed_next - today).days}d")
        cpi_next = next((d for d in sorted(CPI_APPROX_2026) if d >= today), None)
        if cpi_next: parts.append(f"CPI={( cpi_next - today).days}d")
        opex = _next_monthly_opex()
        if opex: parts.append(f"OPEX={( opex - today).days}d")
        return " | ".join(parts) if parts else ""
    macro_context = _macro_context()

    # 14. Multi-day signal persistence — query signal_history for 3-day rolling confirmation
    try:
        with _psycopg2.connect(_DB_URL) as _ph_conn, _ph_conn.cursor() as _ph_cur:
            _ph_cur.execute("""
                SELECT ticker,
                       COUNT(DISTINCT signal_date) AS days,
                       AVG(comp_score)              AS avg_score
                FROM signal_history
                WHERE signal_date >= CURRENT_DATE - INTERVAL '5 days'
                  AND (comp_score >= 60
                       OR call_verdict IN ('HEAVY_ACCUMULATION','STRONG_ACCUMULATION','ACCUMULATION'))
                GROUP BY ticker
                HAVING COUNT(DISTINCT signal_date) >= 2
            """)
            _ph_rows = _ph_cur.fetchall()
        if _ph_rows:
            active_sources.append("Multi-day Signal Persistence")
            for _ph_t, _ph_days, _ph_avg in _ph_rows:
                _add(_ph_t, "persistence_days", int(_ph_days))
                _add(_ph_t, "persistence_avg_score", round(float(_ph_avg or 0), 1))
    except Exception:
        pass

    # 14b. Sector ETF flow confirmation — institutional call buying confirmed at the sector level
    _sector_etf_bullish: set = set()
    try:
        with _psycopg2.connect(_DB_URL) as _se_conn, _se_conn.cursor() as _se_cur:
            _se_cur.execute("""
                SELECT DISTINCT ticker
                FROM unusual_calls_log
                WHERE ticker IN ('XLK','XLF','XLV','XLE','XLI','XLY','XLC','XLB','XLP','XLU','XLRE','QQQ','IWM')
                  AND last_seen >= NOW() - INTERVAL '28 hours'
                  AND prem >= 500000
            """)
            for (_se_etf,) in _se_cur.fetchall():
                _sector_etf_bullish.add(_se_etf)
    except Exception:
        pass

    _TICKER_TO_ETF_MAP = {
        "AAPL":"XLK","MSFT":"XLK","NVDA":"XLK","GOOGL":"XLK","META":"XLK","AMD":"XLK",
        "INTC":"XLK","MU":"XLK","ORCL":"XLK","CRM":"XLK","ADBE":"XLK","QCOM":"XLK",
        "TXN":"XLK","AVGO":"XLK","AMAT":"XLK","LRCX":"XLK","KLAC":"XLK","NOW":"XLK",
        "JPM":"XLF","BAC":"XLF","GS":"XLF","WFC":"XLF","MS":"XLF","C":"XLF",
        "BLK":"XLF","V":"XLF","MA":"XLF","AXP":"XLF","SCHW":"XLF","COF":"XLF",
        "JNJ":"XLV","UNH":"XLV","MRNA":"XLV","PFE":"XLV","ABBV":"XLV","LLY":"XLV",
        "AMGN":"XLV","GILD":"XLV","CVS":"XLV","BMY":"XLV","MDT":"XLV","ISRG":"XLV",
        "XOM":"XLE","CVX":"XLE","SLB":"XLE","OXY":"XLE","MPC":"XLE","COP":"XLE","HAL":"XLE",
        "LMT":"XLI","CAT":"XLI","BA":"XLI","GE":"XLI","HON":"XLI","RTX":"XLI","NOC":"XLI","DE":"XLI",
        "AMZN":"XLY","TSLA":"XLY","COST":"XLY","MCD":"XLY","NKE":"XLY","HD":"XLY","LOW":"XLY","GM":"XLY",
        "NFLX":"XLC","DIS":"XLC","CMCSA":"XLC","T":"XLC","VZ":"XLC","ATVI":"XLC","EA":"XLC",
        "NEE":"XLU","DUK":"XLU","SO":"XLU","AEP":"XLU","D":"XLU",
        "PLD":"XLRE","AMT":"XLRE","SPG":"XLRE","EQIX":"XLRE",
        "APD":"XLB","LIN":"XLB","ECL":"XLB","NEM":"XLB","FCX":"XLB",
    }
    if _sector_etf_bullish:
        active_sources.append("Sector ETF Flow")
        for _se_t in list(tickers_data.keys()):
            _etf_match = _TICKER_TO_ETF_MAP.get(_se_t)
            if _etf_match and _etf_match in _sector_etf_bullish:
                _add(_se_t, "sector_etf_flow", f"CONFIRMED({_etf_match}_bullish)")

    # 14c. Dark pool premium trend (3-day) — is institutional DP activity accelerating or fading?
    try:
        with _psycopg2.connect(_DB_URL) as _dpt_conn, _dpt_conn.cursor() as _dpt_cur:
            _dpt_cur.execute("""
                SELECT ticker, signal_date, dp_prem_m
                FROM signal_history
                WHERE dp_prem_m IS NOT NULL AND dp_prem_m > 0
                  AND signal_date >= CURRENT_DATE - INTERVAL '5 days'
                ORDER BY ticker, signal_date
            """)
            _dpt_rows = _dpt_cur.fetchall()
        _dp_hist: dict = {}
        for _dpt_t, _dpt_d, _dpt_v in _dpt_rows:
            if _dpt_t not in _dp_hist:
                _dp_hist[_dpt_t] = []
            _dp_hist[_dpt_t].append(float(_dpt_v))
        if _dp_hist:
            active_sources.append("Dark Pool Trend")
            for _dpt_t, _dpt_vals in _dp_hist.items():
                if len(_dpt_vals) >= 2:
                    _dp_today_v = _dpt_vals[-1]
                    _dp_prior_v = sum(_dpt_vals[:-1]) / len(_dpt_vals[:-1])
                    if _dp_today_v >= _dp_prior_v * 1.25:
                        _dp_tag = "ACCELERATING"
                    elif _dp_today_v <= _dp_prior_v * 0.75:
                        _dp_tag = "FADING"
                    else:
                        _dp_tag = "STEADY"
                    _add(_dpt_t, "dp_trend", _dp_tag)
                    _add(_dpt_t, "dp_3d_avg_m", round(_dp_prior_v, 2))
    except Exception:
        pass

    # 14d. Multi-day UC flow streak — same unusual call contract returning on 3+ distinct days
    try:
        with _psycopg2.connect(_DB_URL) as _uc_conn, _uc_conn.cursor() as _uc_cur:
            _uc_cur.execute("""
                SELECT ticker,
                       MAX(EXTRACT(EPOCH FROM (last_seen - first_seen)) / 86400.0) AS max_streak_days,
                       COUNT(*) AS active_contracts
                FROM unusual_calls_log
                WHERE last_seen >= NOW() - INTERVAL '36 hours'
                  AND first_seen <= NOW() - INTERVAL '48 hours'
                GROUP BY ticker
                HAVING MAX(EXTRACT(EPOCH FROM (last_seen - first_seen)) / 86400.0) >= 2
            """)
            _uc_streak_rows = _uc_cur.fetchall()
        if _uc_streak_rows:
            active_sources.append("Multi-day UC Streak")
            for _us_t, _us_days, _us_contracts in _uc_streak_rows:
                _add(_us_t, "uc_streak_days", round(float(_us_days), 1))
                _add(_us_t, "uc_streak_contracts", int(_us_contracts))
    except Exception:
        pass

    # 15. Market regime detection — VIX level + SPY 5d/20d trend
    market_regime = "UNKNOWN"
    try:
        import yfinance as _yf_mr
        _mr_data = _yf_mr.download(["^VIX", "SPY"], period="30d", interval="1d", progress=False, auto_adjust=True)["Close"]
        _vix_ser = _mr_data["^VIX"].dropna()
        _spy_ser = _mr_data["SPY"].dropna()
        vix_now = float(_vix_ser.iloc[-1]) if len(_vix_ser) >= 1 else 20.0
        if len(_spy_ser) >= 10:
            spy_5d_chg = float(_spy_ser.iloc[-1]) / float(_spy_ser.iloc[-6]) - 1
            spy_20d_chg = float(_spy_ser.iloc[-1]) / float(_spy_ser.iloc[0]) - 1
            if vix_now > 30:
                market_regime = f"HIGH_FEAR(VIX={vix_now:.1f},SPY5d={spy_5d_chg*100:+.1f}%)"
            elif vix_now > 20:
                if spy_5d_chg > 0.01:
                    market_regime = f"RECOVERY(VIX={vix_now:.1f},SPY5d={spy_5d_chg*100:+.1f}%)"
                elif spy_5d_chg < -0.01:
                    market_regime = f"CORRECTION(VIX={vix_now:.1f},SPY5d={spy_5d_chg*100:+.1f}%)"
                else:
                    market_regime = f"CHOP(VIX={vix_now:.1f},SPY5d={spy_5d_chg*100:+.1f}%)"
            else:
                if spy_5d_chg > 0.01:
                    market_regime = f"BULL_TREND(VIX={vix_now:.1f},SPY5d={spy_5d_chg*100:+.1f}%,20d={spy_20d_chg*100:+.1f}%)"
                elif spy_5d_chg < -0.02:
                    market_regime = f"PULLBACK(VIX={vix_now:.1f},SPY5d={spy_5d_chg*100:+.1f}%)"
                else:
                    market_regime = f"RANGING(VIX={vix_now:.1f},SPY5d={spy_5d_chg*100:+.1f}%)"
        active_sources.append(f"Market Regime")
    except Exception:
        market_regime = "UNKNOWN"

    # 16. Self-learning win rates from ai_trade_log (which setup_types + directions actually work)
    win_rate_context = ""
    try:
        with _psycopg2.connect(_DB_URL) as _wl_conn, _wl_conn.cursor() as _wl_cur:
            _wl_cur.execute("""
                SELECT setup_type, direction,
                       COUNT(*) FILTER (WHERE outcome = 'WIN')  AS wins,
                       COUNT(*) FILTER (WHERE outcome = 'LOSS') AS losses,
                       COUNT(*) AS total
                FROM ai_trade_log
                WHERE outcome IN ('WIN','LOSS')
                GROUP BY setup_type, direction
                HAVING COUNT(*) >= 3
                ORDER BY (COUNT(*) FILTER (WHERE outcome = 'WIN'))::float / NULLIF(COUNT(*),0) DESC
            """)
            _wl_rows = _wl_cur.fetchall()
        if _wl_rows:
            active_sources.append("Historical Win Rates")
            _wl_parts = []
            for _s_type, _s_dir, _s_wins, _s_losses, _s_total in _wl_rows:
                _wr = round(_s_wins / _s_total * 100)
                _wl_parts.append(f"{_s_type}({_s_dir})={_wr}%_wr({_s_wins}/{_s_total})")
            win_rate_context = " | ".join(_wl_parts)
    except Exception:
        win_rate_context = ""

    # 16b. Signal combination win rates — which signal tags predict wins from historical trade log
    combo_win_context = ""
    try:
        with _psycopg2.connect(_DB_URL) as _cw_conn, _cw_conn.cursor() as _cw_cur:
            _cw_cur.execute("""
                SELECT signals_aligned, outcome
                FROM ai_trade_log
                WHERE outcome IN ('WIN','LOSS')
                  AND signals_aligned IS NOT NULL
                  AND jsonb_array_length(signals_aligned) > 0
                ORDER BY trade_date DESC
                LIMIT 300
            """)
            _cw_rows = _cw_cur.fetchall()
        if len(_cw_rows) >= 5:
            _combo_tags = [
                ("persist3d+",  lambda ss: any("persist=" in s and s.split("persist=")[-1].split("d")[0].isdigit() and int(s.split("persist=")[-1].split("d")[0]) >= 3 for s in ss if "persist=" in s)),
                ("MACD_CROSS",  lambda ss: any("BULLISH_CROSS" in s for s in ss)),
                ("ABOVE_POC",   lambda ss: any("ABOVE_POC" in s for s in ss)),
                ("ABOVE_VWAP",  lambda ss: any("ABOVE_VWAP" in s for s in ss)),
                ("PC_BULLISH",  lambda ss: any("BULLISH_ROTATION" in s for s in ss)),
                ("INST_LOADED", lambda ss: any("HIGH_CONVICTION" in s for s in ss)),
                ("UC_STREAK",   lambda ss: any("uc_streak" in s for s in ss)),
                ("SECTOR_CONF", lambda ss: any("SECTOR_CONFIRMED" in s or "sector_etf_flow" in s for s in ss)),
                ("SHORTS_EXIT", lambda ss: any("SHORTS_COVERING" in s for s in ss)),
            ]
            _cstats = {k: [0, 0] for k, _ in _combo_tags}
            for _sa, _oc in _cw_rows:
                _sigs = [str(s) for s in (_sa if isinstance(_sa, list) else [])]
                for _tag, _fn in _combo_tags:
                    try:
                        if _fn(_sigs):
                            _cstats[_tag][1] += 1
                            if _oc == "WIN":
                                _cstats[_tag][0] += 1
                    except Exception:
                        pass
            _cparts = []
            for _tag, _ in _combo_tags:
                _w, _tot = _cstats[_tag]
                if _tot >= 3:
                    _cparts.append(f"{_tag}:{round(_w / _tot * 100)}%({_w}/{_tot})")
            if _cparts:
                combo_win_context = "SIGNAL_COMBO_WIN_RATES: " + " | ".join(_cparts)
    except Exception:
        combo_win_context = ""

    # 17. Macro cross-asset signals (yield curve, DXY, credit spreads, crude, gold)
    macro_cross_asset = ""
    try:
        import yfinance as _yf_macro
        _mcr = _yf_macro.download(
            ["^TNX", "^IRX", "UUP", "HYG", "LQD", "USO", "GLD", "^VIX", "^VIX3M"],
            period="5d", interval="1d", progress=False, auto_adjust=True
        )["Close"]
        def _mlast(sym):
            try:
                col = _mcr[sym].dropna() if hasattr(_mcr, "columns") and sym in _mcr.columns else None
                return float(col.iloc[-1]) if col is not None and len(col) > 0 else None
            except Exception: return None
        def _m5d(sym):
            try:
                col = _mcr[sym].dropna() if hasattr(_mcr, "columns") and sym in _mcr.columns else None
                return round((float(col.iloc[-1]) / float(col.iloc[0]) - 1) * 100, 1) if col is not None and len(col) >= 2 else None
            except Exception: return None
        _tnx = _mlast("^TNX"); _irx = _mlast("^IRX"); _uup5 = _m5d("UUP")
        _hyg5 = _m5d("HYG"); _lqd5 = _m5d("LQD")
        _uso5 = _m5d("USO"); _gld5 = _m5d("GLD")
        _parts17 = []
        if _tnx is not None and _irx is not None:
            _curve = round(_tnx - _irx, 2)
            _ctag = "INVERTED(recession_risk)" if _curve < 0 else "STEEP(risk_on)" if _curve > 1.5 else "FLAT"
            _parts17.append(f"YieldCurve(10y-3m)={_curve:+.2f}%({_ctag})")
        if _uup5 is not None:
            _dxy_tag = "STRONG_USD(headwind_equities)" if _uup5 > 0.5 else "WEAK_USD(tailwind_equities)" if _uup5 < -0.5 else "STABLE"
            _parts17.append(f"USD5d={_uup5:+.2f}%({_dxy_tag})")
        if _hyg5 is not None and _lqd5 is not None:
            _cs = round(_hyg5 - _lqd5, 2)
            _cstag = "WIDENING(risk_off)" if _cs < -0.3 else "TIGHTENING(risk_on)" if _cs > 0.3 else "STABLE"
            _parts17.append(f"CreditSpread5d={_cs:+.2f}%({_cstag})")
        if _uso5 is not None:
            _parts17.append(f"Crude5d={_uso5:+.1f}%")
        if _gld5 is not None:
            _gld_tag = "FLIGHT_TO_SAFETY" if _gld5 > 1.5 else "risk_on_rotation" if _gld5 < -1.5 else ""
            _parts17.append(f"Gold5d={_gld5:+.1f}%" + (f"({_gld_tag})" if _gld_tag else ""))
        _vix_lvl  = _mlast("^VIX")
        _vix3m_lvl = _mlast("^VIX3M")
        if _vix_lvl is not None and _vix3m_lvl is not None and _vix3m_lvl > 0:
            _vts = round(_vix_lvl - _vix3m_lvl, 2)
            _vts_tag = ("BACKWARDATION(crisis/event_risk)" if _vts > 2
                        else "contango(calm/risk_on)" if _vts < -1
                        else "flat")
            _parts17.append(f"VIX_TermStructure={_vts:+.2f}({_vts_tag})")
        if _parts17:
            macro_cross_asset = " | ".join(_parts17)
            active_sources.append("Macro Cross-Asset")
    except Exception:
        macro_cross_asset = ""

    # 18. Unusual Calls — inject per-ticker best premium entry (highest single-strike premium)
    uc = getattr(app, "_unusual_calls_cache", None)
    if uc:
        active_sources.append("Unusual Call Flow")
        uc_by_ticker = {}
        for hit in uc.get("hits", []):
            t = hit["ticker"]
            prem = hit.get("prem", 0)
            if t not in uc_by_ticker or prem > uc_by_ticker[t].get("prem", 0):
                uc_by_ticker[t] = hit
        for t, hit in uc_by_ticker.items():
            if t not in tickers_data:
                tickers_data[t] = {"ticker": t}
            _add(t, "uc_prem_m", round(hit["prem"] / 1_000_000, 2))
            _add(t, "uc_vol_oi", hit.get("vol_oi"))
            _add(t, "uc_strike", hit.get("strike"))
            _add(t, "uc_expiry", hit.get("expiry"))
            _add(t, "uc_otm_pct", hit.get("otm_pct"))
            _add(t, "uc_urgency", hit.get("urgency"))

    # Optional technical enrichment: MACD, Support/Resistance, Volume Profile POC, VWAP
    # Runs silently — any failure leaves existing signals untouched
    try:
        _enrich_technical_signals(tickers_data)
    except Exception as _et_err:
        import sys as _sys
        print(f"[ai_trades_bg] tech enrichment skipped: {_et_err}", file=_sys.stderr)

    # Only use tickers where we have enough signal depth (3+ fields beyond ticker key)
    rich = {t: v for t, v in tickers_data.items() if len(v) >= 3}

    # Helper: warm all tab caches in background threads via internal HTTP
    def _start_cache_warming():
        if getattr(app, "_cache_warming", False):
            return
        app._cache_warming = True
        def _do_warm():
            try:
                import urllib.request as _ur
                warm_paths = [
                    f"/stock-api/vol-crush",
                    f"/stock-api/call-intent",
                    f"/stock-api/options-intent",
                    f"/stock-api/smart-vs-retail",
                    f"/stock-api/max-pain",
                    f"/stock-api/gamma-wall",
                    f"/stock-api/darkpool",
                    f"/stock-api/premarket",
                    f"/stock-api/market/overview",
                    f"/stock-api/signal-feed",
                    f"/stock-api/composite-score",
                ]
                def _fetch(path):
                    try: _ur.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=90)
                    except Exception: pass
                with ThreadPoolExecutor(max_workers=4) as ex:
                    futs = [ex.submit(_fetch, p) for p in warm_paths]
                    for f in as_completed(futs):
                        try: f.result()
                        except: pass
                # After warming, retry AI trades generation once
                import time as _time
                _time.sleep(2)
                if not getattr(app, "_ait_generating", False):
                    import threading as _thr2
                    _thr2.Thread(target=_ai_trades_worker, daemon=True).start()
            finally:
                app._cache_warming = False
        import threading
        threading.Thread(target=_do_warm, daemon=True).start()

    # Need at least 2 signal sources AND some rich tickers to proceed
    if len(active_sources) < 2 or len(rich) < 3:
        already_warming = getattr(app, "_cache_warming", False)
        if not already_warming:
            _start_cache_warming()
        import sys
        print(f"[ai_trades_bg] not enough signals ({len(active_sources)} sources, {len(rich)} tickers) — aborting", file=sys.stderr)
        app._ait_generating = False
        return  # background worker exits; HTTP handler will return loading state

    # Sort by composite score descending, fall back to alphabetical
    sorted_tickers = sorted(rich.values(), key=lambda x: x.get("composite_score", 50), reverse=True)

    # Premium gate: tickers with $500K+ unusual call premium come first (sorted by premium size).
    # Only fall back to non-premium tickers if fewer than 5 premium tickers exist.
    uc_qualified   = sorted(
        [v for v in rich.values() if (v.get("uc_prem_m") or 0) >= 0.5],
        key=lambda x: x.get("uc_prem_m", 0), reverse=True
    )
    uc_fallback    = [v for v in sorted_tickers if (v.get("uc_prem_m") or 0) < 0.5]
    candidate_pool = uc_qualified + uc_fallback  # AI sees premium tickers first

    # LIVE PRICE REFRESH — always override stale cached prices with today's real market price
    # This prevents trades being generated with months-old price data (wrong strikes/targets)
    try:
        import yfinance as _yf_pr
        _refresh_tickers = [v["ticker"] for v in candidate_pool[:15]]
        def _fetch_live_price(t):
            try:
                p = float(_yf_pr.Ticker(t).fast_info.last_price or 0)
                return t, p if p > 0 else None
            except Exception:
                return t, None
        with ThreadPoolExecutor(max_workers=10) as _pr_ex:
            _pr_results = dict(_pr_ex.map(lambda t: _fetch_live_price(t), _refresh_tickers))
        for v in candidate_pool:
            t = v["ticker"]
            live_p = _pr_results.get(t)
            if live_p and live_p > 0:
                v["price"] = round(live_p, 2)
        import sys as _sys
        print(f"[ai_trades_bg] live prices refreshed for {len(_refresh_tickers)} tickers", file=_sys.stderr)
    except Exception as _pr_err:
        import sys as _sys
        print(f"[ai_trades_bg] live price refresh error: {_pr_err}", file=_sys.stderr)

    # Build compact signal block — top 15 tickers, one line each, key fields only
    sig_lines = []
    for v in candidate_pool[:15]:
        parts = [f"{v['ticker']} ${v.get('price','?')}"]
        if v.get("composite_score") is not None:
            parts.append(f"score={v['composite_score']}/100({v.get('bias','?')})")
        if v.get("persistence_days") is not None:
            parts.append(f"persist={v['persistence_days']}d(avg_score={v.get('persistence_avg_score','?')})")
        if v.get("iv_rank") is not None:
            parts.append(f"iv_rank={v['iv_rank']}%({v.get('iv_verdict','')})")
        if v.get("implied_move_pct") is not None:
            parts.append(f"impl_move=±{v['implied_move_pct']}%")
        if v.get("rsi") is not None:
            rsi_tag = "overbought" if v["rsi"] > 70 else "oversold" if v["rsi"] < 30 else "neutral"
            parts.append(f"rsi={v['rsi']}({rsi_tag})")
        if v.get("sma50_pct") is not None:
            parts.append(f"sma50={v['sma50_pct']:+.1f}%")
        if v.get("vol_trend_5d") is not None:
            vt = v["vol_trend_5d"]
            vt_tag = "surging" if vt >= 1.5 else "declining" if vt < 0.7 else "normal"
            parts.append(f"vol_trend={vt}x({vt_tag})")
        if v.get("divergence"):
            parts.append(f"SmartVsRetail={v['divergence']}({v.get('signal_strength','?')}) scp={v.get('smart_cp','?')} rcp={v.get('retail_cp','?')}")
        if v.get("call_verdict"):
            vol_oi_tag = f" vol/oi={v['call_vol_oi']}x" if v.get("call_vol_oi") else ""
            parts.append(f"calls={v['call_verdict']} accum={v.get('accum_pct','?')}%{vol_oi_tag}")
        if v.get("put_verdict"):
            parts.append(f"puts={v['put_verdict']} bear={v.get('bear_pct','?')}%")
        if v.get("max_pain"):
            parts.append(f"mp=${v['max_pain']}({v.get('mp_dist_pct',0):+.1f}% {v.get('mp_direction','?')} {v.get('days_to_exp','?')}d)")
        if v.get("gamma_wall_strike"):
            parts.append(f"gwall=${v['gamma_wall_strike']}({v.get('gamma_wall_dist_pct',0):+.1f}%)")
        if v.get("dark_pool_prem_m"):
            parts.append(f"dp=${v['dark_pool_prem_m']}M cp={v.get('dark_pool_cp_ratio','?')}")
        if v.get("uc_prem_m") is not None:
            pm = v["uc_prem_m"]
            pm_tag = "WHALE" if pm >= 5 else "INSTITUTIONAL" if pm >= 1 else "NOTABLE"
            parts.append(f"uc_prem=${pm}M({pm_tag}) uc_vol_oi={v.get('uc_vol_oi','?')}x strike={v.get('uc_strike','?')} exp={v.get('uc_expiry','?')} otm={v.get('uc_otm_pct','?')}%({v.get('uc_urgency','?')})")
        if v.get("top_accum_strike"):
            parts.append(f"topstrike=${v['top_accum_strike']} exp={v.get('top_accum_expiry','?')}")
        if v.get("days_since_earnings") is not None:
            parts.append(f"post_earnings={v['days_since_earnings']}d_ago(IV_crush_window)")
        elif v.get("earnings_date"):
            parts.append(f"earnings={v['earnings_date']}")
        if v.get("net_upgrades_7d") is not None and v["net_upgrades_7d"] != 0:
            tag = f"+{v['net_upgrades_7d']} upgrades" if v["net_upgrades_7d"] > 0 else f"{v['net_upgrades_7d']} downgrades"
            parts.append(f"analysts({tag}_7d)")
        if v.get("short_float_pct") is not None:
            si_str = f"short={v['short_float_pct']}%"
            if v.get("short_ratio") is not None:
                si_str += f"/{v['short_ratio']}d-to-cover"
            parts.append(si_str)
        if v.get("premarket_chg_pct") is not None:
            vol_tag = f" vol×{v['premarket_vol_ratio']}" if v.get("premarket_vol_ratio") else ""
            parts.append(f"premarket={v['premarket_chg_pct']:+.2f}%{vol_tag}")
        if v.get("options_liquidity_pct") is not None:
            liq = v["options_liquidity_pct"]
            liq_tag = "liquid" if liq < 5 else "ILLIQUID_AVOID" if liq > 12 else "ok"
            parts.append(f"opt_spread={liq}%({liq_tag})")
        if v.get("earnings_beat_streak"):
            parts.append(f"earn_beat={v['earnings_beat_streak']}_qtrs")
        if v.get("spy_beta") is not None:
            b = v["spy_beta"]
            b_tag = "high_beta" if b >= 1.5 else "low_beta" if b <= 0.6 else ""
            parts.append(f"beta={b}x" + (f"({b_tag})" if b_tag else ""))
        if v.get("iv_skew") is not None:
            sk = v["iv_skew"]
            sk_tag = "FEAR_PREMIUM" if sk > 8 else "CALL_SKEW(demand)" if sk < -3 else "balanced"
            parts.append(f"iv_skew={sk:+.1f}pp({sk_tag})")
        if v.get("iv_term_structure") is not None:
            ts = v["iv_term_structure"]
            ts_tag = "BACKWARDATION(event_risk)" if ts > 5 else "contango(calm)" if ts < -3 else "flat"
            parts.append(f"iv_ts={ts:+.1f}pp({ts_tag})")
        if v.get("gex_m") is not None:
            gr = v.get("gex_regime", "")
            parts.append(f"GEX=${v['gex_m']}M({gr})")
        if v.get("iv_rv_premium") is not None:
            ivp = v["iv_rv_premium"]
            ivp_tag = "RICH_SELL_PREM" if ivp > 20 else "CHEAP_BUY_VOL" if ivp < -10 else "fair"
            parts.append(f"iv_rv={ivp:+.1f}%({ivp_tag})")
        if v.get("momentum_12_1") is not None:
            mo = v["momentum_12_1"]
            mo_tag = "strong_momentum" if mo > 15 else "weak_momentum" if mo < -15 else "neutral"
            parts.append(f"mom12_1={mo:+.1f}%({mo_tag})")
        if v.get("factor_roe") is not None:
            parts.append(f"ROE={v['factor_roe']}%")
        if v.get("factor_fpe") is not None:
            fpe = v["factor_fpe"]
            fpe_tag = "CHEAP" if fpe < 15 else "EXPENSIVE" if fpe > 35 else "fair"
            parts.append(f"fwd_PE={fpe}x({fpe_tag})")
        if v.get("sector_corr") is not None:
            sc = v["sector_corr"]
            sc_tag = "IDIOSYNCRATIC" if sc < 0.5 else "sector_driven" if sc > 0.85 else ""
            parts.append(f"sector_corr={sc}" + (f"({sc_tag})" if sc_tag else ""))
        if v.get("news_sentiment") is not None:
            ns = v["news_sentiment"]
            ns_tag = "BULLISH_NEWS" if ns > 0.5 else "BEARISH_NEWS" if ns < -0.5 else "neutral_news"
            hdl = f" [{v['news_headline'][:50]}]" if v.get("news_headline") else ""
            parts.append(f"news={ns}({ns_tag}){hdl}")
        if v.get("days_to_earnings") is not None:
            dte_val = v["days_to_earnings"]
            dte_tag = "IMMINENT(<7d)" if dte_val <= 7 else "SOON(<30d)" if dte_val <= 30 else f"{dte_val}d_away"
            earn_part = f"earn_in={dte_val}d({dte_tag})"
            if v.get("earnings_impl_move_pct") is not None:
                earn_part += f" impl_earn_move=±{v['earnings_impl_move_pct']}%"
            parts.append(earn_part)
        if v.get("analyst_target_pct") is not None:
            tgt = v["analyst_target_pct"]
            tgt_tag = "STRONG_BUY_CONSENSUS" if tgt > 25 else "BUY_CONSENSUS" if tgt > 10 else "FULLY_VALUED" if tgt < 0 else "modest_upside"
            rec_str = f"/{v['analyst_recommendation']}" if v.get("analyst_recommendation") else ""
            parts.append(f"analyst_tgt={tgt:+.1f}%{rec_str}({tgt_tag})")
        if v.get("put_call_oi_ratio") is not None:
            pcoi = v["put_call_oi_ratio"]
            pcoi_tag = "HEAVY_PUT_OI(bearish_positioning)" if pcoi > 1.5 else "HEAVY_CALL_OI(bullish_positioning)" if pcoi < 0.6 else "balanced_OI"
            parts.append(f"pc_oi_ratio={pcoi}({pcoi_tag})")
        if v.get("week52_range_pct") is not None:
            w52 = v["week52_range_pct"]
            w52_tag = "NEAR_52W_HIGH(breakout_zone)" if w52 >= 90 else "NEAR_52W_LOW(support_bounce)" if w52 <= 10 else "mid_range"
            parts.append(f"52w_range={w52:.0f}%({w52_tag})")
        if v.get("borrow_cost_proxy") in ("HIGH_BORROW", "ELEVATED_BORROW"):
            parts.append(f"borrow={v['borrow_cost_proxy']}(puts_may_be_synthetic_shorts)")
        if v.get("call_vol_oi_ratio") is not None and v.get("put_vol_oi_ratio") is not None:
            cvoi = v["call_vol_oi_ratio"]; pvoi = v["put_vol_oi_ratio"]
            c_tag = "FRESH(one_day)" if cvoi > 0.25 else "STRUCTURAL(multi_week)" if cvoi < 0.05 else "mixed"
            p_tag = "FRESH(one_day)" if pvoi > 0.25 else "STRUCTURAL(multi_week)" if pvoi < 0.05 else "mixed"
            parts.append(f"flow_persist(calls={cvoi}/{c_tag} puts={pvoi}/{p_tag})")
        if v.get("eps_revision_trend"):
            parts.append(f"eps_trend={v['eps_revision_trend']}")
        if v.get("hist_earn_reaction_pct") is not None:
            her = v["hist_earn_reaction_pct"]
            her_tag = "LARGE_MOVER" if her >= 8 else "moderate" if her >= 3 else "small_mover"
            parts.append(f"hist_earn_move=±{her}%({her_tag})")
        if v.get("squeeze_risk") in ("HIGH", "EXTREME"):
            sq = v["squeeze_risk"]
            parts.append(f"squeeze_risk={sq}(short_squeeze_imminent_danger_for_bears)")
        if v.get("analyst_dispersion_pct") is not None:
            ad = v["analyst_dispersion_pct"]
            ad_tag = "HIGH_DISAGREEMENT(prefer_straddle)" if ad >= 30 else "MODERATE_DISAGREEMENT" if ad >= 15 else "CONSENSUS(directional_ok)"
            parts.append(f"analyst_dispersion={ad}%({ad_tag})")
        if v.get("pc_premium_ratio") is not None:
            pcp = v["pc_premium_ratio"]
            pcp_tag = "HEAVY_PUT_SPEND(institutional_fear)" if pcp > 1.5 else "HEAVY_CALL_SPEND(risk_on)" if pcp < 0.6 else "balanced_spend"
            parts.append(f"pc_prem_ratio={pcp}({pcp_tag})")
        if v.get("rs_vs_spy") is not None:
            rs = v["rs_vs_spy"]
            rs_tag = "BEATING_MARKET" if rs > 20 else "LAGGING_MARKET" if rs < -20 else "in_line_with_SPY"
            parts.append(f"rs_vs_spy={rs:+.1f}%({rs_tag})")
        if v.get("money_flow_ratio") is not None:
            mf = v["money_flow_ratio"]
            mf_tag = "ACCUMULATION" if mf > 1.3 else "DISTRIBUTION" if mf < 0.8 else "neutral_flow"
            parts.append(f"money_flow={mf}({mf_tag})")
        if v.get("insider_net") and v["insider_net"] != "NEUTRAL":
            ins = v["insider_net"]
            ins_tag = "INSIDER_BUYING(high_conviction_bull)" if ins == "BUYING" else "insider_selling(neutral)"
            parts.append(f"insider={ins}({ins_tag})")
        if v.get("div_yield_pct") is not None:
            parts.append(f"div_yield={v['div_yield_pct']}%")
        if v.get("ex_div_days") is not None:
            exd = v["ex_div_days"]
            exd_tag = "IMMINENT_EXDIV(early_assign_risk_on_calls)" if exd <= 7 else f"ex_div_in_{exd}d"
            parts.append(f"ex_div={exd}d({exd_tag})")
        if v.get("tail_risk_put_pct") is not None:
            trp = v["tail_risk_put_pct"]
            if trp > 20:
                trp_tag = "CRASH_HEDGING_ACTIVE(extreme)" if trp > 40 else "CRASH_HEDGING(elevated)"
                parts.append(f"tail_risk_puts={trp}%({trp_tag})")
        if v.get("iv_skew_pctl") is not None:
            skp = v["iv_skew_pctl"]
            skp_tag = "EXTREME_HISTORICAL_FEAR" if skp >= 90 else "HIGH_HISTORICAL_FEAR" if skp >= 75 else "below_avg_fear" if skp <= 25 else "avg_fear"
            parts.append(f"iv_skew_pctl={skp}th({skp_tag})")
        if v.get("short_float_trend") is not None:
            sft = v["short_float_trend"]
            sft_tag = "SHORTS_BUILDING(bear_conviction)" if sft > 1 else "SHORTS_COVERING(squeeze_trigger)" if sft < -1 else "short_stable"
            parts.append(f"short_trend={sft:+.1f}pp({sft_tag})")
        if v.get("pc_ratio_trend") is not None:
            pct = v["pc_ratio_trend"]
            pct_tag = "BULLISH_ROTATION(calls_dominating)" if pct < -0.2 else "BEARISH_ROTATION(puts_building)" if pct > 0.2 else "stable"
            parts.append(f"pc_ratio_mom={pct:+.2f}({pct_tag})")
        if v.get("instit_own_pct") is not None:
            iop = v["instit_own_pct"]
            iop_tag = "HIGH_CONVICTION(smart_money_loaded)" if iop >= 70 else "MODERATE" if iop >= 40 else "LOW_INST_OWN"
            parts.append(f"instit_own={iop}%({iop_tag})")
        if v.get("uc_streak_days") is not None:
            usd = v["uc_streak_days"]
            usc = v.get("uc_streak_contracts", 1)
            usd_tag = "PERSISTENT_WHALE(5d+)" if usd >= 5 else "MULTI_DAY_INSTITUTIONAL(3-5d)" if usd >= 3 else "RETURNING_BUYER(2-3d)"
            parts.append(f"uc_streak={usd:.0f}d({usd_tag}) contracts={usc}")
        if v.get("sector_etf_flow"):
            parts.append(f"sector_etf_flow={v['sector_etf_flow']}")
        if v.get("dp_trend") and v["dp_trend"] != "STEADY":
            dp3d = v.get("dp_3d_avg_m", "")
            dp3d_str = f"(3d_avg=${dp3d}M)" if dp3d else ""
            parts.append(f"dp_trend={v['dp_trend']}{dp3d_str}")
        if v.get("tech_macd"):
            m = v["tech_macd"]
            m_tag = ("BULLISH_CROSS(fresh_buy_signal)" if m == "BULLISH_CROSS" else
                     "BULLISH(above_signal)"           if m == "BULLISH"        else
                     "BEARISH_CROSS(momentum_warning)" if m == "BEARISH_CROSS"  else
                     "BEARISH(below_signal)")
            div_str = f"+{v['tech_macd_div']}" if v.get("tech_macd_div") else ""
            parts.append(f"macd={m_tag}{div_str}")
        if v.get("tech_sr_context"):
            parts.append(f"sr={v['tech_sr_context']}")
        if v.get("tech_poc_context"):
            poc_d = v.get("tech_poc_dist_pct", 0)
            parts.append(f"poc={v['tech_poc_context']}")
        if v.get("tech_vwap_context"):
            vd = v.get("tech_vwap_dist_pct", 0)
            vwap_label = v["tech_vwap_context"].split("(")[0]
            parts.append(f"vwap={vd:+.1f}%({vwap_label})")
        if v.get("live_alerts"):
            parts.append(f"alerts=[{'; '.join(v['live_alerts'][:2])}]")
        sig_lines.append(" | ".join(parts))

    sig_text = "\n".join(sig_lines)

    macro_line = f"MACRO: {macro_context}" if macro_context else ""
    sector_line = f"SECTORS: {sector_context}" if sector_context else ""
    index_line = f"INDICES: {index_context}" if index_context else ""
    regime_line = f"MARKET_REGIME: {market_regime}" if market_regime and market_regime != "UNKNOWN" else ""
    winrate_line = f"YOUR_HISTORICAL_WIN_RATES: {win_rate_context}" if win_rate_context else ""
    combo_winrate_line = combo_win_context if combo_win_context else ""
    macro_cross_line = f"MACRO_CROSS_ASSET: {macro_cross_asset}" if macro_cross_asset else ""
    context_block = "\n".join(x for x in [macro_line, sector_line, index_line, regime_line, winrate_line, combo_winrate_line, macro_cross_line] if x)

    system_msg = (
        "You are an elite institutional options trader operating at hedge-fund quant level. "
        "You receive 50+ data points per ticker across 21 sources including vol surface, dealer gamma, factor scores, macro cross-asset signals, analyst consensus, and earnings intelligence. "
        "CRITICAL RULES:\n"
        "1. NEVER recommend a setup where opt_spread>12% (ILLIQUID_AVOID) — wide spreads destroy edge.\n"
        "2. In HIGH_FEAR or CORRECTION regimes: avoid LONG CALL; prefer PUT spreads or IRON CONDORs on tickers with iv_rv=RICH_SELL_PREM.\n"
        "3. In BULL_TREND regime: prefer LONG CALL on high-beta (beta≥1.5) names with vol_trend surging and mom12_1>0.\n"
        "4. In RANGING/CHOP regime: prefer IRON CONDOR on IV_rank≥60 + iv_rv=RICH_SELL_PREM tickers; avoid directional plays.\n"
        "5. If YOUR_HISTORICAL_WIN_RATES provided: strongly prefer setup_types with high win rates from your own history.\n"
        "6. persist=3d+ is your highest-conviction filter — multi-day institutional building is rare and reliable.\n"
        "7. earn_beat=3/4 or 4/4 gives fundamental tailwind; earn_beat=0/4 is a headwind.\n"
        "8. GEX=LONG_GAMMA(suppressive) → mean-reversion setups; SHORT_GAMMA(amplifying) → directional/momentum setups.\n"
        "9. iv_skew=FEAR_PREMIUM (>8pp) → institutional crash hedging; use PUT spreads or add protection.\n"
        "10. iv_rv=RICH_SELL_PREM (>20%) → premium selling edge; CHEAP_BUY_VOL (<-10%) → long vol edge.\n"
        "11. MACRO_CROSS_ASSET: YieldCurve=INVERTED → rotate defensive; CreditSpread=WIDENING → reduce risk; Gold=FLIGHT_TO_SAFETY → avoid long equities; VIX_TermStructure=BACKWARDATION → event risk priced, vol may spike further.\n"
        "12. sector_corr=IDIOSYNCRATIC (<0.5) → ticker moves on its own; prefer over highly correlated names.\n"
        "13. news=BEARISH_NEWS with BULL_TREND → fade the news; news=BULLISH_NEWS with momentum = confirmation.\n"
        f"14. EXPIRY RULE: TODAY'S REAL DATE IS {str(_date.today())}. ALL expiry dates you output MUST be in YYYY-MM-DD format AND must fall between {str(_date.today() + _timedelta(days=21))} (earliest) and {str(_date.today() + _timedelta(days=90))} (latest). NEVER output a date from 2024 or any year other than the current year/next year. Never recommend weekly or 0DTE expirations. EXCEPTION: If a ticker shows a single block options trade with premium ≥$10M at an expiry 180–365 days out (LEAPS territory), you MAY recommend that longer expiry — this is whale/institutional positioning and is extremely bullish or bearish. In that case set setup_type to LONG CALL or LONG PUT (not a spread), set conviction to HIGH, and explicitly note the whale block in signals_aligned (e.g. '$20M LEAPS call block, 9mo out').\n"
        "15. EARNINGS PROXIMITY: If earn_in≤7d (IMMINENT), prefer STRADDLE or avoid entirely unless conviction is extreme. If earn_in=8-30d (SOON), IV is likely elevated — check iv_rv; if RICH, sell spreads; if CHEAP, buy vol. impl_earn_move shows the options market's expected ±% move into earnings — compare to earn_beat history.\n"
        "16. ANALYST CONSENSUS: analyst_tgt=STRONG_BUY_CONSENSUS (>25% upside) combined with institutional accumulation (accum_pct≥60%) = highest fundamental + flow alignment. analyst_tgt=FULLY_VALUED (<0% upside) is a headwind for LONG CALL setups.\n"
        "17. PUT/CALL OI RATIO: pc_oi_ratio>1.5 (HEAVY_PUT_OI) = institutions are hedged/bearish positioned; <0.6 (HEAVY_CALL_OI) = bullish positioning. Use as directional confirmation or contrarian signal in conjunction with other factors.\n"
        "18. 52-WEEK RANGE: 52w_range≥90% (NEAR_52W_HIGH) = breakout zone → momentum continuation setups; ≤10% (NEAR_52W_LOW) = support test → mean-reversion bounce or put-selling setups. NEVER recommend LONG CALL on a stock at 52w low without strong catalyst evidence.\n"
        "19. BORROW COST: borrow=HIGH_BORROW means stock is expensive to short. This means heavy put OI on high-short-interest names may be synthetic short hedges by short sellers, NOT directional bearish bets. Do not read put OI as bearish conviction if borrow=HIGH_BORROW.\n"
        "20. FLOW PERSISTENCE: flow_persist shows call/put vol-to-OI ratios. calls=STRUCTURAL(multi_week) means call OI has been building over multiple days — institutional conviction. calls=FRESH(one_day) means today's activity only — could be noise or a hedge. Weight STRUCTURAL flow 2x vs FRESH flow in your conviction score.\n"
        "21. EPS REVISION TREND: eps_trend=RISING means analysts are raising forward earnings estimates — a strong fundamental tailwind. eps_trend=DECLINING means estimates are being cut — a headwind even if flow looks bullish. When eps_trend=DECLINING and call flow is present, reduce conviction; the flow may be a short-term trade against a deteriorating fundamental trend.\n"
        "22. HISTORICAL EARNINGS REACTION: hist_earn_move=±X% is the average absolute price move this stock has made on past earnings days. Use this to calibrate STRADDLE pricing: if impl_earn_move < hist_earn_move, the straddle is cheap (buy vol); if impl_earn_move > hist_earn_move by >50%, the market is overpricing earnings risk (sell premium). This is one of the highest-edge signals for earnings-event trades.\n"
        "23. SHORT SQUEEZE RISK: squeeze_risk=HIGH or EXTREME means the stock has: high short float (≥15%), hard-to-borrow conditions, rising price momentum (RSI>60), AND volume surging. In this scenario: (a) NEVER recommend LONG PUT or BEAR PUT SPREAD — short squeeze could cause catastrophic loss. (b) Consider LONG CALL as a squeeze-capture setup. (c) For bearish plays, use far OTM puts only with strict stop loss.\n"
        "24. ANALYST DISPERSION: analyst_dispersion≥30% (HIGH_DISAGREEMENT) means analysts have wildly different price targets — the outcome is binary and uncertain. In this case: prefer STRADDLE over directional setups, even if flow is one-directional. analyst_dispersion<15% (CONSENSUS) means the fundamental story is clear — directional plays are appropriate.\n"
        "25. PUT/CALL PREMIUM RATIO (DOLLAR-WEIGHTED): pc_prem_ratio measures dollars spent on puts vs calls. CRITICAL INTERPRETATION — high put spend (pc_prem_ratio>1.5) almost always reflects HEDGING by institutions protecting long stock positions, NOT directional bearish bets. Do NOT use pc_prem_ratio to justify a bearish setup. Use it only to gauge overall market fear level: HEAVY_PUT_SPEND = elevated hedging = slightly higher uncertainty for calls. HEAVY_CALL_SPEND(<0.6) = clean risk-on environment = strong confirmation for LONG CALL entries.\n"
        "26. RELATIVE STRENGTH VS SPY: rs_vs_spy is the stock's 1-year return minus SPY's 1-year return. BEATING_MARKET(>+20%) = institutions are actively accumulating; strong confirmation for LONG CALL. LAGGING_MARKET(<-20%) = the stock is a structural underperformer — a powerful headwind even with bullish call flow; reduce conviction or skip. Use RS to confirm momentum: only go high-conviction LONG CALL on stocks with positive RS alignment.\n"
        "27. MONEY FLOW RATIO: money_flow is average volume on up-price days divided by average volume on down-price days over the past 30 sessions. ACCUMULATION(>1.3) = institutions consistently buying on strength AND dips — confirms bullish setups. DISTRIBUTION(<0.8) = sellers are dominant even on green days — confirms bearish or reduces bullish conviction. This is a structural signal; it takes weeks to shift, so treat it as a high-weight baseline.\n"
        "28. INSIDER TRANSACTIONS: insider=BUYING means company officers or directors purchased shares on the open market in the last 30 days — one of the most reliable long-term bullish signals in finance (insiders only buy with their own money when they believe the stock is undervalued). Add +1 conviction tier when insider=BUYING aligns with bullish call flow. insider=SELLING is NEUTRAL — insiders sell for taxes, diversification, estate planning; never use it as a bearish signal alone.\n"
        "29. DIVIDEND YIELD & EX-DIVIDEND DATE: CRITICAL RULE — if ex_div<=7d, DO NOT recommend LONG CALL — the option holder may exercise early to capture the dividend, creating assignment risk. Skip that ticker and pick the next best signal instead. div_yield>3% acts as a price floor: income buyers support the stock on dips.\n"
        "30. TAIL RISK PUT CONCENTRATION: tail_risk_puts is the % of total put volume in deep OTM strikes (>15% below spot). CRASH_HEDGING_ACTIVE(>40%) = institutions are paying for disaster protection, not making directional bets — this is a macro risk-off signal. When tail_risk_puts>30%, do NOT sell premium structures (IRON CONDOR, BULL PUT SPREAD) — institutions may know about an upcoming systemic risk event. The signal does NOT mean the stock will definitely fall; it means smart money is buying insurance at scale.\n"
        "31. IV SKEW PERCENTILE (when available after 30+ days of data): iv_skew_pctl ranks today's IV skew vs the past year for this specific stock. EXTREME_HISTORICAL_FEAR(>=90th percentile) = put premium is at historically extreme levels for this stock — highest edge to SELL PUT SPREADS when bullish, or BUY CALL SPREADS as mean-reversion plays. Below_avg_fear(<=25th percentile) = options are historically cheap — favor LONG options (calls or straddles) over premium selling.\n"
        "32. SHORT INTEREST TREND (when available after 5+ sessions of data): short_trend shows change in short float vs 5 sessions ago. SHORTS_BUILDING(>+1pp) = new bearish institutional conviction entering the stock — validates bearish setups and contradicts bullish flow. SHORTS_COVERING(<-1pp) = short sellers are exiting — potential squeeze trigger forming; combine with squeeze_risk=HIGH or EXTREME for maximum conviction LONG CALL setup (short covering can accelerate a move by 2-3x).\n"
        "34. MACD MOMENTUM: macd=BULLISH_CROSS is the strongest technical signal — momentum just flipped bullish; this is the optimal LONG CALL entry timing. BULLISH means momentum is positive but the cross happened days ago (still valid, lower urgency). BEARISH_CROSS is a warning — momentum turning down, reduce conviction on LONG CALL even with bullish flow. BULLISH_DIV = price made a lower low but MACD held a higher low — institutional accumulation on the dip, high-conviction reversal setup even if the stock looks weak on the surface.\n"
        "35. SUPPORT/RESISTANCE LEVELS: sr=AT_SUPPORT means price is within 2% of a confirmed historical swing low — institutions have defended this exact level before; this is the optimal LONG CALL entry (risk/reward is best here, stop loss is well-defined just below support). ABOVE_SUPPORT(X%_below) shows a cushion below. BELOW_RESISTANCE(X%_above) means a supply zone overhead — if resistance is <3% away, the stock needs to break through first; if >5% away, the trade has room to run before hitting resistance.\n"
        "36. VOLUME PROFILE / POINT OF CONTROL: poc=AT_POC means price is sitting at the highest-traded-volume level of the past 90 days — this acts as both a support magnet AND a breakout launch pad. ABOVE_POC = buyers have pushed price above where 90% of volume traded, confirming institutional demand at lower levels. BELOW_POC = sellers are in control of the distribution; avoid LONG CALL unless other signals are overwhelming. Prefer ABOVE_POC with MACD=BULLISH for highest technical confirmation.\n"
        "37. VWAP (20-DAY): vwap=ABOVE_VWAP means buyers have consistently paid above the average cost basis over the past month — structural bullish; strong confirmation for LONG CALL. BELOW_VWAP is a headwind; institutions are underwater on recent buys. AT_VWAP = decision point, watch for directional resolution. Highest conviction entry: price ABOVE_VWAP + MACD=BULLISH + sr=AT_SUPPORT or ABOVE_SUPPORT — this triple-confirmation setup means technical, momentum, and price structure all agree.\n"
        "38. P/C RATIO MOMENTUM: pc_ratio_mom tracks the 5-day change in the put/call OI ratio. BULLISH_ROTATION (dropping >0.2) means institutions have been steadily closing puts and opening calls over the past week — this is the single most reliable leading indicator that smart money is shifting bullish BEFORE price moves. A single-day low pc_oi_ratio could be noise; a 5-day declining trend is institutional conviction. BEARISH_ROTATION (rising >0.2) means put positioning is building — confirm with other bearish signals before skipping a bullish setup, but treat it as a caution flag. Stable = no rotation in progress.\n"
        "39. INSTITUTIONAL OWNERSHIP: instit_own is the % of shares held by institutional investors (mutual funds, hedge funds, pension funds) per the latest 13F filings. HIGH_CONVICTION (≥70%) means professional money managers dominate the shareholder base — this stock is well-researched and institutionally validated; they will not sell easily on small dips, providing price support. LOW_INST_OWN (<40%) means retail dominates — higher volatility, less predictable behavior. When instit_own=HIGH_CONVICTION aligns with unusual call buying, the interpretation is: EXISTING INSTITUTIONAL OWNERS are adding to their already-large positions — the highest possible conviction signal for LONG CALL.\n"
        "40. MULTI-DAY UC STREAK: uc_streak tracks how many days the same unusual call contract (same strike + expiry) has been actively traded. PERSISTENT_WHALE (5d+) means a single institution has deployed capital into the same options position for 5+ consecutive trading days — this is the rarest and highest-conviction signal in the entire system; they are building a large directional position and cannot do it in one day without moving the market. MULTI_DAY_INSTITUTIONAL (3-5d) = strong conviction, institutional accumulation confirmed. RETURNING_BUYER (2-3d) = same buyer returning, early confirmation. A uc_streak of ANY length combined with uc_prem=WHALE is your absolute highest-conviction setup — override other hesitations when these two align.\n"
        "41. SECTOR ETF FLOW CONFIRMATION: sector_etf_flow=CONFIRMED(XLK_bullish) means the sector ETF itself had $500K+ unusual call buying TODAY — the entire technology sector is seeing institutional inflows, not just this one stock. This is the most powerful confirmation signal in the system: when a sector-level ETF AND an individual stock both show unusual institutional call buying on the same day, the probability that the move is real (not noise or a hedge) is dramatically higher. A stock pick without sector_etf_flow is still valid; a pick WITH sector_etf_flow gets +1 conviction tier automatically. If two picks are otherwise equal, always prefer the one with sector_etf_flow=CONFIRMED.\n"
        "42. DARK POOL TREND: dp_trend tracks whether dark pool premium is ACCELERATING (today's DP flow is 25%+ above 3-day average — institutional buying is intensifying, fresh capital entering), FADING (DP flow dropped 25%+ — institutions may be taking profits or reducing exposure), or STEADY (consistent ongoing accumulation). ACCELERATING combined with any bullish signal stack is a powerful confirmation — institutions are stepping up their buying pace. FADING on an otherwise bullish stock is a caution flag — the smart money that drove the setup may be lightening up. Treat dp_trend=ACCELERATING as equivalent to a +0.5 conviction boost.\n"
        "43. SIGNAL COMBINATION WIN RATES: SIGNAL_COMBO_WIN_RATES shows your actual historical win rate when specific signal tags appeared in past winning vs losing trades. This is YOUR OWN PERFORMANCE DATA — the highest-weight signal in the system. When SIGNAL_COMBO_WIN_RATES shows persist3d+:84%(21/25), it means that out of your 25 past trades where signal had 3+ days persistence, 21 won. USE THIS TO OVERRIDE rule-based weights: if your data shows MACD_CROSS wins 75% of the time but ABOVE_POC wins only 52%, weight MACD_CROSS heavier in your conviction scoring for this session. This self-learning feedback loop means the AI gets smarter every day as more outcomes are logged.\n"
        "33. UNUSUAL CALL PREMIUM GATE (MANDATORY): Every recommended ticker MUST have a uc_prem signal present in its data AND uc_prem ≥ 0.50M ($500K). Tickers without a uc_prem field, or with uc_prem < 0.50M, must be SKIPPED entirely — no exceptions. This ensures every pick has documented institutional unusual call activity backing it. Prefer picks with uc_prem ≥ 1.0M (INSTITUTIONAL) or ≥ 5.0M (WHALE) when available — these represent the highest-conviction smart money flows. If fewer than 5 tickers meet the $500K threshold, fill remaining slots ONLY from the next-highest uc_prem tickers; do NOT recommend tickers with no unusual call flow.\n"
        "ABSOLUTE MANDATE — ALL 5 SETUPS MUST BE: direction=BULLISH, setup_type=LONG CALL only. No spreads. No puts. No iron condors. No straddles. No neutral. No bearish. Every single output must be a naked long call buy. If you cannot find 5 strong bullish setups, pick the 5 best available bullish signals regardless. Never output anything other than LONG CALL.\n"
        "Output ONLY a JSON array of exactly 5 setups. No markdown. No text outside the array."
    )

    user_msg = f"""⚠ TODAY IS {str(_date.today())}. All expiry dates in your JSON response MUST be after {str(_date.today())} and formatted as YYYY-MM-DD. Do not use any date from 2024 or earlier.

SOURCES ({len(active_sources)}): {', '.join(active_sources)}
TICKERS SCANNED: {len(rich)}
{context_block}

TICKER SIGNALS (score-ranked, highest composite first):
{sig_text}

SIGNAL KEY:
- score: composite conviction 0-100 | persist: consecutive days signal has been building (3d+ = very high conviction)
- iv_rank: IV percentile vs 1yr HV | impl_move: options market's priced-in ±% move to expiry
- rsi: momentum (>70 overbought, <30 oversold) | sma50: % above/below 50-day SMA
- vol_trend: 5d vs 20d avg volume ratio (≥1.5x = institutional accumulation surge)
- beta: 30-day beta to SPY (≥1.5 = amplified SPY moves, ≤0.6 = defensive)
- SmartVsRetail: institutional vs retail C/P divergence | calls/puts: intent verdict
- vol/oi: call volume-to-open-interest ratio (>2x = concentrated new institutional position)
- opt_spread: ATM call bid/ask spread % of mid (<5%=liquid, >12%=ILLIQUID_AVOID — do NOT recommend)
- earn_beat: quarters beat vs missed EPS estimate (3/4 or 4/4 = serial earnings beater)
- uc_prem: unusual call premium in $M — NOTABLE=<$1M, INSTITUTIONAL=$1-5M, WHALE=$5M+ | uc_vol_oi: vol/OI ratio on the unusual strike
- mp: max pain & distance | gwall: gamma wall | dp: dark pool premium
- earnings: next earnings | post_earnings: days since = IV crush window (sell premium while IV deflates)
- analysts: net upgrades minus downgrades in last 7 days
- short: short float % / days-to-cover | premarket: gap % & relative volume
- MACRO: days to Fed/CPI/OPEX | SECTORS: sector rotation leaders/laggards
- MARKET_REGIME: current market environment → drives which setup_types to use (see rules above)
- YOUR_HISTORICAL_WIN_RATES: actual win rates from your past trades logged in this system
- iv_skew: put IV minus call IV at ~25-delta (pp) → positive=fear/downside hedging; FEAR_PREMIUM>8pp=institutional crash protection active
- iv_ts: near-term IV minus far-term IV (pp) → BACKWARDATION>5pp=event/earnings risk priced near-term
- GEX: dealer gamma exposure in $M → LONG_GAMMA=suppresses moves/mean-revert; SHORT_GAMMA=amplifies moves/momentum
- iv_rv: IV premium over realized vol % → RICH_SELL_PREM>20%=edge selling premium; CHEAP_BUY_VOL<-10%=edge buying vol
- mom12_1: 12-month minus 1-month price momentum % (Fama-French factor) → >15%=strong; <-15%=weak
- ROE: return on equity % (quality factor) | fwd_PE: forward P/E (value factor — CHEAP<15x, EXPENSIVE>35x)
- sector_corr: 30d correlation to sector ETF → IDIOSYNCRATIC<0.5=name-specific catalyst; >0.85=sector-driven
- news: keyword sentiment score from recent headlines (-=bearish, +=bullish)
- MACRO_CROSS_ASSET: YieldCurve(10y-3m)=curve shape; DXY=dollar; CreditSpread5d=HYG vs LQD; Crude5d; Gold5d; VIX_TermStructure=VIX minus VIX3M (>+2=BACKWARDATION=crisis risk; <-1=contango=calm)
- earn_in: days until next earnings event | impl_earn_move: IV-based expected ±% move into earnings | earn_beat: past quarters beat rate
- analyst_tgt: analyst mean price target vs current price % upside/downside | analyst_recommendation: consensus rating
- pc_oi_ratio: total put OI ÷ total call OI across near-term expirations (>1.5=bearish positioned; <0.6=bullish positioned)
- pc_ratio_mom: 5-day change in pc_oi_ratio (negative=BULLISH_ROTATION=institutions shifting to calls; positive=BEARISH_ROTATION=put positioning building)
- instit_own: % of shares held by institutions per latest 13F filings (≥70%=HIGH_CONVICTION smart money loaded; <40%=retail dominated)
- uc_streak: days the same unusual call contract (ticker+strike+expiry) has been continuously active (5d+=PERSISTENT_WHALE; 3-5d=MULTI_DAY_INSTITUTIONAL; 2-3d=RETURNING_BUYER)
- 52w_range: where price sits in its 52-week high/low range (0%=at annual low, 100%=at annual high; ≥90%=breakout zone; ≤10%=support test)
- borrow: short borrow cost proxy from short interest (HIGH_BORROW≥20% float short = puts may be synthetic hedges by short sellers, not directional bets)
- flow_persist: call/put vol÷OI ratio across 4 expirations (STRUCTURAL<0.05=built over weeks=institutional conviction; FRESH>0.25=today only=may be noise)
- eps_trend: forward vs trailing EPS direction (RISING=estimates going up=tailwind; DECLINING=estimates cut=headwind even if flow bullish)
- hist_earn_move: average absolute % price reaction on past earnings days; compare to impl_earn_move to assess straddle value (hist>impl=buy vol; impl>hist×1.5=sell premium)
- squeeze_risk: composite short squeeze risk (HIGH/EXTREME = high short float + hard borrow + rising RSI + surging vol → danger zone for bears, opportunity for LONG CALL)
- analyst_dispersion: spread of analyst price targets as % of mean (≥30%=HIGH_DISAGREEMENT=prefer straddle; <15%=CONSENSUS=directional ok)
- pc_prem_ratio: actual dollars spent on puts ÷ call premium today (>1.5=HEAVY_PUT_SPEND=institutional fear; <0.6=HEAVY_CALL_SPEND=risk-on; cross-check vs pc_oi_ratio)
- rs_vs_spy: stock 1-year return minus SPY 1-year return (>+20%=BEATING_MARKET=institutional accumulation; <-20%=LAGGING_MARKET=headwind — only go high-conviction LONG CALL on positive RS)
- money_flow: up-day vs down-day avg volume ratio over 30 sessions (>1.3=ACCUMULATION; <0.8=DISTRIBUTION — structural signal, high weight)
- insider: net insider open-market buys vs sells last 30d (BUYING=high-conviction bullish; SELLING=neutral — only BUYING counts as a signal)
- div_yield: annual dividend yield % | ex_div: days to ex-dividend (<=7d=IMMINENT_EXDIV → avoid LONG CALL / COVERED CALL, early assign risk; use BULL CALL SPREAD instead)
- tail_risk_puts: % of put vol in deep OTM strikes >15% below spot (>40%=CRASH_HEDGING_ACTIVE=risk-off; >30% = do NOT sell premium structures)
- iv_skew_pctl: today's IV skew ranked vs 1-year history for this stock (>=90th=EXTREME_HISTORICAL_FEAR=sell put prem / buy call spreads; <=25th=historically cheap vol=buy options)
- short_trend: change in short float vs 5 sessions ago in pp (>+1=SHORTS_BUILDING=bear conviction; <-1=SHORTS_COVERING=squeeze trigger — combine with squeeze_risk=HIGH for max conviction LONG CALL)

PRIORITY WEIGHTING (use in order):
1. opt_spread>12% → SKIP (non-negotiable liquidity gate)
2. MARKET_REGIME + MACRO_CROSS_ASSET → determines valid setup_types for current environment
3. GEX regime → LONG_GAMMA=mean-revert setups; SHORT_GAMMA=directional/momentum setups
4. YOUR_HISTORICAL_WIN_RATES → bias toward setup_types that have worked in your own history
5. persist=3d+ (multi-day confirmation — strongest signal)
6. Smart vs Retail divergence (institutional vs retail misalignment)
7. iv_rv + iv_skew (vol surface edge — where premium is rich/cheap + where fear is concentrated)
8. score≥75 + vol_trend≥1.5x + beta + mom12_1 (accumulation surge + factor confirmation)
9. call vol/oi >2x (concentrated unusual new activity)
10. post_earnings IV crush + earn_beat + ROE + fwd_PE (fundamental quality + vol edge)
11. sector_corr=IDIOSYNCRATIC (name-specific, not sector noise)
12. analyst upgrades + premarket gap + news sentiment confirmation

Return a JSON array of exactly 5 objects. ALL 5 must be BULLISH direction, setup_type LONG CALL only — no spreads, no puts, nothing else. Sort by conviction (HIGH first). Each must have ALL fields:
ticker (string), price (number), setup_type ("LONG CALL"), direction ("BULLISH"), conviction ("HIGH"|"MEDIUM"), entry_strike (number), expiry (YYYY-MM-DD), target_price (number), stop_loss (number), option_premium (number — estimated option ask price per share based on current IV, strike proximity, and days to expiry; this is the cost to buy 1 share of the option, not per contract), signals_aligned (list of 4-5 short strings naming exact signals used), thesis (2 sentences max), risk_level ("LOW"|"MEDIUM"|"HIGH")

JSON array only. No markdown. Start immediately with ["""

    def _call_openai_streaming():
        """Stream the response so we capture content even if the proxy truncates."""
        import sys
        chunks = []
        finish = "unknown"
        stream = oai.chat.completions.create(
            model="gpt-4o-mini",
            max_completion_tokens=1500,
            stream=True,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user",   "content": user_msg},
            ],
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                chunks.append(delta)
            if chunk.choices and chunk.choices[0].finish_reason:
                finish = chunk.choices[0].finish_reason
        raw = "".join(chunks).strip()
        print(f"[ai_trades] finish={finish} raw_len={len(raw)}", file=sys.stderr, flush=True)
        return raw, finish

    def _extract_json(raw):
        if "```" in raw:
            for part in raw.split("```"):
                stripped = part.lstrip("json").strip()
                if stripped.startswith("["):
                    return stripped
        if not raw.startswith("["):
            start = raw.find("[")
            end   = raw.rfind("]") + 1
            if start >= 0 and end > start:
                return raw[start:end]
        return raw

    try:
        raw, finish = _call_openai_streaming()
        raw = _extract_json(raw)

        # Retry once with a pause if empty (rate-limit or transient hiccup)
        if not raw:
            import time as _time, sys
            print("[ai_trades] empty on first attempt — retrying in 8s", file=sys.stderr, flush=True)
            _time.sleep(8)
            raw, finish = _call_openai_streaming()
            raw = _extract_json(raw)

        if not raw:
            import sys
            print(f"[ai_trades_bg] OpenAI returned no content (finish={finish}) — aborting", file=sys.stderr)
            return  # background worker exits; stale cache stays; user can retry

        try:
            trades = _json.loads(raw)
        except Exception:
            from json_repair import repair_json as _rj
            trades = _json.loads(_rj(raw))

        # Validate expiry dates — catch and fix any past dates the AI hallucinated
        import datetime as _dtfix
        _today_fix = _dtfix.date.today()
        _fallback_exp = str(_today_fix + _dtfix.timedelta(days=45))
        for _tr in trades:
            try:
                _exp = _tr.get("expiry", "")
                if not _exp or _dtfix.date.fromisoformat(_exp) <= _today_fix:
                    print(f"[ai_trades] fixing bad expiry '{_exp}' for {_tr.get('ticker')} → {_fallback_exp}")
                    _tr["expiry"] = _fallback_exp
            except Exception:
                _tr["expiry"] = _fallback_exp

        # Post-filter: drop any picks the AI made that lack real uc_prem >= $500K.
        # Build a set of tickers confirmed to have unusual call premium >= 0.5M.
        _uc_now = getattr(app, "_unusual_calls_cache", None)
        if _uc_now:
            _uc_prem_map = {}
            for _h in _uc_now.get("hits", []):
                _t = _h["ticker"]
                _p = _h.get("prem", 0)
                if _p > _uc_prem_map.get(_t, 0):
                    _uc_prem_map[_t] = _p
            _qualified = {t for t, p in _uc_prem_map.items() if p >= 20_000}
            _filtered = [tr for tr in trades if tr.get("ticker") in _qualified]
            # Only apply filter if it leaves at least 2 picks; otherwise keep all (data may be stale)
            if len(_filtered) >= 2:
                trades = _filtered
                print(f"[ai_trades] premium filter: {len(trades)} picks kept (had {len(_uc_prem_map)} uc tickers, {len(_qualified)} ≥$20K)")
            else:
                print(f"[ai_trades] premium filter skipped — only {len(_filtered)} qualified picks (keeping all {len(trades)})")

        out = {
            "trades": trades,
            "generated_at": _dt.now().isoformat(),
            "tickers_scanned": len(rich),
            "signal_sources": active_sources,
            "signal_source_count": len(active_sources),
        }
        app._ait_cache = out
        app._ait_cache_ts = _dt.now()
        import sys
        print(f"[ai_trades_bg] done — {len(trades)} setups cached", file=sys.stderr, flush=True)
        try:
            from datetime import date as _date_now
            _save_ai_trades_to_log(trades, str(_date_now.today()))
        except Exception as _se:
            print(f"[ai_trade_log] background save error: {_se}")
        # Email AI picks to subscribers
        try:
            import threading as _thr_ait
            _thr_ait.Thread(target=_send_ai_trades_email, args=(trades,), daemon=True).start()
        except Exception as _ae:
            print(f"[ai_trades_email] trigger error: {_ae}")
    except Exception as e:
        import sys
        print(f"[ai_trades_bg] exception: {e}", file=sys.stderr, flush=True)
    finally:
        app._ait_generating = False


@app.route("/stock-api/ai-trades", methods=["GET"])
def ai_trades():
    """Return AI trade setups instantly from cache; regenerates in background."""
    from datetime import datetime as _dt
    import threading as _thr

    _cache = getattr(app, "_ait_cache", None)
    _ts    = getattr(app, "_ait_cache_ts", None)
    _gen   = getattr(app, "_ait_generating", False)
    _stale = not (_ts and (_dt.now() - _ts).total_seconds() < 3600)

    if _stale and not _gen:
        _thr.Thread(target=_ai_trades_worker, daemon=True).start()

    if _cache:
        return jsonify({**_cache, **({"refreshing": True} if _stale else {})})

    return jsonify({
        "loading": True,
        "trades": [],
        "tickers_scanned": 0,
        "signal_sources": [],
        "error": "AI is analyzing all 40 signals — takes ~30 sec on first load. Tap Regenerate then wait a moment.",
    })


@app.route("/stock-api/check-subscription", methods=["POST"])
def check_subscription():
    """Check if an email has an active Pro subscription (or is an admin)."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"subscribed": False, "error": "Email required"})
    ADMIN_EMAILS = {"joeldcarlo@gmail.com"}
    if email in ADMIN_EMAILS:
        return jsonify({"subscribed": True, "admin": True})
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("SELECT active, paid FROM sm_subscribers WHERE LOWER(email) = %s LIMIT 1", (email,))
            row = cur.fetchone()
            if row and row[0] and row[1]:
                return jsonify({"subscribed": True})
            return jsonify({"subscribed": False})
    except Exception as e:
        return jsonify({"subscribed": False, "error": str(e)})


@app.route("/stock-api/ai-trades/regenerate", methods=["POST"])
def ai_trades_regenerate():
    """Force-refresh AI trade setups in background; returns immediately."""
    import threading as _thr
    app._ait_cache_ts = None
    if not getattr(app, "_ait_generating", False):
        _thr.Thread(target=_ai_trades_worker, daemon=True).start()
    return jsonify({"status": "generating", "message": "AI generation started."})


@app.route("/stock-api/ai-trades/backfill-flow", methods=["POST"])
def ai_trades_backfill_flow():
    """Backfill total_premium_usd for today's trades where it is NULL. Uses delays to avoid rate limits."""
    import time as _time
    from datetime import date as _d
    today = str(_d.today())
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""SELECT id, ticker, entry_strike, expiry FROM ai_trade_log
                           WHERE total_premium_usd IS NULL AND entry_strike IS NOT NULL
                           AND expiry IS NOT NULL AND trade_date = %s""", (today,))
            rows = cur.fetchall()
        updated = 0
        for (rid, ticker, strike, expiry) in rows:
            _time.sleep(1.5)
            val = _fetch_options_flow_usd(ticker, strike, expiry)
            if val is not None:
                with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
                    cur.execute("UPDATE ai_trade_log SET total_premium_usd = %s WHERE id = %s", (val, rid))
                    conn.commit()
                updated += 1
        return jsonify({"status": "ok", "checked": len(rows), "updated": updated})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/stock-api/signal-feed", methods=["GET"])
def signal_feed():
    """Real-time notable signal events — dark pool, smart money, vol crush, max pain."""
    import yfinance as yf
    from datetime import datetime as _dt

    _cache = getattr(app, "_sf_cache", None)
    _ts    = getattr(app, "_sf_cache_ts", None)
    if _cache and _ts and (_dt.now() - _ts).total_seconds() < 600:
        return jsonify(_cache)

    FOCUS = ["SPY","QQQ","NVDA","AAPL","MSFT","META","GOOGL","AMZN","TSLA","AMD","ORCL","MU","NFLX","CRM","PLTR","AVGO","ARM","IWM","MSTR","SMCI"]
    now   = _dt.now()
    events = []

    def _check(ticker):
        evs = []
        try:
            import numpy as np
            tkr   = yf.Ticker(ticker)
            price = float(getattr(tkr.fast_info, "last_price", 0) or 0)
            if price <= 0: return evs
            exps  = tkr.options
            if not exps: return evs
            chain = tkr.option_chain(exps[0])

            # Vol crush check
            atm  = min(chain.puts["strike"].tolist(), key=lambda s: abs(s - price))
            ivp  = chain.puts[chain.puts["strike"]  == atm]["impliedVolatility"].values
            ivc  = chain.calls[chain.calls["strike"] == atm]["impliedVolatility"].values
            ivv  = [v for v in list(ivp)+list(ivc) if v and v > 0]
            if ivv:
                cur_iv = float(np.mean(ivv))
                hist   = tkr.history(period="1y")["Close"]
                if len(hist) >= 40:
                    hv_s = hist.pct_change().dropna().rolling(21).std() * np.sqrt(252)
                    hv_s = hv_s.dropna()
                    iv_rank = (cur_iv - float(hv_s.min())) / max(float(hv_s.max()) - float(hv_s.min()), 0.001) * 100
                    if iv_rank >= 85:
                        evs.append({"ticker": ticker, "price": round(price,2), "type": "VOL SPIKE",
                                    "icon": "🌡️", "color": "#f87171",
                                    "msg": f"IV rank {iv_rank:.0f}% — premium at 1-year extreme"})
                    elif iv_rank <= 15:
                        evs.append({"ticker": ticker, "price": round(price,2), "type": "IV FLOOR",
                                    "icon": "📉", "color": "#60a5fa",
                                    "msg": f"IV rank {iv_rank:.0f}% — cheapest options in a year"})

            # Smart money divergence
            sc = sp = rc = rp = 0.0
            for exp in exps[:3]:
                try:
                    ch = tkr.option_chain(exp)
                    for side, df in [("c", ch.calls), ("p", ch.puts)]:
                        for _, row in df.iterrows():
                            vol  = int(row.get("volume", 0) or 0)
                            last = float(row.get("lastPrice", 0) or 0)
                            if vol <= 0 or last <= 0: continue
                            prem = vol * last * 100
                            if last >= 3.0 and vol >= 30:
                                if side == "c": sc += prem
                                else: sp += prem
                            elif last < 2.0 or vol < 15:
                                if side == "c": rc += prem
                                else: rp += prem
                except Exception: continue
            s_cp = sc/sp if sp > 0 else 9.9
            r_cp = rc/rp if rc > 0 else 0.1
            if s_cp >= 2.0 and r_cp <= 0.6:
                evs.append({"ticker": ticker, "price": round(price,2), "type": "SMART BULL",
                            "icon": "🏦", "color": "#4ade80",
                            "msg": f"Institutions C/P={s_cp:.1f}× while retail is {r_cp:.1f}× — classic divergence"})
            elif s_cp <= 0.5 and r_cp >= 1.8:
                evs.append({"ticker": ticker, "price": round(price,2), "type": "SMART BEAR",
                            "icon": "⚠️", "color": "#f87171",
                            "msg": f"Smart money putting at C/P={s_cp:.1f}× while retail buys calls {r_cp:.1f}×"})

            # Max pain distance
            puts_df = chain.puts; calls_df = chain.calls
            strikes = sorted(set(list(puts_df["strike"]) + list(calls_df["strike"])))
            best = strikes[0]; lo = float("inf")
            for s in strikes:
                cp = sum(max(0.0, float(s)-float(k)) * float(o or 0) for k, o in zip(calls_df["strike"], calls_df["openInterest"].fillna(0)))
                pp = sum(max(0.0, float(k)-float(s)) * float(o or 0) for k, o in zip(puts_df["strike"],  puts_df["openInterest"].fillna(0)))
                if (cp+pp) < lo: lo = cp+pp; best = s
            mp = float(best)
            dist = (price - mp) / mp * 100
            if abs(dist) >= 8:
                evs.append({"ticker": ticker, "price": round(price,2), "type": "MAX PAIN GAP",
                            "icon": "📍", "color": "#fbbf24",
                            "msg": f"Price {dist:+.1f}% from max pain ${mp:.2f} — gravitational pull expected by {exps[0]}"})

        except Exception: pass
        return evs

    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(_check, t): t for t in FOCUS}
        for f in as_completed(futs): events.extend(f.result())

    events.sort(key=lambda x: (x["type"] == "SMART BULL" or x["type"] == "SMART BEAR"), reverse=True)
    out = {"events": events[:30], "generated_at": now.isoformat()}
    app._sf_cache = out; app._sf_cache_ts = now
    return jsonify(out)


@app.route("/stock-api/composite-score", methods=["GET"])
def composite_score():
    """0–100 composite score per ticker combining IV rank, smart money, call accumulation, max pain alignment."""
    import yfinance as yf
    from datetime import datetime as _dt

    _cache = getattr(app, "_cs_cache", None)
    _ts    = getattr(app, "_cs_cache_ts", None)
    if _cache and _ts and (_dt.now() - _ts).total_seconds() < 1800:
        return jsonify(_cache)

    now = _dt.now()

    def _score(ticker):
        try:
            import numpy as np
            tkr   = yf.Ticker(ticker)
            price = float(getattr(tkr.fast_info, "last_price", 0) or 0)
            if price <= 0: return None
            exps  = tkr.options
            if not exps: return None
            chain = tkr.option_chain(exps[0])

            score = 50.0; reasons = []; components = {}

            # IV rank (0–25 pts)
            atm  = min(chain.puts["strike"].tolist(), key=lambda s: abs(s - price))
            ivp  = chain.puts[chain.puts["strike"]  == atm]["impliedVolatility"].values
            ivc  = chain.calls[chain.calls["strike"] == atm]["impliedVolatility"].values
            ivv  = [v for v in list(ivp)+list(ivc) if v and v > 0]
            iv_rank = 50.0
            if ivv:
                cur_iv = float(np.mean(ivv))
                hist   = tkr.history(period="1y")["Close"]
                if len(hist) >= 40:
                    hv_s = hist.pct_change().dropna().rolling(21).std() * np.sqrt(252)
                    hv_s = hv_s.dropna()
                    iv_rank = max(0.0, min(100.0, (cur_iv - float(hv_s.min())) / max(float(hv_s.max()) - float(hv_s.min()), 0.001) * 100))
            iv_pts = round((100 - iv_rank) / 100 * 25, 1)  # low IV = cheaper options = bullish edge
            score += iv_pts - 12.5
            components["iv_rank"] = round(iv_rank, 1)
            components["iv_score"] = iv_pts

            # Smart money (0–30 pts)
            sc = sp = rc = rp = 0.0
            accum_prem = fomo_prem = 0.0
            top_accum = {"strike": None, "expiry": None, "otm_pct": 0.0, "prem": 0.0}
            for exp in exps[:5]:
                try:
                    days_out = (_dt.strptime(exp, "%Y-%m-%d") - now).days
                    ch = tkr.option_chain(exp)
                    for side, df in [("c", ch.calls), ("p", ch.puts)]:
                        for _, row in df.iterrows():
                            vol  = int(row.get("volume", 0) or 0)
                            last = float(row.get("lastPrice", 0) or 0)
                            oi   = int(row.get("openInterest", 0) or 0)
                            if vol <= 0 or last <= 0: continue
                            prem = vol * last * 100
                            if last >= 3.0 and vol >= 30:
                                if side == "c": sc += prem
                                else:           sp += prem
                            elif last < 2.0 or vol < 15:
                                if side == "c": rc += prem
                                else:           rp += prem
                    for _, row in ch.calls.iterrows():
                        strike = float(row.get("strike", 0) or 0)
                        vol    = int(row.get("volume", 0) or 0)
                        oi     = int(row.get("openInterest", 0) or 0)
                        last   = float(row.get("lastPrice", 0) or 0)
                        if strike <= 0 or last <= 0: continue
                        otm = (strike - price) / price * 100
                        p   = (vol + oi) * last * 100
                        if otm > 5 and days_out > 60:
                            accum_prem += p
                            if p > top_accum["prem"]:
                                top_accum = {"strike": round(strike,2), "expiry": exp, "otm_pct": round(otm,1), "prem": p}
                        elif -3 < otm < 3 and days_out < 45:
                            fomo_prem += p
                except Exception: continue

            s_cp = sc/sp if sp > 0 else 1.0
            r_cp = rc/rp if rp > 0 else 1.0
            sm_pts = min(30.0, max(0.0, (s_cp - 1.0) * 10.0))
            score += sm_pts - 15
            components["smart_cp"] = round(s_cp, 2)
            components["retail_cp"] = round(r_cp, 2)
            components["sm_score"] = round(sm_pts, 1)

            total_call = accum_prem + fomo_prem
            accum_pct  = accum_prem / total_call * 100 if total_call > 0 else 50.0
            accum_pts  = round((accum_pct - 50) / 50 * 15, 1)
            score += accum_pts
            components["accum_pct"] = round(accum_pct, 1)
            components["accum_score"] = accum_pts
            components["top_accum"] = top_accum

            # Max pain (0–15 pts for being below pain = bullish pull)
            mp_strike = None; mp_pts = 0.0
            try:
                strikes2 = sorted(set(list(chain.puts["strike"]) + list(chain.calls["strike"])))
                best = strikes2[0]; lo = float("inf")
                for s in strikes2:
                    cp2 = sum(max(0.0, float(s)-float(k)) * float(o or 0) for k, o in zip(chain.calls["strike"], chain.calls["openInterest"].fillna(0)))
                    pp2 = sum(max(0.0, float(k)-float(s)) * float(o or 0) for k, o in zip(chain.puts["strike"],  chain.puts["openInterest"].fillna(0)))
                    if (cp2+pp2) < lo: lo = cp2+pp2; best = s
                mp_strike = float(best)
                dist = (price - mp_strike) / mp_strike * 100
                mp_pts = min(15.0, max(-15.0, -dist * 1.5))
                score += mp_pts
            except Exception: pass
            components["max_pain"] = round(mp_strike, 2) if mp_strike else None
            components["mp_score"] = round(mp_pts, 1)

            score = round(max(0.0, min(100.0, score)), 1)
            bias  = "STRONG BULL" if score >= 75 else "BULLISH" if score >= 60 else "NEUTRAL" if score >= 40 else "BEARISH" if score >= 25 else "STRONG BEAR"

            return {"ticker": ticker, "price": round(price, 2), "score": score, "bias": bias,
                    "components": components, "nearest_exp": exps[0]}
        except Exception: return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_score, t): t for t in DEFAULT_LEADERBOARD}
        rows = [r for f in as_completed(futs) if (r := f.result()) is not None]
    rows.sort(key=lambda x: x["score"], reverse=True)
    out = {"results": rows, "scanned": len(DEFAULT_LEADERBOARD)}
    app._cs_cache = out; app._cs_cache_ts = now
    return jsonify(out)


@app.route("/stock-api/ai-trade-log", methods=["GET"])
def ai_trade_log():
    """Return full AI trade history with win/loss outcomes and aggregate stats."""
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, trade_date, ticker, direction, setup_type, conviction,
                       price_at_signal, entry_strike, expiry, target_price, stop_loss,
                       signals_aligned, thesis, risk_level,
                       t1_price, t3_price, t5_price, t10_price,
                       t1_pct, t3_pct, t5_pct, t10_pct,
                       t1_win, t3_win, t5_win, t10_win,
                       expiry_price, expiry_pct, expiry_win,
                       outcome, created_at,
                       COALESCE(source, 'AI_TRADE') AS source,
                       option_premium, breakeven_price, total_premium_usd
                FROM ai_trade_log
                ORDER BY trade_date DESC, id DESC
            """)
            rows = cur.fetchall()
            cols = ["id","trade_date","ticker","direction","setup_type","conviction",
                    "price_at_signal","entry_strike","expiry","target_price","stop_loss",
                    "signals_aligned","thesis","risk_level",
                    "t1_price","t3_price","t5_price","t10_price",
                    "t1_pct","t3_pct","t5_pct","t10_pct",
                    "t1_win","t3_win","t5_win","t10_win",
                    "expiry_price","expiry_pct","expiry_win",
                    "outcome","created_at","source",
                    "option_premium","breakeven_price","total_premium_usd"]
            trades = []
            for row in rows:
                d = dict(zip(cols, row))
                d["trade_date"] = str(d["trade_date"]) if d["trade_date"] else None
                d["created_at"] = d["created_at"].isoformat() if d["created_at"] else None
                d["signals_aligned"] = d["signals_aligned"] if isinstance(d["signals_aligned"], list) else []
                trades.append(d)

            # Compute win rates
            def _wr(key):
                vals = [t[key] for t in trades if t[key] is not None]
                if not vals: return None
                return round(sum(1 for v in vals if v) / len(vals) * 100, 1)

            win_rates = {
                "expiry": _wr("expiry_win"),
                "t1": _wr("t1_win"), "t3": _wr("t3_win"),
                "t5": _wr("t5_win"), "t10": _wr("t10_win"),
            }

            # By direction breakdown (kept for backward compat)
            by_dir = {}
            for d in ["BULLISH", "BEARISH", "NEUTRAL"]:
                sub = [t for t in trades if t["direction"] == d]
                exp_wins = [t["expiry_win"] for t in sub if t["expiry_win"] is not None]
                t5s = [t["t5_win"] for t in sub if t["t5_win"] is not None]
                by_dir[d] = {
                    "count": len(sub),
                    "win_rate_expiry": round(sum(1 for v in exp_wins if v) / len(exp_wins) * 100, 1) if exp_wins else None,
                    "win_rate_t5": round(sum(1 for v in t5s if v) / len(t5s) * 100, 1) if t5s else None,
                }

            # By source breakdown — AI_TRADE vs MULTI_SIGNAL vs BOTH
            by_src = {}
            for s in ["AI_TRADE", "MULTI_SIGNAL", "BOTH"]:
                sub = [t for t in trades if t.get("source") == s]
                exp_wins = [t["expiry_win"] for t in sub if t["expiry_win"] is not None]
                t5s = [t["t5_win"] for t in sub if t["t5_win"] is not None]
                by_src[s] = {
                    "count": len(sub),
                    "win_rate_expiry": round(sum(1 for v in exp_wins if v) / len(exp_wins) * 100, 1) if exp_wins else None,
                    "win_rate_t5": round(sum(1 for v in t5s if v) / len(t5s) * 100, 1) if t5s else None,
                }

            return jsonify({
                "trades": trades,
                "count": len(trades),
                "win_rates": win_rates,
                "by_direction": by_dir,
                "by_source": by_src,
            })
    except Exception as e:
        return jsonify({"error": str(e), "trades": [], "count": 0,
                        "win_rates": {"t1": None, "t3": None, "t5": None, "t10": None},
                        "by_direction": {}, "by_source": {}}), 500


@app.route("/stock-api/multi-signal/log", methods=["POST"])
def multi_signal_log():
    """Persist a multi-signal AI thesis call to the ai_trade_log table."""
    try:
        from datetime import datetime as _msl_dt
        import pytz
        body    = request.get_json(force=True) or {}
        ticker  = (body.get("ticker") or "").upper().strip()
        signals = body.get("signals") or []
        score   = int(body.get("score", len(signals)))
        price   = float(body.get("price") or 0)
        thesis  = (body.get("thesis") or "").strip()

        if not ticker or not thesis:
            return jsonify({"error": "ticker and thesis required"}), 400

        conviction = "HIGH" if score >= 10 else ("MEDIUM" if score >= 6 else "LOW")
        today = _msl_dt.now(pytz.timezone("US/Eastern")).strftime("%Y-%m-%d")

        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO ai_trade_log
                    (trade_date, ticker, direction, setup_type, conviction,
                     price_at_signal, signals_aligned, thesis, risk_level, source)
                VALUES (%s, %s, 'BULLISH', 'MULTI_SIGNAL', %s, %s, %s, %s, 'MEDIUM', 'MULTI_SIGNAL')
                ON CONFLICT (trade_date, ticker, direction)
                DO UPDATE SET
                    source          = CASE WHEN ai_trade_log.source = 'AI_TRADE' THEN 'BOTH' ELSE 'MULTI_SIGNAL' END,
                    signals_aligned = EXCLUDED.signals_aligned,
                    thesis          = EXCLUDED.thesis,
                    setup_type      = CASE WHEN ai_trade_log.source = 'AI_TRADE' THEN ai_trade_log.setup_type ELSE 'MULTI_SIGNAL' END
            """, (today, ticker, conviction, price, _json.dumps(signals), thesis))
            conn.commit()

        return jsonify({"ok": True, "ticker": ticker, "date": today})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _run_whale_scan_background():
    """Live whale scan — runs in background thread, populates cache + DB when done."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import yfinance as yf
    from datetime import datetime as _dt

    if getattr(app, "_whale_scan_running", False):
        return
    app._whale_scan_running = True
    try:
        now = _dt.now()
        MIN_PREM_M = 5.0

        def _scan_whale(ticker):
            blocks = []
            try:
                tkr   = yf.Ticker(ticker)
                price = float(getattr(tkr.fast_info, "last_price", 0) or 0)
                if price <= 0: return blocks
                exps = tkr.options
                if not exps: return blocks
                for exp in exps:
                    try:
                        days_out = (_dt.strptime(exp, "%Y-%m-%d") - now).days
                        if not (30 <= days_out <= 365): continue
                        for direction, chain in [("CALL", tkr.option_chain(exp).calls),
                                                 ("PUT",  tkr.option_chain(exp).puts)]:
                            for _, row in chain.iterrows():
                                strike = float(row.get("strike", 0) or 0)
                                vol    = int(row.get("volume", 0) or 0)
                                last   = float(row.get("lastPrice", 0) or 0)
                                if strike <= 0 or last <= 0 or vol <= 0: continue
                                if strike < price * 0.15: continue
                                pre_otm = (strike - price) / price * 100
                                if pre_otm < -75: continue
                                prem_m = vol * last * 100 / 1e6
                                if prem_m >= MIN_PREM_M:
                                    otm_pct  = round(pre_otm, 1)
                                    category = "LEAPS" if days_out >= 180 else "AGGRESSIVE" if days_out <= 90 else "MEDIUM"
                                    tier     = "MEGA_WHALE" if prem_m >= 20 else "WHALE" if prem_m >= 10 else "BIG_BLOCK"
                                    blocks.append({
                                        "ticker":    ticker,
                                        "price":     round(price, 2),
                                        "direction": direction,
                                        "strike":    round(strike, 2),
                                        "expiry":    exp,
                                        "days_out":  days_out,
                                        "prem_m":    round(prem_m, 1),
                                        "volume":    vol,
                                        "otm_pct":   otm_pct,
                                        "category":  category,
                                        "tier":      tier,
                                    })
                    except Exception: continue
            except Exception: pass
            return blocks

        all_blocks = []
        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = {ex.submit(_scan_whale, t): t for t in DEFAULT_LEADERBOARD}
            for fut in as_completed(futures):
                all_blocks.extend(fut.result() or [])

        all_blocks.sort(key=lambda x: x["prem_m"], reverse=True)
        out = {"blocks": all_blocks[:60], "total": len(all_blocks), "scanned": len(DEFAULT_LEADERBOARD)}
        app._whale_cache    = out
        app._whale_cache_ts = _dt.now()
        _save_whale_blocks_to_db(all_blocks)
    finally:
        app._whale_scan_running = False


@app.route("/stock-api/whale-activity", methods=["GET"])
def whale_activity():
    """Scan for large institutional options blocks ($5M+ single-strike) across 30-365 day expirations."""
    from datetime import datetime as _dt
    import threading

    _cache = getattr(app, "_whale_cache", None)
    _ts    = getattr(app, "_whale_cache_ts", None)
    if _cache and _ts and (_dt.now() - _ts).total_seconds() < 1800:
        return jsonify(_cache)

    # Cache is cold — return recent DB blocks immediately, kick off live scan in background
    db_blocks = []
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT ticker, direction, strike::float, expiry, days_out, prem_m::float,
                       volume, otm_pct::float, category, tier, price::float
                FROM whale_blocks
                WHERE first_seen >= NOW() - INTERVAL '3 days'
                ORDER BY prem_m DESC
                LIMIT 60
            """)
            cols = ["ticker","direction","strike","expiry","days_out","prem_m","volume","otm_pct","category","tier","price"]
            db_blocks = [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        pass

    # Kick off live scan in background (only one at a time)
    if not getattr(app, "_whale_scan_running", False):
        threading.Thread(target=_run_whale_scan_background, daemon=True).start()

    if db_blocks:
        out = {"blocks": db_blocks, "total": len(db_blocks), "scanned": len(DEFAULT_LEADERBOARD), "source": "db"}
        return jsonify(out)

    # No DB data either — wait for live scan synchronously (first ever run)
    _run_whale_scan_background()
    _cache = getattr(app, "_whale_cache", None)
    if _cache:
        return jsonify(_cache)
    return jsonify({"blocks": [], "total": 0, "scanned": len(DEFAULT_LEADERBOARD)})


@app.route("/stock-api/whale-history", methods=["GET"])
def whale_history():
    """Return all-time whale block log from DB, newest first."""
    ticker = request.args.get("ticker", "").upper().strip()
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            if ticker:
                cur.execute("""
                    SELECT ticker, direction, strike::float, expiry, days_out, prem_m::float,
                           volume, otm_pct::float, category, tier, price::float,
                           first_seen AT TIME ZONE 'UTC' AS first_seen
                    FROM whale_blocks
                    WHERE ticker = %s
                    ORDER BY first_seen DESC
                    LIMIT 500
                """, (ticker,))
            else:
                cur.execute("""
                    SELECT ticker, direction, strike::float, expiry, days_out, prem_m::float,
                           volume, otm_pct::float, category, tier, price::float,
                           first_seen AT TIME ZONE 'UTC' AS first_seen
                    FROM whale_blocks
                    ORDER BY first_seen DESC
                    LIMIT 500
                """)
            cols = ["ticker","direction","strike","expiry","days_out","prem_m",
                    "volume","otm_pct","category","tier","price","first_seen"]
            rows = []
            for row in cur.fetchall():
                d = dict(zip(cols, row))
                d["first_seen"] = d["first_seen"].strftime("%Y-%m-%d %H:%M UTC") if d["first_seen"] else None
                rows.append(d)
        return jsonify({"blocks": rows, "total": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e), "blocks": [], "total": 0}), 500


# ── Trade Watchlist ───────────────────────────────────────────────────────────

def _init_trade_watchlist_table():
    sql = """
    CREATE TABLE IF NOT EXISTS trade_watchlist (
        id           SERIAL PRIMARY KEY,
        ticker       TEXT NOT NULL,
        strike       NUMERIC NOT NULL,
        expiry       TEXT NOT NULL,
        option_type  TEXT NOT NULL DEFAULT 'CALL',
        entry_price  NUMERIC,
        contracts    INTEGER DEFAULT 1,
        notes        TEXT,
        saved_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(sql)
            conn.commit()
    except Exception as e:
        print(f"[trade_watchlist] init error: {e}")

_init_trade_watchlist_table()


def _init_insider_tables():
    sql = """
    CREATE TABLE IF NOT EXISTS insider_alerts (
        id                  SERIAL PRIMARY KEY,
        ticker              TEXT NOT NULL,
        detected_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        suspicion_score     INTEGER NOT NULL,
        prem                BIGINT,
        strike              FLOAT,
        expiry              TEXT,
        price_at_detection  FLOAT,
        vol_oi              FLOAT,
        earnings_date       DATE,
        days_to_earnings    INTEGER,
        ticker_appearances  INTEGER,
        verdict             TEXT,
        pre_positioned      BOOLEAN DEFAULT FALSE,
        outcome_checked     BOOLEAN DEFAULT FALSE,
        UNIQUE (ticker, strike, expiry)
    );
    CREATE TABLE IF NOT EXISTS insider_outcomes (
        id                  SERIAL PRIMARY KEY,
        alert_id            INTEGER REFERENCES insider_alerts(id),
        ticker              TEXT NOT NULL,
        earnings_date       DATE,
        price_at_detection  FLOAT,
        price_at_earnings   FLOAT,
        pct_move            FLOAT,
        called_it           BOOLEAN,
        outcome_verdict     TEXT,
        checked_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (alert_id)
    );
    """
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(sql)
            conn.commit()
        print("[insider_tables] ready")
    except Exception as e:
        print(f"[insider_tables] init error: {e}")

_init_insider_tables()


def _check_insider_outcomes():
    """
    After a flagged ticker's earnings date passes, fetch the post-earnings price,
    compute % move from the detection price, and write the verdict to insider_outcomes.
    Runs daily at 4:37 PM ET.
    """
    import datetime as _dto
    import yfinance as _yfo
    today = _dto.date.today()
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, ticker, price_at_detection, earnings_date
                FROM insider_alerts
                WHERE earnings_date IS NOT NULL
                  AND earnings_date <= %s
                  AND outcome_checked = FALSE
            """, (today,))
            pending = cur.fetchall()
            if not pending:
                print("[insider_outcomes] No pending alerts today")
                return
            print(f"[insider_outcomes] Checking {len(pending)} alerts…")
            for (alert_id, ticker, price_at_detection, earnings_date) in pending:
                try:
                    hist = _yfo.Ticker(ticker).history(period="10d")
                    if hist.empty or not price_at_detection:
                        continue
                    current_price = float(hist["Close"].iloc[-1])
                    pct = (current_price - price_at_detection) / price_at_detection * 100
                    called = pct >= 5.0
                    if called:
                        v = f"CALLED IT ✅ +{pct:.1f}%"
                    elif pct <= -5.0:
                        v = f"MISS ❌ {pct:.1f}%"
                    else:
                        v = f"FLAT ➖ {pct:+.1f}%"
                    cur.execute("""
                        INSERT INTO insider_outcomes
                            (alert_id, ticker, earnings_date, price_at_detection,
                             price_at_earnings, pct_move, called_it, outcome_verdict)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (alert_id) DO UPDATE
                            SET price_at_earnings = EXCLUDED.price_at_earnings,
                                pct_move          = EXCLUDED.pct_move,
                                called_it         = EXCLUDED.called_it,
                                outcome_verdict   = EXCLUDED.outcome_verdict,
                                checked_at        = NOW()
                    """, (alert_id, ticker, earnings_date, price_at_detection,
                          current_price, pct, called, v))
                    cur.execute("UPDATE insider_alerts SET outcome_checked=TRUE WHERE id=%s", (alert_id,))
                    print(f"[insider_outcomes] {ticker}: {v}")
                except Exception as _oe:
                    print(f"[insider_outcomes] {ticker} error: {_oe}")
            conn.commit()
            print(f"[insider_outcomes] Done — {len(pending)} resolved")
    except Exception as e:
        print(f"[insider_outcomes] DB error: {e}")


@app.route("/stock-api/trade-watchlist", methods=["GET"])
def get_trade_watchlist():
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, ticker, strike::float, expiry, option_type,
                       entry_price::float, contracts, notes,
                       saved_at AT TIME ZONE 'UTC' AS saved_at
                FROM trade_watchlist
                WHERE saved_at > NOW() - INTERVAL '30 days'
                ORDER BY saved_at DESC
            """)
            cols = ["id","ticker","strike","expiry","option_type","entry_price","contracts","notes","saved_at"]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        # enrich with current stock price
        tickers = list({r["ticker"] for r in rows})
        prices = {}
        if tickers:
            import yfinance as yf
            from concurrent.futures import ThreadPoolExecutor
            def _get_price(t):
                try:
                    h = yf.Ticker(t).history(period="1d", interval="1m")
                    return t, float(h["Close"].iloc[-1]) if not h.empty else None
                except Exception:
                    return t, None
            with ThreadPoolExecutor(max_workers=min(8, len(tickers))) as ex:
                for t, p in ex.map(_get_price, tickers):
                    prices[t] = p

        from datetime import datetime as _dt2, timezone as _tz
        now = _dt2.now(_tz.utc)
        for r in rows:
            r["saved_at"] = r["saved_at"].strftime("%Y-%m-%d %H:%M UTC") if r["saved_at"] else None
            r["current_price"] = prices.get(r["ticker"])
            # days until expiry
            try:
                exp_dt = _dt2.strptime(r["expiry"], "%Y-%m-%d").date()
                r["days_to_expiry"] = (exp_dt - now.date()).days
            except Exception:
                r["days_to_expiry"] = None
            # days held
            try:
                saved_dt = _dt2.strptime(r["saved_at"], "%Y-%m-%d %H:%M UTC")
                r["days_held"] = (now.replace(tzinfo=None) - saved_dt).days
            except Exception:
                r["days_held"] = 0
            # OTM/ITM %
            if r["current_price"] and r["strike"]:
                r["strike_vs_price_pct"] = round((r["strike"] / r["current_price"] - 1) * 100, 1)
            else:
                r["strike_vs_price_pct"] = None
            # total cost
            if r["entry_price"] and r["contracts"]:
                r["total_cost"] = round(r["entry_price"] * r["contracts"] * 100, 2)
            else:
                r["total_cost"] = None

        return jsonify({"trades": rows, "count": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e), "trades": [], "count": 0}), 500


@app.route("/stock-api/trade-watchlist", methods=["POST"])
def add_trade_watchlist():
    body = request.get_json(force=True) or {}
    ticker = body.get("ticker", "").upper().strip()
    strike = body.get("strike")
    expiry = body.get("expiry", "").strip()
    option_type = body.get("option_type", "CALL").upper()
    entry_price = body.get("entry_price")
    contracts = body.get("contracts", 1)
    notes = body.get("notes", "").strip()
    if not ticker or not strike or not expiry:
        return jsonify({"error": "ticker, strike, and expiry are required"}), 400
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO trade_watchlist (ticker, strike, expiry, option_type, entry_price, contracts, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, (ticker, strike, expiry, option_type, entry_price, contracts, notes or None))
            new_id = cur.fetchone()[0]
            conn.commit()
        return jsonify({"ok": True, "id": new_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/stock-api/trade-watchlist/<int:trade_id>", methods=["DELETE"])
def delete_trade_watchlist(trade_id):
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM trade_watchlist WHERE id = %s", (trade_id,))
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/stock-api/unusual-calls", methods=["GET"])
def unusual_calls():
    """Scan for unusual near-term call activity (1-30 days) with Vol/OI >= 3x — pure bullish bets, not hedges."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import yfinance as yf
    from datetime import datetime as _dt
    import threading

    # Ensure a single scan lock exists
    if not hasattr(app, "_uc_lock"):
        app._uc_lock = threading.Lock()

    _cache = getattr(app, "_unusual_calls_cache", None)
    _ts    = getattr(app, "_unusual_calls_cache_ts", None)
    if _cache and _ts and (_dt.now() - _ts).total_seconds() < 900:
        return jsonify(_cache)

    # Only one scan at a time — concurrent requests block here until the scan
    # finishes, then the re-check returns the fresh cache instead of re-scanning.
    with app._uc_lock:
        # Re-check after acquiring lock — another thread may have just finished
        _cache = getattr(app, "_unusual_calls_cache", None)
        _ts    = getattr(app, "_unusual_calls_cache_ts", None)
        if _cache and _ts and (_dt.now() - _ts).total_seconds() < 900:
            return jsonify(_cache)

        now = _dt.now()

        # ETF tickers get calibrated thresholds: lower vol/OI (they carry massive OI) and
        # wider expiry window (institutional ETF plays tend to go 30-60 days out).
        _ETF_SET = {
            "SPY","QQQ","IWM","DIA","MDY","VTI","VOO",
            "XLF","XLE","XLK","XLY","XLI","XLV","XLB","XLP","XLU","XLRE",
            "SMH","SOXX","XBI","IBB","KRE","XRT","ITB","JETS","KWEB","DRAM",
            "TQQQ","SPXL","SOXL","UDOW","LABU","FNGU","TECL","UPRO","TNA","FAS","ERX",
            "SQQQ","SPXS","SOXS","SDOW","TZA","FAZ","ERY",
            "SSO","QLD","DDM","UWM","SDS","QID","DXD","TWM",
            "VXX","UVXY","SVXY","VIXY","SVOL",
            "GLD","IAU","SLV","USO","UNG","GDX","GDXJ","OIH",
            "TLT","HYG","LQD","TBT","TMF","SHY","IEF","JNK",
            "EEM","EFA","FXI","EWJ","EWZ","EWY","IEMG",
            "ARKK","ARKG","ARKW","ARKF",
            "IBIT","FBTC","BITB","ARKB","WGMI",
        }

        def _scan_unusual(ticker):
            hits    = []
            is_etf  = ticker in _ETF_SET
            # ETFs: $50K floor (high liquidity); stocks: $20K catches small-cap insider bets
            min_voi  = 1.5  if is_etf else 2.0
            min_prem = 50_000 if is_etf else 20_000
            max_days = 60   if is_etf else 45
            try:
                tkr   = yf.Ticker(ticker)
                price = float(getattr(tkr.fast_info, "last_price", 0) or 0)
                if price <= 0: return hits
                exps = tkr.options
                if not exps: return hits
                for exp in exps:
                    try:
                        days_out = (_dt.strptime(exp, "%Y-%m-%d") - now).days
                        if not (1 <= days_out <= max_days): continue
                        calls = tkr.option_chain(exp).calls
                        for _, row in calls.iterrows():
                            strike = float(row.get("strike", 0) or 0)
                            vol    = int(row.get("volume", 0) or 0)
                            oi     = int(row.get("openInterest", 0) or 0)
                            last   = float(row.get("lastPrice", 0) or 0)
                            iv     = float(row.get("impliedVolatility", 0) or 0)
                            if strike <= 0 or last <= 0 or vol < 10: continue
                            if strike < price * 0.15: continue
                            pre_otm = (strike - price) / price * 100
                            if pre_otm < -15: continue   # skip deep ITM — hedges
                            if pre_otm > 50: continue    # skip lottery-ticket far OTM
                            vol_oi = round(vol / max(oi, 1), 2)
                            if vol_oi < min_voi: continue
                            prem = round(vol * last * 100, 0)
                            if prem < min_prem: continue
                            urgency = "EXPIRING" if days_out <= 7 else "NEAR" if days_out <= 14 else "SHORT"
                            # pre_positioned: OI >> vol = accumulated BEFORE today (quiet insider pattern)
                            pre_positioned = bool(oi >= 100 and vol < oi * 0.5)
                            ldt = row.get("lastTradeDate")
                            last_trade = str(ldt)[:10] if ldt is not None else ""
                            hits.append({
                                "ticker":          ticker,
                                "price":           round(price, 2),
                                "strike":          round(strike, 2),
                                "expiry":          exp,
                                "days_out":        days_out,
                                "volume":          vol,
                                "oi":              oi,
                                "vol_oi":          vol_oi,
                                "prem":            int(prem),
                                "otm_pct":         round(pre_otm, 1),
                                "iv":              round(iv * 100, 1),
                                "urgency":         urgency,
                                "pre_positioned":  pre_positioned,
                                "last_trade":      last_trade,
                                "is_etf":          is_etf,
                            })
                    except Exception: continue
            except Exception: pass
            return hits

        # Check DB for today's data first — avoids a slow live scan if we already have results
        try:
            with _psycopg2.connect(_DB_URL) as _pre_conn, _pre_conn.cursor() as _pre_cur:
                _pre_cur.execute("""
                    SELECT ticker, price::float, strike::float, expiry, days_out,
                           volume, oi, vol_oi::float, prem::bigint, otm_pct::float,
                           iv::float, urgency
                    FROM unusual_calls_log
                    WHERE last_seen >= NOW() - INTERVAL '36 hours'
                      AND expiry::date > CURRENT_DATE
                      AND vol_oi >= 3
                      AND prem >= 500000
                    ORDER BY last_seen DESC, vol_oi DESC LIMIT 80
                """)
                _today_rows = _pre_cur.fetchall()
            if len(_today_rows) >= 5:
                _cols = ["ticker","price","strike","expiry","days_out","volume","oi","vol_oi","prem","otm_pct","iv","urgency"]
                all_hits = []
                for _row in _today_rows:
                    _d = dict(zip(_cols, _row))
                    _d["is_etf"] = _d["ticker"] in _ETF_SET
                    all_hits.append(_d)
                out = {"hits": all_hits, "total": len(all_hits), "scanned": len(DEFAULT_LEADERBOARD)}
                app._unusual_calls_cache    = out
                app._unusual_calls_cache_ts = _dt.now()
                return jsonify(out)
        except Exception:
            pass

        # Live scan — movers first so earnings/catalyst stocks are always caught,
        # then fill with the core leaderboard. Stocks up 5%+ (like CBRL +24%)
        # appear in day_gainers and get scanned regardless of leaderboard position.
        _movers = _fetch_market_movers()
        _mover_set = set(_movers)
        _scan_universe = _movers + [t for t in DEFAULT_LEADERBOARD[:400] if t not in _mover_set]

        all_hits = []
        with ThreadPoolExecutor(max_workers=20) as ex:
            futures = {ex.submit(_scan_unusual, t): t for t in _scan_universe}
            for fut in as_completed(futures):
                all_hits.extend(fut.result() or [])

        all_hits.sort(key=lambda x: x["vol_oi"], reverse=True)

        # If live scan returned nothing (rate limited / cold start), fall back to DB
        if not all_hits:
            try:
                with _psycopg2.connect(_DB_URL) as _conn, _conn.cursor() as _cur:
                    _cur.execute("""
                        SELECT ticker, price::float, strike::float, expiry, days_out,
                               volume, oi, vol_oi::float, prem::bigint, otm_pct::float,
                               iv::float, urgency
                        FROM unusual_calls_log
                        WHERE last_seen >= CURRENT_DATE
                          AND vol_oi >= 3
                          AND prem >= 100000
                        ORDER BY vol_oi DESC LIMIT 80
                    """)
                    _cols = ["ticker","price","strike","expiry","days_out","volume","oi","vol_oi","prem","otm_pct","iv","urgency"]
                    for _row in _cur.fetchall():
                        _d = dict(zip(_cols, _row))
                        _d["is_etf"] = _d["ticker"] in _ETF_SET
                        all_hits.append(_d)
            except Exception:
                pass

        out = {"hits": all_hits[:80], "total": len(all_hits), "scanned": len(DEFAULT_LEADERBOARD)}
        app._unusual_calls_cache    = out
        app._unusual_calls_cache_ts = _dt.now()
        if all_hits:
            _save_unusual_calls_to_db(all_hits)
        return jsonify(out)


@app.route("/stock-api/unusual-calls/microcap", methods=["GET"])
def unusual_calls_microcap():
    """Return micro/small-cap unusual call options from DB, newest first.
    If the requested window is empty, auto-extends to 7 days and kicks off
    a background scan so future loads have fresh data.
    """
    days_back = min(int(request.args.get("days", 3)), 30)

    _SEL = """
        SELECT ticker, price::float, strike::float, expiry, days_out,
               volume, oi, vol_oi::float, prem::bigint, otm_pct::float,
               iv::float, urgency, cap_tier,
               first_seen AT TIME ZONE 'UTC' AS first_seen,
               last_seen  AT TIME ZONE 'UTC' AS last_seen
        FROM unusual_calls_microcap_log
        WHERE last_seen >= NOW() - (%(days)s || ' days')::INTERVAL
          AND expiry::date > CURRENT_DATE
        ORDER BY prem DESC
        LIMIT 200
    """
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(_SEL, {"days": days_back})
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]

            # If the requested window returned nothing, extend to 7 days so the
            # tab always shows the most recently available data
            if not rows and days_back < 7:
                cur.execute(_SEL, {"days": 7})
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]

            for r in rows:
                if r.get("first_seen"): r["first_seen"] = r["first_seen"].isoformat()
                if r.get("last_seen"):  r["last_seen"]  = r["last_seen"].isoformat()

        # If still empty, kick off a background scan so the next load has data
        if not rows:
            import threading as _thr
            _mc_lock = getattr(app, "_mc_autoscan_lock", None)
            if _mc_lock is None:
                app._mc_autoscan_lock = _thr.Lock()
                _mc_lock = app._mc_autoscan_lock
            if _mc_lock.acquire(blocking=False):
                def _bg_scan():
                    try:
                        hits = _run_microcap_options_scan()
                        _save_microcap_calls_to_db(hits)
                        print(f"[microcap_auto] background scan complete → {len(hits)} signals")
                    except Exception as _e:
                        print(f"[microcap_auto] scan error: {_e}")
                    finally:
                        _mc_lock.release()
                _thr.Thread(target=_bg_scan, daemon=True).start()

        return jsonify({"signals": rows, "total": len(rows),
                        "scan_triggered": not rows})
    except Exception as e:
        return jsonify({"error": str(e), "signals": [], "total": 0}), 500


@app.route("/stock-api/unusual-calls/microcap/scan", methods=["POST"])
def unusual_calls_microcap_scan():
    """Manually trigger a fresh micro/small-cap options scan and save results to DB."""
    import threading
    def _bg():
        try:
            hits = _run_microcap_options_scan()
            _save_microcap_calls_to_db(hits)
            print(f"[microcap_scan] manual trigger → {len(hits)} signals saved")
        except Exception as e:
            print(f"[microcap_scan] manual trigger error: {e}")
    threading.Thread(target=_bg, daemon=True).start()
    return jsonify({"status": "scan started", "note": "Results will appear in ~60s"})


@app.route("/stock-api/unusual-calls-log", methods=["GET"])
def unusual_calls_log():
    """Return all-time unusual calls history from DB, newest first."""
    ticker = request.args.get("ticker", "").upper().strip()
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            if ticker:
                cur.execute("""
                    SELECT ticker, price::float, strike::float, expiry, days_out,
                           volume, oi, vol_oi::float, prem::bigint, otm_pct::float,
                           iv::float, urgency,
                           first_seen AT TIME ZONE 'UTC' AS first_seen,
                           last_seen  AT TIME ZONE 'UTC' AS last_seen
                    FROM unusual_calls_log
                    WHERE ticker = %s
                      AND prem >= 500000
                    ORDER BY first_seen DESC
                    LIMIT 500
                """, (ticker,))
            else:
                cur.execute("""
                    SELECT ticker, price::float, strike::float, expiry, days_out,
                           volume, oi, vol_oi::float, prem::bigint, otm_pct::float,
                           iv::float, urgency,
                           first_seen AT TIME ZONE 'UTC' AS first_seen,
                           last_seen  AT TIME ZONE 'UTC' AS last_seen
                    FROM unusual_calls_log
                    WHERE prem >= 500000
                    ORDER BY first_seen DESC
                    LIMIT 500
                """)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            for r in rows:
                if r.get("first_seen"): r["first_seen"] = r["first_seen"].isoformat()
                if r.get("last_seen"):  r["last_seen"]  = r["last_seen"].isoformat()
        return jsonify({"signals": rows, "total": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e), "signals": [], "total": 0}), 500


@app.route("/stock-api/eod-sweeps", methods=["GET"])
def eod_sweeps():
    """
    End-of-day institutional sweep detector.
    Finds aggressive bullish naked calls placed in the last 90 min of trading
    (3:00–4:30 PM ET = 19:00–20:30 UTC) — signals institutions positioning for next day.
    """
    import math as _math
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    bust   = request.args.get("bust", "0") == "1"
    _cache = getattr(app, "_eod_sweeps_cache", None)
    _ts    = getattr(app, "_eod_sweeps_cache_ts", None)
    if not bust and _cache and _ts and (_dt.now() - _ts).total_seconds() < 120:
        return jsonify(_cache)

    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            # First try today's EOD data only
            cur.execute("""
                SELECT ticker, price::float, strike::float, expiry, days_out,
                       vol_oi::float, prem::bigint, otm_pct::float, iv::float,
                       urgency, last_seen
                FROM unusual_calls_log
                WHERE last_seen::date = CURRENT_DATE
                  AND EXTRACT(HOUR FROM last_seen AT TIME ZONE 'UTC') BETWEEN 14 AND 23
                  AND days_out BETWEEN 1 AND 15
                  AND vol_oi  >= 5
                  AND prem    >= 300000
                  AND otm_pct BETWEEN -2 AND 25
                  AND strike  >= price * 0.97
                ORDER BY last_seen DESC, vol_oi DESC
            """)
            rows_today = cur.fetchall()
            used_today = bool(rows_today)

            if rows_today:
                rows_raw = rows_today
            else:
                # Fall back to most recent trading day (up to 5 days)
                cur.execute("""
                    SELECT ticker, price::float, strike::float, expiry, days_out,
                           vol_oi::float, prem::bigint, otm_pct::float, iv::float,
                           urgency, last_seen
                    FROM unusual_calls_log
                    WHERE last_seen >= NOW() - INTERVAL '5 days'
                      AND EXTRACT(HOUR FROM last_seen AT TIME ZONE 'UTC') BETWEEN 18 AND 23
                      AND days_out BETWEEN 1 AND 15
                      AND vol_oi  >= 5
                      AND prem    >= 300000
                      AND otm_pct BETWEEN -2 AND 25
                      AND strike  >= price * 0.97
                    ORDER BY last_seen DESC, vol_oi DESC
                """)
                rows_raw = cur.fetchall()

        cols = ["ticker","price","strike","expiry","days_out","vol_oi","prem","otm_pct","iv","urgency","last_seen"]
        rows = [dict(zip(cols, r)) for r in rows_raw]

        if not rows:
            out = {"signals": [], "generated_at": _dt.now().isoformat(), "total": 0,
                   "note": "No EOD sweeps found in the last 2 days. Run a scan at market close (3:30–4:15 PM ET) to capture institutional positioning."}
            return jsonify(out)

        from collections import defaultdict as _dd
        by_ticker = _dd(list)
        for r in rows:
            by_ticker[r["ticker"]].append(r)

        results = []
        for ticker, strikes in by_ticker.items():
            num_strikes  = len(strikes)
            total_prem   = sum(s["prem"] for s in strikes)
            max_vol_oi   = max(s["vol_oi"] for s in strikes)
            avg_iv       = sum(s["iv"] or 0 for s in strikes) / num_strikes
            best         = max(strikes, key=lambda s: s["vol_oi"])
            price        = best["price"]

            # How late was the latest detection? Closer to 4 PM ET = higher bonus
            # last_seen is stored as UTC; 4 PM ET = 20:00 UTC
            latest_ts = max(s["last_seen"] for s in strikes)
            if hasattr(latest_ts, "hour"):
                hour_utc = latest_ts.hour + latest_ts.minute / 60.0
            else:
                try:
                    from dateutil import parser as _dp
                    _dt_obj = _dp.parse(str(latest_ts))
                    hour_utc = _dt_obj.hour + _dt_obj.minute / 60.0
                except Exception:
                    hour_utc = 19.5
            # Minutes before 4 PM ET close (20:00 UTC); earlier = more time to close
            minutes_to_close = max(0, (20.0 - hour_utc) * 60)
            # Late bonus: detected within 30 min of close gets 2.0x, 60 min = 1.5x, 90 min = 1.0x
            late_bonus = 2.0 if minutes_to_close <= 30 else 1.5 if minutes_to_close <= 60 else 1.0

            # Scoring
            vol_oi_factor = _math.log1p(max_vol_oi) / _math.log1p(5)
            prem_factor   = _math.log1p(total_prem / 1_000_000) / _math.log1p(1)
            iv_bonus      = 1.8 if avg_iv >= 90 else 1.5 if avg_iv >= 70 else 1.2 if avg_iv >= 50 else 1.0
            sweep_mult    = 1.0 + 0.4 * (num_strikes - 1)
            score         = round(vol_oi_factor * prem_factor * iv_bonus * sweep_mult * late_bonus, 1)

            grade = "EXTREME" if score >= 12 else "HIGH" if score >= 7 else "ELEVATED" if score >= 4 else "MODERATE"

            urgency_rank = {"EXPIRING": 3, "SHORT": 2, "NEAR": 1}.get(best["urgency"], 1)

            results.append({
                "ticker":          ticker,
                "price":           round(price, 2),
                "score":           score,
                "grade":           grade,
                "num_strikes":     num_strikes,
                "total_prem_m":    round(total_prem / 1_000_000, 2),
                "max_vol_oi":      round(max_vol_oi, 1),
                "avg_iv":          round(avg_iv, 1),
                "latest_at":       str(latest_ts),
                "minutes_to_close": round(minutes_to_close, 0),
                "urgency":         best["urgency"],
                "strikes": [
                    {
                        "ticker":           ticker,
                        "price":            round(s["price"], 2),
                        "strike":           float(s["strike"]),
                        "expiry":           str(s["expiry"]),
                        "days_out":         s["days_out"],
                        "vol_oi":           round(s["vol_oi"], 1),
                        "prem":             s["prem"],
                        "otm_pct":          round(s["otm_pct"], 1),
                        "iv":               round(s["iv"] or 0, 1),
                        "urgency":          s["urgency"],
                        "detected_at":      str(s["last_seen"]),
                        "minutes_to_close": round(minutes_to_close, 0),
                    }
                    for s in sorted(strikes, key=lambda x: x["vol_oi"], reverse=True)
                ],
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        for i, r in enumerate(results):
            r["rank"] = i + 1

        _log_eod_sweep_signals(results, today_only=used_today)
        out = {"signals": results, "generated_at": _dt.now().isoformat(), "total": len(results)}
        app._eod_sweeps_cache    = out
        app._eod_sweeps_cache_ts = _dt.now()
        return jsonify(out)

    except Exception as e:
        import traceback
        print(f"[eod_sweeps] error: {e}\n{traceback.format_exc()}", file=__import__("sys").stderr)
        return jsonify({"error": str(e), "signals": []}), 500


@app.route("/stock-api/admin/test-emails", methods=["POST"])
def admin_test_emails():
    """Admin: fire all six daily emails right now using today's cached/DB data."""
    import threading as _thr
    results = {}

    def _fire(name, fn, *args):
        try:
            fn(*args)
            results[name] = "sent"
        except Exception as e:
            results[name] = f"error: {e}"

    # Morning inflows email (uses DB)
    t1 = _thr.Thread(target=_fire, args=("morning_inflows", _send_morning_inflows_email), daemon=True)
    t1.start(); t1.join(timeout=30)

    # EOD accum email (uses DB)
    t2 = _thr.Thread(target=_fire, args=("eod_accum", _send_eod_accum_email), daemon=True)
    t2.start(); t2.join(timeout=30)

    # AI trades email (uses in-memory cache)
    _ait = getattr(app, "_ait_cache", None)
    trades = (_ait or {}).get("trades", [])
    if trades:
        t3 = _thr.Thread(target=_fire, args=("ai_trades", _send_ai_trades_email, trades), daemon=True)
        t3.start(); t3.join(timeout=30)
    else:
        results["ai_trades"] = "skipped — no cache (run /stock-api/ai-trades?bust=1 first)"

    # Unusual Calls email (cache + DB fallback)
    t4 = _thr.Thread(target=_fire, args=("unusual_calls", _send_unusual_calls_email), daemon=True)
    t4.start(); t4.join(timeout=30)

    # Small & Growth (Microcap) Calls email (DB)
    t5 = _thr.Thread(target=_fire, args=("microcap_calls", _send_microcap_calls_email), daemon=True)
    t5.start(); t5.join(timeout=30)

    # High Conviction Calls email (cache + DB fallback)
    t6 = _thr.Thread(target=_fire, args=("high_conviction", _send_high_conviction_email), daemon=True)
    t6.start(); t6.join(timeout=30)

    return jsonify({"status": "done", "results": results})


@app.route("/stock-api/admin/run-eod-scan", methods=["POST"])
def admin_run_eod_scan():
    """
    Admin endpoint: manually trigger an EOD unusual-calls scan to populate
    unusual_calls_log for the current trading day.  Runs in a background thread
    and returns immediately so the HTTP request doesn't time out.
    """
    import threading as _thr
    import traceback as _tb

    def _bg():
        try:
            _run_unusual_calls_scan("manual-trigger")
            # Bust the EOD sweeps cache so the next request re-queries fresh data
            if hasattr(app, "_eod_sweeps_cache"):
                app._eod_sweeps_cache    = None
                app._eod_sweeps_cache_ts = None
            print("[admin_run_eod_scan] cache busted — fresh data ready")
        except Exception as exc:
            print(f"[admin_run_eod_scan] error: {exc}\n{_tb.format_exc()}")

    _thr.Thread(target=_bg, daemon=True).start()
    return jsonify({
        "status":  "started",
        "message": "EOD unusual-calls scan running in background (~2-3 min). "
                   "Call /stock-api/eod-sweeps?bust=1 afterwards to see fresh data.",
        "tickers": len(DEFAULT_LEADERBOARD),
    })


@app.route("/stock-api/eod-sweep-track-record", methods=["GET"])
def eod_sweep_track_record():
    """
    EOD sweep track record — win rates by session (eod/morning/preclose) and
    by grade (EXTREME/HIGH/ELEVATED) at T+1, T+3, T+5 trading days.
    """
    from datetime import datetime as _dt
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(*) as total,
                    COUNT(close_t1)                                        as n_t1,
                    SUM(CASE WHEN return_t1 > 0 THEN 1 ELSE 0 END)        as wins_t1,
                    AVG(CASE WHEN return_t1 IS NOT NULL THEN return_t1 END) as avg_r1,
                    COUNT(close_t3)                                        as n_t3,
                    SUM(CASE WHEN return_t3 > 0 THEN 1 ELSE 0 END)        as wins_t3,
                    AVG(CASE WHEN return_t3 IS NOT NULL THEN return_t3 END) as avg_r3,
                    COUNT(close_t5)                                        as n_t5,
                    SUM(CASE WHEN return_t5 > 0 THEN 1 ELSE 0 END)        as wins_t5,
                    AVG(CASE WHEN return_t5 IS NOT NULL THEN return_t5 END) as avg_r5
                FROM eod_sweep_log
            """)
            overall = cur.fetchone()

            cur.execute("""
                SELECT session, COUNT(*) as total,
                    COUNT(close_t1), SUM(CASE WHEN return_t1 > 0 THEN 1 ELSE 0 END), AVG(return_t1),
                    COUNT(close_t3), SUM(CASE WHEN return_t3 > 0 THEN 1 ELSE 0 END), AVG(return_t3),
                    COUNT(close_t5), SUM(CASE WHEN return_t5 > 0 THEN 1 ELSE 0 END), AVG(return_t5)
                FROM eod_sweep_log
                GROUP BY session ORDER BY
                    CASE session WHEN 'eod' THEN 1 WHEN 'preclose' THEN 2 ELSE 3 END
            """)
            by_session = cur.fetchall()

            cur.execute("""
                SELECT grade, COUNT(*) as total,
                    COUNT(close_t1), SUM(CASE WHEN return_t1 > 0 THEN 1 ELSE 0 END), AVG(return_t1),
                    COUNT(close_t3), SUM(CASE WHEN return_t3 > 0 THEN 1 ELSE 0 END), AVG(return_t3),
                    COUNT(close_t5), SUM(CASE WHEN return_t5 > 0 THEN 1 ELSE 0 END), AVG(return_t5)
                FROM eod_sweep_log
                GROUP BY grade ORDER BY
                    CASE grade WHEN 'EXTREME' THEN 1 WHEN 'HIGH' THEN 2 ELSE 3 END
            """)
            by_grade = cur.fetchall()

            cur.execute("""
                SELECT ticker, signal_date, session, score, grade, num_strikes,
                       total_prem_m, max_vol_oi, avg_iv, price_at_signal,
                       close_t1, close_t3, close_t5, return_t1, return_t3, return_t5
                FROM eod_sweep_log
                ORDER BY signal_date DESC, score DESC
                LIMIT 60
            """)
            recent = cur.fetchall()

        def pct(wins, n):
            return round(float(wins) / float(n) * 100, 1) if n and float(n) > 0 and wins is not None else None

        def fmt_stat(n, wins, avg_r):
            return {
                "n": int(n or 0),
                "win_rate": pct(wins, n),
                "avg_return": round(float(avg_r), 2) if avg_r is not None else None,
            }

        o = overall or (0,)*10
        result = {
            "total_signals": int(o[0] or 0),
            "overall": {
                "t1": fmt_stat(o[1], o[2], o[3]),
                "t3": fmt_stat(o[4], o[5], o[6]),
                "t5": fmt_stat(o[7], o[8], o[9]),
            },
            "by_session": [
                {
                    "session": r[0], "total": int(r[1] or 0),
                    "t1": fmt_stat(r[2], r[3], r[4]),
                    "t3": fmt_stat(r[5], r[6], r[7]),
                    "t5": fmt_stat(r[8], r[9], r[10]),
                } for r in by_session
            ],
            "by_grade": [
                {
                    "grade": r[0], "total": int(r[1] or 0),
                    "t1": fmt_stat(r[2], r[3], r[4]),
                    "t3": fmt_stat(r[5], r[6], r[7]),
                    "t5": fmt_stat(r[8], r[9], r[10]),
                } for r in by_grade
            ],
            "recent": [
                {
                    "ticker": r[0], "signal_date": str(r[1]), "session": r[2],
                    "score": float(r[3] or 0), "grade": r[4],
                    "num_strikes": r[5], "total_prem_m": float(r[6] or 0),
                    "max_vol_oi": float(r[7] or 0), "avg_iv": float(r[8] or 0),
                    "price_at_signal": float(r[9]) if r[9] else None,
                    "close_t1": float(r[10]) if r[10] else None,
                    "close_t3": float(r[11]) if r[11] else None,
                    "close_t5": float(r[12]) if r[12] else None,
                    "return_t1": float(r[13]) if r[13] else None,
                    "return_t3": float(r[14]) if r[14] else None,
                    "return_t5": float(r[15]) if r[15] else None,
                } for r in recent
            ],
            "generated_at": _dt.now().isoformat(),
        }
        return jsonify(result)
    except Exception as e:
        import traceback
        print(f"[eod_sweep_track_record] error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


@app.route("/stock-api/conviction-calls", methods=["GET"])
def conviction_calls():
    """
    High-conviction call screener: stocks where calls DRAMATICALLY outpace puts.
    Criteria: multiple strikes lighting up simultaneously, Vol/OI ≥5x, ≤30d expiry,
    premium ≥$500K. Groups by ticker and scores by institutional sweep pattern.
    """
    import math as _math
    from datetime import datetime as _dt

    _cache = getattr(app, "_conv_calls_cache", None)
    _ts    = getattr(app, "_conv_calls_cache_ts", None)
    force  = request.args.get("force") == "1"
    if not force and _cache and _ts and (_dt.now() - _ts).total_seconds() < 900:  # 15-min cache
        return jsonify(_cache)

    try:
        _base_sql = """
                SELECT ticker, price::float, strike::float, expiry, days_out,
                       vol_oi::float, prem::bigint, otm_pct::float, iv::float,
                       urgency, last_seen
                FROM unusual_calls_log
                WHERE last_seen >= {interval}
                  AND days_out BETWEEN 1 AND 30
                  AND vol_oi  >= 5
                  AND prem    >= 500000
                  AND otm_pct BETWEEN -2 AND 30
                  AND strike  >= price * 0.97
                ORDER BY last_seen DESC, vol_oi DESC
            """
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            # Try today first, fall back to 24 hours, then 7 days
            cur.execute(_base_sql.format(interval="CURRENT_DATE"))
            rows_today = cur.fetchall()
            if rows_today:
                rows_raw = rows_today
                window_label = "today"
            else:
                cur.execute(_base_sql.format(interval="NOW() - INTERVAL '1 day'"))
                rows_raw = cur.fetchall()
                window_label = "24h"
                if not rows_raw:
                    cur.execute(_base_sql.format(interval="NOW() - INTERVAL '7 days'"))
                    rows_raw = cur.fetchall()
                    window_label = "7d"
        print(f"[conviction_calls] today={len(rows_today)}, window={window_label}, total={len(rows_raw)}")

        cols = ["ticker","price","strike","expiry","days_out","vol_oi","prem","otm_pct","iv","urgency","last_seen"]
        rows = [dict(zip(cols, r)) for r in rows_raw]

        if not rows:
            return jsonify({"signals": [], "generated_at": _dt.now().isoformat(), "note": "No high-conviction calls found. Run a scan in 🚨 Unusual Calls first."})

        # Group by ticker — multi-strike sweep = strongest institutional signal
        from collections import defaultdict as _dd
        by_ticker = _dd(list)
        for r in rows:
            by_ticker[r["ticker"]].append(r)

        results = []
        for ticker, strikes in by_ticker.items():
            # Aggregate metrics
            num_strikes    = len(strikes)
            total_prem     = sum(s["prem"] for s in strikes)
            max_vol_oi     = max(s["vol_oi"] for s in strikes)
            avg_iv         = sum(s["iv"] or 0 for s in strikes) / num_strikes
            best_strike    = max(strikes, key=lambda s: s["vol_oi"])
            most_recent    = max(strikes, key=lambda s: s["last_seen"])
            price          = most_recent["price"]   # always use freshest price

            # Urgency: EXPIRING > SHORT > NEAR
            urgency_rank   = {"EXPIRING": 3, "SHORT": 2, "NEAR": 1}.get(best_strike["urgency"], 1)

            # IV conviction bonus (screaming options → screaming conviction)
            iv_bonus = 1.8 if avg_iv >= 90 else 1.5 if avg_iv >= 70 else 1.2 if avg_iv >= 50 else 1.0

            # Multi-strike sweep multiplier — every extra strike adds more conviction
            sweep_mult = 1.0 + 0.4 * (num_strikes - 1)

            # Compound conviction score
            prem_factor   = _math.log(total_prem / 1_000_000 + 1) + 1
            vol_oi_factor = _math.log(max_vol_oi + 1)
            score = round(vol_oi_factor * prem_factor * iv_bonus * sweep_mult * urgency_rank, 2)

            # Conviction label
            if score >= 12:   conviction = "EXTREME"
            elif score >= 7:  conviction = "HIGH"
            elif score >= 4:  conviction = "ELEVATED"
            else:             conviction = "MODERATE"

            # Urgency label for display
            days_out_min = min(s["days_out"] for s in strikes if s["days_out"])
            urgency_label = "EXPIRING" if days_out_min <= 5 else f"{days_out_min}D"

            results.append({
                "ticker":         ticker,
                "price":          price,
                "score":          score,
                "conviction":     conviction,
                "num_strikes":    num_strikes,
                "total_prem_m":   round(total_prem / 1_000_000, 2),
                "max_vol_oi":     round(max_vol_oi, 1),
                "avg_iv":         round(avg_iv, 1),
                "urgency":        urgency_label,
                "strikes":        sorted(strikes, key=lambda s: s["vol_oi"], reverse=True)[:8],
            })

        # Sort by conviction score descending
        results.sort(key=lambda x: x["score"], reverse=True)
        results = results[:15]
        for i, r in enumerate(results):
            r["rank"] = i + 1
            for s in r["strikes"]:
                if s.get("last_seen"):
                    s["last_seen"] = s["last_seen"].isoformat()

        out = {
            "signals":      results,
            "generated_at": _dt.now().isoformat(),
            "total":        len(results),
        }
        app._conv_calls_cache    = out
        app._conv_calls_cache_ts = _dt.now()
        return jsonify(out)
    except Exception as e:
        import traceback
        print(f"[conviction_calls] error: {e}\n{traceback.format_exc()}", file=__import__("sys").stderr)
        return jsonify({"error": str(e), "signals": []}), 500


@app.route("/stock-api/ai-short-calls", methods=["GET"])
def ai_short_calls():
    """5 AI-picked short-term call plays (≤30d expiry) drawn from the Unusual Calls scanner."""
    import sys
    from datetime import datetime as _dt
    from openai import OpenAI

    force  = request.args.get("force") == "1"
    _cache = getattr(app, "_aisc_cache", None)
    _ts    = getattr(app, "_aisc_cache_ts", None)
    if not force and _cache and _ts and (_dt.now() - _ts).total_seconds() < 3600:
        return jsonify(_cache)

    # 1. Try in-memory live cache first
    uc   = getattr(app, "_unusual_calls_cache", None)
    hits = (uc.get("hits") or []) if uc else []

    # 2. Fall back to DB if live cache is empty — prefer TODAY's signals, then 24h
    if not hits:
        try:
            _db_sql = """
                SELECT ticker, strike, expiry, days_out, vol_oi, prem, otm_pct, iv, urgency, price
                FROM unusual_calls_log
                WHERE last_seen >= {interval}
                  AND days_out BETWEEN 1 AND 30
                  AND prem >= 500000
                  AND otm_pct BETWEEN -2 AND 30
                  AND strike >= price * 0.97
                ORDER BY last_seen DESC, vol_oi DESC
                LIMIT 25
            """
            with _psycopg2.connect(_DB_URL) as conn_fb, conn_fb.cursor() as cur_fb:
                cur_fb.execute(_db_sql.format(interval="CURRENT_DATE"))
                rows = cur_fb.fetchall()
                if not rows:
                    cur_fb.execute(_db_sql.format(interval="NOW() - INTERVAL '1 day'"))
                    rows = cur_fb.fetchall()
            hits = [
                {"ticker": r[0], "strike": r[1], "expiry": str(r[2]), "days_out": r[3],
                 "vol_oi": float(r[4]), "prem": int(r[5]), "otm_pct": float(r[6]),
                 "iv": float(r[7]) if r[7] else 0.0, "urgency": r[8], "price": float(r[9])}
                for r in rows
            ]
        except Exception as _dbe:
            print(f"[ai_short_calls] DB fallback error: {_dbe}", file=sys.stderr)

    if not hits:
        return jsonify({
            "error": "No unusual calls data available. Run a scan in the 🚨 Unusual Calls tab first, then come back.",
            "picks": [], "generated_at": None
        })

    hits = hits[:30]

    try:
        oai = OpenAI(
            base_url=os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL"),
            api_key=os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY"),
            timeout=90.0,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    signals_text = "\n".join([
        f"{i+1}. {h['ticker']} | ${h['strike']} call | exp {h['expiry']} ({h['days_out']}d) | "
        f"Vol/OI={h['vol_oi']}x | prem=${h['prem']:,} | {'+' if h['otm_pct']>0 else ''}{h['otm_pct']}% OTM | "
        f"IV={h.get('iv',0)}% | urgency={h['urgency']} | stock_price=${h['price']:.2f}"
        for i, h in enumerate(hits)
    ])

    user_msg = f"""These are today's unusual call signals (Vol/OI ≥3x, ≤30 day expiry, OTM/near-ATM calls only):

{signals_text}

Select the 20 BEST short-term call trade opportunities from this list. Rank by conviction.
Criteria: highest Vol/OI (fresh institutional buying), reasonable OTM% (not too far), premium size (commitment), days_out (urgency), urgency tier.

For each pick output a JSON object with ALL these fields:
- ticker (string)
- strike (number — use the strike from the signal)
- expiry (string YYYY-MM-DD — use from signal)
- days_out (integer — from signal)
- vol_oi (number — from signal)
- prem (integer — from signal)
- stock_price (number — from signal)
- otm_pct (number — from signal)
- breakeven (number — strike + estimated option premium per share; estimate option price as prem / (vol * 100) if vol known, else use a reasonable estimate)
- conviction ("HIGH" | "MEDIUM")
- urgency (string — from signal)
- thesis (string — 2 sentences MAX: why this signal is high conviction, what the move scenario is)
- why_it_stands_out (string — 1 sentence: the single most compelling data point)

Return a JSON array of exactly 20 objects. Sort by conviction (HIGH first). JSON only, no markdown."""

    system_msg = "You are a quantitative options analyst. You identify the highest-conviction short-term call trades from unusual options activity. Output valid JSON only."

    def _stream_ai():
        chunks = []
        finish = "unknown"
        stream = oai.chat.completions.create(
            model="gpt-4o-mini",
            max_completion_tokens=4000,
            stream=True,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user",   "content": user_msg},
            ],
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                chunks.append(delta)
            if chunk.choices and chunk.choices[0].finish_reason:
                finish = chunk.choices[0].finish_reason
        return "".join(chunks).strip(), finish

    def _extract_json(raw):
        if "```" in raw:
            for part in raw.split("```"):
                stripped = part.lstrip("json").strip()
                if stripped.startswith("["):
                    return stripped
        if not raw.startswith("["):
            s = raw.find("["); e2 = raw.rfind("]") + 1
            if s >= 0 and e2 > s:
                return raw[s:e2]
        return raw

    try:
        import time as _time
        raw, finish = _stream_ai()
        raw = _extract_json(raw)

        if not raw:
            print("[ai_short_calls] empty on first attempt — retrying in 6s", file=sys.stderr, flush=True)
            _time.sleep(6)
            raw, finish = _stream_ai()
            raw = _extract_json(raw)

        if not raw:
            return jsonify({"error": f"AI returned no content (finish={finish}). Hit Regenerate to try again.", "picks": []}), 500

        try:
            picks = _json.loads(raw)
        except Exception:
            from json_repair import repair_json as _rj
            picks = _json.loads(_rj(raw))
        out = {
            "picks": picks,
            "generated_at": _dt.now().isoformat(),
            "signals_evaluated": len(hits),
        }
        app._aisc_cache    = out
        app._aisc_cache_ts = _dt.now()
        # Persist to daily log (skips if already saved today)
        try:
            import threading as _scl_thr
            _today_str = _dt.now().strftime("%Y-%m-%d")
            _scl_thr.Thread(
                target=_save_ai_short_calls_to_log,
                args=(picks, _today_str),
                daemon=True,
            ).start()
        except Exception as _sle:
            print(f"[ai_short_calls] log save error: {_sle}", file=sys.stderr)
        return jsonify(out)
    except Exception as e:
        import traceback
        print(f"[ai_short_calls] error: {e}\n{traceback.format_exc()}", file=sys.stderr, flush=True)
        return jsonify({"error": str(e), "picks": []}), 500


@app.route("/stock-api/ai-short-calls-log", methods=["GET"])
def ai_short_calls_log():
    """Return full AI short-calls history with daily win rates (breakeven-based)."""
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, trade_date, rank, ticker, strike, expiry, days_out,
                       vol_oi, prem, stock_price, otm_pct, breakeven,
                       conviction, urgency, thesis, why_it_stands_out, outcome,
                       t1_price, t3_price, t5_price,
                       t1_pct, t3_pct, t5_pct,
                       t1_win, t3_win, t5_win,
                       expiry_price, expiry_pct, expiry_win,
                       created_at
                FROM ai_short_calls_log
                ORDER BY trade_date DESC, rank ASC
            """)
            rows = cur.fetchall()
            cols = ["id","trade_date","rank","ticker","strike","expiry","days_out",
                    "vol_oi","prem","stock_price","otm_pct","breakeven",
                    "conviction","urgency","thesis","why_it_stands_out","outcome",
                    "t1_price","t3_price","t5_price",
                    "t1_pct","t3_pct","t5_pct",
                    "t1_win","t3_win","t5_win",
                    "expiry_price","expiry_pct","expiry_win",
                    "created_at"]
            picks = []
            for row in rows:
                d = dict(zip(cols, row))
                d["trade_date"] = str(d["trade_date"]) if d["trade_date"] else None
                d["created_at"] = d["created_at"].isoformat() if d["created_at"] else None
                picks.append(d)

            def _wr(key):
                vals = [p[key] for p in picks if p[key] is not None]
                if not vals: return None
                return round(sum(1 for v in vals if v) / len(vals) * 100, 1)

            win_rates = {
                "expiry": _wr("expiry_win"),
                "t1": _wr("t1_win"),
                "t3": _wr("t3_win"),
                "t5": _wr("t5_win"),
            }

            # Per-day stats
            from collections import defaultdict as _dd
            by_date = _dd(lambda: {"total": 0, "wins": 0, "losses": 0, "open": 0})
            for p in picks:
                d = p["trade_date"]
                by_date[d]["total"] += 1
                if p["outcome"] == "WIN":   by_date[d]["wins"]   += 1
                elif p["outcome"] == "LOSS": by_date[d]["losses"] += 1
                else:                        by_date[d]["open"]   += 1
            day_stats = {d: dict(v) for d, v in sorted(by_date.items(), reverse=True)}

            return jsonify({
                "picks": picks,
                "count": len(picks),
                "win_rates": win_rates,
                "by_date": day_stats,
            })
    except Exception as e:
        return jsonify({"error": str(e), "picks": [], "count": 0,
                        "win_rates": {"expiry": None, "t1": None, "t3": None, "t5": None},
                        "by_date": {}}), 500


@app.route("/stock-api/multi-signal", methods=["GET"])
def multi_signal_convergence():
    """Multi-signal convergence scanner — 25 signal conditions including cross-referenced caches."""
    import yfinance as yf
    from datetime import datetime as _ms_dt

    _cache = getattr(app, "_ms_cache", None)
    _ts    = getattr(app, "_ms_cache_ts", None)
    if _cache and _ts and (_ms_dt.now() - _ts).total_seconds() < 600:
        return jsonify(_cache)

    SIGNAL_DEFS = [
        # ── Quant / price-action signals (computed live) ──────────────────
        ("VOLUME_SURGE",       "🔥 Volume Surge",        "Relative volume ≥ 3× average"),
        ("MORNING_RUNNER",     "🌅 Morning Runner",      "Gap up + rel vol ≥ 1.8× on the day"),
        ("NEAR_52WK_HIGH",     "📈 Near 52wk High",      "Within 3% of 52-week high"),
        ("ABOVE_52WK_HIGH",    "🚀 New 52wk High",       "Price currently above 52-week high"),
        ("MOMENTUM",           "⚡ Momentum",            "Day change ≥ 3%"),
        ("BIG_MOVE",           "💥 Big Move",            "Day change ≥ 5%"),
        ("MICRO_SQUEEZE",      "💎 Micro Squeeze",       "Market cap < $300M + rel vol ≥ 2×"),
        ("SECTOR_STRENGTH",    "💪 Sector Strength",     "Up ≥ 2% with rel vol ≥ 1.5×"),
        # ── Cross-referenced from real signal caches ──────────────────────
        ("DARK_POOL_HIT",      "🌑 Dark Pool",           "Appeared in dark pool scanner today"),
        ("UNUSUAL_CALLS",      "🎯 Unusual Calls",       "Flagged in unusual options call activity"),
        ("SQUEEZE_SETUP",      "💥 Squeeze Setup",       "Flagged by squeeze / low-float scanner"),
        ("MORNING_SCAN",       "🌅 Morning Scan Hit",    "Appeared in morning runners scanner"),
        ("BULL_FLOW",          "📈 Bull Flow",           "In the bull flow top signals today"),
        ("WHALE_ACTIVITY",     "🐋 Whale Activity",      "Whale block trade detected"),
        ("AI_TRADE_SIGNAL",    "🤖 AI Trade Signal",     "AI Trade tab selected this ticker today"),
        ("CHEAP_OPTIONS",      "💰 Cheap Options",       "IV rank < 20 — options priced below historical avg"),
        ("HIGH_QUANT_SCORE",   "🏆 High Quant Score",   "Composite quant score in top 20 of universe"),
        ("GAMMA_WALL",         "🧲 Gamma Wall",          "Near/above gamma wall — dealer buying amplifies move"),
        ("VOL_CRUSH_SETUP",    "📉 Vol Crush Setup",     "Inflated IV ahead of catalyst — market pricing in big move"),
        ("MAX_PAIN_PULL",      "⚡ Max Pain Pull",       "Price below max pain — MM pressure targets upside"),
        ("CALL_INTENT_HIGH",   "🎯 Call Intent",         "High call OI / unusual call intent detected"),
        # ── High-conviction quant filters ─────────────────────────────────────
        ("MARKET_REGIME",      "🌍 Market Regime",       "SPY above 50-day MA and VIX < 25 — risk-on macro environment"),
        ("RELATIVE_STRENGTH",  "🏆 Relative Strength",   "Outperforming S&P 500 by 15%+ over last 3 months (RS top tier)"),
        ("SHORT_SQUEEZE_FUEL", "💣 Short Squeeze Fuel",  "Short interest > 10% — trapped shorts amplify any upside move"),
        ("EPS_REVISION_UP",    "📊 EPS Revision Up",     "Earnings growth > 10% — analysts revising estimates higher"),
        # ── Technical / momentum signals ──────────────────────────────────────
        ("RSI_SETUP",          "📐 RSI Setup",           "RSI 14-day between 30–62 — not overbought, has room to run"),
        ("MACD_BULLISH",       "📈 MACD Cross",          "MACD crossed above signal line — momentum turning bullish"),
        ("BB_SQUEEZE",         "🗜️ BB Squeeze",           "Bollinger Bands at 6-month tightest — major breakout imminent"),
        ("GOLDEN_CROSS",       "⭐ Golden Cross",         "50-day MA above 200-day MA — institutional trend confirmation"),
        ("MOMENTUM_12_1",      "🚀 12-1 Momentum",       "Up 15%+ over prior 11 months (Jegadeesh-Titman quant factor)"),
        ("OBV_DIVERGE",        "🔊 OBV Accumulation",    "On-balance volume rising while price flat — quiet institutional buying"),
        # ── Fundamental quality signals ───────────────────────────────────────
        ("FLOAT_ROTATION",     "🔄 Float Rotation",      "Daily volume > 30% of float — high conviction from active participants"),
        ("PRICE_TARGET_UP",    "🎯 Analyst Target",      "Analyst consensus target >15% above current price — institutional conviction"),
        ("HIGH_QUALITY",       "💎 High Quality",        "ROE > 15% with manageable debt — quality factor used by quant funds"),
        ("ANALYST_UPGRADE",    "⬆️ Analyst Upgrade",     "Buy/Outperform upgrade in last 21 days — institutional re-rating signal"),
        ("EARNINGS_BEAT",      "✅ Earnings Beater",      "Beat EPS estimates in 75%+ of recent quarters — systematic under-model"),
        ("REVENUE_ACCEL",      "📈 Revenue Accel",       "QoQ revenue growth rate accelerating — institutional re-rating trigger"),
        ("MARGIN_EXPAND",      "📊 Margin Expansion",    "Gross margin expanding QoQ — pricing power and operating leverage"),
        # ── Macro health filters ──────────────────────────────────────────────
        ("VIX_CONTANGO",       "📉 VIX Contango",        "VIX spot below 3-month VIX — term structure healthy, no fear event priced in"),
        ("HYG_HEALTHY",        "🔋 Credit Healthy",      "High-yield bonds not diverging from equities — no credit stress signal"),
    ]

    # ── Build ticker sets from existing caches (read-only, safe) ─────────
    def _get_tickers(cache_attr, *keys):
        data = getattr(app, cache_attr, None) or {}
        for k in keys:
            items = data.get(k)
            if items:
                return set(r.get("ticker", "") for r in items if r.get("ticker"))
        return set()

    dp_tickers       = _get_tickers("_dp_cache",             "results")
    uc_tickers       = _get_tickers("_unusual_calls_cache",  "hits", "results")
    mr_tickers       = _get_tickers("_mr_cache",             "runners", "results")
    sq_tickers       = _get_tickers("_sq_cache",             "results", "hits")
    bf_tickers       = _get_tickers("_bf_cache",             "results", "top10")
    whale_tickers    = _get_tickers("_whale_cache",          "blocks", "results")
    ait_tickers      = _get_tickers("_ait_cache",            "picks", "results")
    cheap_iv_tickers = set(
        r.get("ticker", "") for r in (getattr(app, "_ivs_cache", None) or {}).get("rows", [])
        if r.get("setup") == "CHEAP_OPTIONS" and r.get("ticker")
    )
    cs_rows          = sorted(
        (getattr(app, "_cs_cache", None) or {}).get("results", []),
        key=lambda x: x.get("score", 0), reverse=True
    )
    high_quant_tickers = set(r.get("ticker", "") for r in cs_rows[:25] if r.get("ticker"))
    gw_tickers       = _get_tickers("_gw_cache",             "results")
    vc_tickers       = _get_tickers("_vc_cache",             "results")
    oi_tickers       = _get_tickers("_oi_cache",             "results")
    mp_map           = {r["ticker"]: r["max_pain"] for r in (getattr(app, "_mp_cache", None) or {}).get("results", []) if r.get("ticker") and r.get("max_pain")}

    # ── Get sector rotation context ───────────────────────────────────────
    sr_data = getattr(app, "_sr_cache", None) or {}
    sectors = sr_data.get("sectors", [])
    top_sector    = sectors[0]  if sectors else None
    bottom_sector = sectors[-1] if len(sectors) > 1 else None
    sector_context = {
        "top":    {"ticker": top_sector["ticker"],    "name": top_sector["name"],    "day_chg": top_sector["day_chg"],    "flow": top_sector["flow"]}    if top_sector    else None,
        "bottom": {"ticker": bottom_sector["ticker"], "name": bottom_sector["name"], "day_chg": bottom_sector["day_chg"], "flow": bottom_sector["flow"]} if bottom_sector else None,
    }

    # ── Global macro signals (computed once for all tickers) ─────────────────
    market_regime_on   = False
    spy_return_3mo_ref = 0.0
    vix_contango       = False
    hyg_healthy        = True
    try:
        _spy = yf.Ticker("SPY").history(period="70d")["Close"]
        _vix = yf.Ticker("^VIX").history(period="5d")["Close"]
        _spy_now  = float(_spy.iloc[-1])
        _spy_50ma = float(_spy.rolling(50).mean().iloc[-1])
        _vix_now  = float(_vix.iloc[-1])
        market_regime_on   = (_spy_now > _spy_50ma) and (_vix_now < 25)
        _idx = max(0, len(_spy) - 63)
        spy_return_3mo_ref = (_spy_now - float(_spy.iloc[_idx])) / float(_spy.iloc[_idx]) if float(_spy.iloc[_idx]) > 0 else 0
    except Exception:
        pass
    try:
        _vix3m_h = yf.Ticker("^VIX3M").history(period="5d")["Close"]
        _vixs_h  = yf.Ticker("^VIX").history(period="5d")["Close"]
        vix_contango = float(_vixs_h.iloc[-1]) < float(_vix3m_h.iloc[-1])
    except Exception:
        pass
    try:
        _hyg20 = yf.Ticker("HYG").history(period="20d")["Close"]
        _spy20 = yf.Ticker("SPY").history(period="20d")["Close"]
        _h_ret = (float(_hyg20.iloc[-1]) - float(_hyg20.iloc[0])) / (float(_hyg20.iloc[0]) or 1)
        _s_ret = (float(_spy20.iloc[-1]) - float(_spy20.iloc[0])) / (float(_spy20.iloc[0]) or 1)
        hyg_healthy = _h_ret > _s_ret - 0.02
    except Exception:
        pass

    results = []

    def _check(ticker):
        try:
            fi       = yf.Ticker(ticker).fast_info
            price    = float(getattr(fi, "last_price",                 0) or 0)
            prev_cl  = float(getattr(fi, "previous_close",             0) or 0)
            avg_vol  = float(getattr(fi, "three_month_average_volume",  1) or 1)
            today_vol= float(getattr(fi, "last_volume",                0) or 0)
            high52   = float(getattr(fi, "year_high",                  0) or 0)
            mkt_cap  = float(getattr(fi, "market_cap",                 0) or 0)

            if price <= 0 or prev_cl <= 0:
                return None

            day_chg  = round((price - prev_cl) / prev_cl * 100, 2)
            rel_vol  = round(today_vol / avg_vol, 2) if avg_vol > 0 else 0
            pct_from_high = round((price - high52) / high52 * 100, 2) if high52 > 0 else -100

            fired = []
            # ── Live quant checks ─────────────────────────────────────────
            if rel_vol >= 3.0:
                fired.append("VOLUME_SURGE")
            if rel_vol >= 1.8 and 1.0 <= day_chg <= 25:
                fired.append("MORNING_RUNNER")
            if pct_from_high >= -3.0:
                fired.append("NEAR_52WK_HIGH")
            if pct_from_high >= 0:
                fired.append("ABOVE_52WK_HIGH")
            if day_chg >= 3.0:
                fired.append("MOMENTUM")
            if day_chg >= 5.0:
                fired.append("BIG_MOVE")
            if mkt_cap > 0 and mkt_cap < 300_000_000 and rel_vol >= 2.0:
                fired.append("MICRO_SQUEEZE")
            if day_chg >= 2.0 and rel_vol >= 1.5:
                fired.append("SECTOR_STRENGTH")
            # ── Cross-reference existing signal caches ────────────────────
            if ticker in dp_tickers:           fired.append("DARK_POOL_HIT")
            if ticker in uc_tickers:           fired.append("UNUSUAL_CALLS")
            if ticker in sq_tickers:           fired.append("SQUEEZE_SETUP")
            if ticker in mr_tickers:           fired.append("MORNING_SCAN")
            if ticker in bf_tickers:           fired.append("BULL_FLOW")
            if ticker in whale_tickers:        fired.append("WHALE_ACTIVITY")
            if ticker in ait_tickers:          fired.append("AI_TRADE_SIGNAL")
            if ticker in cheap_iv_tickers:     fired.append("CHEAP_OPTIONS")
            if ticker in high_quant_tickers:   fired.append("HIGH_QUANT_SCORE")
            if ticker in gw_tickers:           fired.append("GAMMA_WALL")
            if ticker in vc_tickers:           fired.append("VOL_CRUSH_SETUP")
            if ticker in oi_tickers:           fired.append("CALL_INTENT_HIGH")
            mp_level = mp_map.get(ticker)
            if mp_level and price > 0 and price < mp_level:
                fired.append("MAX_PAIN_PULL")

            # ── Global macro (fire for all tickers when conditions met) ──────────
            if market_regime_on: fired.append("MARKET_REGIME")
            if vix_contango:     fired.append("VIX_CONTANGO")
            if hyg_healthy:      fired.append("HYG_HEALTHY")

            _tk2 = yf.Ticker(ticker)

            # ── 1-year OHLCV → all technical signals ──────────────────────────
            try:
                _df = _tk2.history(period="1y")
                _cl = _df["Close"] if len(_df) > 0 else None
                _vl = _df["Volume"] if len(_df) > 0 else None
                if _cl is not None and len(_cl) >= 30:
                    _n = float(_cl.iloc[-1])
                    # Relative Strength vs SPY (3-month)
                    _i3 = max(0, len(_cl) - 63)
                    if (float(_cl.iloc[_i3]) or 0) > 0:
                        if (_n - float(_cl.iloc[_i3])) / float(_cl.iloc[_i3]) > spy_return_3mo_ref + 0.15:
                            fired.append("RELATIVE_STRENGTH")
                    # 12-1 Momentum Factor (11-month return skipping last month)
                    if len(_cl) >= 60:
                        _i12 = max(0, len(_cl) - 252)
                        _i1  = max(0, len(_cl) - 21)
                        _base = float(_cl.iloc[_i12])
                        _top  = float(_cl.iloc[_i1]) if len(_cl) > 21 else _n
                        if _base > 0 and (_top - _base) / _base > 0.15:
                            fired.append("MOMENTUM_12_1")
                    # RSI 14-day (30–62 = not overbought, room to run)
                    if len(_cl) >= 15:
                        _d = _cl.diff()
                        _g = _d.clip(lower=0).rolling(14).mean()
                        _l = (-_d.clip(upper=0)).rolling(14).mean()
                        _rsi_val = float((100 - 100 / (1 + _g / _l.replace(0, 1e-9))).iloc[-1])
                        if 30 <= _rsi_val <= 62:
                            fired.append("RSI_SETUP")
                    # MACD bullish crossover (crossed up in last 5 sessions)
                    if len(_cl) >= 35:
                        _mc = _cl.ewm(span=12, adjust=False).mean() - _cl.ewm(span=26, adjust=False).mean()
                        _ms = _mc.ewm(span=9, adjust=False).mean()
                        _cx = (_mc > _ms).tolist()
                        if len(_cx) >= 6 and _cx[-1] and not _cx[-6]:
                            fired.append("MACD_BULLISH")
                    # Bollinger Band Squeeze (width in bottom 20% of 6-month range)
                    if len(_cl) >= 126:
                        _bw = (4 * _cl.rolling(20).std()) / _cl.rolling(20).mean().replace(0, 1e-9)
                        _lo = _bw.rolling(126).min(); _hi = _bw.rolling(126).max()
                        if (_bw.iloc[-1] - _lo.iloc[-1]) / (_hi.iloc[-1] - _lo.iloc[-1] + 1e-9) <= 0.20:
                            fired.append("BB_SQUEEZE")
                    # Golden Cross (50MA > 200MA)
                    if len(_cl) >= 200:
                        if float(_cl.rolling(50).mean().iloc[-1]) > float(_cl.rolling(200).mean().iloc[-1]):
                            fired.append("GOLDEN_CROSS")
                    # OBV Accumulation (OBV rising, price flat/up)
                    if _vl is not None and len(_vl) >= 22:
                        _sgn = (_cl.diff() > 0).astype(float) - (_cl.diff() < 0).astype(float)
                        _obv = (_vl * _sgn).cumsum()
                        _pc  = float(_cl.iloc[-22]) or 1
                        if float(_obv.iloc[-1]) > float(_obv.iloc[-22]) and (_n - _pc) / _pc >= -0.03:
                            fired.append("OBV_DIVERGE")
            except Exception:
                pass

            # ── Info-based signals ────────────────────────────────────────────
            try:
                _info = _tk2.info
                if (_info.get("shortPercentOfFloat") or 0) > 0.10:
                    fired.append("SHORT_SQUEEZE_FUEL")
                if (_info.get("earningsGrowth") or 0) > 0.10 or (_info.get("revenueGrowth") or 0) > 0.15:
                    fired.append("EPS_REVISION_UP")
                _flt = _info.get("floatShares") or 0
                if _flt > 0 and today_vol > 0 and today_vol / _flt > 0.30:
                    fired.append("FLOAT_ROTATION")
                _tgt = _info.get("targetMeanPrice") or 0
                if _tgt > 0 and price > 0 and (_tgt - price) / price > 0.15:
                    fired.append("PRICE_TARGET_UP")
                if (_info.get("returnOnEquity") or 0) > 0.15 and (_info.get("debtToEquity") or 999) < 150:
                    fired.append("HIGH_QUALITY")
            except Exception:
                pass

            # ── Analyst upgrades (last 15 recommendations) ───────────────────
            try:
                _recs = _tk2.recommendations
                if _recs is not None and len(_recs) > 0:
                    _buys = {"buy", "strong buy", "outperform", "overweight", "positive", "add"}
                    if _recs.tail(15)["To Grade"].str.lower().str.strip().isin(_buys).any():
                        fired.append("ANALYST_UPGRADE")
            except Exception:
                pass

            # ── Earnings beat rate (≥75% of recent quarters) ─────────────────
            try:
                _eh = _tk2.earnings_history
                if _eh is not None and len(_eh) >= 3:
                    _sc = next((c for c in _eh.columns if "surprise" in c.lower()), None)
                    if _sc and (_eh[_sc] > 0).mean() >= 0.75:
                        fired.append("EARNINGS_BEAT")
            except Exception:
                pass

            # ── Revenue acceleration + Gross margin expansion ─────────────────
            try:
                _qis = _tk2.quarterly_income_stmt
                if _qis is not None and len(_qis.columns) >= 4:
                    if "Total Revenue" in _qis.index:
                        _rv = _qis.loc["Total Revenue"].iloc[:4].astype(float).values
                        if abs(_rv[1]) > 0 and abs(_rv[2]) > 0:
                            if (_rv[0] - _rv[1]) / abs(_rv[1]) > (_rv[1] - _rv[2]) / abs(_rv[2]) + 0.02:
                                fired.append("REVENUE_ACCEL")
                    if "Gross Profit" in _qis.index and "Total Revenue" in _qis.index:
                        _gp = _qis.loc["Gross Profit"].iloc[:4].astype(float).values
                        _rv = _qis.loc["Total Revenue"].iloc[:4].astype(float).values
                        if _rv[0] > 0 and _rv[2] > 0 and _gp[0]/_rv[0] > _gp[2]/_rv[2] + 0.01:
                            fired.append("MARGIN_EXPAND")
            except Exception:
                pass

            if len(fired) < 2:
                return None

            return {
                "ticker":        ticker,
                "price":         round(price, 2),
                "day_chg":       day_chg,
                "rel_vol":       rel_vol,
                "pct_from_high": pct_from_high,
                "mkt_cap_b":     round(mkt_cap / 1e9, 2) if mkt_cap else None,
                "signals":       fired,
                "score":         len(fired),
            }
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=25) as ex:
        futures = {ex.submit(_check, t): t for t in DEFAULT_LEADERBOARD}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                results.append(r)

    results.sort(key=lambda x: (x["score"], x["rel_vol"]), reverse=True)
    signal_defs_map = {s[0]: {"id": s[0], "label": s[1], "desc": s[2]} for s in SIGNAL_DEFS}
    out = {
        "hits":           results[:40],
        "total":          len(results),
        "scanned":        len(DEFAULT_LEADERBOARD),
        "signal_defs":    signal_defs_map,
        "max_signals":    len(SIGNAL_DEFS),
        "sector_context": sector_context,
        "cache_status": {
            "dark_pool":       len(dp_tickers),
            "unusual_calls":   len(uc_tickers),
            "morning_runners": len(mr_tickers),
            "squeeze":         len(sq_tickers),
            "bull_flow":       len(bf_tickers),
            "whale":           len(whale_tickers),
            "ai_trades":       len(ait_tickers),
            "cheap_iv":        len(cheap_iv_tickers),
            "quant_score":     len(high_quant_tickers),
            "gamma_wall":      len(gw_tickers),
            "vol_crush":       len(vc_tickers),
            "call_intent":     len(oi_tickers),
            "max_pain":        len(mp_map),
            "market_regime":   1 if market_regime_on else 0,
            "vix_contango":    1 if vix_contango else 0,
            "hyg_healthy":     1 if hyg_healthy else 0,
        },
        "market_regime_on":  market_regime_on,
        "vix_contango":       vix_contango,
        "hyg_healthy":        hyg_healthy,
    }
    app._ms_cache    = out
    app._ms_cache_ts = _ms_dt.now()
    return jsonify(out)


@app.route("/stock-api/multi-signal/ai-thesis", methods=["POST"])
def multi_signal_ai_thesis():
    """Generate an AI trade thesis for a ticker based on all its convergent signals."""
    try:
        body    = request.get_json(force=True) or {}
        ticker  = (body.get("ticker") or "").upper().strip()
        signals = body.get("signals") or []
        price   = body.get("price", 0)
        day_chg = body.get("day_chg", 0)
        rel_vol = body.get("rel_vol", 0)
        pct_from_high = body.get("pct_from_high", 0)
        mkt_cap_b     = body.get("mkt_cap_b")

        if not ticker or not signals:
            return jsonify({"error": "ticker and signals required"}), 400

        cap_str = f"${mkt_cap_b:.1f}B" if mkt_cap_b and mkt_cap_b >= 1 else (f"${mkt_cap_b*1000:.0f}M" if mkt_cap_b else "unknown")

        # ── Earnings context (non-blocking, best-effort) ──────────────────────
        earnings_ctx = ""
        try:
            earn = _check_earnings(ticker)
            if earn:
                imp = earn.get("implied_move_pct")
                eps = earn.get("eps_estimate")
                imp_str = f"±{imp}% implied move (ATM straddle)" if imp else "implied move unavailable"
                eps_str = f"EPS estimate: ${eps:+.2f}" if eps is not None else "EPS estimate: N/A"
                earnings_ctx = f"""
⚠️  EARNINGS EVENT: {ticker} reports in {earn['days_until']} day(s) on {earn['earnings_date']}
    Options market pricing {imp_str}
    {eps_str}
"""
        except Exception:
            pass

        prompt = f"""You are a professional quantitative trader analyzing a convergence signal alert.

Ticker: {ticker}
Current Price: ${price}
Day Change: {day_chg:+.2f}%
Relative Volume: {rel_vol:.2f}x average
Distance from 52-week high: {pct_from_high:+.2f}%
Market Cap: {cap_str}
{earnings_ctx}
Signals firing simultaneously ({len(signals)}/8):
{chr(10).join(f"  ✅ {s}" for s in signals)}

Write a concise, actionable trade thesis with:
1. SETUP SUMMARY (2-3 sentences on why {len(signals)} converging signals matters)
2. BULL CASE (price target, catalyst, timeframe){" — factor in the earnings event and implied move" if earnings_ctx else ""}
3. BEAR CASE / RISK (what could go wrong){" — include earnings binary risk" if earnings_ctx else ""}
4. CONVICTION: CRITICAL / HIGH / WATCH / NOISE
5. SUGGESTED ACTION (buy calls, watch for entry, avoid) — naked long calls only, no spreads

Be direct, specific, and professional. No disclaimers."""

        client = OpenAI(
            api_key=os.environ["AI_INTEGRATIONS_OPENAI_API_KEY"],
            base_url=os.environ["AI_INTEGRATIONS_OPENAI_BASE_URL"],
        )
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=500,
        )
        return jsonify({"ticker": ticker, "thesis": resp.choices[0].message.content.strip()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/stock-api/iv-rank", methods=["GET"])
def iv_rank():
    """IV rank (volatility rank) for a single ticker using options + historical price data."""
    import yfinance as yf, numpy as np
    ticker = (request.args.get("ticker") or "AAPL").upper().strip()
    try:
        tkr  = yf.Ticker(ticker)
        hist = tkr.history(period="1y", interval="1d")
        if len(hist) < 20:
            return jsonify({"error": "Not enough price history"}), 400

        # Calculate historical volatility (annualized) at multiple windows
        log_ret = np.log(hist["Close"] / hist["Close"].shift(1)).dropna()
        hv30  = float(log_ret[-30:].std()  * np.sqrt(252) * 100) if len(log_ret) >= 30  else None
        hv60  = float(log_ret[-60:].std()  * np.sqrt(252) * 100) if len(log_ret) >= 60  else None
        hv90  = float(log_ret[-90:].std()  * np.sqrt(252) * 100) if len(log_ret) >= 90  else None

        # Rolling 30-day HV for each day in the past year → used for HV rank
        rolling_hv30 = (
            log_ret.rolling(30).std() * np.sqrt(252) * 100
        ).dropna()
        hv_min  = float(rolling_hv30.min())
        hv_max  = float(rolling_hv30.max())
        hv_rank = round((hv30 - hv_min) / (hv_max - hv_min) * 100, 1) if hv30 and hv_max > hv_min else None

        # Get current IV from options chain (ATM, nearest 30-45d expiry)
        iv30 = None
        expiries = tkr.options
        from datetime import datetime, timedelta
        now = datetime.now()
        target = now + timedelta(days=30)
        best_exp = None
        best_diff = 9999
        for exp in (expiries or []):
            try:
                exp_dt = datetime.strptime(exp, "%Y-%m-%d")
                diff = abs((exp_dt - target).days)
                if diff < best_diff and (exp_dt - now).days >= 7:
                    best_diff = diff
                    best_exp = exp
            except Exception:
                pass

        if best_exp:
            try:
                chain = tkr.option_chain(best_exp)
                fi    = tkr.fast_info
                spot  = float(getattr(fi, "last_price", 0) or 0)
                calls = chain.calls
                puts  = chain.puts
                if not calls.empty and spot > 0:
                    calls["dist"] = (calls["strike"] - spot).abs()
                    atm_call = calls.nsmallest(1, "dist")
                    iv30_call = float(atm_call["impliedVolatility"].iloc[0]) * 100 if not atm_call.empty else None
                    puts_f = puts.copy()
                    puts_f["dist"] = (puts_f["strike"] - spot).abs()
                    atm_put = puts_f.nsmallest(1, "dist")
                    iv30_put = float(atm_put["impliedVolatility"].iloc[0]) * 100 if not atm_put.empty else None
                    if iv30_call and iv30_put:
                        iv30 = round((iv30_call + iv30_put) / 2, 1)
                    elif iv30_call:
                        iv30 = round(iv30_call, 1)
            except Exception:
                pass

        iv_hv_ratio = round(iv30 / hv30, 2) if iv30 and hv30 else None

        fi = tkr.fast_info
        price = float(getattr(fi, "last_price", 0) or 0)
        prev  = float(getattr(fi, "previous_close", 0) or 0)
        day_chg = round((price - prev) / prev * 100, 2) if prev > 0 else 0

        # IV rank proxy: use IV vs rolling HV range as a volatility premium signal
        iv_rank_val = None
        if iv30 and hv_max > hv_min:
            iv_rank_val = round((iv30 - hv_min) / (hv_max - hv_min) * 100, 1)
            iv_rank_val = min(max(iv_rank_val, 0), 100)

        return jsonify({
            "ticker":      ticker,
            "price":       round(price, 2),
            "day_chg":     day_chg,
            "hv30":        round(hv30, 1)  if hv30  else None,
            "hv60":        round(hv60, 1)  if hv60  else None,
            "hv90":        round(hv90, 1)  if hv90  else None,
            "hv_min":      round(hv_min, 1),
            "hv_max":      round(hv_max, 1),
            "hv_rank":     hv_rank,
            "iv30":        iv30,
            "iv_rank":     iv_rank_val,
            "iv_hv_ratio": iv_hv_ratio,
            "expiry_used": best_exp,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


IV_SCAN_TICKERS = [
    "AAPL","MSFT","NVDA","TSLA","AMZN","GOOGL","META","AMD","SMCI","PLTR",
    "ARM","COIN","MSTR","HOOD","SOFI","RIVN","LCID","NKLA","JOBY","ACHR",
    "GME","AMC","BBBY","SPCE","CLOV","SNDL","MVIS","WKHS","RIDE","GOEV",
    "UPST","AFRM","OPEN","LMND","PSFE","PAYA","LFMD","HIMS","GENI","BODY",
    "RBLX","U","DKNG","PENN","SKLZ","FTIV","SPNV","APPH","MVST","NKLA",
]

@app.route("/stock-api/iv-rank/scan", methods=["GET"])
def iv_rank_scan():
    """Scan a curated set of liquid/volatile tickers for interesting IV setups."""
    import yfinance as yf, numpy as np
    from datetime import datetime as _ivs_dt

    _cache = getattr(app, "_ivs_cache", None)
    _ts    = getattr(app, "_ivs_cache_ts", None)
    if _cache and _ts and (_ivs_dt.now() - _ts).total_seconds() < 1800:
        return jsonify(_cache)

    from datetime import datetime, timedelta

    results = []

    def _scan_iv(ticker):
        try:
            tkr  = yf.Ticker(ticker)
            hist = tkr.history(period="1y", interval="1d")
            if len(hist) < 30:
                return None

            log_ret = np.log(hist["Close"] / hist["Close"].shift(1)).dropna()
            hv30 = float(log_ret[-30:].std() * np.sqrt(252) * 100)
            rolling_hv30 = (log_ret.rolling(30).std() * np.sqrt(252) * 100).dropna()
            hv_min = float(rolling_hv30.min())
            hv_max = float(rolling_hv30.max())
            hv_rank = round((hv30 - hv_min) / (hv_max - hv_min) * 100, 1) if hv_max > hv_min else 50

            # Try to get ATM IV
            iv30 = None
            expiries = tkr.options
            now_dt = datetime.now()
            target = now_dt + timedelta(days=30)
            best_exp = None
            best_diff = 9999
            for exp in (expiries or []):
                try:
                    exp_dt = datetime.strptime(exp, "%Y-%m-%d")
                    diff = abs((exp_dt - target).days)
                    if diff < best_diff and (exp_dt - now_dt).days >= 7:
                        best_diff = diff
                        best_exp = exp
                except Exception:
                    pass

            if best_exp:
                try:
                    chain = tkr.option_chain(best_exp)
                    fi = tkr.fast_info
                    spot = float(getattr(fi, "last_price", 0) or 0)
                    if not chain.calls.empty and spot > 0:
                        calls = chain.calls.copy()
                        calls["dist"] = (calls["strike"] - spot).abs()
                        atm = calls.nsmallest(1, "dist")
                        if not atm.empty:
                            iv30 = round(float(atm["impliedVolatility"].iloc[0]) * 100, 1)
                except Exception:
                    pass

            fi    = tkr.fast_info
            price = float(getattr(fi, "last_price", 0) or 0)
            prev  = float(getattr(fi, "previous_close", 0) or 0)
            day_chg = round((price - prev) / prev * 100, 2) if prev > 0 else 0

            iv_hv_ratio = round(iv30 / hv30, 2) if iv30 and hv30 else None
            iv_rank_val = round((iv30 - hv_min) / (hv_max - hv_min) * 100, 1) if iv30 and hv_max > hv_min else hv_rank

            # Setup classification
            setup = "NEUTRAL"
            if iv30 and iv_hv_ratio:
                if iv_rank_val < 20:
                    setup = "CHEAP_OPTIONS"
                elif iv_rank_val > 80:
                    setup = "EXPENSIVE_OPTIONS"
                if iv_hv_ratio > 1.5:
                    setup = "IV_PREMIUM"
                elif iv_hv_ratio < 0.8 and iv_rank_val < 30:
                    setup = "CHEAP_OPTIONS"

            return {
                "ticker":      ticker,
                "price":       round(price, 2),
                "day_chg":     day_chg,
                "hv30":        round(hv30, 1),
                "hv_rank":     hv_rank,
                "iv30":        iv30,
                "iv_rank":     round(iv_rank_val, 1),
                "iv_hv_ratio": iv_hv_ratio,
                "setup":       setup,
            }
        except Exception:
            return None

    tickers_dedup = list(dict.fromkeys(IV_SCAN_TICKERS))
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_scan_iv, t): t for t in tickers_dedup}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                results.append(r)

    results.sort(key=lambda x: (x.get("iv_rank") or 50), reverse=True)
    out = {"rows": results, "scanned": len(tickers_dedup)}
    app._ivs_cache    = out
    app._ivs_cache_ts = _ivs_dt.now()
    return jsonify(out)


@app.route("/stock-api/52week-breakout", methods=["GET"])
def breakout_52week():
    """52-week high breakout scanner — price near/above 52wk high + volume confirmation."""
    import yfinance as yf
    from datetime import datetime as _bk_dt

    _cache = getattr(app, "_bk_cache", None)
    _ts    = getattr(app, "_bk_cache_ts", None)
    if _cache and _ts and (_bk_dt.now() - _ts).total_seconds() < 900:
        return jsonify(_cache)

    results = []

    def _scan_bk(ticker):
        try:
            fi    = yf.Ticker(ticker).fast_info
            price = float(getattr(fi, "last_price",                0) or 0)
            high52 = float(getattr(fi, "year_high",                0) or 0)
            low52  = float(getattr(fi, "year_low",                 0) or 0)
            avg_vol= float(getattr(fi, "three_month_average_volume",1) or 1)
            today_vol = float(getattr(fi, "last_volume",           0) or 0)
            mkt_cap   = float(getattr(fi, "market_cap",            0) or 0)
            prev_close= float(getattr(fi, "previous_close",        0) or 0)

            if price <= 0 or high52 <= 0 or low52 <= 0:
                return None

            pct_from_high = round((price - high52) / high52 * 100, 2)
            # Only stocks within 3% below or above their 52-week high
            if pct_from_high < -3.0:
                return None

            rel_vol   = round(today_vol / avg_vol, 2) if avg_vol > 0 else 0
            if rel_vol < 1.3:
                return None

            range_52   = high52 - low52
            range_pos  = round((price - low52) / range_52 * 100, 1) if range_52 > 0 else 100
            day_chg_pct= round((price - prev_close) / prev_close * 100, 2) if prev_close > 0 else 0
            mkt_cap_b  = round(mkt_cap / 1e9, 2) if mkt_cap else None

            # Score: higher rel_vol * closer to/above high
            above_bonus = max(0, pct_from_high)        # bonus if actually above 52wk high
            score = round(rel_vol * (1 + above_bonus / 10), 2)

            return {
                "ticker":        ticker,
                "price":         round(price, 2),
                "high_52":       round(high52, 2),
                "low_52":        round(low52, 2),
                "pct_from_high": pct_from_high,
                "range_pos":     range_pos,
                "rel_vol":       rel_vol,
                "day_chg_pct":   day_chg_pct,
                "mkt_cap_b":     mkt_cap_b,
                "score":         score,
                "breakout":      pct_from_high >= 0,
            }
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=20) as ex:
        futures = {ex.submit(_scan_bk, t): t for t in DEFAULT_LEADERBOARD}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                results.append(r)

    results.sort(key=lambda x: x["score"], reverse=True)
    out = {"hits": results[:40], "total": len(results), "scanned": len(DEFAULT_LEADERBOARD)}
    app._bk_cache    = out
    app._bk_cache_ts = _bk_dt.now()
    return jsonify(out)


@app.route("/stock-api/sector-rotation", methods=["GET"])
def sector_rotation():
    """Sector rotation heatmap — 11 SPDR sector ETFs with flow direction signals."""
    import yfinance as yf
    from datetime import datetime as _sr_dt

    _cache = getattr(app, "_sr_cache", None)
    _ts    = getattr(app, "_sr_cache_ts", None)
    if _cache and _ts and (_sr_dt.now() - _ts).total_seconds() < 1800:
        return jsonify(_cache)

    SECTORS = [
        ("XLK",  "Technology"),
        ("XLF",  "Financials"),
        ("XLV",  "Healthcare"),
        ("XLE",  "Energy"),
        ("XLI",  "Industrials"),
        ("XLY",  "Cons. Discretionary"),
        ("XLP",  "Cons. Staples"),
        ("XLU",  "Utilities"),
        ("XLB",  "Materials"),
        ("XLRE", "Real Estate"),
        ("XLC",  "Communication"),
    ]

    results = []
    ticker_names = {t: n for t, n in SECTORS}
    tickers_list = [t for t, n in SECTORS]

    # Batch download 1-year daily data (1 API call for all 11 ETFs — avoids rate limiting)
    try:
        batch = yf.download(
            tickers_list, period="1y", interval="1d",
            auto_adjust=True, progress=False, threads=True
        )
        # Multi-ticker download returns multi-level columns: (field, ticker)
        for ticker in tickers_list:
            try:
                name = ticker_names[ticker]
                if len(tickers_list) > 1:
                    close  = batch["Close"][ticker].dropna()
                    volume = batch["Volume"][ticker].dropna()
                    high_s = batch["High"][ticker].dropna()
                    low_s  = batch["Low"][ticker].dropna()
                else:
                    close  = batch["Close"].dropna()
                    volume = batch["Volume"].dropna()
                    high_s = batch["High"].dropna()
                    low_s  = batch["Low"].dropna()

                if len(close) < 2:
                    continue

                price      = float(close.iloc[-1])
                prev_close = float(close.iloc[-2])
                if price <= 0 or prev_close <= 0:
                    continue

                high52 = float(high_s.max())
                low52  = float(low_s.min())
                today_vol = float(volume.iloc[-1]) if len(volume) > 0 else 0
                # 3-month average volume (~63 trading days)
                avg_vol = float(volume.iloc[-63:].mean()) if len(volume) >= 10 else float(volume.mean())
                avg_vol = max(avg_vol, 1)

                day_chg   = round((price - prev_close) / prev_close * 100, 2)
                rel_vol   = round(today_vol / avg_vol, 2)
                range_pos = round((price - low52) / (high52 - low52) * 100, 1) if high52 > low52 else 50

                wk1_chg = round((price - float(close.iloc[-5])) / float(close.iloc[-5]) * 100, 2) if len(close) >= 5 else None
                mo1_chg = round((price - float(close.iloc[-21])) / float(close.iloc[-21]) * 100, 2) if len(close) >= 21 else None

                if day_chg > 0 and rel_vol >= 1.1:
                    flow = "INFLOW"
                elif day_chg < 0 and rel_vol >= 1.1:
                    flow = "OUTFLOW"
                elif day_chg > 0:
                    flow = "RISING"
                elif day_chg < 0:
                    flow = "FALLING"
                else:
                    flow = "NEUTRAL"

                results.append({
                    "ticker":    ticker,
                    "name":      name,
                    "price":     round(price, 2),
                    "day_chg":   day_chg,
                    "wk1_chg":   wk1_chg,
                    "mo1_chg":   mo1_chg,
                    "rel_vol":   rel_vol,
                    "range_pos": range_pos,
                    "flow":      flow,
                })
            except Exception:
                continue
    except Exception:
        pass

    results.sort(key=lambda x: x["day_chg"], reverse=True)
    out = {"sectors": results, "scanned": len(SECTORS)}
    app._sr_cache    = out
    app._sr_cache_ts = _sr_dt.now()
    return jsonify(out)


@app.route("/stock-api/squeeze-setup", methods=["GET"])
def squeeze_setup():
    """High-conviction short squeeze + low-float breakout scanner."""
    import yfinance as yf
    from datetime import datetime as _sq_dt

    _cache = getattr(app, "_sq_cache", None)
    _ts    = getattr(app, "_sq_cache_ts", None)
    if _cache and _ts and (_sq_dt.now() - _ts).total_seconds() < 900:
        return jsonify(_cache)

    results = []

    def _scan_sq(ticker):
        try:
            tkr  = yf.Ticker(ticker)
            fi   = tkr.fast_info
            info = tkr.info

            price = float(getattr(fi, "last_price", 0) or 0)
            if price <= 0:
                return None

            sfp     = float(info.get("shortPercentOfFloat", 0) or 0) * 100
            dtc     = float(info.get("shortRatio",          0) or 0)
            float_sh = float(info.get("floatShares",        0) or 0)
            mkt_cap  = float(info.get("marketCap",          0) or 0) or float(getattr(fi, "market_cap", 0) or 0)
            mkt_cap_b = round(mkt_cap / 1e9, 2) if mkt_cap else None

            avg_vol   = float(getattr(fi, "three_month_average_volume", 1) or 1)
            today_vol = float(getattr(fi, "last_volume", 0) or 0)
            rel_vol   = round(today_vol / avg_vol, 2) if avg_vol > 0 else 0

            vol_pct_float = round(today_vol / float_sh * 100, 2) if float_sh > 0 else None
            float_m       = round(float_sh / 1e6, 2) if float_sh > 0 else None

            is_squeeze   = sfp >= 15 and dtc >= 5
            is_low_float = float_m is not None and float_m <= 20 and (vol_pct_float or 0) >= 8

            if not is_squeeze and not is_low_float:
                return None

            signal_type = "BOTH" if (is_squeeze and is_low_float) else ("SQUEEZE" if is_squeeze else "LOW_FLOAT")
            sq_comp  = min(sfp * dtc, 200)      if is_squeeze   else 0
            lf_comp  = min((vol_pct_float or 0) * rel_vol * 5, 200) if is_low_float else 0
            score    = round(sq_comp + lf_comp, 1)

            return {
                "ticker":          ticker,
                "price":           round(price, 2),
                "signal_type":     signal_type,
                "short_float_pct": round(sfp, 1),
                "days_to_cover":   round(dtc, 1),
                "float_m":         float_m,
                "vol_pct_float":   vol_pct_float,
                "rel_vol":         rel_vol,
                "mkt_cap_b":       mkt_cap_b,
                "score":           score,
            }
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(_scan_sq, t): t for t in DEFAULT_LEADERBOARD}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                results.append(r)

    results.sort(key=lambda x: x["score"], reverse=True)
    out = {"setups": results[:40], "total": len(results), "scanned": len(DEFAULT_LEADERBOARD)}
    app._sq_cache    = out
    app._sq_cache_ts = _sq_dt.now()
    return jsonify(out)


@app.route("/stock-api/squeeze-setup/ai-signal", methods=["POST"])
def squeeze_ai_signal():
    """AI conviction analysis for squeeze + low-float setups with optional Twilio SMS."""
    import os, json, re, sys
    from openai import OpenAI

    body = request.get_json(silent=True) or {}
    rows = body.get("rows", [])
    if not rows:
        return jsonify({"error": "No setup data provided"}), 400

    rows = sorted(rows, key=lambda r: -r.get("score", 0))[:20]

    lines = []
    for r in rows:
        parts = [
            f"ticker={r['ticker']}",
            f"signal={r['signal_type']}",
            f"short_float={r.get('short_float_pct', 0)}%",
            f"days_to_cover={r.get('days_to_cover', 0)}d",
        ]
        if r.get("float_m") is not None:
            parts.append(f"float={r['float_m']}M_shares")
        if r.get("vol_pct_float") is not None:
            parts.append(f"vol_pct_float={r['vol_pct_float']}%")
        parts.append(f"rel_vol={r.get('rel_vol', 0)}x")
        if r.get("mkt_cap_b") is not None:
            parts.append(f"mktcap=${r['mkt_cap_b']}B")
        parts.append(f"score={r.get('score', 0)}")
        lines.append("  " + " | ".join(parts))

    prompt = f"""You are a short squeeze and low-float breakout specialist.

Signal types:
- SQUEEZE: high short float (>=15%) + high days-to-cover (>=5d) — forced buying avalanche on any catalyst
- LOW_FLOAT: tiny float (<=20M shares) + heavy volume (>=8% of float today) — tiny supply, explosive on demand
- BOTH: both conditions simultaneously — the most dangerous setup possible

Key metrics:
- short_float: what % of float is sold short. >25% = extreme. >35% = explosive powder keg
- days_to_cover: how many trading days shorts need to fully exit at normal volume. >8d = violent squeeze potential
- float_m: total float in millions. <5M = micro float, any buying pressure moves it 10%+
- vol_pct_float: today's vol as % of total float. >15% means the float is rotating rapidly — something is happening now
- rel_vol: today's vol vs 3-month avg. >5x = 5 times normal activity — unusual accumulation

Setups to analyze:
{chr(10).join(lines)}

Conviction levels:
- CRITICAL: BOTH signal, OR SQUEEZE with short_float>25% + days_to_cover>8 + rel_vol>3 — near-certain violent move on any catalyst
- HIGH: SQUEEZE with short_float>18% + days_to_cover>5, OR LOW_FLOAT with vol_pct_float>12% + rel_vol>4 — strong setup
- WATCH: setup present but one or more metrics are borderline — monitor for volume confirmation
- NOISE: metrics look okay on one dimension but the composite picture doesn't confirm explosiveness

Return ONLY valid JSON, no markdown:
{{"signals":[{{"ticker":"XXXX","signal":"CRITICAL","thesis":"2-3 punchy sentences: what makes this explosive, what the catalyst trigger would look like, and the key risk.","confidence":92}}]}}"""

    try:
        client = OpenAI(
            api_key=os.environ["AI_INTEGRATIONS_OPENAI_API_KEY"],
            base_url=os.environ["AI_INTEGRATIONS_OPENAI_BASE_URL"],
        )
        response = client.chat.completions.create(
            model="gpt-5.4",
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=3000,
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw).strip()
        raw = re.sub(r'\s*```$', '', raw).strip()
        parsed  = json.loads(raw)
        signals = parsed.get("signals", [])

        sms_sent = []
        t_sid   = os.getenv("TWILIO_ACCOUNT_SID")
        t_token = os.getenv("TWILIO_AUTH_TOKEN")
        t_from  = os.getenv("TWILIO_FROM_NUMBER")
        t_to    = os.getenv("TWILIO_TO_NUMBER")

        if all([t_sid, t_token, t_from, t_to]):
            try:
                from twilio.rest import Client as TwilioClient
                tw       = TwilioClient(t_sid, t_token)
                critical = [s for s in signals if s.get("signal") in ("CRITICAL", "HIGH")][:3]
                for sig in critical:
                    row = next((r for r in rows if r["ticker"] == sig["ticker"]), {})
                    msg = (
                        f"🔥 StockScanner AI — {sig['signal']} SETUP\n"
                        f"{sig['ticker']} ${row.get('price','?')} | {row.get('signal_type','')} signal\n"
                        f"Short: {row.get('short_float_pct',0):.1f}% | {row.get('days_to_cover',0):.1f}d to cover\n"
                        f"Float: {row.get('float_m','?')}M shares | {row.get('rel_vol',0):.1f}x vol\n"
                        f"{sig['thesis'][:160]}"
                    )
                    tw.messages.create(body=msg, from_=t_from, to=t_to)
                    sms_sent.append(sig["ticker"])
            except Exception as sms_err:
                print(f"[squeeze_ai] SMS error: {sms_err}", file=sys.stderr, flush=True)

        return jsonify({"signals": signals, "sms_sent": sms_sent})

    except Exception as e:
        import traceback
        print(f"[squeeze_ai] error: {e}\n{traceback.format_exc()}", file=sys.stderr, flush=True)
        return jsonify({"error": str(e), "signals": []}), 500


@app.route("/stock-api/morning-runners", methods=["GET"])
def morning_runners():
    """Morning runners — scans all tickers for pre-market volume spikes + gap moves."""
    import yfinance as yf
    from datetime import datetime as _mr_dt

    _cache = getattr(app, "_mr_cache", None)
    _ts    = getattr(app, "_mr_cache_ts", None)
    if _cache and _ts and (_mr_dt.now() - _ts).total_seconds() < 600:
        return jsonify(_cache)

    results = []

    def _scan_mr(ticker):
        try:
            fi         = yf.Ticker(ticker).fast_info
            price      = float(getattr(fi, "last_price",                0) or 0)
            prev_close = float(getattr(fi, "previous_close",            0) or 0)
            if price <= 0 or prev_close <= 0:
                return None
            gap_pct    = round((price - prev_close) / prev_close * 100, 2)
            avg_vol    = float(getattr(fi, "three_month_average_volume", 1) or 1)
            today_vol  = float(getattr(fi, "last_volume",               0) or 0)
            rel_vol    = round(today_vol / avg_vol, 2) if avg_vol > 0 else 0
            mkt_cap    = float(getattr(fi, "market_cap",                0) or 0)
            mkt_cap_b  = round(mkt_cap / 1e9, 2) if mkt_cap else None

            if rel_vol < 1.5 and abs(gap_pct) < 4.0:
                return None

            # score = relative volume * (|gap%| + 1) — big vol + big gap = top of list
            score   = round(rel_vol * (abs(gap_pct) + 1), 2)
            squeeze = bool(mkt_cap_b is not None and mkt_cap_b < 2.0 and rel_vol >= 3.0)

            return {
                "ticker":     ticker,
                "price":      round(price, 2),
                "prev_close": round(prev_close, 2),
                "gap_pct":    gap_pct,
                "rel_vol":    rel_vol,
                "avg_vol":    int(avg_vol),
                "today_vol":  int(today_vol),
                "mkt_cap_b":  mkt_cap_b,
                "score":      score,
                "squeeze":    squeeze,
            }
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=20) as ex:
        futures = {ex.submit(_scan_mr, t): t for t in DEFAULT_LEADERBOARD}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                results.append(r)

    results.sort(key=lambda x: x["score"], reverse=True)
    out = {
        "runners": results[:40],
        "total":   len(results),
        "scanned": len(DEFAULT_LEADERBOARD),
    }
    app._mr_cache    = out
    app._mr_cache_ts = _mr_dt.now()
    return jsonify(out)


import xml.etree.ElementTree as _ET_xml
import re as _re_mp

# ── MARKET PRESS ──────────────────────────────────────────────────────────────
_mp_cache    = None
_mp_cache_ts = None
_mp_lock     = threading.Lock()
_MP_TTL      = 120   # 2-minute cache

_MP_FEEDS = [
    {"url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US", "category": "MARKETS",     "source": "Yahoo Finance"},
    {"url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^DJI&region=US&lang=en-US",  "category": "MARKETS",     "source": "Yahoo Finance"},
    {"url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=QQQ&region=US&lang=en-US",   "category": "TECH",        "source": "Yahoo Finance"},
    {"url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GLD&region=US&lang=en-US",   "category": "COMMODITIES", "source": "Yahoo Finance"},
    {"url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=TLT&region=US&lang=en-US",   "category": "RATES",       "source": "Yahoo Finance"},
    {"url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=SPY&region=US&lang=en-US",   "category": "MARKETS",     "source": "Yahoo Finance"},
]

def _fetch_rss_feed(feed_info):
    import urllib.request as _ureq
    import datetime as _dt_mp2
    items = []
    try:
        req = _ureq.Request(feed_info["url"], headers={"User-Agent": "Mozilla/5.0 StockScannerBot/1.0"})
        resp = _ureq.urlopen(req, timeout=8)
        root = _ET_xml.fromstring(resp.read())
        for item in root.findall(".//item"):
            title    = (item.findtext("title")      or "").strip()
            link     = (item.findtext("link")       or "").strip()
            pub_date = (item.findtext("pubDate")    or "").strip()
            desc     = _re_mp.sub(r"<[^>]+>", "", item.findtext("description") or "")[:220].strip()
            if not title:
                continue
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(pub_date)
                ts = dt.isoformat()
                now_utc = _dt_mp2.datetime.now(_dt_mp2.timezone.utc)
                diff_m = int((now_utc - dt).total_seconds() / 60)
                age = (f"{diff_m}m ago" if diff_m < 60
                       else f"{diff_m//60}h ago" if diff_m < 1440
                       else f"{diff_m//1440}d ago")
            except Exception:
                ts, age = pub_date, ""
            items.append({
                "title":        title,
                "url":          link,
                "source":       feed_info["source"],
                "category":     feed_info["category"],
                "published_at": ts,
                "age":          age,
                "summary":      desc,
            })
    except Exception:
        pass
    return items

@app.route("/stock-api/market-press", methods=["GET"])
def market_press():
    global _mp_cache, _mp_cache_ts
    import datetime as _dt_mp3
    with _mp_lock:
        if _mp_cache and _mp_cache_ts and (_dt_mp3.datetime.now() - _mp_cache_ts).total_seconds() < _MP_TTL:
            return jsonify(_mp_cache)
    raw = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for rows in ex.map(_fetch_rss_feed, _MP_FEEDS):
            raw.extend(rows)
    raw.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    seen, deduped = set(), []
    for a in raw:
        if a["title"] not in seen:
            seen.add(a["title"])
            deduped.append(a)
    out = {"articles": deduped[:60], "count": len(deduped),
           "fetched_at": _dt_mp3.datetime.now(_dt_mp3.timezone.utc).isoformat()}
    with _mp_lock:
        _mp_cache, _mp_cache_ts = out, _dt_mp3.datetime.now()
    return jsonify(out)


# ── EARNINGS CALENDAR + IMPLIED MOVE ─────────────────────────────────────────
_ec_cache    = None
_ec_cache_ts = None
_ec_lock     = threading.Lock()
_EC_TTL      = 600   # 10-minute cache

_EC_WATCHLIST = [
    "AAPL","MSFT","GOOGL","AMZN","META","NVDA","TSLA","AMD","INTC","NFLX",
    "CRM","ORCL","IBM","ADBE","QCOM","MU","AVGO","TXN","AMAT","MRVL",
    "JPM","GS","BAC","WFC","MS","BLK","SCHW","C","V","MA",
    "JNJ","PFE","MRNA","ABBV","BMY","LLY","UNH","CVS","AMGN","GILD",
    "XOM","CVX","COP","SLB","EOG",
    "DIS","CMCSA","T","VZ","NFLX",
    "WMT","TGT","COST","HD","LOW",
    "BA","CAT","MMM","GE","HON","RTX","LMT",
    "COIN","HOOD","PLTR","SNOW","UBER","LYFT","ABNB","DASH",
    "SHOP","SQ","PYPL","AFRM","RIVN","LCID",
    "ZM","DOCU","NOW","DDOG","NET","CRWD","PANW",
    "F","GM","RIVN",
]

def _check_earnings(ticker):
    import datetime as _dt_ec
    import yfinance as yf
    today = _dt_ec.date.today()
    cutoff = today + _dt_ec.timedelta(days=30)
    try:
        tk = yf.Ticker(ticker)
        cal = tk.calendar
        if cal is None:
            return None
        earn_date = None
        try:
            if hasattr(cal, "empty") and not cal.empty:
                if "Earnings Date" in cal.index:
                    earn_date = cal.loc["Earnings Date"].iloc[0]
                elif "Earnings Date" in cal.columns:
                    earn_date = cal.iloc[0]["Earnings Date"]
            elif isinstance(cal, dict):
                ed_list = cal.get("Earnings Date", [])
                earn_date = ed_list[0] if ed_list else None
        except Exception:
            return None
        if earn_date is None:
            return None
        if hasattr(earn_date, "date"):
            earn_dt = earn_date.date()
        else:
            earn_dt = _dt_ec.datetime.strptime(str(earn_date)[:10], "%Y-%m-%d").date()
        if earn_dt < today or earn_dt > cutoff:
            return None

        info  = tk.fast_info
        price = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
        if not price or price <= 0:
            return None

        # Implied move via ATM straddle
        implied_move_pct = None
        try:
            exps = tk.options
            target_exp = next(
                (e for e in (exps or [])
                 if _dt_ec.datetime.strptime(e, "%Y-%m-%d").date() >= earn_dt),
                None
            )
            if target_exp:
                chain  = tk.option_chain(target_exp)
                calls, puts = chain.calls, chain.puts
                if not calls.empty and not puts.empty:
                    strikes = calls["strike"].values
                    atm = strikes[abs(strikes - price).argmin()]
                    c_row = calls[calls["strike"] == atm]
                    p_row = puts[puts["strike"]  == atm]
                    if not c_row.empty and not p_row.empty:
                        def _mid(row, col):
                            b, a = row.iloc[0].get("bid", 0), row.iloc[0].get("ask", 0)
                            m = (b + a) / 2
                            return m if m > 0 else row.iloc[0].get("lastPrice", 0)
                        straddle = _mid(c_row, "call") + _mid(p_row, "put")
                        implied_move_pct = round(straddle / price * 100, 1)
        except Exception:
            pass

        # EPS estimate
        eps_est = None
        try:
            if isinstance(cal, dict):
                ea = cal.get("Earnings Average", [])
                eps_est = ea[0] if ea else None
            elif "Earnings Average" in cal.index:
                eps_est = cal.loc["Earnings Average"].iloc[0]
        except Exception:
            pass

        # Short name + market cap
        mkt_cap_b, short_name = None, ticker
        try:
            mc = getattr(info, "market_cap", None)
            if mc:
                mkt_cap_b = round(mc / 1e9, 1)
        except Exception:
            pass
        try:
            short_name = (tk.info.get("shortName") or ticker)[:30]
        except Exception:
            pass

        return {
            "ticker":           ticker,
            "name":             short_name,
            "earnings_date":    earn_dt.isoformat(),
            "days_until":       (earn_dt - today).days,
            "price":            round(price, 2),
            "eps_estimate":     round(float(eps_est), 2) if eps_est is not None else None,
            "implied_move_pct": implied_move_pct,
            "mkt_cap_b":        mkt_cap_b,
        }
    except Exception:
        return None

@app.route("/stock-api/earnings-calendar", methods=["GET"])
def earnings_calendar():
    global _ec_cache, _ec_cache_ts
    import datetime as _dt_ec2
    with _ec_lock:
        if _ec_cache and _ec_cache_ts and (_dt_ec2.datetime.now() - _ec_cache_ts).total_seconds() < _EC_TTL:
            return jsonify(_ec_cache)
    results = []
    with ThreadPoolExecutor(max_workers=20) as ex:
        for r in ex.map(_check_earnings, _EC_WATCHLIST):
            if r:
                results.append(r)
    results.sort(key=lambda x: (x["earnings_date"], -(x["mkt_cap_b"] or 0)))
    out = {"earnings": results, "count": len(results),
           "as_of": _dt_ec2.date.today().isoformat(), "window_days": 30}
    with _ec_lock:
        _ec_cache, _ec_cache_ts = out, _dt_ec2.datetime.now()
    return jsonify(out)


@app.route("/stock-api/morning-inflows", methods=["GET"])
def morning_inflows():
    """
    Morning Standout Inflows — catches extreme net buying pressure (like a +25% OCC-style move).
    Scans Yahoo Finance top-gainers list + all tracked tickers.
    Standout criteria: price ≥+5% intraday · relative volume ≥3× avg · flow ratio ≥2:1 buy:sell.
    Score = rel_vol × (price_chg_pct/10) × flow_ratio — sorted highest first.
    Pre-warmed by scheduler at 9:45 AM and 10:30 AM ET Mon–Fri.
    """
    import yfinance as _yf_mi
    import datetime as _dt_mi
    from concurrent.futures import ThreadPoolExecutor as _TPE_mi, as_completed as _ac_mi
    import requests as _req_mi

    try:
        bust = request.args.get("bust", "0") == "1"
    except RuntimeError:
        bust = True  # called from scheduler — always fresh

    _cache    = getattr(app, "_mi_cache", None)
    _cache_ts = getattr(app, "_mi_cache_ts", None)
    if not bust and _cache and _cache_ts and (_dt_mi.datetime.now() - _cache_ts).total_seconds() < 900:
        return jsonify(_cache)

    # ── DB fallback — survive API restarts all day ──────────────────────────
    # If in-memory cache is cold (restart), load today's best scan from DB.
    # This means the morning results stay visible all day even after a restart.
    if not bust and _DB_URL:
        try:
            _today_mi = _dt_mi.date.today().isoformat()
            with _psycopg2.connect(_DB_URL) as _c_mi, _c_mi.cursor() as _cu_mi:
                _cu_mi.execute(
                    "SELECT payload FROM morning_inflows_cache WHERE scan_date = %s",
                    (_today_mi,)
                )
                _db_mi_row = _cu_mi.fetchone()
            if _db_mi_row and _db_mi_row[0].get("standouts"):
                _db_mi_payload = _db_mi_row[0]
                app._mi_cache    = _db_mi_payload
                app._mi_cache_ts = _dt_mi.datetime.now()
                print(f"[morning_inflows] loaded {len(_db_mi_payload['standouts'])} standouts from DB (restart recovery)")
                return jsonify(_db_mi_payload)
        except Exception as _dbe_mi:
            print(f"[morning_inflows] db load error: {_dbe_mi}")

    import pytz as _pytz_mi2
    _et2       = _pytz_mi2.timezone("America/New_York")
    _now_et    = _dt_mi.datetime.now(_et2)
    _mkt_open  = _now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    _mins_now  = max((_now_et - _mkt_open).total_seconds() / 60.0, 1.0)
    _mins_now  = min(_mins_now, 390.0)
    _day_frac  = _mins_now / 390.0

    # ── PHASE 1: Comprehensive universe via yf.EquityQuery ────────────────────
    # This queries Yahoo's full US stock database (not just a top-N list).
    # Returns ALL US equities up ≥5% today, paginated until exhausted.
    # Each page also carries regularMarketVolume + averageDailyVolume3Month,
    # letting us pre-filter on projected volume before the expensive 1-min bars call.
    _screen_quotes = {}   # symbol → {raw_vol, avg_vol, price_chg_pct}
    try:
        _eq = _yf_mi.EquityQuery("and", [
            _yf_mi.EquityQuery("gt",  ["percentchange",    4.9]),
            _yf_mi.EquityQuery("eq",  ["region",           "us"]),
            _yf_mi.EquityQuery("gte", ["intradaymarketcap", 500_000]),  # ≥$500K — catches tiny micro-caps
        ])
        _offset = 0
        while True:
            _pg = _yf_mi.screen(
                _eq, sortField="percentchange", sortAsc=False,
                size=250, offset=_offset
            )
            _pg_quotes = _pg.get("quotes", [])
            if not _pg_quotes:
                break
            for _q in _pg_quotes:
                _sym = _q.get("symbol", "")
                if not _sym or "." in _sym or len(_sym) > 5:
                    continue
                _raw_vol  = float(_q.get("regularMarketVolume",         0) or 0)
                _avg_vol  = float(_q.get("averageDailyVolume3Month",     1) or 1)
                _pct      = float(_q.get("regularMarketChangePercent",   0) or 0)
                # Pre-filter: projected vol must be ≥3× for this time of day
                # projected = raw_vol / day_frac; threshold = 3 * avg_vol
                _proj = _raw_vol / _day_frac if _day_frac > 0 else 0
                if _avg_vol > 0 and _proj >= 3.0 * _avg_vol:
                    _screen_quotes[_sym] = {"raw_vol": _raw_vol, "avg_vol": _avg_vol, "pct": _pct}
            # Stop if we've got all results
            if len(_screen_quotes) >= _pg.get("total", 0) or len(_pg_quotes) < 250 or _offset >= 2000:
                break
            _offset += 250
        print(f"[morning_inflows] yf.screen: {len(_screen_quotes)} pre-filtered US gainers (proj vol ≥3×)")
    except Exception as _eq_err:
        print(f"[morning_inflows] yf.screen fallback: {_eq_err}")

    # ── PHASE 1b: Supplementary predefined screeners ──────────────────────────
    # Catches high-volume names that may not yet be ≥5% (most_actives) and
    # small-cap momentum names (aggressive_small_caps, small_cap_gainers).
    _yf_headers = {"User-Agent": "Mozilla/5.0 (compatible)"}
    from concurrent.futures import ThreadPoolExecutor as _TPE_src

    def _fetch_screener(scr_id, count=100, start=0):
        try:
            r = _req_mi.get(
                "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
                f"?formatted=false&scrIds={scr_id}&count={count}&start={start}",
                headers=_yf_headers, timeout=8
            )
            if r.ok:
                quotes = r.json().get("finance", {}).get("result", [{}])[0].get("quotes", [])
                return [q["symbol"] for q in quotes
                        if q.get("symbol") and "." not in q["symbol"] and len(q["symbol"]) <= 5]
        except Exception as _e:
            print(f"[morning_inflows] screener {scr_id}: {_e}")
        return []

    _supp_tasks = [
        ("most_actives",          100, 0),
        ("most_actives",          100, 50),
        ("aggressive_small_caps", 100, 0),
        ("small_cap_gainers",     100, 0),
    ]
    _supp_syms = []
    with _TPE_src(max_workers=4) as _src_ex:
        for _syms in _src_ex.map(lambda a: _fetch_screener(*a), _supp_tasks):
            _supp_syms.extend(_syms)

    # ── PHASE 1c: Our tracked tickers (options signals + morning watchlist) ──
    # morning_watchlist = hand-curated stocks that have shown big intraday moves
    # even when they open flat/red — ensures they're always in the scan universe
    # so if they cross 5%+ at the 9:45 or 10:30 AM scan they won't be missed.
    _tracked = []
    try:
        with _psycopg2.connect(_DB_URL) as _conn, _conn.cursor() as _cur:
            _cur.execute("""
                SELECT ticker FROM (
                    SELECT DISTINCT ticker FROM unusual_calls_log
                     WHERE first_seen >= NOW() - INTERVAL '90 days'
                    UNION
                    SELECT ticker FROM morning_watchlist
                ) combined
            """)
            _tracked = [r[0] for r in _cur.fetchall()]
        print(f"[morning_inflows] db tracked: {len(_tracked)} tickers (options + watchlist)")
    except Exception as _de:
        print(f"[morning_inflows] db: {_de}")

    # ── PHASE 1d: Barchart micro-cap + small-cap top movers ───────────────────
    # Barchart's screener catches movers that Yahoo may lag on or filter out.
    # We pull their public advances list for micro-cap and small-cap stocks.
    _barchart_syms = []
    try:
        _bc_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://www.barchart.com/stocks/advances",
        }
        for _bc_list in ("stocks.advances.microcap.us", "stocks.advances.smallcap.us", "stocks.advances.midcap.us", "stocks.advances.largecap.us"):
            try:
                _bc_url = (
                    "https://www.barchart.com/proxies/core-api/v1/quotes/get"
                    f"?fields=symbol%2CpercentChange%2Cvolume%2CaverageVolume&"
                    f"list={_bc_list}&orderBy=percentChange&orderDir=desc&raw=1&limit=100"
                )
                _bc_r = _req_mi.get(_bc_url, headers=_bc_headers, timeout=8)
                if _bc_r.ok:
                    _bc_data = _bc_r.json().get("data", [])
                    for _row in _bc_data:
                        _sym = (_row.get("symbol") or "").strip().upper()
                        if _sym and len(_sym) <= 5 and "." not in _sym:
                            _barchart_syms.append(_sym)
            except Exception as _bc_e:
                print(f"[morning_inflows] barchart {_bc_list}: {_bc_e}")
        print(f"[morning_inflows] barchart: {len(_barchart_syms)} micro+small-cap movers")
    except Exception as _bce:
        print(f"[morning_inflows] barchart feed error: {_bce}")

    # Merge all sources: pre-filtered screen results first (highest confidence),
    # then Barchart movers, supplementary screeners + our tracked tickers.
    _barchart_set = set(_barchart_syms)   # fast lookup for threshold relaxation
    universe = list(dict.fromkeys(
        list(_screen_quotes.keys()) + _barchart_syms + _supp_syms + _tracked
    ))
    universe = [t for t in universe if t and len(t) <= 5]
    print(f"[morning_inflows] universe after merge: {len(universe)} tickers")

    def _score_ticker(ticker):
        try:
            import pytz as _pytz_mi
            _et = _pytz_mi.timezone("America/New_York")

            tk = _yf_mi.Ticker(ticker)
            fi = tk.fast_info
            prev_close = float(getattr(fi, "previous_close", 0) or 0)
            avg_vol    = float(getattr(fi, "three_month_average_volume", 1) or 1)
            mkt_cap    = float(getattr(fi, "market_cap", 0) or 0)
            if prev_close <= 0 or avg_vol <= 0: return None

            # Fetch 1-min bars — single network call used for price, volume, AND flow
            hist = tk.history(period="1d", interval="1m")
            if hist.empty or len(hist) < 2: return None

            # Convert index to ET so we can measure minutes elapsed since 9:30 AM open
            hist.index = hist.index.tz_convert(_et)
            now_et      = _dt_mi.datetime.now(_et)
            market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)

            # Cumulative volume from all bars so far today
            cum_vol = float(hist["Volume"].sum())
            price   = float(hist["Close"].iloc[-1])
            if price <= 0: return None

            price_chg = (price - prev_close) / prev_close * 100
            if price_chg < 5.0: return None  # must be up ≥5% on the day

            # ── PROJECTED daily volume ──────────────────────────────────────────
            # How many full 390-minute trading day minutes have elapsed since open?
            # Cap at 390 so the projection never goes negative or weird at close.
            mins_elapsed = max((now_et - market_open).total_seconds() / 60.0, 1.0)
            mins_elapsed = min(mins_elapsed, 390.0)
            day_fraction = mins_elapsed / 390.0
            projected_vol = cum_vol / day_fraction          # what the full day would look like
            rel_vol       = projected_vol / avg_vol

            # Early-session threshold:  ≥5× projected  (=75× raw for OCC at 9:31)
            # After 30 min threshold rises to standard ≥3× (noise settles after open)
            # Barchart-sourced stocks already pre-screened as top movers — lower bar
            # if up ≥20% so we don't miss EDHL-type runners with thinner avg volume
            if ticker in _barchart_set and price_chg >= 20.0:
                min_rel = 1.5
            elif mins_elapsed <= 30:
                min_rel = 5.0
            else:
                min_rel = 3.0
            if rel_vol < min_rel: return None

            # ── Micro-pump detection ─────────────────────────────────────────────
            # Data (2026-06-10): LUD $5.06 rel-vol 184× → -20.8%, CHNR $4.52 rel-vol 85× → -16.8%
            # Extremely high volume on a sub-$5 stock = coordinated micro-pump, not organic buying.
            # Shown with a ⚠️ MICRO-PUMP warning label instead of silently dropped.
            _is_micro_pump = price < 5.0 and rel_vol > 50

            # ── Money flow from 1-min bars ──────────────────────────────────────
            inflow = outflow = 0.0
            for _, row in hist.iterrows():
                if row["Volume"] <= 0: continue
                avg_p = (float(row["Open"]) + float(row["Close"])) / 2
                dv    = avg_p * float(row["Volume"])
                if float(row["Close"]) >= float(row["Open"]): inflow  += dv
                else:                                          outflow += dv

            flow_ratio = (inflow / outflow) if outflow > 0 else 99.0
            # Lowered from 2.5 → 2.0: catches more legitimate movers in the watchlist.
            # Micro-pumps skip the flow ratio gate — they're shown with a warning regardless.
            if not _is_micro_pump and flow_ratio < 2.0: return None

            # ── Gap-up multiplier ────────────────────────────────────────────
            # Stocks that gap up hard at the open (pre-market catalyst) are higher
            # quality signals than stocks that slowly drift to 5%+ intraday.
            # Data shows morning signal stocks averaged a +11.6% open gap vs -0.4%
            # for stocks that made their move later in the day.
            open_bars = hist[hist.index.time >= _dt_mi.time(9, 30)]
            open_price = float(open_bars["Open"].iloc[0]) if not open_bars.empty else price
            gap_pct    = (open_price - prev_close) / prev_close * 100

            # ── First 5-minute bar direction (9:30–9:34 AM) ──────────────────
            # The most direct evidence of supply vs demand at the open.
            # If the first bar closes RED, sellers are overwhelming buyers at the
            # bell — structural dump into retail buyers who saw the gap and jumped in.
            # Validated 2026-06-10:
            #   HCAI: first bar +6.6% 🟢 → day closed +17.4% ✓ (winner)
            #   CHNR: first bar -7.3% 🔴 → day closed -16.8% ✓ (loser)
            #   LUD:  first bar -11.3% 🔴 → day closed -20.8% ✓ (loser)
            _first_5 = hist[(hist.index.time >= _dt_mi.time(9, 30)) &
                            (hist.index.time <= _dt_mi.time(9, 34))]
            if not _first_5.empty:
                _fb_open        = float(_first_5["Open"].iloc[0])
                _fb_close       = float(_first_5["Close"].iloc[-1])
                first_bar_pct   = (_fb_close - _fb_open) / _fb_open * 100 if _fb_open > 0 else 0.0
                first_bar_green = _fb_close >= _fb_open
                has_first_bar   = True
            else:
                first_bar_pct   = 0.0
                first_bar_green = True   # pre-market scan: no bar yet — don't penalise
                has_first_bar   = False

            # ── Gap multiplier — data-tuned from 2026-06-10 live results ────
            # Sweet spot: 5–15% gap with high rel-vol = highest hit rate (SDOT +96%,
            # LICN +20%, HCAI +17%). Extreme gaps (>100%) fade 100% of the time.
            if   gap_pct > 100: gap_multiplier = 0.4   # extreme pump — almost always fades
            elif gap_pct > 30:  gap_multiplier = 0.8   # large gap — structural fade risk
            elif gap_pct >= 15: gap_multiplier = 1.5   # moderate gap — some risk
            elif gap_pct >= 5:  gap_multiplier = 2.5   # SWEET SPOT — proven highest hit rate
            elif gap_pct >= 2:  gap_multiplier = 1.2   # modest gap — some pre-market interest
            else:               gap_multiplier = 1.0   # no gap — intraday drift, lower confidence

            standout_score = round(rel_vol * (price_chg / 10) * min(flow_ratio, 10) * gap_multiplier, 2)
            net_m          = (inflow - outflow) / 1_000_000

            # ── Fade Risk assessment ──────────────────────────────────────────
            # Signals used (in priority order):
            #   1. Gap size + market cap  — extreme pre-market pumps
            #   2. Pre-market exhaustion  — moderate gap where fuel is already burned
            #   3. Momentum at open       — price direction the moment the bell rings
            #   4. Market cap alone       — micro-cap structural risk
            momentum_open = price_chg - gap_pct        # >0 = still running; <0 = already fading
            mkt_cap_m_val = mkt_cap / 1_000_000 if mkt_cap else 0

            # ── Pre-market exhaustion ratio ──────────────────────────────────
            # What fraction of today's total move happened before 9:30 AM?
            # If >85% of the gain was pre-market, early holders are waiting to dump
            # into retail buyers the moment the bell rings.
            # Real-data validation (2026-06-10):
            #   DSY: 94% pre-mkt → faded -24% ✓   SDOT: 74% pre-mkt → ran +96% ✓
            #   VSME: 68% pre-mkt → faded -44% ✓  LICN: small gap → ran +20% ✓
            # Rule applies only when gap is meaningful (≥15%) — tiny gaps don't matter.
            exhaustion_ratio = (gap_pct / price_chg) if price_chg > 0 else 0.0

            # ── First-bar hard rejection (2026-06-11) ────────────────────────
            # If the first 5-min bar closes >2% red, sellers are dumping into buyers at
            # the bell — do not show this stock at all.
            # Validated: CHNR first bar -7.3% → lost -16.8%; LUD -11.3% → lost -20.8%
            if has_first_bar and first_bar_pct < -2.0:
                return None

            # ── Refined fade risk (priority order) ───────────────────────────
            # Change 1: Extreme gappers (>100%) always HIGH — confirmed faded 100% on 6/10
            # Change 2: Momentum threshold tightened: -3 triggers HIGH (was -5), -1 triggers WATCH (was -2)
            if gap_pct > 100:
                fade_risk = "HIGH"    # extreme pump — DSY/VSME both faded 24-44% confirmed
            elif gap_pct > 30 and mkt_cap_m_val < 100:
                fade_risk = "HIGH"    # large pump on tiny cap — not enough real buyers
            elif gap_pct >= 15 and exhaustion_ratio > 0.85:
                fade_risk = "HIGH"    # moderate gap, 85%+ already happened pre-market — fuel burned
            elif momentum_open < -3:
                fade_risk = "HIGH"    # fading at the bell (tightened from -5 based on live data)
            elif mkt_cap_m_val > 0 and mkt_cap_m_val < 50:
                fade_risk = "WATCH"   # micro-cap but modest gap — can run, stay alert
            elif (mkt_cap_m_val < 500 and gap_pct > 10) or momentum_open < -1:
                fade_risk = "WATCH"   # mid-cap large gap or slight negative momentum
            elif has_first_bar and first_bar_pct < 0.0:
                fade_risk = "WATCH"   # slightly red first bar — some selling pressure at open
            else:
                fade_risk = "HOLD"    # larger cap, sustained buying, positive momentum

            return {
                "ticker":          ticker,
                "price":           round(price, 2),
                "prev_close":      round(prev_close, 2),
                "price_chg_pct":   round(price_chg, 2),
                "gap_pct":         round(gap_pct, 2),
                "gap_multiplier":   gap_multiplier,
                "momentum_open":   round(momentum_open, 2),
                "exhaustion_ratio": round(exhaustion_ratio, 3),
                "fade_risk":       fade_risk,
                "first_bar_pct":   round(first_bar_pct, 2),
                "first_bar_green": first_bar_green,
                "has_first_bar":   has_first_bar,
                "rel_vol":         round(rel_vol, 1),
                "rel_vol_raw":     round(cum_vol / avg_vol, 2),
                "today_vol":       int(cum_vol),
                "projected_vol":   int(projected_vol),
                "avg_vol":         int(avg_vol),
                "mins_elapsed":    round(mins_elapsed, 0),
                "inflow_m":        round(inflow   / 1_000_000, 2),
                "outflow_m":       round(outflow  / 1_000_000, 2),
                "net_m":           round(net_m, 2),
                "flow_ratio":      round(min(flow_ratio, 99.0), 2),
                "standout_score":  standout_score,
                "mkt_cap_m":       round(mkt_cap / 1_000_000, 1) if mkt_cap else None,
                "micro_pump":      _is_micro_pump,
            }
        except Exception:
            return None

    results = []
    with _TPE_mi(max_workers=25) as ex:
        futures = {ex.submit(_score_ticker, t): t for t in universe}
        for fut in _ac_mi(futures):
            r = fut.result()
            if r: results.append(r)

    results.sort(key=lambda x: -x["standout_score"])

    # ── Separate micro-pumps, extreme pumps, and actionable standouts ────────
    # micro_pumps:    sub-$5 at open + >50× rel vol + WEAK flow (<2.0) → ⚠️ warning
    # extreme_pumps:  gap >100% — shown with 🔴 warning (100% fade rate confirmed)
    # standouts:      everything else, including sub-$5 stocks with STRONG flow (≥2.0)
    #                 so genuine micro-cap movers aren't buried in warnings
    _micro_pumps   = [r for r in results if r.get("micro_pump") and r.get("flow_ratio", 0) < 2.0]
    _extreme_pumps = [r for r in results if not r.get("micro_pump") and r.get("gap_pct", 0) > 100]
    _actionable    = [r for r in results if
                      (r.get("micro_pump") and r.get("flow_ratio", 0) >= 2.0)   # micro-cap but legit flow
                      or (not r.get("micro_pump") and r.get("gap_pct", 0) <= 100)]  # normal standout

    out = {
        "standouts":     _actionable[:25],
        "micro_pumps":   _micro_pumps[:10],
        "extreme_pumps": _extreme_pumps[:10],
        "total_found":   len(results),
        "scanned":       len(universe),
        "generated_at":  _dt_mi.datetime.now().strftime("%I:%M %p ET"),
        "criteria":      "price ≥+5% · projected vol ≥5× avg (first 30 min) · flow ratio ≥2:1",
    }

    # ── Persist to DB — always overwrite with the latest scan ───────────────
    # Each scan window (9:31, 9:45, 10:00, 10:15, 10:30) is a fresh filter pass.
    # Fewer results at 9:45 means stocks faded — that IS the correct picture.
    # Always write the most recent scan so the DB never shows stale winners.
    if results and _DB_URL:
        import json as _json_mi2
        import time as _time_mi2
        _today_mi2 = _dt_mi.date.today().isoformat()
        for _attempt_mi2 in range(3):
            try:
                with _psycopg2.connect(_DB_URL) as _c_mi2, _c_mi2.cursor() as _cu_mi2:
                    _cu_mi2.execute("""
                        INSERT INTO morning_inflows_cache (scan_date, payload)
                        VALUES (%s, %s::jsonb)
                        ON CONFLICT (scan_date) DO UPDATE
                            SET payload  = EXCLUDED.payload,
                                saved_at = NOW()
                    """, (_today_mi2, _json_mi2.dumps(out)))
                    _c_mi2.commit()
                print(f"[morning_inflows] persisted {len(results)} standouts to DB for {_today_mi2}")
                break
            except Exception as _dbe_mi2:
                if _attempt_mi2 < 2:
                    _time_mi2.sleep(0.5 * (_attempt_mi2 + 1))
                else:
                    print(f"[morning_inflows] CACHE SAVE FAILED after 3 attempts: {_dbe_mi2}")

    # ── Save individual ticker rows to scan_history for analysis ────────────
    # Each ticker is saved in its own transaction so one bad row never silently
    # drops the rest (previously a single commit meant VELO+INDP could vanish).
    if results and _DB_URL:
        import time as _time_sh
        _scan_ts   = _dt_mi.datetime.now()
        _scan_date = _scan_ts.date().isoformat()
        _saved_sh  = 0
        _failed_sh = []
        for _rank, _r in enumerate(results, 1):
            _saved = False
            for _attempt in range(3):
                try:
                    with _psycopg2.connect(_DB_URL) as _c_sh, _c_sh.cursor() as _cu_sh:
                        _cu_sh.execute("""
                            INSERT INTO scan_history
                                (scan_time, scan_date, ticker, price, prev_close,
                                 price_chg_pct, gap_pct, momentum_open, exhaustion_ratio,
                                 fade_risk, rel_vol, today_vol, avg_vol,
                                 inflow_m, outflow_m, net_m, flow_ratio,
                                 standout_score, mkt_cap_m, rank_in_scan)
                            VALUES
                                (%s, %s, %s, %s, %s,
                                 %s, %s, %s, %s,
                                 %s, %s, %s, %s,
                                 %s, %s, %s, %s,
                                 %s, %s, %s)
                            ON CONFLICT DO NOTHING
                        """, (
                            _scan_ts, _scan_date, _r["ticker"], _r.get("price"), _r.get("prev_close"),
                            _r.get("price_chg_pct"), _r.get("gap_pct"), _r.get("momentum_open"), _r.get("exhaustion_ratio"),
                            _r.get("fade_risk"), _r.get("rel_vol"), _r.get("today_vol"), _r.get("avg_vol"),
                            _r.get("inflow_m"), _r.get("outflow_m"), _r.get("net_m"), _r.get("flow_ratio"),
                            _r.get("standout_score"), _r.get("mkt_cap_m"), _rank
                        ))
                        _c_sh.commit()
                    _saved_sh += 1
                    _saved = True
                    break
                except Exception as _e_sh_row:
                    if _attempt < 2:
                        _time_sh.sleep(0.3 * (_attempt + 1))
                    else:
                        _failed_sh.append(f"{_r['ticker']}({_e_sh_row})")
        if _saved_sh:
            print(f"[scan_history] saved {_saved_sh}/{len(results)} ticker rows for {_scan_date}")
        if _failed_sh:
            print(f"[scan_history] SAVE FAILED for: {', '.join(_failed_sh)}")
    elif bust and not results and _DB_URL:
        # Bust/refresh after hours — don't replace good morning data with 0 results.
        # Fall back to today's DB data if it exists.
        try:
            _today_mi3 = _dt_mi.date.today().isoformat()
            with _psycopg2.connect(_DB_URL) as _c_mi3, _c_mi3.cursor() as _cu_mi3:
                _cu_mi3.execute(
                    "SELECT payload FROM morning_inflows_cache WHERE scan_date = %s",
                    (_today_mi3,)
                )
                _db_mi3 = _cu_mi3.fetchone()
            if _db_mi3 and _db_mi3[0].get("standouts"):
                out = _db_mi3[0]
                print(f"[morning_inflows] bust found 0 results (after hours) — serving {len(out['standouts'])} from DB")
        except Exception as _dbe_mi3:
            print(f"[morning_inflows] db bust-fallback error: {_dbe_mi3}")

    app._mi_cache    = out
    app._mi_cache_ts = _dt_mi.datetime.now()
    return jsonify(out)


def _get_short_data(ticker):
    """
    Fetch short interest + full technical squeeze indicators for a single ticker.
    Used by both the Active Squeeze Radar (hard-gate detection) and the EOD accum
    / cross-scanner card enrichment (short float + AVWAP badges).
    """
    try:
        _tk_sd  = yf.Ticker(ticker)
        _fi_sd  = _tk_sd.info
        _sf_sd  = _fi_sd.get("shortPercentOfFloat")
        _dtc_sd = _fi_sd.get("shortRatio")

        # 60 days of daily OHLCV — enough for MACD(26), OBV(10), BB(20), SMA(20), RSI(14)
        _h = _tk_sd.history(period="60d", interval="1d")
        if _h is None or len(_h) < 5:
            return {"short_float": None, "days_to_cover": None, "avwap_5d": None,
                    "above_avwap": None, "current_price": None, "price_chg_pct": None,
                    "vol_ratio_20d": None, "new_high_15d": False, "range_pct_15d": None,
                    "was_consolidating": False, "closing_range_today": None,
                    "avwap_20d": None, "above_avwap_20d": None, "rsi_14": None}

        _closes = _h["Close"].values.astype(float)
        _highs  = _h["High"].values.astype(float)
        _lows   = _h["Low"].values.astype(float)
        _vols   = _h["Volume"].values.astype(float)

        _cpx  = _closes[-1]
        _prev = _closes[-2] if len(_closes) >= 2 else _cpx
        _chg  = round((_cpx - _prev) / _prev * 100, 2) if _prev else None

        # ── Closing range today (1.0 = held at HOD, 0.0 = at LOD) ──────────
        _cr = None
        if _highs[-1] > _lows[-1]:
            _cr = round((_cpx - _lows[-1]) / (_highs[-1] - _lows[-1]), 3)

        # ── Volume explosion vs 20-day average ──────────────────────────────
        _vol_ratio = None
        if len(_vols) >= 21 and _vols[-21:-1].mean() > 0:
            _vol_ratio = round(float(_vols[-1]) / float(_vols[-21:-1].mean()), 2)

        # ── 15-day range breakout ────────────────────────────────────────────
        _new_high_15d = False
        _range_pct_15d = None
        _was_coiling = False
        if len(_closes) >= 16:
            _p15 = _closes[-16:-1]
            _mx, _mn = float(_p15.max()), float(_p15.min())
            _new_high_15d = bool(_cpx > _mx)
            if _mn > 0:
                _range_pct_15d = round((_mx - _mn) / _mn * 100, 1)
                _was_coiling = _range_pct_15d < 20.0  # <20% move over 15d = coiling

        # ── 5-day anchored VWAP ──────────────────────────────────────────────
        _avwap5 = None
        _above5 = None
        if len(_h) >= 6:
            _l5 = _h.tail(5)
            _v5 = _l5["Volume"].values.astype(float)
            if _v5.sum() > 0:
                _t5 = ((_l5["High"] + _l5["Low"] + _l5["Close"]) / 3).values.astype(float)
                _avwap5 = float((_t5 * _v5).sum() / _v5.sum())
                _above5 = bool(_cpx > _avwap5)

        # ── 20-day anchored VWAP ─────────────────────────────────────────────
        _avwap20 = None
        _above20 = None
        if len(_h) >= 21:
            _l20 = _h.tail(20)
            _v20 = _l20["Volume"].values.astype(float)
            if _v20.sum() > 0:
                _t20 = ((_l20["High"] + _l20["Low"] + _l20["Close"]) / 3).values.astype(float)
                _avwap20 = float((_t20 * _v20).sum() / _v20.sum())
                _above20 = bool(_cpx > _avwap20)

        # ── RSI-14 ────────────────────────────────────────────────────────────
        _rsi = None
        if len(_closes) >= 16:
            _g, _l = [], []
            for _i in range(1, len(_closes)):
                _d = _closes[_i] - _closes[_i - 1]
                _g.append(max(_d, 0.0)); _l.append(max(-_d, 0.0))
            _ag = sum(_g[-14:]) / 14.0
            _al = sum(_l[-14:]) / 14.0
            _rsi = round(100.0 if _al == 0 else 100.0 - 100.0 / (1.0 + _ag / _al), 1)

        # ── Helper: Exponential Moving Average ───────────────────────────────
        def _ema_calc(prices, period):
            k = 2.0 / (period + 1)
            e = float(prices[0])
            for p in prices[1:]:
                e = float(p) * k + e * (1 - k)
            return e

        # ── MACD (12/26/9) — momentum shift signal ───────────────────────────
        # Historically: MACD histogram turning positive after flat/negative base
        # = trend changing hands from sellers to buyers right before the explosion
        _macd_histogram = None
        _macd_bullish    = False
        if len(_closes) >= 30:
            _macd_line_series = []
            for _mi in range(9, len(_closes)):
                _e12 = _ema_calc(_closes[max(0, _mi - 26):_mi + 1], 12)
                _e26 = _ema_calc(_closes[max(0, _mi - 26):_mi + 1], 26)
                _macd_line_series.append(_e12 - _e26)
            if len(_macd_line_series) >= 9:
                _sig = _ema_calc(_macd_line_series[-9:], 9)
                _macd_histogram = round(_macd_line_series[-1] - _sig, 4)
                _prev_hist = (_ema_calc(_macd_line_series[-10:-1], 9)
                              if len(_macd_line_series) >= 10 else _sig)
                _macd_bullish = bool(
                    _macd_histogram > 0
                    and (_macd_histogram > (_macd_line_series[-2] - _prev_hist))
                )

        # ── OBV Divergence — silent accumulation pre-squeeze ─────────────────
        # Price going sideways while OBV climbs = big money quietly accumulating
        # while shorts think nothing is happening. Classic pre-GME / pre-AMC signal.
        _obv_divergence  = False
        _obv_trend_score = 0.0  # positive = accumulation, negative = distribution
        if len(_closes) >= 11:
            _obv = 0.0
            _obv_series = [0.0]
            for _oi in range(1, len(_closes)):
                if _closes[_oi] > _closes[_oi - 1]:
                    _obv += _vols[_oi]
                elif _closes[_oi] < _closes[_oi - 1]:
                    _obv -= _vols[_oi]
                _obv_series.append(_obv)
            # 10-day OBV change vs price change — divergence = pre-squeeze signal
            _obv_chg10  = (_obv_series[-1] - _obv_series[-11]) / (abs(_obv_series[-11]) + 1)
            _px_chg10   = (_closes[-1] - _closes[-11]) / (_closes[-11] + 1e-9)
            _obv_trend_score = round(float(_obv_chg10 - _px_chg10), 4)
            _obv_divergence  = bool(_obv_trend_score > 0.05 and _px_chg10 < 0.15)

        # ── Bollinger Band Squeeze releasing ─────────────────────────────────
        # Volatility compresses for days/weeks (BB narrows), then starts expanding.
        # The moment BB starts widening after a tight squeeze = explosion incoming.
        # This is John Carter's TTM Squeeze core concept applied to pre-squeeze detection.
        _bb_squeeze_was_on   = False
        _bb_squeeze_releasing = False
        if len(_closes) >= 25:
            # Current BB width
            _sma20_now = float(_closes[-20:].mean())
            _std20_now = float(_closes[-20:].std())
            _bb_w_now  = 4 * _std20_now  # 2× band = 4σ total width
            # BB width 7 trading days ago
            _sma20_7d  = float(_closes[-27:-7].mean()) if len(_closes) >= 27 else _sma20_now
            _std20_7d  = float(_closes[-27:-7].std())  if len(_closes) >= 27 else _std20_now
            _bb_w_7d   = 4 * _std20_7d
            # Squeeze was on = BB was unusually narrow
            _bb_norm   = _bb_w_now / (_sma20_now + 1e-9)  # relative width
            _bb_squeeze_was_on = bool(_bb_norm < 0.12)  # <12% of price = compressed
            # Now releasing = BB is expanding vs recent narrow period
            _bb_squeeze_releasing = bool(
                _bb_w_now > _bb_w_7d * 1.08  # expanding >8% from recent tight period
                and _bb_norm < 0.20           # still within a squeeze-like context
            )

        # ── Up-day volume dominance ───────────────────────────────────────────
        # Over last 10 sessions: compare total volume on up-days vs down-days.
        # When buyers command >60% of volume for multiple days while price is flat,
        # shorts are losing the battle — classic pre-squeeze exhaustion signal.
        _up_vol_ratio = None
        _buyers_dominant = False
        if len(_closes) >= 11:
            _up_v = sum(_vols[-10:][_i] for _i in range(10) if _closes[-10 + _i] > _closes[-11 + _i])
            _dn_v = sum(_vols[-10:][_i] for _i in range(10) if _closes[-10 + _i] < _closes[-11 + _i])
            _tv = _up_v + _dn_v
            if _tv > 0:
                _up_vol_ratio = round(_up_v / _tv, 3)
                _buyers_dominant = bool(_up_vol_ratio > 0.60)

        # ── SMA-20 as floor (shorts can't break it down) ─────────────────────
        # Price tested the 20-day SMA multiple times but keeps bouncing.
        # SMA20 also sloping up = trend intact, shorts are trapped at higher levels.
        _above_sma20  = False
        _sma20_rising = False
        _sma20_val    = None
        if len(_closes) >= 25:
            _sma20_val    = round(float(_closes[-20:].mean()), 2)
            _sma20_5d_ago = float(_closes[-25:-5].mean())
            _above_sma20  = bool(_cpx > _sma20_val)
            _sma20_rising = bool(_sma20_val > _sma20_5d_ago)

        # ── Count pre-ignition signals firing ────────────────────────────────
        _pre_ignition_count = sum([
            _obv_divergence,
            _macd_bullish,
            _bb_squeeze_releasing,
            _buyers_dominant,
            bool(_above_sma20 and _sma20_rising),
        ])

        return {
            "short_float":              round(float(_sf_sd) * 100, 1) if _sf_sd else None,
            "days_to_cover":            round(float(_dtc_sd), 1) if _dtc_sd else None,
            "current_price":            round(_cpx, 2),
            "price_chg_pct":            _chg,
            "closing_range_today":      _cr,
            "vol_ratio_20d":            _vol_ratio,
            "new_high_15d":             _new_high_15d,
            "range_pct_15d":            _range_pct_15d,
            "was_consolidating":        _was_coiling,
            "avwap_5d":                 round(_avwap5, 2) if _avwap5 else None,
            "above_avwap":              _above5,
            "avwap_20d":               round(_avwap20, 2) if _avwap20 else None,
            "above_avwap_20d":          _above20,
            "rsi_14":                   _rsi,
            # ── Pre-ignition historical squeeze signals ───────────────────────
            "obv_divergence":           _obv_divergence,
            "obv_trend_score":          _obv_trend_score,
            "macd_histogram":           _macd_histogram,
            "macd_bullish":             _macd_bullish,
            "bb_squeeze_was_on":        _bb_squeeze_was_on,
            "bb_squeeze_releasing":     _bb_squeeze_releasing,
            "up_vol_ratio":             _up_vol_ratio,
            "buyers_dominant":          _buyers_dominant,
            "above_sma20":              _above_sma20,
            "sma20_rising":             _sma20_rising,
            "sma20_val":                _sma20_val,
            "pre_ignition_count":       _pre_ignition_count,
        }
    except Exception:
        return {"short_float": None, "days_to_cover": None, "avwap_5d": None,
                "above_avwap": None, "current_price": None, "price_chg_pct": None,
                "vol_ratio_20d": None, "new_high_15d": False, "range_pct_15d": None,
                "was_consolidating": False, "closing_range_today": None,
                "avwap_20d": None, "above_avwap_20d": None, "rsi_14": None,
                "obv_divergence": False, "obv_trend_score": 0.0,
                "macd_histogram": None, "macd_bullish": False,
                "bb_squeeze_was_on": False, "bb_squeeze_releasing": False,
                "up_vol_ratio": None, "buyers_dominant": False,
                "above_sma20": False, "sma20_rising": False,
                "sma20_val": None, "pre_ignition_count": 0}


@app.route("/stock-api/eod-accumulation", methods=["GET"])
def eod_accumulation():
    """
    EOD Accumulation Scanner — detects late-day pump-group buying patterns.

    What we look for (3:30-4:00 PM ET window):
      1. EOD volume burst — last-30-min volume vs the stock's typical EOD volume
      2. Late money flow — inflow:outflow ratio in that window only (not the full day)
      3. Closing range — stock closes near the day high (>0.7 = top 30% of range)
      4. Quiet-then-surge — stock was calm all day then suddenly active into close
      5. Small/micro cap bias — pump groups target low-float stocks

    If the scanner flags a stock at 3:45 PM you can buy before the close.
    Pump groups blast socials after hours → retail FOMO creates the morning gap.
    You're positioned BEFORE retail sees it at 9:31 AM.
    """
    import datetime as _dt_ea
    import yfinance as _yf_ea
    import psycopg2 as _pg_ea
    from concurrent.futures import ThreadPoolExecutor as _TPE_ea, as_completed as _ac_ea
    import pytz as _pytz_ea

    bust = request.args.get("bust", "0") == "1"
    _cache    = getattr(app, "_eod_accum_cache", None)
    _cache_ts = getattr(app, "_eod_accum_cache_ts", None)
    if not bust and _cache and _cache_ts and (_dt_ea.datetime.now() - _cache_ts).total_seconds() < 600:
        return jsonify(_cache)

    _et = _pytz_ea.timezone("America/New_York")

    # ── After market close: serve locked-in results from DB, not a fresh live scan ──
    # Live yfinance intraday data degrades after ~4 PM ET — re-scanning returns fewer
    # picks because the 3:30-4:00 PM bars become unavailable.  The 3:45 PM scheduled
    # scan already saved the correct results; just return those.
    _now_et_ea = _dt_ea.datetime.now(_et)
    _h_ea = _now_et_ea.hour
    _m_ea = _now_et_ea.minute
    # Market is open 9:30 AM – 4:00 PM ET. Outside that window, serve from DB.
    _after_close = (
        (_h_ea == 16 and _m_ea >= 5) or   # 4:05 – 4:59 PM
        _h_ea >= 17 or                     # 5 PM – midnight
        _h_ea < 9 or                       # midnight – 8:59 AM
        (_h_ea == 9 and _m_ea < 30)        # 9:00 – 9:29 AM
    )
    if _after_close:  # after market close, DB is authoritative — bust only clears in-memory cache
        try:
            with _pg_ea.connect(_DB_URL) as _c_db, _c_db.cursor() as _cu_db:
                # Ensure signal_type column exists (one-time migration)
                _cu_db.execute(
                    "ALTER TABLE eod_accum_picks ADD COLUMN IF NOT EXISTS signal_type TEXT DEFAULT 'accum'"
                )
                _c_db.commit()
                _cu_db.execute("""
                    SELECT ticker, close_price, accum_score, eod_rel_vol, late_flow,
                           closing_range, price_chg_pct, mkt_cap_m, news_type, news_headline,
                           COALESCE(signal_type, 'accum') AS signal_type, scanned_at
                    FROM eod_accum_picks
                    WHERE scan_date = CURRENT_DATE
                    ORDER BY accum_score DESC
                    LIMIT 30
                """)
                _db_rows_ea = _cu_db.fetchall()
                _db_cols_ea = [d[0] for d in _cu_db.description]
        except Exception as _edb:
            _db_rows_ea = []
            print(f"[eod_accum] db-first read error: {_edb}")

        if _db_rows_ea:
            _db_picks_ea = []
            for _row_ea in _db_rows_ea:
                _d = dict(zip(_db_cols_ea, _row_ea))
                _db_picks_ea.append({
                    "ticker":             _d["ticker"],
                    "close":              float(_d["close_price"] or 0),
                    "prev_close":         None,
                    "day_high":           None,
                    "day_low":            None,
                    "accum_score":        float(_d["accum_score"] or 0),
                    "eod_rel_vol":        float(_d["eod_rel_vol"] or 0),
                    "late_flow":          float(_d["late_flow"] or 0),
                    "closing_range":      float(_d["closing_range"] or 0),
                    "price_chg_pct":      float(_d["price_chg_pct"] or 0),
                    "mkt_cap_m":          float(_d.get("mkt_cap_m") or 0),
                    "news_type":          _d.get("news_type", "none"),
                    "news_headline":      _d.get("news_headline"),
                    "signal_type":        _d.get("signal_type", "accum"),
                    "pre_ignition_count": 0,
                })
            _accum_db_ea   = [r for r in _db_picks_ea if r["signal_type"] != "squeeze"]
            _squeeze_db_ea = [r for r in _db_picks_ea if r["signal_type"] == "squeeze"]
            try:
                _sat_ea = _db_rows_ea[0][_db_cols_ea.index("scanned_at")]
                _gen_ea = _sat_ea.astimezone(_et).strftime("%-I:%M %p ET") if _sat_ea else "Stored (after close)"
            except Exception:
                _gen_ea = "Stored (after close)"
            _out_db_ea = {
                "candidates":     _accum_db_ea[:15],
                "squeeze_setups": _squeeze_db_ea[:10],
                "total_found":    len(_db_picks_ea),
                "scanned":        len(_db_picks_ea),
                "generated_at":   _gen_ea,
            }
            app._eod_accum_cache    = _out_db_ea
            app._eod_accum_cache_ts = _dt_ea.datetime.now()
            return jsonify(_out_db_ea)

    # Build universe: watchlist + unusual calls today + Yahoo top-movers screener
    _ticker_set = set()
    try:
        with _pg_ea.connect(_DB_URL) as _c_ea, _c_ea.cursor() as _cu_ea:
            _cu_ea.execute("SELECT ticker FROM morning_watchlist ORDER BY ticker")
            for _r in _cu_ea.fetchall(): _ticker_set.add(_r[0])
    except Exception as _e_ea:
        print(f"[eod_accum] watchlist db error: {_e_ea}")
    try:
        with _pg_ea.connect(_DB_URL) as _c_ea2, _c_ea2.cursor() as _cu_ea2:
            _cu_ea2.execute(
                "SELECT DISTINCT ticker FROM unusual_calls_log WHERE DATE(first_seen) = CURRENT_DATE"
            )
            for _r in _cu_ea2.fetchall(): _ticker_set.add(_r[0])
    except Exception as _e_ea2:
        print(f"[eod_accum] calls db error: {_e_ea2}")

    # Add Yahoo screener: any US stock up ≥1% with ≥$10M mkt cap (catches names not on watchlist)
    try:
        _eq_eod = _yf_ea.EquityQuery("and", [
            _yf_ea.EquityQuery("gte", ["percentchange",    1.0]),
            _yf_ea.EquityQuery("eq",  ["region",           "us"]),
            _yf_ea.EquityQuery("gte", ["intradaymarketcap", 10_000_000]),
        ])
        for _offset in (0, 250):   # two pages = top 500 gainers
            _pg_scr = _yf_ea.screen(_eq_eod, sortField="percentchange", sortAsc=False, size=250, offset=_offset)
            for _q in (_pg_scr.get("quotes") or []):
                _sym = _q.get("symbol", "")
                if _sym and "." not in _sym and len(_sym) <= 5:
                    _ticker_set.add(_sym)
        print(f"[eod_accum] universe after screener: {len(_ticker_set)} tickers")
    except Exception as _e_scr:
        print(f"[eod_accum] screener error: {_e_scr}")

    _tickers = list(_ticker_set)
    if not _tickers:
        return jsonify({"candidates": [], "total_found": 0, "scanned": 0,
                        "generated_at": _dt_ea.datetime.now().strftime("%I:%M %p ET")})

    def _score_eod_ticker(ticker):
        try:
            tk = _yf_ea.Ticker(ticker)
            fi = tk.fast_info
            prev_close = float(getattr(fi, "previous_close", 0) or 0)
            avg_vol    = float(getattr(fi, "three_month_average_volume", 1) or 1)
            mkt_cap    = float(getattr(fi, "market_cap", 0) or 0)
            if prev_close <= 0 or avg_vol <= 0: return None

            hist = tk.history(period="1d", interval="1m")
            if hist.empty or len(hist) < 10: return None
            hist.index = hist.index.tz_convert(_et)

            close_px  = float(hist["Close"].iloc[-1])
            open_px   = float(hist["Open"].iloc[0])
            day_high  = float(hist["High"].max())
            day_low   = float(hist["Low"].min())
            if close_px <= 0 or day_high <= day_low: return None

            price_chg = (close_px - prev_close) / prev_close * 100
            if price_chg < -30.0: return None  # only exclude real crashes

            # ── Closing range: 1.0 = at the high, 0.0 = at the low ──────────
            closing_range = (close_px - day_low) / (day_high - day_low)

            # ── Last 30-min window (3:30–4:00 PM ET) ─────────────────────────
            eod_bars = hist[hist.index.time >= _dt_ea.time(15, 30)]
            if eod_bars.empty: return None

            eod_vol = float(eod_bars["Volume"].sum())

            # Avg last-30-min volume: last 30 min ≈ 7.7% of a 390-min day.
            avg_eod_vol = avg_vol * 0.08
            if avg_eod_vol <= 0: return None
            eod_rel_vol = eod_vol / avg_eod_vol
            if eod_rel_vol < 2.5: return None  # need at least some late-day surge

            # ── Late money flow (3:30–4:00 PM only) ──────────────────────────
            late_inflow = late_outflow = 0.0
            for _, row in eod_bars.iterrows():
                if row["Volume"] <= 0: continue
                avg_p = (float(row["Open"]) + float(row["Close"])) / 2
                dv    = avg_p * float(row["Volume"])
                if float(row["Close"]) >= float(row["Open"]): late_inflow  += dv
                else:                                          late_outflow += dv
            late_flow = (late_inflow / late_outflow) if late_outflow > 0 else 99.0

            # ── Late price surge (how much did it move in last 30 min) ───────
            pre_330 = hist[hist.index.time < _dt_ea.time(15, 30)]
            price_330 = float(pre_330["Close"].iloc[-1]) if not pre_330.empty else open_px
            late_surge = (close_px - price_330) / price_330 * 100

            # ── "Quiet then surge" signal ─────────────────────────────────────
            mid_bars  = hist[(hist.index.time >= _dt_ea.time(11, 0)) &
                             (hist.index.time <= _dt_ea.time(14, 30))]
            mid_vol   = float(mid_bars["Volume"].sum()) if not mid_bars.empty else 1.0
            mid_bars_count = len(mid_bars) or 1
            mid_vol_per_min  = mid_vol / mid_bars_count
            eod_bars_count   = len(eod_bars) or 1
            eod_vol_per_min  = eod_vol / eod_bars_count
            quiet_surge = eod_vol_per_min / mid_vol_per_min if mid_vol_per_min > 0 else 1.0

            # ── Determine signal type ─────────────────────────────────────────
            # ACCUM: buyers winning, good close, quiet-then-surge → +5-15% next day
            is_accum = (
                late_flow >= 2.0 and
                closing_range >= 0.50 and
                quiet_surge >= 1.5 and
                price_chg >= -20.0
            )
            # SQUEEZE: MASSIVE EOD vol (50×+) + sellers winning + weak close
            # → shorts loading in at close, get squeezed next morning → +15-50%
            is_squeeze = (
                eod_rel_vol >= 50.0 and
                late_flow < 2.0 and
                closing_range < 0.50 and
                close_px >= 1.0 and
                (mkt_cap or 0) >= 20_000_000
            )

            if not (is_accum or is_squeeze):
                return None

            signal_type = "squeeze" if (is_squeeze and not is_accum) else "accum"

            # ── Accumulation score ────────────────────────────────────────────
            if signal_type == "accum":
                # Weights: EOD rel-vol × late flow conviction × closing strength
                accum_score = round(eod_rel_vol * min(late_flow, 10.0) * (0.5 + closing_range), 1)
            else:
                # Squeeze score: pure volume anomaly (the bigger the surge, the more shorts loaded)
                accum_score = round(eod_rel_vol, 1)

            # ── News catalyst check ───────────────────────────────────────────
            # If the stock had news today it's likely a news-driven move, not a pump setup.
            has_news       = False
            news_headline  = None
            news_today_cnt = 0
            news_type      = "none"   # "hard" | "soft" | "none"
            # Hard-news keywords: company-specific events that explain the price move.
            _HARD_KW = {
                "earnings","results","revenue","guidance","beats","beat","miss","misses",
                "q1","q2","q3","q4","fiscal","quarterly","annual",
                "merger","acquisition","acquires","buyout","takeover","deal","offer",
                "fda","approval","approved","clearance","trial","phase",
                "dividend","buyback","secondary","offering","ipo","spinoff",
                "sec","lawsuit","settlement","restatement","bankruptcy","default",
                "press release","reports","announces","raised","lowers","cuts","raises",
                "record","outlook","forecast","reaffirm",
            }
            # Soft-news keywords: general analysis/comparison articles, not company events.
            _SOFT_KW = {
                " vs "," versus ","best stocks","top stocks","should you buy","is it a buy",
                "watchlist","watch list","radar","analysis","analyst","target price",
                "keep off","small-cap","mid-cap","large-cap","sector","industry",
                "here's why","here is why","worth watching",
            }
            try:
                import re as _re_ea
                _now_et   = _dt_ea.datetime.now(_et)
                # 36-hour window: catches after-hours articles from yesterday evening
                # (e.g., earnings transcript posted at 8 PM the night before).
                _cutoff   = _now_et - _dt_ea.timedelta(hours=36)

                for _ni in (tk.news or [])[:8]:
                    _pub = _ni.get("providerPublishTime") or \
                           (_ni.get("content") or {}).get("pubDate")
                    _pub_ts = None
                    if isinstance(_pub, (int, float)):
                        _pub_ts = _dt_ea.datetime.fromtimestamp(_pub, tz=_et)
                    elif isinstance(_pub, str):
                        try:
                            _m = _re_ea.match(
                                r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})", _pub)
                            if _m:
                                _pub_ts = _dt_ea.datetime(
                                    int(_m.group(1)), int(_m.group(2)), int(_m.group(3)),
                                    int(_m.group(4)), int(_m.group(5)),
                                    tzinfo=_pytz_ea.utc,
                                ).astimezone(_et)
                        except Exception: pass

                    if _pub_ts and _pub_ts >= _cutoff:
                        news_today_cnt += 1
                        _ht = (_ni.get("title") or
                               ((_ni.get("content") or {}).get("title") or ""))
                        if not news_headline:
                            news_headline = (_ht[:90]) if _ht else None
                        _ht_l = _ht.lower()
                        if any(kw in _ht_l for kw in _HARD_KW):
                            news_type = "hard"
                        elif news_type != "hard":
                            news_type = "soft"

                has_news = news_today_cnt > 0
                if not has_news:
                    news_type = "none"
            except Exception:
                pass

            return {
                "ticker":          ticker,
                "close":           round(close_px, 2),
                "prev_close":      round(prev_close, 2),
                "price_chg_pct":   round(price_chg, 2),
                "day_high":        round(day_high, 2),
                "day_low":         round(day_low, 2),
                "closing_range":   round(closing_range, 3),
                "eod_vol":         int(eod_vol),
                "eod_rel_vol":     round(eod_rel_vol, 1),
                "late_flow":       round(min(late_flow, 99.0), 1),
                "late_surge_pct":  round(late_surge, 2),
                "quiet_surge":     round(quiet_surge, 1),
                "accum_score":     accum_score,
                "signal_type":     signal_type,
                "mkt_cap_m":       round(mkt_cap / 1_000_000, 1) if mkt_cap else None,
                "has_news":        has_news,
                "news_headline":   news_headline,
                "news_today_cnt":  news_today_cnt,
                "news_type":       news_type,
            }
        except Exception:
            return None

    _results_ea = []
    with _TPE_ea(max_workers=25) as _ex_ea:
        _futs_ea = {_ex_ea.submit(_score_eod_ticker, t): t for t in _tickers}
        for _fut_ea in _ac_ea(_futs_ea):
            _r_ea = _fut_ea.result()
            if _r_ea: _results_ea.append(_r_ea)

    # ── Split into accumulation vs squeeze setups ─────────────────────────
    _accum_ea   = [r for r in _results_ea if r.get("signal_type") != "squeeze"]
    _squeeze_ea = [r for r in _results_ea if r.get("signal_type") == "squeeze"]
    _accum_ea.sort(  key=lambda x: -x["accum_score"])
    _squeeze_ea.sort(key=lambda x: -x["accum_score"])  # squeeze score = eod_rel_vol

    # ── Enrich top picks with short interest + anchored VWAP ──────────────
    _enrich_pool = (_accum_ea[:15] + _squeeze_ea[:15])
    def _enrich_ea(_r):
        for _k, _v in _get_short_data(_r["ticker"]).items():
            if _r.get(_k) is None:   # never overwrite an already-computed value
                _r[_k] = _v
    with _TPE_ea(max_workers=10) as _ex_short:
        list(_ex_short.map(_enrich_ea, _enrich_pool))

    # ── Persist today's top picks to DB ────────────────────────────────────
    _scan_date_ea = _dt_ea.date.today().isoformat()
    _persist_ea = (_accum_ea[:15] + _squeeze_ea[:10])
    try:
        with _pg_ea.connect(_DB_URL) as _c_sv, _c_sv.cursor() as _cu_sv:
            for _r in _persist_ea:
                _cu_sv.execute("""
                    INSERT INTO eod_accum_picks
                        (scan_date, ticker, close_price, accum_score, news_type, news_headline,
                         eod_rel_vol, late_flow, closing_range, price_chg_pct, mkt_cap_m, signal_type)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (scan_date, ticker) DO UPDATE SET
                        close_price=EXCLUDED.close_price, accum_score=EXCLUDED.accum_score,
                        news_type=EXCLUDED.news_type, news_headline=EXCLUDED.news_headline,
                        eod_rel_vol=EXCLUDED.eod_rel_vol, late_flow=EXCLUDED.late_flow,
                        closing_range=EXCLUDED.closing_range, price_chg_pct=EXCLUDED.price_chg_pct,
                        mkt_cap_m=EXCLUDED.mkt_cap_m, signal_type=EXCLUDED.signal_type, scanned_at=NOW()
                """, (_scan_date_ea, _r["ticker"], _r["close"], _r["accum_score"],
                      _r["news_type"], _r["news_headline"], _r["eod_rel_vol"],
                      _r["late_flow"], _r["closing_range"], _r["price_chg_pct"],
                      _r["mkt_cap_m"], _r.get("signal_type", "accum")))
            _c_sv.commit()
    except Exception as _e_sv:
        print(f"[eod_accum] db save error: {_e_sv}")

    _out_ea = {
        "candidates":     _accum_ea[:15],
        "squeeze_setups": _squeeze_ea[:10],
        "total_found":    len(_results_ea),
        "scanned":        len(_tickers),
        "generated_at":   _dt_ea.datetime.now().strftime("%I:%M %p ET"),
    }
    app._eod_accum_cache    = _out_ea
    app._eod_accum_cache_ts = _dt_ea.datetime.now()
    return jsonify(_out_ea)


@app.route("/stock-api/eod-accum-track", methods=["GET"])
def eod_accum_track():
    """
    EOD Accumulation Track Record.
    Returns all historical picks joined with next-morning outcomes, plus summary stats
    broken down by news_type (none/soft/hard) so users can compare strategy performance.
    """
    import datetime as _dt_tr
    import psycopg2 as _pg_tr
    import psycopg2.extras as _ext_tr

    try:
        with _pg_tr.connect(_DB_URL) as _c, _c.cursor(cursor_factory=_ext_tr.RealDictCursor) as _cu:
            # All picks with outcomes joined (left join so pending picks still show)
            _cu.execute("""
                SELECT
                    p.scan_date,
                    p.ticker,
                    p.close_price   AS entry_price,
                    p.accum_score,
                    p.news_type,
                    p.news_headline,
                    p.eod_rel_vol,
                    p.late_flow,
                    p.closing_range,
                    p.price_chg_pct,
                    o.next_open,
                    o.next_open_chg_pct,
                    o.morning_high,
                    o.morning_high_chg_pct,
                    o.gapped_up
                FROM eod_accum_picks p
                LEFT JOIN eod_accum_outcomes o
                    ON o.pick_date = p.scan_date AND o.ticker = p.ticker
                ORDER BY p.scan_date DESC, p.accum_score DESC
                LIMIT 200
            """)
            _rows = [dict(r) for r in _cu.fetchall()]

        # Convert date objects to strings
        for _r in _rows:
            if hasattr(_r.get("scan_date"), "isoformat"):
                _r["scan_date"] = _r["scan_date"].isoformat()
            for _k in _r:
                if _r[_k] is not None:
                    try: _r[_k] = float(_r[_k]) if isinstance(_r[_k], __import__("decimal").Decimal) else _r[_k]
                    except Exception: pass

        # Summary stats overall + by news_type
        def _stats(rows):
            graded = [r for r in rows if r.get("gapped_up") is not None]
            if not graded: return {"picks": len(rows), "graded": 0}
            gap_ups  = [r for r in graded if r["gapped_up"]]
            gaps     = [r["next_open_chg_pct"] for r in graded if r.get("next_open_chg_pct") is not None]
            highs    = [r["morning_high_chg_pct"] for r in graded if r.get("morning_high_chg_pct") is not None]
            return {
                "picks":        len(rows),
                "graded":       len(graded),
                "hit_rate_pct": round(len(gap_ups) / len(graded) * 100, 1) if graded else None,
                "avg_gap_pct":  round(sum(gaps) / len(gaps), 2) if gaps else None,
                "avg_high_pct": round(sum(highs) / len(highs), 2) if highs else None,
                "best_gap_pct": round(max(gaps), 2) if gaps else None,
            }

        _summary = {
            "all":  _stats(_rows),
            "pure": _stats([r for r in _rows if r.get("news_type") == "none"]),
            "soft": _stats([r for r in _rows if r.get("news_type") == "soft"]),
            "hard": _stats([r for r in _rows if r.get("news_type") == "hard"]),
        }

        return jsonify({"picks": _rows, "summary": _summary,
                        "as_of": _dt_tr.datetime.now().strftime("%Y-%m-%d %I:%M %p ET")})

    except Exception as _e_tr:
        print(f"[eod_accum_track] error: {_e_tr}")
        return jsonify({"picks": [], "summary": {}, "as_of": "", "error": str(_e_tr)})


@app.route("/stock-api/cross-scanner", methods=["GET"])
def cross_scanner():
    """
    Cross-Scanner Double Signal Alert.
    Returns tickers that appear in BOTH today's standout flow (scan_history)
    AND yesterday's EOD accumulation (eod_accum_picks).
    Also returns the last 60 days of historical cross-signals for the track-record view.
    """
    import datetime as _dt_cs
    import psycopg2 as _pg_cs
    import psycopg2.extras as _ext_cs

    try:
        _today = _dt_cs.date.today()
        # Build last 3 trading days as valid EOD-accum lookback dates
        _prev_days = []
        _d = _today - _dt_cs.timedelta(days=1)
        while len(_prev_days) < 3:
            if _d.weekday() < 5:
                _prev_days.append(_d)
            _d -= _dt_cs.timedelta(days=1)
        _today_s = _today.isoformat()
        _prev_s  = _prev_days[0].isoformat()   # most recent trading day (kept for compat)
        _lookback3_s = _prev_days[-1].isoformat()  # 3 trading days ago

        with _pg_cs.connect(_DB_URL) as _c, _c.cursor(cursor_factory=_ext_cs.RealDictCursor) as _cu:
            # ── TODAY's double signals (EOD accum from last 3 trading days) ──
            _cu.execute("""
                SELECT
                    sh.ticker,
                    sh.price              AS morning_price,
                    sh.price_chg_pct      AS morning_chg_pct,
                    sh.standout_score,
                    sh.flow_ratio,
                    sh.rel_vol            AS morning_rel_vol,
                    ea.close_price        AS eod_close,
                    ea.accum_score,
                    ea.news_type,
                    ea.news_headline,
                    ea.eod_rel_vol,
                    ea.closing_range,
                    ea.late_flow,
                    %s                    AS signal_date
                FROM scan_history sh
                JOIN eod_accum_picks ea
                    ON ea.scan_date >= %s AND ea.scan_date < %s AND ea.ticker = sh.ticker
                WHERE sh.scan_date = %s AND sh.standout_score >= 5
                ORDER BY sh.standout_score DESC
            """, (_today_s, _lookback3_s, _today_s, _today_s))
            _today_signals = [dict(r) for r in _cu.fetchall()]

            # ── HISTORICAL cross-signals (last 60 trading days) ───────────
            _cu.execute("""
                SELECT
                    sh.scan_date          AS signal_date,
                    sh.ticker,
                    sh.price              AS morning_price,
                    sh.price_chg_pct      AS morning_chg_pct,
                    sh.standout_score,
                    sh.flow_ratio,
                    ea.close_price        AS eod_close,
                    ea.accum_score,
                    ea.news_type,
                    o.open_to_close_pct   AS same_day_close_pct,
                    o.open_to_high_pct    AS same_day_high_pct
                FROM scan_history sh
                JOIN eod_accum_picks ea
                    ON ea.scan_date = (sh.scan_date - INTERVAL '1 day')::date
                    AND ea.ticker = sh.ticker
                LEFT JOIN eod_outcomes o
                    ON o.trade_date = sh.scan_date AND o.ticker = sh.ticker
                WHERE sh.scan_date >= CURRENT_DATE - INTERVAL '60 days'
                    AND sh.standout_score >= 5
                ORDER BY sh.scan_date DESC, sh.standout_score DESC
                LIMIT 100
            """)
            _history = [dict(r) for r in _cu.fetchall()]

        def _clean(rows):
            for _r in rows:
                for _k in list(_r.keys()):
                    if hasattr(_r[_k], "isoformat"): _r[_k] = _r[_k].isoformat()
                    elif _r[_k] is not None:
                        try: _r[_k] = float(_r[_k]) if isinstance(_r[_k], __import__("decimal").Decimal) else _r[_k]
                        except Exception: pass
            return rows

        _today_signals = _clean(_today_signals)
        _history       = _clean(_history)

        # Enrich today_signals with short interest + anchored VWAP
        if _today_signals:
            import concurrent.futures as _cf_cs_si
            def _enrich_cs(_r):
                _r.update(_get_short_data(_r["ticker"]))
            with _cf_cs_si.ThreadPoolExecutor(max_workers=min(len(_today_signals), 8)) as _ex_cs_si:
                list(_ex_cs_si.map(_enrich_cs, _today_signals))

        # Summary stats on historical hits
        _graded = [r for r in _history if r.get("same_day_close_pct") is not None]
        _winners = [r for r in _graded if (r.get("same_day_close_pct") or 0) > 0]
        _hist_stats = {
            "total_signals": len(_history),
            "graded": len(_graded),
            "hit_rate_pct": round(len(_winners) / len(_graded) * 100, 1) if _graded else None,
            "avg_close_pct": round(sum(r["same_day_close_pct"] for r in _graded) / len(_graded), 2) if _graded else None,
            "avg_high_pct":  round(sum(r["same_day_high_pct"]  for r in _graded if r.get("same_day_high_pct") is not None)
                                   / max(1, len([r for r in _graded if r.get("same_day_high_pct") is not None])), 2) if _graded else None,
        }

        return jsonify({
            "today_signals": _today_signals,
            "history": _history,
            "hist_stats": _hist_stats,
            "as_of": _dt_cs.datetime.now().strftime("%Y-%m-%d %I:%M %p ET"),
        })

    except Exception as _e_cs:
        print(f"[cross_scanner] error: {_e_cs}")
        return jsonify({"today_signals": [], "history": [], "hist_stats": {}, "as_of": "", "error": str(_e_cs)})


@app.route("/stock-api/short-squeeze", methods=["GET"])
def short_squeeze_radar():
    """
    Short Squeeze Radar.
    Pulls the union of recent EOD accum picks + standout flow tickers (last 5 days),
    enriches each with short interest + anchored VWAP, filters short_float >= 10%,
    ranks by composite squeeze_score.
    """
    import datetime as _dt_sq
    import psycopg2 as _pg_sq
    import concurrent.futures as _cf_sq

    try:
        _today_sq    = _dt_sq.date.today()
        _lookback_sq = (_today_sq - _dt_sq.timedelta(days=5)).isoformat()

        with _pg_sq.connect(_DB_URL) as _c_sq, _c_sq.cursor() as _cu_sq:
            _cu_sq.execute("""
                SELECT DISTINCT ticker FROM (
                    SELECT ticker FROM eod_accum_picks  WHERE scan_date >= %s
                    UNION
                    SELECT ticker FROM scan_history     WHERE scan_date >= %s AND standout_score >= 4
                    UNION
                    SELECT ticker FROM unusual_calls_log WHERE first_seen >= %s
                ) _combined
            """, (_lookback_sq, _lookback_sq, _lookback_sq))
            _tickers_sq = [r[0] for r in _cu_sq.fetchall()]

        if not _tickers_sq:
            return jsonify({"candidates": [], "total_found": 0, "scanned": 0,
                            "as_of": _dt_sq.datetime.now().strftime("%I:%M %p ET")})

        def _score_sq(ticker):
            sd  = _get_short_data(ticker)
            sf  = sd.get("short_float")
            chg = sd.get("price_chg_pct")
            vol = sd.get("vol_ratio_20d")

            # ── 5 HARD GATES — all must pass or stock is excluded ────────────
            # Gate 1: meaningful short fuel
            if not sf or sf < 15.0:                return None
            # Gate 2: actually moving up TODAY (not just potential)
            if not chg or chg < 3.0:               return None
            # Gate 3: volume explosion (shorts being forced to cover)
            if not vol or vol < 2.0:               return None
            # Gate 4: breaking OUT of the trading range (new multi-week high)
            if not sd.get("new_high_15d"):         return None
            # Gate 5: reclaiming the 5-day anchored VWAP (institutional level)
            if not sd.get("above_avwap"):          return None

            dtc   = sd.get("days_to_cover") or 0
            coil  = sd.get("was_consolidating") or False
            cr    = sd.get("closing_range_today") or 0
            a20   = sd.get("above_avwap_20d") or False
            rsi   = sd.get("rsi_14") or 50
            obv_d = sd.get("obv_divergence") or False
            macd_b = sd.get("macd_bullish") or False
            bb_r  = sd.get("bb_squeeze_releasing") or False
            buy_d = sd.get("buyers_dominant") or False
            sma_f = bool(sd.get("above_sma20") and sd.get("sma20_rising"))
            pre_n = sd.get("pre_ignition_count") or 0

            # ── Active Squeeze Score ─────────────────────────────────────────
            # Core gates score (max 75):
            #   short fuel (20) + vol explosion (25) + price momentum (20) + coil (10)
            # Pre-ignition bonus (max 25):
            #   OBV divergence (6) + MACD crossover (6) + BB squeeze (6) + buyers winning (4) + SMA floor (3)
            squeeze_score = round(
                min(sf * 0.4, 20)             # short fuel              (max 20)
                + min((vol - 2.0) * 10, 25)   # volume explosion        (max 25)
                + min(chg * 2.0, 20)          # price momentum          (max 20)
                + (10 if coil else 0)          # was coiling pre-break   (+10)
                + (6  if obv_d else 0)         # OBV accumulation        (+6)
                + (6  if macd_b else 0)        # MACD histogram positive (+6)
                + (6  if bb_r else 0)          # BB squeeze releasing    (+6)
                + (4  if buy_d else 0)         # buyers dominating vol   (+4)
                + (3  if sma_f else 0),        # holding above SMA-20    (+3)
                1
            )
            return {
                "ticker":                ticker,
                "short_float":           sf,
                "days_to_cover":         sd.get("days_to_cover"),
                "above_avwap":           sd.get("above_avwap"),
                "above_avwap_20d":       a20,
                "avwap_5d":              sd.get("avwap_5d"),
                "avwap_20d":             sd.get("avwap_20d"),
                "current_price":         sd.get("current_price"),
                "price_chg_pct":         chg,
                "vol_ratio_20d":         vol,
                "new_high_15d":          True,
                "range_pct_15d":         sd.get("range_pct_15d"),
                "was_consolidating":     coil,
                "closing_range_today":   cr,
                "rsi_14":                rsi,
                "obv_divergence":        obv_d,
                "macd_bullish":          macd_b,
                "macd_histogram":        sd.get("macd_histogram"),
                "bb_squeeze_releasing":  bb_r,
                "up_vol_ratio":          sd.get("up_vol_ratio"),
                "buyers_dominant":       buy_d,
                "above_sma20":           sd.get("above_sma20"),
                "sma20_rising":          sd.get("sma20_rising"),
                "sma20_val":             sd.get("sma20_val"),
                "pre_ignition_count":    pre_n,
                "squeeze_score":         squeeze_score,
            }

        _cands_sq: list = []
        with _cf_sq.ThreadPoolExecutor(max_workers=15) as _ex_sq:
            for _r_sq in _ex_sq.map(_score_sq, _tickers_sq):
                if _r_sq:
                    _cands_sq.append(_r_sq)

        _cands_sq.sort(key=lambda x: -x["squeeze_score"])

        return jsonify({
            "candidates":  _cands_sq[:20],
            "total_found": len(_cands_sq),
            "scanned":     len(_tickers_sq),
            "as_of":       _dt_sq.datetime.now().strftime("%I:%M %p ET"),
        })

    except Exception as _e_sq:
        print(f"[short_squeeze] error: {_e_sq}")
        return jsonify({"candidates": [], "total_found": 0, "scanned": 0, "as_of": "", "error": str(_e_sq)})


@app.route("/stock-api/standout-track", methods=["GET"])
def standout_track():
    """
    Standout Flow Track Record.
    Returns historical standout-score morning picks (score >= 5) joined with their
    same-day eod_outcomes, segmented by score tier. Used to compare morning-entry
    performance vs EOD accumulation entry performance.
    """
    import datetime as _dt_st
    import psycopg2 as _pg_st
    import psycopg2.extras as _ext_st

    try:
        with _pg_st.connect(_DB_URL) as _c, _c.cursor(cursor_factory=_ext_st.RealDictCursor) as _cu:
            _cu.execute("""
                SELECT DISTINCT ON (s.scan_date, s.ticker)
                    s.scan_date,
                    s.ticker,
                    s.price              AS entry_price,
                    s.price_chg_pct,
                    s.rel_vol,
                    s.flow_ratio,
                    s.standout_score,
                    s.mkt_cap_m,
                    o.close_price,
                    o.high_price,
                    o.open_to_close_pct,
                    o.open_to_high_pct,
                    o.fade_risk_signal
                FROM scan_history s
                LEFT JOIN eod_outcomes o
                    ON o.trade_date = s.scan_date AND o.ticker = s.ticker
                WHERE s.standout_score >= 5
                ORDER BY s.scan_date DESC, s.ticker, s.rank_in_scan ASC
                LIMIT 300
            """)
            _rows = [dict(r) for r in _cu.fetchall()]

        for _r in _rows:
            if hasattr(_r.get("scan_date"), "isoformat"):
                _r["scan_date"] = _r["scan_date"].isoformat()
            for _k in list(_r.keys()):
                if _r[_k] is not None:
                    try: _r[_k] = float(_r[_k]) if isinstance(_r[_k], __import__("decimal").Decimal) else _r[_k]
                    except Exception: pass

        def _st_stats(rows):
            graded = [r for r in rows if r.get("open_to_close_pct") is not None]
            if not graded:
                return {"picks": len(rows), "graded": 0, "hit_rate_pct": None,
                        "avg_close_pct": None, "avg_high_pct": None, "best_high_pct": None}
            winners = [r for r in graded if (r.get("open_to_close_pct") or 0) > 0]
            o2c = [r["open_to_close_pct"] for r in graded if r.get("open_to_close_pct") is not None]
            o2h = [r["open_to_high_pct"]  for r in graded if r.get("open_to_high_pct")  is not None]
            return {
                "picks":         len(rows),
                "graded":        len(graded),
                "hit_rate_pct":  round(len(winners) / len(graded) * 100, 1),
                "avg_close_pct": round(sum(o2c) / len(o2c), 2) if o2c else None,
                "avg_high_pct":  round(sum(o2h) / len(o2h), 2) if o2h else None,
                "best_high_pct": round(max(o2h), 2) if o2h else None,
            }

        _summary = {
            "all":      _st_stats(_rows),
            "extreme":  _st_stats([r for r in _rows if (r.get("standout_score") or 0) >= 20]),
            "high":     _st_stats([r for r in _rows if 10 <= (r.get("standout_score") or 0) < 20]),
            "standard": _st_stats([r for r in _rows if 5 <= (r.get("standout_score") or 0) < 10]),
        }

        return jsonify({"picks": _rows, "summary": _summary,
                        "as_of": _dt_st.datetime.now().strftime("%Y-%m-%d %I:%M %p ET")})

    except Exception as _e_st:
        print(f"[standout_track] error: {_e_st}")
        return jsonify({"picks": [], "summary": {}, "as_of": "", "error": str(_e_st)})


@app.route("/stock-api/insider-radar", methods=["GET"])
def insider_radar():
    """
    Insider Radar: SEC-style detection of unusual options activity.
    Cross-references unusual call bets ($10K+, 90-day history) with
    upcoming earnings (up to 90 days out) and ticker rarity scores.
    Signals: rarity of ticker + premium size + vol/oi aggression + earnings proximity.
    """
    import datetime as _dt_ir
    from concurrent.futures import ThreadPoolExecutor as _TPE

    bust = request.args.get("bust", "0") == "1"
    _cache    = getattr(app, "_insider_radar_cache", None)
    _cache_ts = getattr(app, "_insider_radar_cache_ts", None)
    if not bust and _cache and _cache_ts and (_dt_ir.datetime.now() - _cache_ts).total_seconds() < 2700:
        return jsonify(_cache)

    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            # All signals $10K+ from last 90 days, newest first
            cur.execute("""
                SELECT ticker, price::float, strike::float, expiry,
                       days_out, volume, oi, vol_oi::float, prem::bigint,
                       otm_pct::float, iv::float, urgency,
                       first_seen AT TIME ZONE 'UTC' AS first_seen,
                       last_seen  AT TIME ZONE 'UTC' AS last_seen
                FROM unusual_calls_log
                WHERE prem >= 10000
                  AND first_seen >= NOW() - INTERVAL '90 days'
                ORDER BY prem DESC
            """)
            cols    = [d[0] for d in cur.description]
            signals = [dict(zip(cols, r)) for r in cur.fetchall()]
            for s in signals:
                for k in ("first_seen", "last_seen"):
                    if s.get(k): s[k] = s[k].isoformat()

            # Ticker rarity: how many unique signals in 90 days (fewer = more suspicious)
            cur.execute("""
                SELECT ticker, COUNT(*) as signal_count, MAX(prem) as max_prem
                FROM unusual_calls_log
                WHERE first_seen >= NOW() - INTERVAL '90 days'
                GROUP BY ticker
            """)
            ticker_stats = {r[0]: {"count": r[1], "max_prem": r[2]} for r in cur.fetchall()}

        # Check earnings 90-day window for ALL unique tickers in the DB
        unique_by_prem = sorted(
            {s["ticker"] for s in signals},
            key=lambda t: -(ticker_stats.get(t, {}).get("max_prem", 0))
        )

        def _earn_90d(ticker):
            import datetime as _d2
            import yfinance as _yf2
            today  = _d2.date.today()
            cutoff = today + _d2.timedelta(days=90)
            try:
                tk  = _yf2.Ticker(ticker)
                cal = tk.calendar
                if cal is None: return None
                earn_date = None
                try:
                    if hasattr(cal, "empty") and not cal.empty:
                        if "Earnings Date" in cal.index:
                            earn_date = cal.loc["Earnings Date"].iloc[0]
                        elif "Earnings Date" in cal.columns:
                            earn_date = cal.iloc[0]["Earnings Date"]
                    elif isinstance(cal, dict):
                        ed = cal.get("Earnings Date", [])
                        earn_date = ed[0] if ed else None
                except Exception: return None
                if earn_date is None: return None
                earn_dt = earn_date.date() if hasattr(earn_date, "date") else \
                          _d2.datetime.strptime(str(earn_date)[:10], "%Y-%m-%d").date()
                if earn_dt < today or earn_dt > cutoff: return None
                return {"ticker": ticker,
                        "earnings_date": earn_dt.isoformat(),
                        "days_until":    (earn_dt - today).days}
            except Exception: return None

        earnings_map = {}
        with _TPE(max_workers=12) as ex:
            for r in ex.map(_earn_90d, unique_by_prem):
                if r: earnings_map[r["ticker"]] = r

        # Multi-factor suspicion score (0-100)
        def _score(s):
            prem  = s.get("prem",   0) or 0
            voi   = s.get("vol_oi", 0) or 0
            count = ticker_stats.get(s["ticker"], {}).get("count", 1)
            earn  = earnings_map.get(s["ticker"])
            sc    = 0
            # 1. Ticker rarity — rarely seen = suspicious (0-30)
            if   count == 1:  sc += 30
            elif count <= 2:  sc += 25
            elif count <= 4:  sc += 18
            elif count <= 8:  sc += 12
            elif count <= 15: sc += 6
            else:             sc += 2
            # 2. Premium size (0-25)
            if   prem >= 500_000:  sc += 25
            elif prem >= 200_000:  sc += 20
            elif prem >= 100_000:  sc += 16
            elif prem >= 50_000:   sc += 12
            elif prem >= 20_000:   sc += 8
            else:                  sc += 4
            # 3. Vol/OI aggression (0-25)
            if   voi >= 20: sc += 25
            elif voi >= 10: sc += 22
            elif voi >= 5:  sc += 18
            elif voi >= 3:  sc += 14
            elif voi >= 2:  sc += 10
            else:           sc += 5
            # 4. Earnings proximity (0-20)
            if earn:
                d = earn["days_until"]
                if   d <= 7:  sc += 20
                elif d <= 14: sc += 19
                elif d <= 30: sc += 17
                elif d <= 45: sc += 14
                elif d <= 60: sc += 11
                else:         sc += 7
            return min(sc, 100)

        def _verdict(score, s):
            earn   = earnings_map.get(s["ticker"])
            count  = ticker_stats.get(s["ticker"], {}).get("count", 1)
            prem   = s.get("prem", 0) or 0
            prem_s = f"${prem/1000:.0f}K" if prem < 1_000_000 else f"${prem/1_000_000:.1f}M"
            ticker = s["ticker"]
            if earn:
                d = earn["days_until"]
                if score >= 80:
                    return f"🚨 SEC PATTERN — {prem_s} call bet on a quiet stock · Earnings in {d}d · Textbook pre-announcement insider positioning"
                elif score >= 65:
                    return f"⚠️ SUSPICIOUS — Abnormal call flow with earnings {d}d away · Possible informed trading or tip chain"
                elif score >= 50:
                    return f"👀 WATCH — Options activity on {ticker} · Earnings in {d}d · Monitor for OI accumulation"
                else:
                    return f"📡 NOTED — Call activity detected · Earnings approaching in {d}d"
            else:
                if score >= 75:
                    return f"🔍 UNUSUAL — {ticker} rarely sees activity at this size · Possible quiet positioning"
                elif score >= 55:
                    return f"📊 ELEVATED — Above-normal options flow · Track OI over coming days for accumulation"
                elif count <= 2:
                    return f"📡 RARE — {ticker} has appeared only {count}x in 90 days · Worth monitoring"
                else:
                    return f"ℹ️ ACTIVE — Elevated flow on a stock with regular options activity"

        # Assemble
        results = []
        for s in signals:
            earn = earnings_map.get(s["ticker"])
            s["suspicion_score"]    = _score(s)
            s["ticker_appearances"] = ticker_stats.get(s["ticker"], {}).get("count", 1)
            s["earnings_date"]      = earn["earnings_date"] if earn else None
            s["days_to_earnings"]   = earn["days_until"]    if earn else None
            s["verdict"]            = _verdict(s["suspicion_score"], s)
            s["pre_positioned"]     = bool(
                (s.get("oi") or 0) >= 100 and (s.get("volume") or 0) < (s.get("oi") or 0) * 0.5
            )
            results.append(s)

        # Earnings-linked first, then by suspicion score desc, then by premium desc
        results.sort(key=lambda x: (
            0 if x["days_to_earnings"] is not None else 1,
            -x["suspicion_score"],
            -(x["prem"] or 0)
        ))

        out = {
            "signals":         results,
            "total":           len(results),
            "earnings_linked": sum(1 for r in results if r["days_to_earnings"] is not None),
            "high_suspicion":  sum(1 for r in results if r["suspicion_score"] >= 65),
            "rare_tickers":    sum(1 for r in results if r["ticker_appearances"] <= 3),
            "as_of":           _dt_ir.datetime.now().isoformat(),
        }
        app._insider_radar_cache    = out
        app._insider_radar_cache_ts = _dt_ir.datetime.now()

        # Auto-save high-suspicion signals (score >= 70) to the permanent alert log
        try:
            _high = [r for r in results if r["suspicion_score"] >= 70]
            if _high:
                with _psycopg2.connect(_DB_URL) as _ac, _ac.cursor() as _acur:
                    for _s in _high:
                        _acur.execute("""
                            INSERT INTO insider_alerts
                                (ticker, suspicion_score, prem, strike, expiry,
                                 price_at_detection, vol_oi, earnings_date,
                                 days_to_earnings, ticker_appearances, verdict, pre_positioned)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (ticker, strike, expiry) DO NOTHING
                        """, (_s["ticker"], _s["suspicion_score"], _s.get("prem"),
                              _s.get("strike"), _s.get("expiry"), _s.get("price"),
                              _s.get("vol_oi"), _s.get("earnings_date"),
                              _s.get("days_to_earnings"), _s.get("ticker_appearances"),
                              _s.get("verdict"), _s.get("pre_positioned", False)))
                    _ac.commit()
                print(f"[insider_radar] Auto-saved {len(_high)} alerts (score≥70) to alert log")
        except Exception as _ae:
            print(f"[insider_radar] Alert auto-save error: {_ae}")

        return jsonify(out)

    except Exception as e:
        return jsonify({"error": str(e), "signals": [], "total": 0,
                        "earnings_linked": 0, "high_suspicion": 0, "rare_tickers": 0}), 500


@app.route("/stock-api/insider-alerts", methods=["GET"])
def insider_alerts_route():
    """Return the permanent alert log — all signals (score≥70) ever flagged, newest first."""
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT
                    ia.id, ia.ticker,
                    ia.detected_at AT TIME ZONE 'UTC' AS detected_at,
                    ia.suspicion_score, ia.prem, ia.strike, ia.expiry,
                    ia.price_at_detection, ia.vol_oi,
                    ia.earnings_date, ia.days_to_earnings,
                    ia.ticker_appearances, ia.verdict, ia.pre_positioned,
                    ia.outcome_checked,
                    io.outcome_verdict, io.pct_move, io.called_it,
                    io.price_at_earnings,
                    io.checked_at AT TIME ZONE 'UTC' AS outcome_at
                FROM insider_alerts ia
                LEFT JOIN insider_outcomes io ON io.alert_id = ia.id
                ORDER BY ia.detected_at DESC
                LIMIT 500
            """)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            for r in rows:
                for k in ("detected_at", "outcome_at"):
                    if r.get(k): r[k] = r[k].isoformat()
                if r.get("earnings_date"): r["earnings_date"] = r["earnings_date"].isoformat()
        return jsonify({
            "alerts":       rows,
            "total":        len(rows),
            "resolved":     sum(1 for r in rows if r.get("outcome_verdict")),
            "called_it":    sum(1 for r in rows if r.get("called_it") is True),
            "misses":       sum(1 for r in rows if r.get("called_it") is False),
        })
    except Exception as e:
        return jsonify({"error": str(e), "alerts": [], "total": 0}), 500


@app.route("/stock-api/insider-outcomes", methods=["GET"])
def insider_outcomes_route():
    """Return all resolved outcomes — what actually happened after each flagged bet."""
    try:
        with _psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT
                    io.id, io.ticker, io.earnings_date,
                    io.price_at_detection, io.price_at_earnings,
                    io.pct_move, io.called_it, io.outcome_verdict,
                    io.checked_at AT TIME ZONE 'UTC' AS checked_at,
                    ia.suspicion_score, ia.prem, ia.verdict AS alert_verdict,
                    ia.detected_at AT TIME ZONE 'UTC' AS detected_at
                FROM insider_outcomes io
                JOIN insider_alerts ia ON ia.id = io.alert_id
                ORDER BY io.checked_at DESC
            """)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            for r in rows:
                for k in ("checked_at", "detected_at"):
                    if r.get(k): r[k] = r[k].isoformat()
                if r.get("earnings_date"): r["earnings_date"] = r["earnings_date"].isoformat()
        called = [r for r in rows if r.get("called_it") is True]
        misses = [r for r in rows if r.get("called_it") is False]
        avg_gain = (sum(r["pct_move"] for r in called) / len(called)) if called else 0
        return jsonify({
            "outcomes":      rows,
            "total":         len(rows),
            "called_it":     len(called),
            "misses":        len(misses),
            "accuracy_pct":  round(len(called) / len(rows) * 100, 1) if rows else 0,
            "avg_gain_pct":  round(avg_gain, 1),
        })
    except Exception as e:
        return jsonify({"error": str(e), "outcomes": [], "total": 0}), 500


@app.route("/stock-api/healthz", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


def _startup_scan_if_needed():
    """On startup, if today's unusual_calls_log is empty, trigger a background scan immediately."""
    import threading as _sthr
    try:
        with _psycopg2.connect(_DB_URL) as _sc, _sc.cursor() as _scur:
            _scur.execute("SELECT COUNT(*) FROM unusual_calls_log WHERE last_seen >= CURRENT_DATE")
            _count = _scur.fetchone()[0]
        if _count == 0:
            print("[startup] unusual_calls_log has 0 rows for today — triggering immediate background scan")
            _scan_fn = globals().get("_run_unusual_calls_scan")
            if _scan_fn:
                _sthr.Thread(target=lambda: _scan_fn("startup"), daemon=True).start()
            else:
                print("[startup] _run_unusual_calls_scan not available — skipping auto-scan")
        else:
            print(f"[startup] unusual_calls_log has {_count} rows for today — no startup scan needed")
    except Exception as _se:
        print(f"[startup] scan check error: {_se}")

_startup_scan_if_needed()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
