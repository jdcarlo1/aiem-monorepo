"""
backtest_falsenegatives.py — What did the filtered-out WINNERS have in common?
===============================================================================
Takes every signal our filters EXCLUDED (Monday skip, extreme cap, price zone,
ATR 2-3x, pre-D1 vol surge) and isolates the ones that still gained ≥5% over
5 days. Then compares those false-negative winners against the filtered-out
losers to find new rescue indicators.

Output: per-filter breakdown showing what the false-negative winners shared
that losers in the same filtered pool did NOT.
"""

import re, sys, time, statistics, datetime
from collections import defaultdict

try:
    import requests
    import yfinance as yf
except ImportError:
    sys.exit(1)

_FV_HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36"}

# ── Finviz + yfinance helpers ─────────────────────────────────────────────────

def finviz_universe(filters, max_pages=6, min_vol=100_000):
    tickers, seen = [], set()
    for pg in range(max_pages):
        start = pg * 20 + 1
        url = f"https://finviz.com/screener.ashx?v=111&f={filters}&o=-volume&r={start}"
        try:
            r = requests.get(url, headers=_FV_HDR, timeout=15)
            if not r.ok: break
            new = 0
            for chunk in re.split(r'<tr class="styled-row', r.text)[1:]:
                cells = [re.sub(r"<[^>]+>","",c).strip()
                         for c in re.findall(r"<td[^>]*>(.*?)</td>",chunk,re.S)]
                if len(cells) < 11: continue
                tk = cells[1].upper().strip()
                if not tk or len(tk)>5 or "." in tk or tk in seen: continue
                try: vol = int(cells[10].replace(",",""))
                except: vol = 0
                if vol < min_vol: continue
                seen.add(tk); tickers.append(tk); new += 1
            if new == 0: break
            time.sleep(0.4)
        except Exception as e:
            print(f"  Finviz pg {pg+1}: {e}"); break
    return tickers


def download_ohlcv(tickers, period="3mo"):
    result = {}
    for i in range(0, len(tickers), 100):
        batch = tickers[i:i+100]
        try:
            raw = yf.download(batch, period=period, interval="1d",
                              auto_adjust=True, progress=False, threads=True)
            if raw.empty: continue
            for tkr in batch:
                try:
                    s = len(batch)
                    o  = raw["Open"][tkr].dropna()   if s>1 else raw["Open"].dropna()
                    h  = raw["High"][tkr].dropna()   if s>1 else raw["High"].dropna()
                    lo = raw["Low"][tkr].dropna()    if s>1 else raw["Low"].dropna()
                    c  = raw["Close"][tkr].dropna()  if s>1 else raw["Close"].dropna()
                    v  = raw["Volume"][tkr].dropna() if s>1 else raw["Volume"].dropna()
                    rows = []
                    for idx in c.index:
                        rows.append((idx.date(), float(o.get(idx,0)), float(h.get(idx,0)),
                                     float(lo.get(idx,0)), float(c[idx]), float(v.get(idx,0))))
                    if len(rows) >= 15:
                        result[tkr] = sorted(rows, key=lambda x: x[0])
                except Exception:
                    pass
        except Exception:
            pass
        if i+100 < len(tickers): time.sleep(1)
    return result


# ── Signal enricher — computes ALL metrics for a single candidate ─────────────

def enrich(rows, i):
    """Returns a dict of all metrics for index i, or None if insufficient data."""
    if i < 7 or i + 5 >= len(rows):
        return None
    d0 = rows[i-1]; d1 = rows[i]; d5 = rows[i+5]
    d0c = d0[4]; d1o = d1[1]; d1h = d1[2]; d1lo = d1[3]; d1c = d1[4]; d1v = d1[5]
    d5c = d5[4]; d1_date = d1[0]
    if d0c <= 0 or d1c <= 0 or d5c <= 0: return None

    d1_gain   = (d1c - d0c) / d0c * 100
    gap_pct   = (d1o - d0c) / d0c * 100
    dow       = d1_date.weekday()
    d5_ret    = (d5c - d1c) / d1c * 100

    # RVOL (5-day)
    prev_vols = [rows[j][5] for j in range(i-5, i) if rows[j][5] > 0]
    avg_vol   = statistics.mean(prev_vols) if prev_vols else d1v
    rvol      = d1v / avg_vol if avg_vol > 0 else 1.0

    # 5-day ATR
    atr_vals = []
    for j in range(i-6, i-1):
        h_ = rows[j][2]; lo_ = rows[j][3]; pc_ = rows[j-1][4]
        atr_vals.append(max(h_ - lo_, abs(h_ - pc_), abs(lo_ - pc_)))
    atr5     = statistics.mean(atr_vals) if atr_vals else 0
    d1_range = d1h - d1lo
    atr_mult = d1_range / atr5 if atr5 > 0 else 1.0

    # 3-day pre-D1 volume trend
    vol_trend = 0.0
    if i >= 4 and rows[i-4][5] > 0:
        vol_trend = (rows[i-2][5] - rows[i-4][5]) / rows[i-4][5] * 100

    # Prior-day gain (D0)
    prior_gain = (d0c - rows[i-2][4]) / rows[i-2][4] * 100 if rows[i-2][4] > 0 else 0.0

    # Consecutive up days going into D1
    consec = 0
    for j in range(i-1, max(i-7, 0), -1):
        if rows[j][4] > rows[j-1][4]: consec += 1
        else: break

    # Body ratio (abs candle body / total range)
    body_ratio = abs(d1c - d1o) / d1_range if d1_range > 0 else 0.5
    green_body = d1c >= d1o

    # Distance from 10-day high
    recent_highs = [rows[j][2] for j in range(max(0, i-10), i)]
    max_10d      = max(recent_highs) if recent_highs else d1c
    near_high    = d1c / max_10d

    # SPY-like proxy: just flag if it's a Friday gap (we don't have SPY in the signal)
    is_friday  = (dow == 4)
    is_monday  = (dow == 0)

    return {
        "d1_gain":    d1_gain,
        "gap_pct":    gap_pct,
        "dow":        dow,
        "d5_ret":     d5_ret,
        "won":        d5_ret > 0,
        "rvol":       rvol,
        "atr_mult":   atr_mult,
        "vol_trend":  vol_trend,
        "prior_gain": prior_gain,
        "consec":     consec,
        "body_ratio": body_ratio,
        "green_body": green_body,
        "near_high":  near_high,
        "d1_close":   d1c,
    }


# ── Label which filter(s) rejected each signal ───────────────────────────────

def label_filtered(sig, thr, ecap, wpz, tier_key):
    """Returns list of filter names that would exclude this signal, or [] if it passes."""
    filters_hit = []
    # Monday filter
    if sig["dow"] == 0:
        if sig["gap_pct"] < 0 and tier_key in ("large", "small"):
            pass   # rescued
        else:
            filters_hit.append("monday")
    # Extreme gain cap
    if sig["d1_gain"] > ecap:
        ext_ok = (tier_key == "small"
                  and 0 <= sig["gap_pct"] < 2
                  and sig["rvol"] >= 4.0)
        if not ext_ok:
            filters_hit.append("extreme_gain")
    # Price zone
    if wpz and wpz[0] <= sig["d1_close"] < wpz[1]:
        filters_hit.append("price_zone")
    # ATR 2-3x (mid/small only — new filter)
    if tier_key in ("mid", "small") and 2.0 <= sig["atr_mult"] < 3.0:
        filters_hit.append("atr_2to3x")
    # Pre-D1 vol surge >30% (small only — new filter)
    if tier_key == "small" and sig["vol_trend"] > 30:
        filters_hit.append("vol_surge")
    return filters_hit


# ── Print winner-vs-loser comparison for a filtered group ────────────────────

def analyze_false_negatives(filter_name, fn_winners, fn_losers):
    n_w = len(fn_winners); n_l = len(fn_losers)
    if n_w < 5:
        print(f"  {filter_name}: only {n_w} winners — too few to analyze\n")
        return

    print(f"\n  {'─'*68}")
    print(f"  Filter: {filter_name}")
    print(f"  Excluded pool: {n_w+n_l} total → {n_w} won D5≥0 (FN) vs {n_l} lost")
    wr = n_w / (n_w + n_l) * 100
    print(f"  FN win rate: {wr:.1f}%  (if rescued ALL, this is the WR you'd add)")
    print(f"  {'─'*68}")

    if not fn_winners: return

    def avg(lst, key): return round(statistics.mean(s[key] for s in lst), 2) if lst else 0
    def pct_true(lst, key): return round(sum(1 for s in lst if s.get(key)) / len(lst) * 100, 1) if lst else 0

    metrics = [
        ("D1 gain (%)",          "d1_gain",    None),
        ("Gap % (open vs prev)", "gap_pct",    None),
        ("RVOL",                 "rvol",       None),
        ("ATR multiple",         "atr_mult",   None),
        ("Prior-day gain (%)",   "prior_gain", None),
        ("Consecutive up days",  "consec",     None),
        ("Body ratio",           "body_ratio", None),
        ("Pre-D1 vol trend (%)", "vol_trend",  None),
        ("Near-high ratio",      "near_high",  None),
    ]

    print(f"\n  {'Metric':<28}  {'FN Winners':>11}  {'FN Losers':>11}  {'Diff':>8}  Rescue signal?")
    print(f"  {'─'*28}  {'─'*11}  {'─'*11}  {'─'*8}  {'─'*20}")
    rescue_signals = []
    for label, key, _ in metrics:
        w = avg(fn_winners, key)
        l = avg(fn_losers, key)  if fn_losers else 0
        diff = w - l
        rel  = abs(diff) / max(abs(w), 0.001)
        flag = ""
        if rel >= 0.20 and n_w >= 8:
            flag = "✅ RESCUE INDICATOR"
            rescue_signals.append((label, key, w, l, diff))
        print(f"  {label:<28}  {w:>+11.3f}  {l:>+11.3f}  {diff:>+8.3f}  {flag}")

    # Binary signals
    gb_w = pct_true(fn_winners, "green_body")
    gb_l = pct_true(fn_losers,  "green_body")
    flag = "✅ RESCUE INDICATOR" if abs(gb_w - gb_l) >= 15 else ""
    print(f"  {'Green body (%)':<28}  {gb_w:>10.1f}%  {gb_l:>10.1f}%  {gb_w-gb_l:>+7.1f}%  {flag}")

    # Bucket breakdown for the strongest rescue signals
    if rescue_signals:
        print(f"\n  TOP RESCUE BUCKETS for false-negative winners in '{filter_name}':")
        for label, key, w_avg, l_avg, diff in rescue_signals[:3]:
            # Split at midpoint between winner and loser averages
            threshold = (w_avg + l_avg) / 2
            direction = ">" if diff > 0 else "<"
            in_zone = [s for s in fn_winners + fn_losers
                       if (s[key] > threshold if diff > 0 else s[key] < threshold)]
            out_zone = [s for s in fn_winners + fn_losers
                        if not (s[key] > threshold if diff > 0 else s[key] < threshold)]
            if in_zone and out_zone:
                wr_in  = sum(1 for s in in_zone  if s["won"]) / len(in_zone)  * 100
                wr_out = sum(1 for s in out_zone if s["won"]) / len(out_zone) * 100
                print(f"    {label} {direction} {threshold:.2f}:  "
                      f"WR={wr_in:.0f}% (n={len(in_zone)}) vs "
                      f"WR={wr_out:.0f}% (n={len(out_zone)}) outside zone")


TIERS = [
    {"name": "LARGE CAP", "filter": "cap_large,sh_opt_option",
     "min_vol": 500_000, "pages": 6, "thr": 3.0, "ecap": 15.0, "wpz": None, "key": "large"},
    {"name": "MID CAP",   "filter": "cap_mid,sh_opt_option",
     "min_vol": 200_000, "pages": 6, "thr": 5.0, "ecap": 15.0, "wpz": (15.0, 50.0), "key": "mid"},
    {"name": "SMALL CAP", "filter": "cap_small,sh_opt_option",
     "min_vol": 100_000, "pages": 6, "thr": 7.0, "ecap": 17.0, "wpz": (15.0, 50.0), "key": "small"},
]

SEP = "═" * 72

def main():
    print("\nAnalyzing filtered-out signals that STILL WON +5 days out...\n")
    D5_WIN_THRESHOLD = 0.0  # any positive 5-day return counts as "won"

    all_cross_tier = defaultdict(lambda: {"winners": [], "losers": []})

    for cfg in TIERS:
        print(SEP)
        print(f"  {cfg['name']}  (D1 gain ≥{cfg['thr']}%)")
        print(SEP)
        tickers = finviz_universe(cfg["filter"], max_pages=cfg["pages"], min_vol=cfg["min_vol"])
        print(f"  {len(tickers)} tickers — downloading...")
        ohlcv = download_ohlcv(tickers)

        # Bucket: filter_name → list of enriched signals (winners and losers)
        buckets = defaultdict(lambda: {"winners": [], "losers": []})

        for tkr, rows in ohlcv.items():
            for i in range(7, len(rows) - 5):
                d0c = rows[i-1][4]; d1c = rows[i][4]
                if d0c <= 0 or d1c <= 0: continue
                d1_gain = (d1c - d0c) / d0c * 100
                if d1_gain < cfg["thr"]: continue

                sig = enrich(rows, i)
                if sig is None: continue

                filters = label_filtered(sig, cfg["thr"], cfg["ecap"], cfg["wpz"], cfg["key"])
                if not filters:
                    continue   # this signal PASSES our filters — skip, we want only filtered-out

                for f in filters:
                    if sig["d5_ret"] > D5_WIN_THRESHOLD:
                        buckets[f]["winners"].append(sig)
                        all_cross_tier[f]["winners"].append(sig)
                    else:
                        buckets[f]["losers"].append(sig)
                        all_cross_tier[f]["losers"].append(sig)

        for filter_name, data in sorted(buckets.items()):
            analyze_false_negatives(
                f"{cfg['name']} / {filter_name}",
                data["winners"], data["losers"]
            )

    # ── Cross-tier synthesis ──────────────────────────────────────────────────
    print(f"\n\n{'═'*72}")
    print("  CROSS-TIER SYNTHESIS — Rescue patterns consistent across tiers")
    print(f"{'═'*72}")
    print("  If a metric separates winners from losers in ≥2 tiers → add it as a rescue gate.\n")

    for filter_name, data in sorted(all_cross_tier.items()):
        wn = data["winners"]; ln = data["losers"]
        if len(wn) < 10: continue
        wr = len(wn) / (len(wn)+len(ln)) * 100
        print(f"\n  [{filter_name}] total excluded: {len(wn)+len(ln)}  FN win rate: {wr:.1f}%")
        print(f"  FN winners avg metrics vs FN losers avg metrics:")

        def avg(lst, key): return statistics.mean(s[key] for s in lst) if lst else 0
        for key in ["rvol","gap_pct","atr_mult","near_high","body_ratio","vol_trend","prior_gain"]:
            w_avg = avg(wn, key); l_avg = avg(ln, key)
            diff  = w_avg - l_avg
            rel   = abs(diff) / max(abs(w_avg), 0.001)
            flag  = " ← RESCUE SIGNAL" if rel >= 0.20 and len(wn) >= 10 else ""
            print(f"    {key:<20}  winners={w_avg:>+7.3f}  losers={l_avg:>+7.3f}  diff={diff:>+7.3f}{flag}")

    print(f"\n{'═'*72}")
    print("  WHAT TO DO WITH THESE FINDINGS:")
    print("  For any filter where FN win rate > 50% AND a clear metric separates")
    print("  winners from losers: add that metric as a RESCUE EXCEPTION.")
    print("  For any filter where FN win rate < 45%: the filter is correct — keep it.")
    print(f"{'═'*72}\n")


if __name__ == "__main__":
    main()
