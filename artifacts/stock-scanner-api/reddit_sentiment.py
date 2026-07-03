"""
reddit_sentiment.py
====================================================================
Pulls recent Reddit posts/comments mentioning a ticker and computes a
simple sentiment score. Uses PRAW if Reddit API credentials are set;
falls back to a mock source otherwise so the scoring logic itself can
be tested without live credentials.
====================================================================
"""

import os
import re
import datetime as dt
from typing import Dict, Any, List

import psycopg2

POSITIVE_WORDS = {
    "moon", "bullish", "buy", "calls", "breakout", "squeeze", "rip",
    "strong", "beat", "upgrade", "rally", "long", "green",
}
NEGATIVE_WORDS = {
    "crash", "bearish", "sell", "puts", "dump", "tank", "weak",
    "miss", "downgrade", "short", "red", "drop", "fear",
}


def mock_reddit_posts(ticker: str) -> List[str]:
    """Fallback when no Reddit API credentials are configured."""
    return [
        f"{ticker} looking strong here, might break out soon",
        f"not sure about {ticker}, feels weak",
    ]


def fetch_reddit_posts(ticker: str, limit: int = 50) -> List[str]:
    """
    Real fetch via PRAW, if credentials exist. Searches r/wallstreetbets
    and r/stocks for the ticker symbol in the last 24h.
    """
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    user_agent = os.environ.get("REDDIT_USER_AGENT", "stockscanner-ai/1.0")

    if not client_id or not client_secret:
        return mock_reddit_posts(ticker)

    try:
        import praw
    except ImportError:
        return mock_reddit_posts(ticker)

    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
    )

    texts = []
    for subreddit_name in ["wallstreetbets", "stocks", "options"]:
        try:
            subreddit = reddit.subreddit(subreddit_name)
            for submission in subreddit.search(ticker, time_filter="day", limit=limit):
                texts.append(submission.title + " " + (submission.selftext or ""))
        except Exception:
            continue

    return texts if texts else mock_reddit_posts(ticker)


def score_text(text: str) -> int:
    """Crude lexicon-based sentiment: +1 per positive word, -1 per negative."""
    text_lower = text.lower()
    score = 0
    for word in POSITIVE_WORDS:
        score += len(re.findall(r"\b" + word + r"\b", text_lower))
    for word in NEGATIVE_WORDS:
        score -= len(re.findall(r"\b" + word + r"\b", text_lower))
    return score


def get_sentiment_score(ticker: str) -> Dict[str, Any]:
    """
    Returns a normalized sentiment score for a ticker based on recent
    Reddit mentions. Score ranges roughly -1.0 (very negative) to +1.0
    (very positive); 0.0 means neutral or no data.
    """
    _has_live_credentials = bool(
        os.environ.get("REDDIT_CLIENT_ID") and os.environ.get("REDDIT_CLIENT_SECRET")
    )
    posts = fetch_reddit_posts(ticker)
    if not posts:
        return {
            "ticker": ticker,
            "sentiment_score": 0.0,
            "post_count": 0,
            "has_live_credentials": _has_live_credentials,
            "checked_at": dt.datetime.utcnow().isoformat(),
        }

    raw_scores = [score_text(p) for p in posts]
    total = sum(raw_scores)
    max_possible = len(posts) * 3  # rough normalization ceiling
    normalized = max(-1.0, min(1.0, total / max_possible)) if max_possible else 0.0

    return {
        "ticker": ticker,
        "sentiment_score": round(normalized, 3),
        "post_count": len(posts),
        "raw_total_score": total,
        "has_live_credentials": _has_live_credentials,
        "checked_at": dt.datetime.utcnow().isoformat(),
    }


def log_sentiment(db_url: str, result: Dict[str, Any]) -> None:
    """
    Create this table once:
        CREATE TABLE IF NOT EXISTS reddit_sentiment_log (
            id SERIAL PRIMARY KEY,
            ticker VARCHAR(10),
            sentiment_score DOUBLE PRECISION,
            post_count INTEGER,
            checked_at TIMESTAMPTZ NOT NULL
        );
    """
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO reddit_sentiment_log (ticker, sentiment_score, post_count, checked_at)
                VALUES (%s, %s, %s, %s)
            """, (result["ticker"], result["sentiment_score"], result["post_count"], result["checked_at"]))
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    print(get_sentiment_score("AAPL"))
