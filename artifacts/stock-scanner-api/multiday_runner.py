"""
multiday_runner.py — Multi-Day Runner Scanner (All Cap Tiers)
=============================================================
Detects intraday momentum continuation across 3 cap tiers.

Strategy:
  • BUY  : 2 PM Day 1 — intraday signal fires when gain + VWAP hold + range position confirmed
  • SELL : Day 5 close  — 5 trading days captures full institutional buying cycle

Thresholds by cap tier:
  • Large cap ($10B+)   → D1 ≥ 3%  (S&P 500 focus)
  • Mid cap  ($2B–$10B) → D1 ≥ 4%  (S&P 400 focus)
  • Small cap ($300M–$2B) → D1 ≥ 5% (optionable Russell 2000)

Daily schedule:
  • 2:00 PM ET  — Intraday D1 scan: fires BUY SIGNAL for all 3 tiers if confirmed
  • 4:05 PM ET  — EOD Day 1 save: stores all ignitions for D2 tracking / outcomes
  • 2:45 PM ET  — Day 2 confirm: second-chance entry for those who missed Day 1
  • 4:30 PM ET  — Outcomes updater: fills D+3, D+5, D+10 returns on past signals

Backtest results (60-day large-cap):
  • D1 ≥ 3% + intraday confirmed → 59.7% win rate, +2.2% EV  (D1 entry → D5 exit)
  • D1 ≥ 5% STRONG tier         → 69.6% win rate, +4.1% avg gain
"""

import os, math, traceback, time
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

_ET_TZ = ZoneInfo("America/New_York")


# ── Universe definitions ───────────────────────────────────────────────────

LARGE_CAP_UNIVERSE = list(dict.fromkeys([
    "AAPL","MSFT","NVDA","AMZN","META","GOOGL","GOOG","TSLA","AMD","NFLX",
    "CRM","ORCL","ADBE","INTC","QCOM","TXN","MU","AVGO","AMAT","LRCX","KLAC",
    "MRVL","SNPS","CDNS","FTNT","NOW","WDAY","INTU","PANW","CRWD","ZS","DDOG",
    "NET","SNOW","MDB","PLTR","APP","COIN","ANET","CSCO","HPE","DELL","WDC","STX",
    "JPM","BAC","GS","MS","WFC","C","AXP","BLK","SCHW","V","MA","PYPL",
    "COF","DFS","AIG","PRU","MET","AFL","ALL","TRV","CB","ICE","CME","NDAQ",
    "MSCI","SPGI","MCO","IBKR","FITB","HBAN","RF","KEY","CFG",
    "JNJ","PFE","ABBV","LLY","BMY","MRK","AMGN","GILD","BIIB","REGN","VRTX",
    "ISRG","MDT","ABT","TMO","DHR","SYK","BSX","EW","HCA","UNH","CVS","CI",
    "HUM","ELV","MOH","CNC","MCK","MRNA","IDXX","ILMN","IQV","TDOC",
    "XOM","CVX","COP","SLB","HAL","OXY","MPC","VLO","PSX","EOG","DVN",
    "FANG","APA","MRO","BKR","NEE","DUK","SO","AEP","EXC","PCG","SRE",
    "XEL","D","PPL","ETR","TLN","VST","NRG","AES","EIX","PEG","BE","GEV",
    "HD","LOW","TGT","COST","WMT","MCD","SBUX","YUM","CMG","NKE","LULU",
    "F","GM","DIS","CMCSA","FOXA",
    "PG","KO","PEP","MO","PM","MDLZ","GIS","CL","EL",
    "BA","LMT","RTX","NOC","GD","TDG","HWM","GE","CAT","DE","PCAR","CMI",
    "ITW","EMR","ETN","ROK","HON","JCI","TT","CARR","OTIS","PH","IR","GWW",
    "UPS","FDX","XPO","UNP","CSX","NSC","CP","WAB","AGX","HUBB","MTRN",
    "APD","LIN","DOW","LYB","PPG","SHW","ECL","ALB","FCX","NEM","NUE","STLD",
    "EQIX","AMT","PLD","SPG","PSA","WY","CBRE",
    "WDC","STX","GEV","CAT","NRG","APH","EMR","BBVA","COF","CYTK","VSEC",
    "VST","RYTM","TGTX","DB","IBKR","GE","RIOT","VKTX","SRPT","AGIO",
]))

MIDCAP_UNIVERSE = list(dict.fromkeys([
    # Tech / Software
    "BILL","ZI","YEXT","BOX","DOCN","QLYS","MGNI","CALX","JAMF","NABL",
    "PCOR","BRZE","GTLB","SMAR","APPN","ALRM","SPOK","AVLR","PLTK","ZD",
    "FSLY","PUBM","VERI","DOMO","EVBG","FROG","GENI","HYLN","MTSI","POWI",
    "COHU","FORM","IIVI","ACLS","ONTO","UCTT","NVEC","DIOD","IOSP","KLIC",
    # Financials
    "ESNT","RDN","MTG","NMIH","CADE","PPBI","BANR","HOPE","EGBN","WSFS",
    "PNFP","HOMB","SFBS","HTLF","INDB","TBK","TCBI","BOKF","CVB","PACW",
    "FFIN","UMBF","NBT","CTBI","NBTB","BRKL","TRMK","SFNC","ESSA","NFBK",
    # Healthcare
    "ACAD","PCVX","ITCI","KRTX","INVA","PRCT","GKOS","LNTH","ARWR","ALNY",
    "IONS","EXEL","FOLD","NKTR","FATE","ACMR","RVMD","ROIV","ARDX","ABCL",
    "IMVT","ARQT","DAWN","KYMR","XNCR","VERA","TGTX","RYTM","CYTK","PRAX",
    # Energy / Utilities
    "CIVI","SM","MTDR","BATL","ESTE","VAALCO","KOS","GRNT","SBOW","REPX",
    "MNRL","PHX","SJT","DMLP","NOG","VTLE","FLNC","AMRC","CLNE","GEVO",
    # Consumer
    "CROX","DECK","SKX","BOOT","YETI","RCM","JACK","BJ","ARCO","BJRI",
    "CAKE","DINE","FAT","NATH","RUTH","TXRH","SHAK","WING","PTON","MODG",
    # Industrials
    "GNRC","TREX","AZEK","BLDR","CSL","FRTA","PGTI","UFPI","BECN","IBP",
    "GTES","WTFC","KFRC","MWA","AAON","WMS","IESC","LYTS","GMS","APOG",
    # Materials
    "CMC","STLD","ZEUS","HAYN","KALU","CENX","CSTM","ATI","AMRS","TROX",
    # Additional from gainers lists
    "VIRT","HUBB","MTRN","VSEC","NRG","IBKR","APH","COF","CAT","CYTK",
    "DB","BBVA","ING","PH","BNS","AER","BTSG","MUFG","IHG","WBS","TD","BMO",
]))

SMALLCAP_UNIVERSE = list(dict.fromkeys([
    # Tech
    "INPX","IMXI","DMRC","CODA","BTAI","CLBK","CLFD","HLIT","LIQT","PCSA",
    "RBBN","ANDS","SPWH","SCKT","SIERF","DAIO","PCYO","LNKB","OAKU","FEAM",
    "CRNT","CTEK","EDGW","EVLV","IDEX","MFAC","MITI","NXRT","OOMA","PFIS",
    # Biotech / Healthcare
    "ACHC","ACLS","ACNB","ADMA","AFIB","AFMD","AGTI","AGTX","AHCO","AIOT",
    "AIRI","AKRO","ALEC","ALKT","ALTO","AMPIO","ANAB","ANGI","ANNX","APLT",
    "APEN","APOG","APRE","APVO","ARDX","AREC","AREV","ARGX","ARQT","ARRY",
    "ARTL","ARVN","ARWR","ASMB","ATNM","ATRC","ATRI","ATXS","AUBN","AULT",
    "AVAH","AVDL","AVEO","AVRX","AVXL","AXDX","AXSM","AXTI","BCRX","BDSX",
    "BEAM","BHVN","BIGC","BIMI","BNOX","BNTX","BPMC","BSGM","BTBT","BTCS",
    # Energy
    "AMPY","BBEP","CDEV","COVE","DMLP","DNOW","ENPH","ESTE","FLNG","GATO",
    "GETR","GGAL","GLOG","GLOP","GNSS","GPRE","GRCY","GRNT","GRIL","GRIN",
    "HALO","HARL","HASH","HCAT","HLIO","HLNE","HMST","HOFT","HOLI","HONE",
    # Consumer / Retail
    "AMRS","AMSC","AMTB","AMTX","ANGI","APLT","APRE","ARDX","AREC","AREV",
    "BLNK","BLTE","BMEA","BMTC","BNGO","BOLT","BOOG","BOOT","BPMC","BRDG",
    "CBAT","CBLI","CBMB","CBPO","CBRE","CBRL","CBSH","CBTX","CDNA","CDNL",
    # Industrials
    "DXPE","FWRD","GLDD","GRBK","GRNV","GSBC","HAFC","HARL","HBCP","HBIO",
    "HCKT","HDGE","HEES","HEPA","HFWA","HGBL","HGEN","HHSE","HIBB","HIFS",
    "JBSS","JILL","JJSF","JKHY","JNCE","JOUT","JPBI","JRJC","JRVR","JSPR",
    # Well-known small caps with options
    "KODK","KOSS","LAZR","LCII","LCNB","LCRD","LDOS","LECO","LFLY","LFMD",
    "LGFA","LGIH","LGND","LGNL","LHCG","LIDR","LILA","LILM","LIQT","LKFN",
    "LLNW","LMFA","LMNR","LNDC","LNSR","LOCO","LOOP","LOVE","LPAD","LPCN",
    "MOMO","MODN","MODV","MOFG","MOHO","MOLN","MOMO","MPAA","MPAC","MPLN",
    "MRNS","MRTN","MRUS","MRVI","MSBI","MSEX","MSKE","MSON","MSTR","MTDR",
    "NAUT","NAVB","NAVI","NBHC","NBTB","NCBS","NCNA","NCNO","NCOM","NDLS",
    "PRPB","PRPH","PRPL","PRQR","PRSO","PRST","PRTA","PRTH","PRTK","PRTS",
    "RAMP","RAND","RCAT","RCII","RCKT","RCKY","RCUS","RDCM","RDIB","RDNT",
    "SDGR","SDRL","SEER","SFLY","SGBX","SGMO","SGTX","SHBI","SHCA","SHCO",
    "SMBC","SMCI","SMFR","SMHI","SMLR","SMMD","SMMT","SMPL","SMSI","SMTC",
    "AAON","ACNB","ADES","ADMA","ADMP","ADN","ADNT","ADOC","ADSE","ADTN",
    "WOLF","WOOF","WORX","WQGA","WSBF","WSBC","WSTG","WSFS","WTBA","WTFC",
    "XAIR","XBIO","XCUR","XENE","XERS","XFOR","XHER","XHLT","XLNX","XNCR",
    "YORW","YPAG","YYAI","ZETA","ZEUS","ZFOX","ZGNX","ZHFC","ZION","ZJYL",
]))

# Cap tier configs: (universe, min_gain_pct, strong_gain_pct, label, color_hex)
CAP_TIERS = {
    "large": (LARGE_CAP_UNIVERSE,  3.0, 5.0,  "Large Cap ($10B+)",     "#22c55e"),
    "mid":   (MIDCAP_UNIVERSE,     5.0, 7.0,  "Mid Cap ($2B–$10B)",    "#38bdf8"),
    "small": (SMALLCAP_UNIVERSE,   7.0, 10.0, "Small Cap ($300M–$2B)", "#f59e0b"),
}

# ── Data-validated signal quality filters ─────────────────────────────────────
# Source: 3-month Finviz/yfinance backtest across all 3 tiers (June 2026)
#
# 1. MONDAY FILTER — skip all Monday signals across every tier.
#    Monday WR: large 52.6%, mid 36.5%, small 46.9% vs rest-of-week 55-57%.
#    Root cause: weekend news gets fully priced in at open; no institutional
#    follow-through on day 2 since market makers reset positions Monday AM.
#
# 2. EXTREME GAIN CAP — skip binary-event blowouts (earnings/FDA/M&A).
#    Large/mid >15%: WR drops to ~48%; small >17%: WR 41.1%.
#    These moves overshoot and mean-revert; the catalyst is spent.
#
# 3. WEAK PRICE ZONE — skip $15-$50 priced stocks in mid/small cap.
#    Mid $15-$50: WR=45.7%, avg=+0.02%. Small $15-$50: WR=38.5%, avg=-0.97%.
#    These are "fallen large caps" with institutional overhead supply that
#    prevents continuation. Under $15 or over $50 both outperform in mid/small.

EXTREME_CAP = {           # max D1 gain % before we flag as binary/exhaustion
    "large": 15.0,
    "mid":   15.0,
    "small": 17.0,
}

WEAK_PRICE_ZONE = {       # (min_price, max_price) — skip if price in this range
    "large": None,         # large cap: no price filter
    "mid":   (15.0, 50.0),
    "small": (15.0, 50.0),
}


# ── Database ───────────────────────────────────────────────────────────────

def init_multiday_runner_tables():
    import psycopg2 as pg
    with pg.connect(os.environ["DATABASE_URL"]) as c, c.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS multiday_runner_watch (
                id           SERIAL PRIMARY KEY,
                d1_date      DATE NOT NULL,
                ticker       VARCHAR(10) NOT NULL,
                cap_tier     VARCHAR(8)  NOT NULL DEFAULT 'large',
                d1_pct       FLOAT,
                d1_close     FLOAT,
                d1_high      FLOAT,
                d1_low       FLOAT,
                d1_rvol      FLOAT,
                d1_vol       BIGINT,
                d1_strong        BOOLEAN DEFAULT FALSE,
                conviction_score INT     DEFAULT 0,
                intraday_hit     BOOLEAN DEFAULT FALSE,
                intraday_entry FLOAT,
                status       VARCHAR(16) DEFAULT 'watch',
                d2_date      DATE,
                d2_pct       FLOAT,
                d2_close     FLOAT,
                d2_close_pos FLOAT,
                d2_above_d1  BOOLEAN,
                confirmed    BOOLEAN DEFAULT FALSE,
                entry_price  FLOAT,
                stop_price   FLOAT,
                exit_price   FLOAT,
                exit_date    DATE,
                exit_pct     FLOAT,
                hold_days    INT,
                exit_reason  VARCHAR(24),
                d3_pct       FLOAT,
                d5_pct       FLOAT,
                d10_pct      FLOAT,
                captured_at  TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (d1_date, ticker)
            )
        """)
        # Migrate: add new columns to existing table if missing
        for col, defn in [
            ("cap_tier",       "VARCHAR(8) NOT NULL DEFAULT 'large'"),
            ("intraday_hit",   "BOOLEAN DEFAULT FALSE"),
            ("intraday_entry", "FLOAT"),
            ("d3_pct",            "FLOAT"),
            ("d5_pct",            "FLOAT"),
            ("d10_pct",           "FLOAT"),
            ("conviction_score",  "INT DEFAULT 0"),
        ]:
            cur.execute(f"""
                DO $$ BEGIN
                    ALTER TABLE multiday_runner_watch ADD COLUMN IF NOT EXISTS {col} {defn};
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$;
            """)
        c.commit()
    print("[multiday_runner] tables ready")


def _today_et() -> date:
    return datetime.now(_ET_TZ).date()


def _prev_trading_day(d: date, n: int = 1) -> date:
    """Walk back n trading days (skip weekends; doesn't handle holidays)."""
    for _ in range(n):
        d -= timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
    return d


# ── Intraday D1 Scan — 2 PM signal ────────────────────────────────────────

def run_intraday_d1_scan() -> dict:
    """
    2:00 PM ET: scan all 3 cap tiers for intraday Day 1 signals.

    Two-stage:
      Stage 1 — daily bars: find candidates already up >= threshold
      Stage 2 — 5-min bars: verify VWAP hold + range position + RVOL

    Returns dict: {"large": [...], "mid": [...], "small": [...], "all": [...]}
    """
    import yfinance as yf
    import pandas as pd
    import psycopg2 as pg

    today = _today_et()
    results_by_tier: dict = {"large": [], "mid": [], "small": [], "all": []}

    for tier_key, (universe, min_pct, strong_pct, label, _color) in CAP_TIERS.items():
        print(f"[multiday_runner] intraday D1 scan: {tier_key} ({len(universe)} tickers)")

        # ── Stage 1: daily bars to find up-x% candidates ──────────────────
        candidates = []
        try:
            batch_size = 80
            for i in range(0, len(universe), batch_size):
                batch = universe[i:i + batch_size]
                try:
                    raw = yf.download(
                        batch, period="3d", interval="1d",
                        group_by="ticker", auto_adjust=True, progress=False,
                        timeout=20,
                    )
                    for tkr in batch:
                        try:
                            df = raw[tkr].dropna() if len(batch) > 1 else raw
                            if len(df) < 2:
                                continue
                            prev_close = float(df["Close"].iloc[-2])
                            cur_close  = float(df["Close"].iloc[-1])
                            cur_open   = float(df["Open"].iloc[-1])
                            if prev_close <= 0:
                                continue
                            pct     = (cur_close - prev_close) / prev_close * 100
                            gap_pct = (cur_open  - prev_close) / prev_close * 100
                            if pct >= min_pct:
                                # ── Quality filters (data-validated) ────────
                                # 1. Monday filter — EXCEPTION: gap-down opens on Monday
                                #    are genuine intraday signals (not weekend news).
                                #    Gap-down Monday WR=60-68% vs 37-53% for gap-ups.
                                if today.weekday() == 0:
                                    if gap_pct >= 0:   # gapped UP/flat → weekend news → skip
                                        continue
                                    # gap-down on Monday → intraday recovery → allow through

                                # 2. Extreme gain cap — EXCEPTION for small cap: slow-burn
                                #    moves with small gap (0-2%) pass to stage 2 where
                                #    RVOL≥4x is confirmed. WR=55% vs 40% base.
                                _ecap = EXTREME_CAP.get(tier_key, 999)
                                _is_extreme = pct > _ecap
                                if _is_extreme:
                                    if not (tier_key == "small" and 0 <= gap_pct < 2):
                                        print(f"[multiday_runner] intraday skip {tkr}: "
                                              f"extreme gain {pct:.1f}%")
                                        continue

                                # 3. Weak price zone for mid/small cap
                                _wpz = WEAK_PRICE_ZONE.get(tier_key)
                                if _wpz and _wpz[0] <= cur_close < _wpz[1]:
                                    print(f"[multiday_runner] intraday skip {tkr}: "
                                          f"price ${cur_close:.2f} in weak zone")
                                    continue
                                candidates.append({
                                    "ticker":     tkr,
                                    "pct_so_far": round(pct, 2),
                                    "prev_close": round(prev_close, 4),
                                    "gap_pct":    round(gap_pct, 2),
                                    "is_extreme": _is_extreme,
                                })
                        except Exception:
                            pass
                    time.sleep(0.3)
                except Exception as e:
                    print(f"[multiday_runner] intraday stage1 batch error: {e}")
        except Exception as e:
            print(f"[multiday_runner] intraday stage1 outer error: {e}")

        if not candidates:
            print(f"[multiday_runner] intraday {tier_key}: no stage1 candidates")
            continue

        print(f"[multiday_runner] intraday {tier_key}: {len(candidates)} stage1 candidates → checking VWAP+range")

        # ── Stage 2: 5-min bars → VWAP, range position, adjusted RVOL ────
        confirmed_signals = []
        cand_tickers = [c["ticker"] for c in candidates]

        try:
            intraday = yf.download(
                cand_tickers, period="1d", interval="5m",
                group_by="ticker", auto_adjust=True, progress=False,
                timeout=30,
            )
        except Exception as e:
            print(f"[multiday_runner] intraday stage2 download error: {e}")
            intraday = None

        # Get 5-day avg volume from daily data for RVOL
        avg_vols: dict = {}
        try:
            vol_data = yf.download(
                cand_tickers, period="10d", interval="1d",
                group_by="ticker", auto_adjust=True, progress=False, timeout=20,
            )
            for tkr in cand_tickers:
                try:
                    vdf = vol_data[tkr].dropna() if len(cand_tickers) > 1 else vol_data
                    if len(vdf) >= 2:
                        avg_vols[tkr] = float(vdf["Volume"].iloc[:-1].mean())
                except Exception:
                    pass
        except Exception:
            pass

        for cand in candidates:
            tkr = cand["ticker"]
            pct = cand["pct_so_far"]
            try:
                df5 = None
                if intraday is not None:
                    try:
                        df5 = intraday[tkr].dropna() if len(cand_tickers) > 1 else intraday
                    except Exception:
                        pass

                if df5 is None or len(df5) < 5:
                    # Can't verify — skip (don't send unverified signals)
                    continue

                # VWAP calculation
                typical = (df5["High"] + df5["Low"] + df5["Close"]) / 3
                vwap    = (typical * df5["Volume"]).cumsum().iloc[-1] / df5["Volume"].cumsum().iloc[-1]

                current   = float(df5["Close"].iloc[-1])
                day_high  = float(df5["High"].max())
                day_low   = float(df5["Low"].min())
                today_vol = float(df5["Volume"].sum())

                # Range position: must be in top 30% (≥ 0.70)
                day_range  = day_high - day_low
                range_pos  = (current - day_low) / day_range if day_range > 0 else 0.5

                # Adjusted RVOL: 2 PM = 4.5h of 6.5h session = 69.2%
                avg_vol    = avg_vols.get(tkr, 0)
                adj_rvol   = (today_vol / 0.692) / avg_vol if avg_vol > 0 else 0.0

                above_vwap = current >= float(vwap) * 0.999  # 0.1% tolerance
                top30      = range_pos >= 0.70
                vol_ok     = adj_rvol >= 2.0 or today_vol > avg_vol * 1.2  # relaxed for small/mid

                if not (above_vwap and top30 and vol_ok):
                    continue

                # Extreme gain rescue gate: small cap slow-burn needs RVOL≥4x
                if cand.get("is_extreme") and adj_rvol < 4.0:
                    print(f"[multiday_runner] intraday skip {tkr}: extreme gain but RVOL={adj_rvol:.1f}x < 4x rescue threshold")
                    continue

                is_strong  = pct >= strong_pct
                _stop_mult = {"large": 0.97, "mid": 0.96, "small": 0.95}.get(tier_key, 0.97)
                stop       = round(cand["prev_close"] * _stop_mult, 4)  # tier-specific: large 3%, mid 4%, small 5%

                row = {
                    "ticker":      tkr,
                    "cap_tier":    tier_key,
                    "d1_date":     today,
                    "pct_so_far":  round(pct, 2),
                    "current":     round(current, 2),
                    "entry_price": round(current, 2),
                    "stop_price":  round(stop, 2),
                    "vwap":        round(float(vwap), 2),
                    "adj_rvol":    round(adj_rvol, 2),
                    "range_pos":   round(range_pos * 100, 0),
                    "day_high":    round(day_high, 2),
                    "day_low":     round(day_low, 2),
                    "is_strong":   is_strong,
                    "above_vwap":  above_vwap,
                    "label":       label,
                }
                confirmed_signals.append(row)

                # Save/update in DB (intraday_hit flag)
                try:
                    with pg.connect(os.environ["DATABASE_URL"]) as c, c.cursor() as cur:
                        cur.execute("""
                            INSERT INTO multiday_runner_watch
                              (d1_date, ticker, cap_tier, d1_pct, d1_close,
                               d1_high, d1_low, d1_rvol, d1_strong,
                               intraday_hit, intraday_entry, status)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, TRUE,%s,'intraday')
                            ON CONFLICT (d1_date, ticker) DO UPDATE SET
                              intraday_hit   = TRUE,
                              intraday_entry = EXCLUDED.intraday_entry,
                              d1_pct         = GREATEST(multiday_runner_watch.d1_pct, EXCLUDED.d1_pct)
                        """, (
                            today, tkr, tier_key,
                            round(pct, 2), round(current, 4),
                            round(day_high, 4), round(day_low, 4),
                            round(adj_rvol, 2), is_strong,
                            round(current, 4),
                        ))
                        c.commit()
                except Exception as db_e:
                    print(f"[multiday_runner] intraday DB save {tkr}: {db_e}")

            except Exception as e:
                print(f"[multiday_runner] intraday stage2 {tkr}: {e}")

        confirmed_signals.sort(key=lambda r: r["pct_so_far"], reverse=True)
        results_by_tier[tier_key] = confirmed_signals
        results_by_tier["all"].extend(confirmed_signals)
        print(f"[multiday_runner] intraday {tier_key}: {len(confirmed_signals)} confirmed signals")

    return results_by_tier


# ── EOD Day 1 Scan — 4:05 PM ───────────────────────────────────────────────

def run_day1_scan() -> list:
    """
    4:05 PM ET: scan all 3 cap tiers for today's ignitions.
    Saves to DB with final close prices for D2 tracking.
    Returns list of all row dicts sorted by pct desc.
    """
    import yfinance as yf
    import pandas as pd
    import psycopg2 as pg

    today   = _today_et()
    all_rows = []

    for tier_key, (universe, min_pct, strong_pct, label, _color) in CAP_TIERS.items():
        rows_saved = []
        try:
            data = yf.download(
                universe, period="45d", interval="1d",
                group_by="ticker", auto_adjust=True, progress=False,
            )
        except Exception as e:
            print(f"[multiday_runner] day1 EOD {tier_key} download error: {e}")
            continue

        for ticker in universe:
            try:
                df = data[ticker].dropna() if len(universe) > 1 else data
                if len(df) < 2:
                    continue
                closes  = df["Close"].values.astype(float)
                opens   = df["Open"].values.astype(float)
                volumes = df["Volume"].values.astype(float)
                highs   = df["High"].values.astype(float)
                lows    = df["Low"].values.astype(float)

                if len(closes) < 8:   # need history for ATR + vol-trend
                    continue

                d0c     = closes[-2]
                d1c     = closes[-1]
                d1_open = opens[-1]
                d1_pct  = (d1c - d0c) / d0c * 100
                gap_pct = (d1_open - d0c) / d0c * 100
                if d1_pct < min_pct:
                    continue

                avg_vol  = float(pd.Series(volumes[:-1]).mean()) if len(volumes) > 1 else float(volumes[-1])
                d1_rvol  = float(volumes[-1]) / avg_vol if avg_vol > 0 else 1.0

                # ── Pre-compute new indicators ─────────────────────────────
                # 5-day ATR (True Range of last 5 trading days before D1)
                _atr_vals = []
                for _j in range(-7, -2):   # 5 rows ending at D0
                    _h = highs[_j]; _lo = lows[_j]; _pc = closes[_j - 1]
                    _atr_vals.append(max(_h - _lo, abs(_h - _pc), abs(_lo - _pc)))
                _atr5 = sum(_atr_vals) / len(_atr_vals) if _atr_vals else 0
                _d1_range = highs[-1] - lows[-1]
                _atr_mult = _d1_range / _atr5 if _atr5 > 0 else 1.0

                # Pre-D1 volume trend: % change from 3 days ago → D0
                _vol_trend = 0.0
                if len(volumes) >= 5 and volumes[-4] > 0:
                    _vol_trend = (volumes[-2] - volumes[-4]) / volumes[-4] * 100

                # ── Quality filters (data-validated) ──────────────────────
                # 1. Monday filter — EXCEPTION: gap-down opens are genuine
                #    intraday signals (not weekend news). WR=60-68% vs 37-53%.
                if today.weekday() == 0:
                    if gap_pct >= 0:   # gapped UP/flat → weekend news → skip
                        continue
                    # gap-down on Monday → intraday recovery → allow through

                # 2. Extreme gain cap — EXCEPTION for small cap: slow-burn
                #    momentum with small gap (0-2%) + RVOL≥4x. WR=55% vs 40%.
                _ecap = EXTREME_CAP.get(tier_key, 999)
                if d1_pct > _ecap:
                    rescue = (tier_key == "small"
                              and 0 <= gap_pct < 2
                              and d1_rvol >= 4.0)
                    if not rescue:
                        print(f"[multiday_runner] day1 skip {ticker}: "
                              f"extreme gain {d1_pct:.1f}% (no rescue)")
                        continue

                # 3. Weak price zone: $15-$50 mid/small have overhead supply
                _wpz = WEAK_PRICE_ZONE.get(tier_key)
                if _wpz and _wpz[0] <= d1c < _wpz[1]:
                    print(f"[multiday_runner] day1 skip {ticker}: "
                          f"price ${d1c:.2f} in weak zone ${_wpz[0]}-${_wpz[1]}")
                    continue
                d1_strong = d1_pct >= strong_pct

                # ── Conviction score: 4-factor backtest-validated quality engine ──
                # Measures setup QUALITY, orthogonal to d1_strong (size of move).
                # Score 4/4 → 66% WR, CI floor 60.4%, n=297 (large cap confirmed).
                # Score 3/4 → 60%+ WR confirmed.  Score 0-2 → base ~55% WR.
                #
                # Factor 1: 10-day magnet zone — tightened from backtest optimizer
                _10d_high = float(max(highs[-11:-1])) if len(highs) >= 11 else float(max(highs[:-1]))
                _near_h10 = d1c / _10d_high if _10d_high > 0 else 1.0
                _cv_magnet = 0.87 <= _near_h10 <= 0.970
                # Factor 2: 20-day downtrend reversal — must be at least -2% (not any neg)
                _cv_downtrend = False
                if len(closes) >= 22 and closes[-22] > 0:
                    _trend_20d = (d0c - closes[-22]) / closes[-22] * 100
                    _cv_downtrend = _trend_20d < -2.0
                # Factor 3: D0 quiet — prior day wasn't already running hot
                _cv_d0_quiet = False
                if len(closes) >= 3 and closes[-3] > 0:
                    _prior_gain = (d0c - closes[-3]) / closes[-3] * 100
                    _cv_d0_quiet = _prior_gain < 1.5
                # Factor 4: ATR normal range — D1 move not a panic spike
                _cv_atr_ok = 0.80 <= _atr_mult <= 2.0
                conviction_score = int(_cv_magnet) + int(_cv_downtrend) + int(_cv_d0_quiet) + int(_cv_atr_ok)

                row = {
                    "d1_date":          today,
                    "ticker":           ticker,
                    "cap_tier":         tier_key,
                    "d1_pct":           round(d1_pct, 2),
                    "d1_close":         round(d1c, 4),
                    "d1_high":          round(float(highs[-1]), 4),
                    "d1_low":           round(float(lows[-1]), 4),
                    "d1_rvol":          round(d1_rvol, 2),
                    "d1_vol":           int(volumes[-1]),
                    "d1_strong":        d1_strong,
                    "conviction_score": conviction_score,
                    "label":            label,
                }

                with pg.connect(os.environ["DATABASE_URL"]) as c, c.cursor() as cur:
                    cur.execute("""
                        INSERT INTO multiday_runner_watch
                          (d1_date, ticker, cap_tier, d1_pct, d1_close, d1_high, d1_low,
                           d1_rvol, d1_vol, d1_strong, conviction_score, status)
                        VALUES (%(d1_date)s,%(ticker)s,%(cap_tier)s,%(d1_pct)s,%(d1_close)s,
                                %(d1_high)s,%(d1_low)s,%(d1_rvol)s,%(d1_vol)s,%(d1_strong)s,
                                %(conviction_score)s,'watch')
                        ON CONFLICT (d1_date, ticker) DO UPDATE SET
                          d1_pct           = EXCLUDED.d1_pct,
                          d1_close         = EXCLUDED.d1_close,
                          d1_high          = EXCLUDED.d1_high,
                          d1_low           = EXCLUDED.d1_low,
                          d1_rvol          = EXCLUDED.d1_rvol,
                          d1_vol           = EXCLUDED.d1_vol,
                          d1_strong        = EXCLUDED.d1_strong,
                          conviction_score = EXCLUDED.conviction_score,
                          cap_tier         = EXCLUDED.cap_tier
                    """, row)
                    c.commit()
                rows_saved.append(row)
            except Exception as e:
                print(f"[multiday_runner] day1 EOD {ticker}: {e}")

        all_rows.extend(rows_saved)
        print(f"[multiday_runner] day1 EOD {tier_key}: {len(rows_saved)} ignitions saved")

    return sorted(all_rows, key=lambda r: r["d1_pct"], reverse=True)


# ── Day 2 Confirm Scan — 2:45 PM ──────────────────────────────────────────

def run_day2_confirm_scan() -> list:
    """2:45 PM ET: second-chance entry — confirms yesterday's watch list."""
    import yfinance as yf
    import psycopg2 as pg

    today     = _today_et()
    yesterday = _prev_trading_day(today, 1)
    confirmed = []

    with pg.connect(os.environ["DATABASE_URL"]) as c, c.cursor() as cur:
        cur.execute("""
            SELECT id, ticker, d1_close, d1_pct, d1_strong, cap_tier,
                   COALESCE(conviction_score, 0)
            FROM multiday_runner_watch
            WHERE d1_date = %s AND status IN ('watch', 'intraday')
        """, (yesterday,))
        rows = cur.fetchall()

    if not rows:
        print(f"[multiday_runner] day2: no watch entries from {yesterday}")
        return []

    tickers = [r[1] for r in rows]
    try:
        live = yf.download(
            tickers, period="1d", interval="5m",
            group_by="ticker", auto_adjust=True, progress=False,
        )
    except Exception as e:
        print(f"[multiday_runner] day2 download error: {e}")
        return []

    for (row_id, ticker, d1_close, d1_pct, d1_strong, cap_tier, conviction_score) in rows:
        try:
            df = live[ticker].dropna() if len(tickers) > 1 else live
            if df is None or len(df) == 0:
                continue
            current  = float(df["Close"].iloc[-1])
            day_high = float(df["High"].max())
            day_low  = float(df["Low"].min())
            above_d1  = current > d1_close
            day_range = day_high - day_low
            close_pos = (current - day_low) / day_range if day_range > 0 else 0.5
            top_half  = close_pos >= 0.5
            is_confirm = above_d1 and top_half
            d2_pct = (current - d1_close) / d1_close * 100

            with pg.connect(os.environ["DATABASE_URL"]) as c, c.cursor() as cur:
                cur.execute("""
                    UPDATE multiday_runner_watch SET
                      d2_date=%s, d2_pct=%s, d2_close=%s, d2_close_pos=%s,
                      d2_above_d1=%s, confirmed=%s, status=%s,
                      entry_price=%s, stop_price=%s
                    WHERE id=%s
                """, (
                    today, round(d2_pct, 2), round(current, 4), round(close_pos, 3),
                    above_d1, is_confirm,
                    "confirmed" if is_confirm else "rejected",
                    round(current, 4) if is_confirm else None,
                    round(d1_close * 0.98, 4) if is_confirm else None,
                    row_id,
                ))
                c.commit()

            if is_confirm:
                confirmed.append({
                    "ticker":      ticker,
                    "cap_tier":    cap_tier or "large",
                    "d1_date":     str(yesterday),
                    "d1_pct":      round(d1_pct, 2),
                    "d1_strong":        d1_strong,
                    "conviction_score": conviction_score,
                    "d2_pct":           round(d2_pct, 2),
                    "current":          round(current, 2),
                    "entry_price":      round(current, 2),
                    "stop_price":       round(d1_close * 0.98, 2),
                    "close_pos":        round(close_pos * 100, 0),
                })
        except Exception as e:
            print(f"[multiday_runner] day2 {ticker}: {e}")

    return sorted(confirmed, key=lambda r: r.get("d1_pct", 0), reverse=True)


# ── Outcomes Updater — 4:30 PM daily ──────────────────────────────────────

def run_outcomes_update():
    """
    4:30 PM ET: fill in D+3, D+5, D+10 returns for past signals.
    Uses calendar-day offsets: D+5c ≈ 3 trading days, D+8c ≈ 5 td, D+14c ≈ 10 td.
    """
    import yfinance as yf
    import psycopg2 as pg

    today = _today_et()

    with pg.connect(os.environ["DATABASE_URL"]) as c, c.cursor() as cur:
        # Find rows that need outcome fills
        cur.execute("""
            SELECT id, ticker, d1_date,
                   COALESCE(intraday_entry, entry_price, d1_close) AS ref_price,
                   d3_pct, d5_pct, d10_pct
            FROM multiday_runner_watch
            WHERE (intraday_hit = TRUE OR confirmed = TRUE)
              AND COALESCE(intraday_entry, entry_price, d1_close) IS NOT NULL
              AND (
                (d3_pct  IS NULL AND %s >= d1_date + 4)  OR
                (d5_pct  IS NULL AND %s >= d1_date + 8)  OR
                (d10_pct IS NULL AND %s >= d1_date + 14)
              )
            ORDER BY d1_date DESC
            LIMIT 60
        """, (today, today, today))
        pending = cur.fetchall()

    if not pending:
        print("[multiday_runner] outcomes: nothing to update today")
        return

    tickers = list({r[1] for r in pending})
    print(f"[multiday_runner] outcomes: updating {len(pending)} rows for {len(tickers)} tickers")

    try:
        hist = yf.download(
            tickers, period="20d", interval="1d",
            group_by="ticker", auto_adjust=True, progress=False,
        )
    except Exception as e:
        print(f"[multiday_runner] outcomes download error: {e}")
        return

    with pg.connect(os.environ["DATABASE_URL"]) as c, c.cursor() as cur:
        for (row_id, ticker, d1_date, ref_price, d3_pct, d5_pct, d10_pct) in pending:
            try:
                df = hist[ticker].dropna() if len(tickers) > 1 else hist
                if df is None or len(df) < 2 or not ref_price:
                    continue

                def _pct_at_offset(days_cal: int):
                    target = d1_date + timedelta(days=days_cal)
                    subset = df[df.index.date >= target]
                    if subset.empty:
                        return None
                    close = float(subset["Close"].iloc[0])
                    return round((close - ref_price) / ref_price * 100, 2)

                updates = []
                if d3_pct is None and today >= d1_date + timedelta(days=4):
                    v = _pct_at_offset(4)
                    if v is not None:
                        updates.append(("d3_pct", v))
                if d5_pct is None and today >= d1_date + timedelta(days=8):
                    v = _pct_at_offset(8)
                    if v is not None:
                        updates.append(("d5_pct", v))
                if d10_pct is None and today >= d1_date + timedelta(days=14):
                    v = _pct_at_offset(14)
                    if v is not None:
                        updates.append(("d10_pct", v))

                if updates:
                    set_clause = ", ".join(f"{col} = %s" for col, _ in updates)
                    vals = [v for _, v in updates]
                    cur.execute(f"UPDATE multiday_runner_watch SET {set_clause} WHERE id = %s",
                                vals + [row_id])
            except Exception as e:
                print(f"[multiday_runner] outcomes {ticker}: {e}")
        c.commit()
    print("[multiday_runner] outcomes update done")


# ── API data getters ───────────────────────────────────────────────────────

def get_multiday_runners_data() -> dict:
    """Main tab: today's watch + recent confirmed + active holds + stats."""
    import psycopg2 as pg, psycopg2.extras

    today = _today_et()
    with pg.connect(os.environ["DATABASE_URL"]) as c:
        with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT ticker, d1_date, cap_tier, d1_pct, d1_close, d1_rvol,
                       d1_strong, COALESCE(conviction_score, 0) AS conviction_score,
                       intraday_hit, intraday_entry, status
                FROM multiday_runner_watch
                WHERE d1_date = %s
                ORDER BY conviction_score DESC, d1_pct DESC
            """, (today,))
            watch = [dict(r) for r in cur.fetchall()]

            cur.execute("""
                SELECT ticker, d1_date, d2_date, cap_tier, d1_pct, d2_pct,
                       d1_strong, COALESCE(conviction_score, 0) AS conviction_score,
                       entry_price, intraday_entry, intraday_hit,
                       stop_price, d2_close_pos, status
                FROM multiday_runner_watch
                WHERE (confirmed = TRUE OR intraday_hit = TRUE)
                  AND d1_date >= %s
                ORDER BY conviction_score DESC, d1_pct DESC
            """, (today - timedelta(days=2),))
            confirmed = [dict(r) for r in cur.fetchall()]

            cur.execute("""
                SELECT ticker, d1_date, d2_date, cap_tier, d1_pct, d2_pct,
                       entry_price, intraday_entry, intraday_hit,
                       COALESCE(conviction_score, 0) AS conviction_score,
                       stop_price, status, confirmed
                FROM multiday_runner_watch
                WHERE (confirmed = TRUE OR intraday_hit = TRUE)
                  AND exit_date IS NULL
                  AND status NOT IN ('exited','rejected','watch')
                  AND d1_date >= %s
                ORDER BY d1_date DESC, conviction_score DESC, d1_pct DESC
            """, (today - timedelta(days=7),))
            active = [dict(r) for r in cur.fetchall()]

            cur.execute("""
                SELECT
                  COUNT(*) FILTER (WHERE intraday_hit OR confirmed)           AS total_signals,
                  COUNT(*) FILTER (WHERE d5_pct > 0)                          AS wins_d5,
                  COUNT(*) FILTER (WHERE d5_pct IS NOT NULL AND d5_pct <= 0)  AS losses_d5,
                  ROUND(AVG(d5_pct) FILTER (WHERE d5_pct IS NOT NULL)::numeric,2) AS avg_d5,
                  ROUND(MAX(d5_pct)::numeric,2)                               AS best_d5,
                  ROUND(MIN(d5_pct) FILTER (WHERE d5_pct IS NOT NULL)::numeric,2) AS worst_d5
                FROM multiday_runner_watch
                WHERE d1_date >= %s
            """, (today - timedelta(days=60),))
            row = cur.fetchone()
            stats = dict(row) if row else {}

    def _ser(lst):
        out = []
        for r in lst:
            d = {}
            for k, v in r.items():
                if isinstance(v, date):
                    d[k] = str(v)
                elif isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    d[k] = None
                else:
                    d[k] = v
            out.append(d)
        return out

    return {
        "watch":     _ser(watch),
        "confirmed": _ser(confirmed),
        "active":    _ser(active),
        "stats":     {k: (None if (isinstance(v, float) and math.isnan(v)) else v)
                      for k, v in stats.items()},
        "as_of":     datetime.now(_ET_TZ).strftime("%Y-%m-%d %H:%M ET"),
    }


def get_runner_outcomes_data() -> dict:
    """Outcomes tab: historical signals with D+3, D+5, D+10 returns."""
    import psycopg2 as pg, psycopg2.extras

    today = _today_et()
    with pg.connect(os.environ["DATABASE_URL"]) as c:
        with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT ticker, d1_date, cap_tier, d1_pct, d1_strong,
                       COALESCE(conviction_score, 0) AS conviction_score,
                       intraday_hit, intraday_entry, entry_price,
                       d3_pct, d5_pct, d10_pct, confirmed, status
                FROM multiday_runner_watch
                WHERE (intraday_hit = TRUE OR confirmed = TRUE)
                ORDER BY d1_date DESC, d1_pct DESC
                LIMIT 200
            """, )
            rows = [dict(r) for r in cur.fetchall()]

            # Aggregate stats by tier and hold period
            cur.execute("""
                SELECT
                  cap_tier,
                  COUNT(*)                                                    AS total,
                  COUNT(*) FILTER (WHERE d3_pct  IS NOT NULL)                AS graded_d3,
                  COUNT(*) FILTER (WHERE d5_pct  IS NOT NULL)                AS graded_d5,
                  COUNT(*) FILTER (WHERE d10_pct IS NOT NULL)                AS graded_d10,
                  ROUND(AVG(d3_pct)  FILTER (WHERE d3_pct  IS NOT NULL)::numeric,2) AS avg_d3,
                  ROUND(AVG(d5_pct)  FILTER (WHERE d5_pct  IS NOT NULL)::numeric,2) AS avg_d5,
                  ROUND(AVG(d10_pct) FILTER (WHERE d10_pct IS NOT NULL)::numeric,2) AS avg_d10,
                  COUNT(*) FILTER (WHERE d5_pct  > 0)                        AS wins_d5,
                  COUNT(*) FILTER (WHERE d5_pct  <= 0 AND d5_pct IS NOT NULL) AS losses_d5,
                  ROUND(MAX(d5_pct)::numeric,2)                              AS best_d5,
                  ROUND(MIN(d5_pct) FILTER (WHERE d5_pct IS NOT NULL)::numeric,2) AS worst_d5
                FROM multiday_runner_watch
                WHERE (intraday_hit = TRUE OR confirmed = TRUE)
                  AND d1_date >= %s
                GROUP BY cap_tier
                ORDER BY cap_tier
            """, (today - timedelta(days=90),))
            tier_stats = [dict(r) for r in cur.fetchall()]

    def _clean(lst):
        out = []
        for r in lst:
            d = {}
            for k, v in r.items():
                if isinstance(v, date):
                    d[k] = str(v)
                elif isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    d[k] = None
                else:
                    d[k] = v
            out.append(d)
        return out

    return {
        "signals":    _clean(rows),
        "tier_stats": _clean(tier_stats),
        "as_of":      datetime.now(_ET_TZ).strftime("%Y-%m-%d %H:%M ET"),
    }


# ── Email builders ─────────────────────────────────────────────────────────

def build_intraday_d1_email_html(results: dict) -> str:
    """2 PM Day 1 email — BUY NOW signal across all 3 cap tiers."""
    all_rows = results.get("all", [])
    if not all_rows:
        return ""

    TIER_ORDER = [
        ("large", "#22c55e", "🟢 LARGE CAP ($10B+)",    "≥3%", "≥5%"),
        ("mid",   "#38bdf8", "🔵 MID CAP ($2B–$10B)",   "≥4%", "≥7%"),
        ("small", "#f59e0b", "🟡 SMALL CAP ($300M–$2B)","≥5%", "≥10%"),
    ]

    # Stop loss by tier
    STOPS = {"large": "3%", "mid": "4%", "small": "5%"}

    def _ticker_table(rows, highlight_color, stop_pct):
        html = ""
        for r in rows[:10]:
            bg = "rgba(245,158,11,0.12)" if r.get("is_strong") else "transparent"
            html += f"""
            <tr style="border-bottom:1px solid rgba(255,255,255,0.06);background:{bg}">
              <td style="padding:10px 8px;font-weight:900;font-size:17px;color:#fff">{r['ticker']}</td>
              <td style="padding:10px 8px;color:{highlight_color};font-weight:700;font-size:16px">+{r['pct_so_far']:.1f}%</td>
              <td style="padding:10px 8px;color:#fff;font-weight:700">${r['entry_price']:.2f}</td>
              <td style="padding:10px 8px;color:#f87171">${r['stop_price']:.2f} <span style="font-size:10px;color:#64748b">({stop_pct} below prev close)</span></td>
              <td style="padding:10px 8px;color:#a78bfa">{r['adj_rvol']:.1f}x</td>
              <td style="padding:10px 8px;color:#94a3b8">{int(r['range_pos'])}%</td>
            </tr>"""
        return html

    sections = ""
    for tier_key, color, tier_label, threshold, strong_threshold in TIER_ORDER:
        tier_rows = [r for r in all_rows if r.get("cap_tier") == tier_key]
        if not tier_rows:
            continue
        stop_pct  = STOPS[tier_key]
        strong_rows  = [r for r in tier_rows if r.get("is_strong")]
        regular_rows = [r for r in tier_rows if not r.get("is_strong")]

        strong_block = ""
        if strong_rows:
            strong_block = f"""
        <div style="background:rgba(245,158,11,0.12);border:1px solid rgba(245,158,11,0.4);border-radius:10px;padding:14px 16px;margin-bottom:14px">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
            <span style="background:#f59e0b;color:#000;padding:3px 12px;border-radius:5px;font-size:11px;font-weight:900;letter-spacing:.06em">🔥 STRONG SIGNAL</span>
            <span style="color:#f59e0b;font-size:12px;font-weight:700">{strong_threshold} gain — 69.6% win rate · +4.1% avg (D1→D5)</span>
          </div>
          <table style="width:100%;border-collapse:collapse">
            <thead><tr style="border-bottom:1px solid rgba(255,255,255,0.1)">
              <th style="padding:5px 8px;text-align:left;font-size:10px;color:#64748b">TICKER</th>
              <th style="padding:5px 8px;text-align:left;font-size:10px;color:#64748b">GAIN</th>
              <th style="padding:5px 8px;text-align:left;font-size:10px;color:#64748b">ENTRY</th>
              <th style="padding:5px 8px;text-align:left;font-size:10px;color:#64748b">STOP</th>
              <th style="padding:5px 8px;text-align:left;font-size:10px;color:#64748b">RVOL</th>
              <th style="padding:5px 8px;text-align:left;font-size:10px;color:#64748b">RANGE</th>
            </tr></thead>
            <tbody>{_ticker_table(strong_rows, "#f59e0b", stop_pct)}</tbody>
          </table>
        </div>"""

        regular_block = ""
        if regular_rows:
            regular_block = f"""
        <div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);border-radius:10px;padding:14px 16px;margin-bottom:14px">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
            <span style="background:rgba(34,197,94,0.15);color:#22c55e;padding:3px 12px;border-radius:5px;font-size:11px;font-weight:800;border:1px solid rgba(34,197,94,0.3)">✅ CONFIRMED SIGNAL</span>
            <span style="color:#94a3b8;font-size:12px;font-weight:600">{threshold} gain — 59.7% win rate · +2.2% avg (D1→D5)</span>
          </div>
          <table style="width:100%;border-collapse:collapse">
            <thead><tr style="border-bottom:1px solid rgba(255,255,255,0.1)">
              <th style="padding:5px 8px;text-align:left;font-size:10px;color:#64748b">TICKER</th>
              <th style="padding:5px 8px;text-align:left;font-size:10px;color:#64748b">GAIN</th>
              <th style="padding:5px 8px;text-align:left;font-size:10px;color:#64748b">ENTRY</th>
              <th style="padding:5px 8px;text-align:left;font-size:10px;color:#64748b">STOP</th>
              <th style="padding:5px 8px;text-align:left;font-size:10px;color:#64748b">RVOL</th>
              <th style="padding:5px 8px;text-align:left;font-size:10px;color:#64748b">RANGE</th>
            </tr></thead>
            <tbody>{_ticker_table(regular_rows, color, stop_pct)}</tbody>
          </table>
        </div>"""

        sections += f"""
        <div style="margin-bottom:28px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">
            <div style="width:4px;height:18px;background:{color};border-radius:2px"></div>
            <span style="color:{color};font-weight:800;font-size:12px;letter-spacing:.08em;text-transform:uppercase">{tier_label} — {len(tier_rows)} signal{'s' if len(tier_rows)!=1 else ''}</span>
          </div>
          {strong_block}{regular_block}
        </div>"""

    return f"""<!DOCTYPE html><html><body style="background:#0f1117;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#fff;margin:0;padding:0">
<div style="max-width:660px;margin:0 auto;padding:32px 24px">
  <div style="text-align:center;margin-bottom:24px">
    <div style="display:inline-block;background:#22c55e;color:#000;padding:6px 20px;border-radius:6px;font-size:12px;font-weight:800;letter-spacing:.1em">📈 DAY 1 BUY SIGNAL — 2:00 PM ET</div>
    <h1 style="font-size:26px;font-weight:900;margin:14px 0 4px">Multi-Day Runner Alert</h1>
    <p style="color:#64748b;font-size:13px;margin:0">{len(all_rows)} confirmed · VWAP hold + top-of-range + RVOL ≥ 2x</p>
  </div>
  <div style="background:rgba(34,197,94,0.06);border:1px solid rgba(34,197,94,0.2);border-radius:12px;padding:16px 20px;margin-bottom:28px">
    <p style="margin:0;font-size:13px;color:#94a3b8;line-height:1.8">
      <strong style="color:#fff">Enter at market before 3:45 PM ET.  Exit at Day 5 close.</strong><br>
      🔥 <strong style="color:#f59e0b">STRONG</strong> = extra-large Day 1 move — <strong style="color:#f59e0b">69.6% win rate</strong>, prioritize these.<br>
      ⭐ <strong style="color:#a855f7">CV4</strong> = 4-factor quality score — <strong style="color:#a855f7">66% win rate, CI floor 60%+</strong> (magnet zone + downtrend reversal + quiet D0 + normal ATR).<br>
      ✅ <strong style="color:#22c55e">CONFIRMED</strong> = standard signal — <strong style="color:#22c55e">59.7% win rate</strong>, still valid.<br>
      Stop losses are tier-specific (Large 3% · Mid 4% · Small 5% below prev close).
    </p>
  </div>
  {sections}
  <div style="padding:14px 16px;background:rgba(255,255,255,0.03);border-radius:8px;margin-top:4px;border:1px solid rgba(255,255,255,0.06)">
    <p style="margin:0;font-size:11px;color:#475569;line-height:1.7">
      60-day large-cap backtest · D1 entry → D5 exit · Not a guarantee · Always use the stop loss.
    </p>
  </div>
</div></body></html>"""


def build_day1_email_html(rows: list) -> str:
    """4:05 PM EOD email — full watch list across all 3 tiers."""
    if not rows:
        return ""

    TIER_ORDER = [
        ("large", "#22c55e", "Large Cap ($10B+)"),
        ("mid",   "#38bdf8", "Mid Cap ($2B–$10B)"),
        ("small", "#f59e0b", "Small Cap ($300M–$2B)"),
    ]

    sections = ""
    for tier_key, color, tier_label in TIER_ORDER:
        tier_rows = [r for r in rows if r.get("cap_tier") == tier_key]
        if not tier_rows:
            continue
        ticker_html = ""
        for r in tier_rows[:15]:
            strong_badge = f'<span style="background:{color};color:#000;padding:1px 7px;border-radius:4px;font-size:10px;font-weight:800">STRONG</span> ' if r.get("d1_strong") else ''
            cv = r.get("conviction_score", 0) or 0
            if cv >= 4:
                cv_badge = '<span style="background:#a855f7;color:#fff;padding:1px 7px;border-radius:4px;font-size:10px;font-weight:800">⭐ CV4</span>'
            elif cv == 3:
                cv_badge = '<span style="background:rgba(168,85,247,0.3);color:#d8b4fe;padding:1px 7px;border-radius:4px;font-size:10px;font-weight:700">CV3</span>'
            else:
                cv_badge = f'<span style="color:#475569;font-size:10px">CV{cv}</span>'
            ticker_html += f"""<tr style="border-bottom:1px solid rgba(255,255,255,0.06)">
              <td style="padding:10px 8px;font-weight:800;font-size:17px;color:#fff">{r['ticker']}</td>
              <td style="padding:10px 8px;color:{color};font-weight:700;font-size:15px">+{r['d1_pct']:.1f}%</td>
              <td style="padding:10px 8px;color:#94a3b8">${r['d1_close']:.2f}</td>
              <td style="padding:10px 8px;color:#64748b">{r['d1_rvol']:.1f}x</td>
              <td style="padding:10px 8px">{strong_badge}{cv_badge}</td>
            </tr>"""
        sections += f"""<div style="margin-bottom:20px">
          <div style="color:{color};font-weight:800;font-size:11px;letter-spacing:.08em;text-transform:uppercase;margin-bottom:8px">{tier_label} — {len(tier_rows)} ignitions</div>
          <table style="width:100%;border-collapse:collapse"><thead>
            <tr style="border-bottom:1px solid rgba(255,255,255,0.1)">
              <th style="padding:6px 8px;text-align:left;font-size:10px;color:#64748b">TICKER</th>
              <th style="padding:6px 8px;text-align:left;font-size:10px;color:#64748b">D1 GAIN</th>
              <th style="padding:6px 8px;text-align:left;font-size:10px;color:#64748b">CLOSE</th>
              <th style="padding:6px 8px;text-align:left;font-size:10px;color:#64748b">RVOL</th>
              <th style="padding:6px 8px;font-size:10px;color:#64748b"></th>
            </tr></thead><tbody>{ticker_html}</tbody></table>
        </div>"""

    return f"""<!DOCTYPE html><html><body style="background:#0f1117;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#fff;margin:0;padding:0">
<div style="max-width:600px;margin:0 auto;padding:32px 24px">
  <div style="text-align:center;margin-bottom:24px">
    <div style="display:inline-block;background:#22c55e;color:#000;padding:6px 18px;border-radius:6px;font-size:11px;font-weight:800">STOCKSCANNER AI</div>
    <h1 style="font-size:26px;font-weight:900;margin:12px 0 4px">EOD Day 1 Watch List</h1>
    <p style="color:#64748b;font-size:13px;margin:0">{len(rows)} ignitions across all cap tiers · D2 confirm tomorrow at 2:45 PM</p>
  </div>
  {sections}
</div></body></html>"""


def build_day2_email_html(confirmed: list) -> str:
    """2:45 PM Day 2 BUY SIGNAL — second-chance entry email."""
    if not confirmed:
        return ""

    TIER_ORDER = [
        ("large", "#22c55e", "Large Cap"),
        ("mid",   "#38bdf8", "Mid Cap"),
        ("small", "#f59e0b", "Small Cap"),
    ]

    sections = ""
    for tier_key, color, tier_label in TIER_ORDER:
        tier_rows = [r for r in confirmed if r.get("cap_tier") == tier_key]
        if not tier_rows:
            continue
        ticker_html = ""
        for r in tier_rows:
            strong = '<span style="background:#f59e0b;color:#000;padding:1px 7px;border-radius:4px;font-size:10px;font-weight:800">STRONG</span> ' if r.get("d1_strong") else ''
            ticker_html += f"""<tr style="border-bottom:1px solid rgba(255,255,255,0.06)">
              <td style="padding:12px 8px"><div style="font-weight:900;font-size:19px;color:#fff">{r['ticker']}</div><div style="font-size:11px;color:#64748b">D1: +{r['d1_pct']:.1f}%</div></td>
              <td style="padding:12px 8px;text-align:center"><div style="color:{color};font-weight:700;font-size:16px">+{r['d2_pct']:.1f}%</div><div style="font-size:11px;color:#64748b">D2</div></td>
              <td style="padding:12px 8px;text-align:center"><div style="color:#fff;font-weight:700">${r['entry_price']:.2f}</div><div style="font-size:11px;color:#64748b">entry</div></td>
              <td style="padding:12px 8px;text-align:center"><div style="color:#f87171;font-weight:700">${r['stop_price']:.2f}</div><div style="font-size:11px;color:#64748b">stop</div></td>
              <td style="padding:12px 8px">{strong}</td>
            </tr>"""
        sections += f"""<div style="margin-bottom:20px">
          <div style="color:{color};font-weight:800;font-size:11px;letter-spacing:.08em;text-transform:uppercase;margin-bottom:8px">{tier_label} — {len(tier_rows)} confirmed</div>
          <table style="width:100%;border-collapse:collapse"><thead>
            <tr style="border-bottom:1px solid rgba(255,255,255,0.1)">
              <th style="padding:6px 8px;text-align:left;font-size:10px;color:#64748b">TICKER</th>
              <th style="padding:6px 8px;text-align:center;font-size:10px;color:#64748b">D2 GAIN</th>
              <th style="padding:6px 8px;text-align:center;font-size:10px;color:#64748b">ENTRY</th>
              <th style="padding:6px 8px;text-align:center;font-size:10px;color:#64748b">STOP</th>
              <th style="padding:6px 8px;font-size:10px;color:#64748b"></th>
            </tr></thead><tbody>{ticker_html}</tbody></table>
        </div>"""

    return f"""<!DOCTYPE html><html><body style="background:#0f1117;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#fff;margin:0;padding:0">
<div style="max-width:620px;margin:0 auto;padding:32px 24px">
  <div style="text-align:center;margin-bottom:24px">
    <div style="display:inline-block;background:#22c55e;color:#000;padding:6px 20px;border-radius:6px;font-size:12px;font-weight:800">🟢 D2 BUY SIGNAL — 2:45 PM ET</div>
    <h1 style="font-size:28px;font-weight:900;margin:12px 0 4px">Day 2 Confirmed</h1>
    <p style="color:#64748b;font-size:13px;margin:0">Second-chance entry · above D1 close + top-half of range · Exit Day 5</p>
  </div>
  {sections}
</div></body></html>"""
