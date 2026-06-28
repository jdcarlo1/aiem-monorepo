"""
Options backtest: AI short-term calls that expired June 12, 2026.
For each signal: was the stock above the breakeven at expiry close?
Did it touch the strike intraday? What happened to the underlying?
"""

import os, json
import yfinance as yf
import psycopg2
from datetime import date
from collections import defaultdict

DATABASE_URL = os.environ["DATABASE_URL"]

# ── 1. Load signals from DB ────────────────────────────────────────────────────
conn = psycopg2.connect(DATABASE_URL)
cur  = conn.cursor()
cur.execute("""
    SELECT trade_date, rank, ticker, strike, expiry, days_out,
           stock_price, otm_pct, breakeven, conviction, urgency, thesis
    FROM ai_short_calls_log
    WHERE expiry <= '2026-06-12'
    ORDER BY trade_date, rank
""")
rows = cur.fetchall()
cur.close(); conn.close()

COLS = ["trade_date","rank","ticker","strike","expiry","days_out",
        "stock_price","otm_pct","breakeven","conviction","urgency","thesis"]
signals = [dict(zip(COLS, r)) for r in rows]

# De-dupe AAPL typo (APPL → AAPL)
for s in signals:
    if s["ticker"] == "APPL":
        s["ticker"] = "AAPL"

print(f"Loaded {len(signals)} signals across {len(set(s['trade_date'] for s in signals))} trade dates")

# ── 2. Fetch June 12 OHLC for every unique ticker ─────────────────────────────
tickers = list(set(s["ticker"] for s in signals))
print(f"Fetching Jun 12 data for: {sorted(tickers)}")

data = yf.download(
    tickers, start="2026-06-11", end="2026-06-13",
    interval="1d", progress=False, auto_adjust=False
)

jun12 = {}
for tk in tickers:
    try:
        if len(tickers) == 1:
            row = data.loc["2026-06-12"]
            jun12[tk] = {
                "close": float(row["Close"]),
                "high":  float(row["High"]),
                "low":   float(row["Low"]),
                "open":  float(row["Open"]),
            }
        else:
            close = float(data["Close"][tk]["2026-06-12"])
            high  = float(data["High"][tk]["2026-06-12"])
            low   = float(data["Low"][tk]["2026-06-12"])
            opn   = float(data["Open"][tk]["2026-06-12"])
            jun12[tk] = {"close": close, "high": high, "low": low, "open": opn}
    except Exception as e:
        print(f"  ⚠️  No data for {tk}: {e}")

# ── 3. Score each signal ───────────────────────────────────────────────────────
results = []
for s in signals:
    tk = s["ticker"]
    if tk not in jun12:
        continue
    d = jun12[tk]

    strike_hit  = d["high"]  >= s["strike"]         # touched strike intraday
    be          = s["breakeven"] if s["breakeven"] else s["strike"]
    expiry_win  = d["close"] >= be                   # closed above breakeven → profit
    chg_from_sig = (d["close"] - s["stock_price"]) / s["stock_price"] * 100

    results.append({
        **s,
        "jun12_open":  d["open"],
        "jun12_high":  d["high"],
        "jun12_close": d["close"],
        "chg_from_sig_pct": round(chg_from_sig, 2),
        "strike_touched": strike_hit,
        "expiry_win": expiry_win,
    })

# ── 4. Summary stats ───────────────────────────────────────────────────────────
total    = len(results)
wins     = [r for r in results if r["expiry_win"]]
losses   = [r for r in results if not r["expiry_win"]]
touched  = [r for r in results if r["strike_touched"]]

print(f"\n{'='*65}")
print(f"TOTAL SIGNALS BACKTESTED : {total}")
print(f"WINS (closed ≥ breakeven): {len(wins)} — {100*len(wins)/total:.1f}%")
print(f"LOSSES                   : {len(losses)} — {100*len(losses)/total:.1f}%")
print(f"Strike touched intraday  : {len(touched)} — {100*len(touched)/total:.1f}%")
print(f"{'='*65}")

# ── 5. By ticker summary ──────────────────────────────────────────────────────
by_ticker = defaultdict(list)
for r in results:
    by_ticker[r["ticker"]].append(r)

print("\n── BY TICKER ──")
print(f"{'Ticker':<8} {'Signals':>7} {'Wins':>5} {'WR%':>6} {'Jun12 Close':>12} {'Sig Price':>10} {'Chg%':>7}")
for tk in sorted(by_ticker):
    sigs  = by_ticker[tk]
    w     = sum(1 for r in sigs if r["expiry_win"])
    wr    = 100*w/len(sigs)
    cl    = sigs[0]["jun12_close"]
    sp    = sigs[0]["stock_price"]
    chg   = (cl - sp) / sp * 100
    print(f"{tk:<8} {len(sigs):>7} {w:>5} {wr:>5.0f}%  ${cl:>10.2f}  ${sp:>8.2f}  {chg:>+6.1f}%")

# ── 6. By conviction ──────────────────────────────────────────────────────────
print("\n── BY CONVICTION ──")
for conv in ["HIGH","MEDIUM","LOW"]:
    grp = [r for r in results if r["conviction"] == conv]
    if not grp: continue
    w   = sum(1 for r in grp if r["expiry_win"])
    print(f"{conv:8}: {len(grp)} signals — {100*w/len(grp):.0f}% WR")

# ── 7. By days-out ────────────────────────────────────────────────────────────
print("\n── BY DAYS TO EXPIRY ──")
for d in sorted(set(r["days_out"] for r in results)):
    grp = [r for r in results if r["days_out"] == d]
    w   = sum(1 for r in grp if r["expiry_win"])
    print(f"{d} days out: {len(grp)} signals — {100*w/len(grp):.0f}% WR")

# ── 8. Full loser list ────────────────────────────────────────────────────────
print(f"\n── LOSERS (closed below breakeven) ──")
print(f"{'Date':<12}{'Ticker':<7}{'Strike':>8}{'Sig$':>8}{'Jun12$':>8}{'Chg%':>7}{'BE':>9}  Conviction  OTM%")
for r in sorted(losses, key=lambda x: x["chg_from_sig_pct"]):
    print(f"{str(r['trade_date']):<12}{r['ticker']:<7}"
          f"  {r['strike']:>7.1f}  {r['stock_price']:>6.2f}  {r['jun12_close']:>6.2f}"
          f"  {r['chg_from_sig_pct']:>+5.1f}%  {r['breakeven']:>7.2f}  {r['conviction']:<8}  {r['otm_pct']:>+.1f}%")

# ── 9. Full winner list ───────────────────────────────────────────────────────
print(f"\n── WINNERS (closed at/above breakeven) ──")
print(f"{'Date':<12}{'Ticker':<7}{'Strike':>8}{'Sig$':>8}{'Jun12$':>8}{'Chg%':>7}{'BE':>9}  Conviction  OTM%")
for r in sorted(wins, key=lambda x: -x["chg_from_sig_pct"]):
    print(f"{str(r['trade_date']):<12}{r['ticker']:<7}"
          f"  {r['strike']:>7.1f}  {r['stock_price']:>6.2f}  {r['jun12_close']:>6.2f}"
          f"  {r['chg_from_sig_pct']:>+5.1f}%  {r['breakeven']:>7.2f}  {r['conviction']:<8}  {r['otm_pct']:>+.1f}%")

# ── 10. OTM analysis — did big-OTM calls cluster in losers? ──────────────────
print(f"\n── OTM% ANALYSIS ──")
otm_winners = [r["otm_pct"] for r in wins]
otm_losers  = [r["otm_pct"] for r in losses]
if otm_winners:
    print(f"Avg OTM% — winners: {sum(otm_winners)/len(otm_winners):+.1f}%")
if otm_losers:
    print(f"Avg OTM% — losers:  {sum(otm_losers)/len(otm_losers):+.1f}%")

# High-OTM (>3%) analysis
hi_otm = [r for r in results if r["otm_pct"] > 3]
lo_otm = [r for r in results if r["otm_pct"] <= 3]
if hi_otm:
    w = sum(1 for r in hi_otm if r["expiry_win"])
    print(f"OTM >3%  ({len(hi_otm)} signals): {100*w/len(hi_otm):.0f}% WR")
if lo_otm:
    w = sum(1 for r in lo_otm if r["expiry_win"])
    print(f"OTM ≤3%  ({len(lo_otm)} signals): {100*w/len(lo_otm):.0f}% WR")

print("\nDone.")
