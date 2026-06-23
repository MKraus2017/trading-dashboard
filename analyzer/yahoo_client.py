"""Yahoo Finance OHLC-Daten mit lokalem Caching."""
import json
import os
import time
from datetime import datetime, timedelta
from typing import Optional

import requests

import config


CACHE_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "prices_cache.json")
CACHE_TTL_SECONDS = 15 * 60  # 15 Minuten


def _load_cache():
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, default=str)


def _is_cache_valid(entry):
    ts = entry.get("timestamp")
    if not ts:
        return False
    try:
        cached = datetime.fromisoformat(ts)
        return (datetime.utcnow() - cached).total_seconds() < CACHE_TTL_SECONDS
    except Exception:
        return False


def fetch_yahoo(ticker: str, interval: str = "1d", range_: str = "6mo", retries: int = 2) -> Optional[dict]:
    """
    Holt OHLCV-Daten für einen Ticker.
    Rückgabe: {
        "symbol": str,
        "currency": str,
        "closes": list[float],
        "opens": list[float],
        "highs": list[float],
        "lows": list[float],
        "volumes": list[int],
        "timestamps": list[str],
        "latest": float,
        "previous": float,
        "change_pct": float,
    }
    """
    cache = _load_cache()
    cache_key = f"{ticker}_{interval}_{range_}"
    cached = cache.get(cache_key)
    if cached and _is_cache_valid(cached):
        return cached.get("data")

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {"interval": interval, "range": range_}
    headers = {"User-Agent": config.YAHOO_USER_AGENT}

    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=12)
            resp.raise_for_status()
            payload = resp.json()
            result = payload.get("chart", {}).get("result", [None])[0]
            if not result:
                return None

            meta = result.get("meta", {})
            quote = result.get("indicators", {}).get("quote", [{}])[0]
            timestamps = result.get("timestamp", [])

            closes = quote.get("close", [])
            opens = quote.get("open", [])
            highs = quote.get("high", [])
            lows = quote.get("low", [])
            volumes = quote.get("volume", [])

            # Drop Nones at the beginning/trailing (Yahoo gelegentlich None am Ende)
            valid_idx = [i for i, c in enumerate(closes) if c is not None]
            if not valid_idx:
                return None

            closes = [closes[i] for i in valid_idx]
            opens = [opens[i] if opens[i] is not None else closes[i] for i in valid_idx]
            highs = [highs[i] if highs[i] is not None else closes[i] for i in valid_idx]
            lows = [lows[i] if lows[i] is not None else closes[i] for i in valid_idx]
            volumes = [volumes[i] if volumes[i] is not None else 0 for i in valid_idx]
            timestamps = [datetime.utcfromtimestamp(timestamps[i]).isoformat() for i in valid_idx]

            latest = closes[-1]
            previous = closes[-2] if len(closes) >= 2 else latest
            change_pct = ((latest - previous) / previous * 100) if previous else 0.0

            data = {
                "symbol": ticker,
                "currency": meta.get("currency", "USD"),
                "closes": closes,
                "opens": opens,
                "highs": highs,
                "lows": lows,
                "volumes": volumes,
                "timestamps": timestamps,
                "latest": latest,
                "previous": previous,
                "change_pct": change_pct,
            }

            cache[cache_key] = {"timestamp": datetime.utcnow().isoformat(), "data": data}
            _save_cache(cache)
            return data
        except Exception as e:
            if attempt == retries:
                print(f"[Yahoo] Fehler bei {ticker}: {e}")
                return None
            time.sleep(1)
    return None


def fetch_latest_price(ticker: str) -> Optional[float]:
    data = fetch_yahoo(ticker, interval="1d", range_="5d")
    return data["latest"] if data else None
