"""News + Sentiment ohne Pflicht-API-Key."""
import json
import os
import re
from datetime import datetime, timedelta
from html import unescape
from typing import List, Tuple

import feedparser
import requests

import config


_NEWS_CACHE_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "news_cache.json")
_NEWS_CACHE_TTL = 30 * 60  # 30 Minuten


def _load_cache():
    try:
        with open(_NEWS_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache):
    os.makedirs(os.path.dirname(_NEWS_CACHE_FILE), exist_ok=True)
    with open(_NEWS_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, default=str)


# Einfache Wortlisten als Naive-Sentiment-Proxy
POSITIVE_WORDS = {
    "profit", "growth", "beat", "strong", "bullish", "rally", "surge", "gain", "upgrade",
    "outperform", "record", "breakthrough", "partnership", "expansion", "dividend",
    "gewinn", "wachstum", "übertreffen", "stark", "bullisch", "aufschwung", "kaufen",
}
NEGATIVE_WORDS = {
    "loss", "miss", "weak", "bearish", "crash", "drop", "fall", "downgrade",
    "underperform", "layoff", "lawsuit", "debt", "recession", "bankrupt",
    "verlust", "schwach", "bärisch", "absturz", "rückgang", "verkaufen", "krise",
}


def _sentiment_score(headlines: List[str]) -> Tuple[float, List[str]]:
    """Gibt einen Score zwischen -1 (negativ) und +1 (positiv) zurück."""
    if not headlines:
        return 0.0, []
    pos, neg = 0, 0
    for h in headlines:
        words = set(re.findall(r"[a-zA-ZäöüÄÖÜß]+", h.lower()))
        pos += len(words & POSITIVE_WORDS)
        neg += len(words & NEGATIVE_WORDS)
    total = pos + neg
    if total == 0:
        return 0.0, headlines
    return (pos - neg) / total, headlines


def _fetch_yahoo_news(symbol: str) -> List[str]:
    """RSS-Feed über Yahoo Finance News."""
    try:
        url = f"https://finance.yahoo.com/rss/headline?s={symbol}"
        feed = feedparser.parse(url)
        return [unescape(entry.get("title", "")) for entry in feed.entries[:10]]
    except Exception:
        return []


def _fetch_newsapi(q: str) -> List[str]:
    if not config.NEWSAPI_KEY:
        return []
    try:
        url = "https://newsapi.org/v2/everything"
        params = {
            "q": q,
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": 10,
            "apiKey": config.NEWSAPI_KEY,
        }
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        return [a.get("title", "") for a in data.get("articles", [])]
    except Exception:
        return []


def get_news_sentiment(symbol: str, company_name: str) -> dict:
    cache = _load_cache()
    cache_key = f"{symbol}_{datetime.utcnow().strftime('%Y-%m-%d-%H')}"
    if cache_key in cache:
        return cache[cache_key]

    headlines = _fetch_yahoo_news(symbol)
    if config.NEWSAPI_KEY:
        headlines += _fetch_newsapi(company_name)

    score, used = _sentiment_score(headlines)

    result = {
        "score": round(score, 2),
        "headlines": used[:5],
        "count": len(used),
    }
    cache[cache_key] = result
    # nur letzte 50 Einträge halten
    if len(cache) > 50:
        cache = dict(list(cache.items())[-50:])
    _save_cache(cache)
    return result
