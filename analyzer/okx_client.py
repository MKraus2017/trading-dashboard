"""OKX Public API Client — Live-Krypto-Preise ohne API-Key (nur öffentliche Marktdaten)."""
import time
import urllib.request
import json as _json
from typing import Optional, List, Dict

OKX_BASE = "https://www.okx.com"

_price_cache: Dict[str, dict] = {}
_CACHE_TTL = 30  # Sekunden

_FETCH_STATS = {"calls": 0, "errors": 0, "cache_hits": 0}


def get_fetch_stats() -> dict:
    return dict(_FETCH_STATS)


def _http_get(path: str, timeout: int = 10) -> Optional[dict]:
    url = OKX_BASE + path
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        _FETCH_STATS["calls"] += 1
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = _json.loads(resp.read().decode())
            if data.get("code") != "0":
                _FETCH_STATS["errors"] += 1
                return None
            return data
    except Exception as e:
        _FETCH_STATS["errors"] += 1
        print(f"[OKX] Error fetching {path}: {e}")
        return None


def to_okx_symbol(symbol: str) -> str:
    """Normalisiert z.B. 'BTC' -> 'BTC-USDT' (Spot-Ticker)."""
    symbol = symbol.upper()
    if "-" in symbol:
        return symbol
    return f"{symbol}-USDT"


def fetch_ticker(symbol: str) -> Optional[dict]:
    """Live-Ticker: last price, 24h high/low/vol, change."""
    inst_id = to_okx_symbol(symbol)
    now = time.time()
    cached = _price_cache.get(inst_id)
    if cached and (now - cached["ts"]) < _CACHE_TTL:
        _FETCH_STATS["cache_hits"] += 1
        return cached["value"]

    data = _http_get(f"/api/v5/market/ticker?instId={inst_id}")
    if not data or not data.get("data"):
        return None
    t = data["data"][0]
    try:
        result = {
            "symbol": symbol.upper(),
            "inst_id": inst_id,
            "last": float(t["last"]),
            "high24h": float(t["high24h"]),
            "low24h": float(t["low24h"]),
            "vol24h": float(t.get("volCcy24h", 0) or 0),
            "open24h": float(t["open24h"]),
            "change_pct": round((float(t["last"]) - float(t["open24h"])) / float(t["open24h"]) * 100, 2) if float(t["open24h"]) else 0.0,
            "ts": now,
        }
    except (KeyError, ValueError, ZeroDivisionError):
        return None
    _price_cache[inst_id] = {"ts": now, "value": result}
    return result


def fetch_candles(symbol: str, bar: str = "1H", limit: int = 200) -> Optional[dict]:
    """Historische Candles. bar: 1m,5m,15m,1H,4H,1D,1W. Liefert closes/highs/lows/volumes (älteste zuerst)."""
    inst_id = to_okx_symbol(symbol)
    data = _http_get(f"/api/v5/market/candles?instId={inst_id}&bar={bar}&limit={limit}")
    if not data or not data.get("data"):
        return None
    rows = list(reversed(data["data"]))  # OKX liefert neueste zuerst -> umdrehen
    try:
        closes = [float(r[4]) for r in rows]
        highs = [float(r[2]) for r in rows]
        lows = [float(r[3]) for r in rows]
        opens = [float(r[1]) for r in rows]
        volumes = [float(r[5]) for r in rows]
        timestamps = [int(r[0]) for r in rows]
    except (IndexError, ValueError):
        return None
    return {
        "symbol": symbol.upper(),
        "closes": closes,
        "highs": highs,
        "lows": lows,
        "opens": opens,
        "volumes": volumes,
        "timestamps": timestamps,
    }


def fetch_history_days(symbol: str, days: int = 365, bar: str = "1D") -> Optional[dict]:
    """Holt möglichst lange Tagesdaten für Backtesting (OKX begrenzt auf ~300 Kerzen/Request,
    daher ggf. mehrere Requests mit 'after'-Pagination nötig für > 300 Tage)."""
    inst_id = to_okx_symbol(symbol)
    all_rows: List[list] = []
    after = None
    remaining = days
    while remaining > 0:
        limit = min(300, remaining)
        path = f"/api/v5/market/history-candles?instId={inst_id}&bar={bar}&limit={limit}"
        if after:
            path += f"&after={after}"
        data = _http_get(path)
        if not data or not data.get("data"):
            break
        rows = data["data"]
        if not rows:
            break
        all_rows.extend(rows)
        after = rows[-1][0]  # ältester Timestamp dieser Seite -> weiter zurück
        remaining -= len(rows)
        if len(rows) < limit:
            break
        time.sleep(0.15)  # Rate-Limit schonen

    if not all_rows:
        return None
    rows = list(reversed(all_rows))  # älteste zuerst
    try:
        closes = [float(r[4]) for r in rows]
        highs = [float(r[2]) for r in rows]
        lows = [float(r[3]) for r in rows]
        opens = [float(r[1]) for r in rows]
        volumes = [float(r[5]) for r in rows]
        timestamps = [int(r[0]) for r in rows]
    except (IndexError, ValueError):
        return None
    return {
        "symbol": symbol.upper(),
        "closes": closes,
        "highs": highs,
        "lows": lows,
        "opens": opens,
        "volumes": volumes,
        "timestamps": timestamps,
    }


def fetch_latest_price(symbol: str) -> Optional[float]:
    t = fetch_ticker(symbol)
    return t["last"] if t else None
