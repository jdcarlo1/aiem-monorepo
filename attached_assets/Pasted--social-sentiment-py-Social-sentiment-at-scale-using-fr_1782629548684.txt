"""
social_sentiment.py
---------------------------
Social sentiment-at-scale, using free/public data sources only —
StockTwits' public symbol stream (no auth required for basic access) and
optionally Reddit via PRAW if you register a free Reddit API app.

This deliberately does NOT attempt satellite imagery or credit-card panel
data — those are paid enterprise vendor feeds (Orbital Insight, Eagle
Alpha, M Science, etc.), typically thousands of dollars/month, and require
a vendor contract + their specific API, not a Python module. If you ever
want those, the next step is evaluating vendors, not writing code.

WHAT THIS GIVES YOU
--------------------
- message_volume: how much chatter a ticker is getting right now vs its
  own recent baseline (volume SPIKES are often more informative than
  sentiment direction itself — a stock everyone is suddenly talking about,
  bullish or bearish, is behaviorally different from one nobody mentions)
- bullish_pct: StockTwits' own crowd-tagged sentiment (users self-tag posts
  Bullish/Bearish) — noisy, retail-skewed, but real and free
- vote-style indicator matching market_regime_overlay.py's contract, so it
  can plug in as one more vote alongside VIX/breadth/macro/GARCH

HONEST LIMITATIONS
-------------------
StockTwits is heavily retail-skewed and easy to manipulate for small/illiquid
names (a handful of accounts can spike "message volume" on a $20M float
stock). Treat this as a CONTRARIAN-leaning signal for thin names (extreme
bullish chatter on a illiquid microcap is often a warning, not confirmation)
and a confirming signal only for liquid, widely-followed names.

REQUIRES: requests (stdlib-adjacent, almost certainly already installed)
"""

import time
import datetime as dt
from typing import Dict, Any, Optional, List

import requests


STOCKTWITS_SYMBOL_URL = "https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"


def fetch_stocktwits_stream(ticker: str, max_retries: int = 2) -> Optional[Dict[str, Any]]:
    """
    Pulls the most recent ~30 messages for a ticker from StockTwits' public
    API. No auth required for this endpoint, but it IS rate-limited
    (roughly 200 requests/hour unauthenticated as of this writing) - cache
    results, don't poll continuously for your whole watchlist.
    """
    url = STOCKTWITS_SYMBOL_URL.format(ticker=ticker.upper())
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"[social_sentiment] StockTwits fetch failed for {ticker}: {e}")
                return None
            time.sleep(1)
    return None


def compute_sentiment_snapshot(ticker: str) -> Dict[str, Any]:
    """
    Returns message volume + bullish/bearish split for the most recent
    batch of messages. StockTwits messages are user-tagged Bullish/Bearish
    voluntarily — most messages have NO tag, so bullish_pct is computed
    only over the tagged subset, with the untagged count reported
    separately so you can see how much signal is actually behind the
    percentage (20 tagged messages saying 80% bullish means something
    different than 2 tagged messages saying the same 80%).
    """
    data = fetch_stocktwits_stream(ticker)
    if not data or "messages" not in data:
        return {
            "ticker": ticker, "message_count": 0, "tagged_count": 0,
            "bullish_pct": None, "error": "no data returned",
        }

    messages = data["messages"]
    tagged = [m for m in messages if m.get("entities", {}).get("sentiment")]
    bullish = sum(1 for m in tagged if m["entities"]["sentiment"]["basic"] == "Bullish")

    return {
        "ticker": ticker,
        "message_count": len(messages),
        "tagged_count": len(tagged),
        "bullish_pct": round(bullish / len(tagged), 3) if tagged else None,
        "fetched_at": dt.datetime.utcnow().isoformat(),
    }


def compute_volume_zscore(ticker: str, recent_snapshot: Dict[str, Any],
                           historical_message_counts: List[int]) -> Optional[float]:
    """
    Z-score of current message volume vs its own recent history. This is
    the more useful signal than raw bullish_pct — a sudden 5x spike in
    chatter (regardless of direction) often precedes a move; a stock with
    consistently 30 messages/snapshot saying nothing new isn't informative
    either way.

    historical_message_counts: list of past message_count values for the
    same ticker (you'll need to snapshot and store these over time — see
    snapshot_sentiment_to_db below).
    """
    import numpy as np
    if len(historical_message_counts) < 10:
        return None
    arr = np.array(historical_message_counts)
    std = arr.std()
    if std == 0:
        return None
    return float((recent_snapshot["message_count"] - arr.mean()) / std)


def sentiment_indicator(ticker: str, historical_message_counts: List[int],
                         is_thin_float: bool = False) -> Dict[str, Any]:
    """
    Vote-style indicator: {"vote": -1/0/1, "reason": str}. Same contract as
    market_regime_overlay.py's other indicators, scoped to one ticker
    rather than the broad market — use this as a per-ticker input to a
    signal score, not as a market-wide overlay vote.

    is_thin_float: pass True for illiquid/small-float names. For those,
    extreme bullish chatter + volume spike VOTES CAUTIOUS (-1) rather than
    confirming, per the manipulation-risk caveat in the module docstring.
    For liquid/widely-followed names, the same pattern votes confirming (1).
    """
    snapshot = compute_sentiment_snapshot(ticker)
    if snapshot.get("error") or snapshot["bullish_pct"] is None:
        return {"vote": 0, "reason": f"insufficient StockTwits data for {ticker}"}

    z = compute_volume_zscore(ticker, snapshot, historical_message_counts)
    if z is None:
        return {"vote": 0, "reason": "insufficient history to compute volume z-score yet"}

    volume_spike = z > 1.5
    strongly_bullish = snapshot["bullish_pct"] > 0.70 and snapshot["tagged_count"] >= 5

    if volume_spike and strongly_bullish:
        if is_thin_float:
            return {
                "vote": -1,
                "reason": f"{ticker}: message volume spike (z={z:.1f}) with "
                          f"{snapshot['bullish_pct']:.0%} bullish on thin float — "
                          f"manipulation/pump risk, treating as caution not confirmation",
            }
        return {
            "vote": 1,
            "reason": f"{ticker}: message volume spike (z={z:.1f}) with "
                      f"{snapshot['bullish_pct']:.0%} bullish on liquid name — "
                      f"genuine attention, consistent with the setup",
        }

    if volume_spike and not strongly_bullish:
        return {
            "vote": 0,
            "reason": f"{ticker}: volume spike (z={z:.1f}) but sentiment mixed "
                      f"({snapshot['bullish_pct']:.0%} bullish) — attention without consensus",
        }

    return {"vote": 0, "reason": f"{ticker}: no notable sentiment/volume anomaly"}
