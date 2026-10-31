"""OKX Public API Client — Live-Krypto-Preise ohne API-Key (nur öffentliche Marktdaten)."""
import time
import math
import urllib.request
import json as _json
from typing import Optional, List, Dict

OKX_BASE = "https://www.okx.com"

_price_cache: Dict[str, dict] = {}
_trade_pressure_cache: Dict[str, dict] = {}
_strict_trade_pressure_cache: Dict[str, dict] = {}
_CACHE_TTL = 30  # Sekunden
TRADE_PRESSURE_VERSION = "fresh_trade_pressure_v1"
TRADE_PRESSURE_WINDOW_SECONDS = 300
TRADE_PRESSURE_MAX_AGE_SECONDS = 60
TRADE_PRESSURE_MAX_FUTURE_SECONDS = 5

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
    # Public exchange time is separate from local cache time. Existing consumers
    # retain their behavior; the paper execution policy explicitly validates it.
    try:
        result["market_timestamp"] = float(t["ts"]) / 1000
    except (KeyError, TypeError, ValueError, OverflowError):
        result["market_timestamp"] = None
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
        quote_volumes = [float(r[7]) if len(r) > 7 and r[7] not in (None, "") else float(r[5]) * float(r[4]) for r in rows]
        timestamps = [int(r[0]) for r in rows]
        confirmed = [len(r) > 8 and str(r[8]) == "1" for r in rows]
    except (IndexError, ValueError):
        return None
    return {
        "symbol": symbol.upper(),
        "closes": closes,
        "highs": highs,
        "lows": lows,
        "opens": opens,
        "volumes": volumes,
        "quote_volumes": quote_volumes,
        "timestamps": timestamps,
        "confirmed": confirmed,
    }


def _pressure_number(value, name):
    if isinstance(value, bool):
        raise ValueError(name)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(name) from None
    if not math.isfinite(number) or number <= 0:
        raise ValueError(name)
    return number


def _pressure_unavailable(reason, now, fetched_at=None):
    return {"version": TRADE_PRESSURE_VERSION, "ok": False, "reason": reason,
            "buy_ratio": None, "buy_quote": None, "sell_quote": None,
            "trade_count": 0, "market_timestamp": None, "oldest_trade_at": None,
            "fetched_at": fetched_at, "checked_at": now, "trades": []}


def revalidate_trade_pressure(sample: dict, symbol: str, now: float = None) -> dict:
    """Pure owner-paper check; recalculate the rolling sample at decision time.

    Canonical trades retain exchange timestamps, so cache hits and delayed entry
    plans can expire individual rows. A single valid trade is allowed but its
    count/duration remain visible; this is never a whole-market pressure claim.
    """
    now = time.time() if now is None else now
    fetched_at = None
    try:
        now = _pressure_number(now, "pressure_check_time_invalid")
        if not isinstance(sample, dict) or sample.get("version") != TRADE_PRESSURE_VERSION:
            raise ValueError("pressure_version_missing_or_invalid")
        fetched_at = _pressure_number(sample.get("fetched_at"), "pressure_fetch_time_invalid")
        if fetched_at > now + TRADE_PRESSURE_MAX_FUTURE_SECONDS:
            raise ValueError("pressure_fetch_time_future")
        if sample.get("ok") is False:
            raise ValueError(str(sample.get("reason") or "pressure_unavailable"))
        rows = sample.get("trades")
        if not isinstance(rows, list):
            raise ValueError("pressure_trades_invalid")
        expected = to_okx_symbol(symbol)
        unique = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("pressure_trade_invalid")
            trade_id = row.get("trade_id")
            if not isinstance(trade_id, str) or not trade_id.strip():
                raise ValueError("pressure_trade_id_invalid")
            if row.get("inst_id") != expected:
                raise ValueError("pressure_instrument_mismatch")
            if row.get("side") not in ("buy", "sell"):
                raise ValueError("pressure_side_invalid")
            price = _pressure_number(row.get("price"), "pressure_price_invalid")
            size = _pressure_number(row.get("size"), "pressure_size_invalid")
            _pressure_number(price * size, "pressure_notional_invalid")
            stamp = _pressure_number(row.get("timestamp"), "pressure_timestamp_invalid")
            if stamp > now + TRADE_PRESSURE_MAX_FUTURE_SECONDS:
                raise ValueError("pressure_trade_future")
            trade = {"trade_id": trade_id, "inst_id": expected, "side": row["side"],
                     "price": price, "size": size, "timestamp": stamp}
            if trade_id in unique and unique[trade_id] != trade:
                raise ValueError("pressure_conflicting_duplicate")
            unique[trade_id] = trade
        # Validate every returned row before discarding old observations.
        trades = sorted((row for row in unique.values()
                         if row["timestamp"] >= now - TRADE_PRESSURE_WINDOW_SECONDS),
                        key=lambda row: (row["timestamp"], row["trade_id"]))
        if not trades:
            raise ValueError("pressure_no_recent_trades")
        latest = trades[-1]["timestamp"]
        if latest < now - TRADE_PRESSURE_MAX_AGE_SECONDS:
            raise ValueError("pressure_stale")
        buy = math.fsum(row["price"] * row["size"] for row in trades if row["side"] == "buy")
        sell = math.fsum(row["price"] * row["size"] for row in trades if row["side"] == "sell")
        total = _pressure_number(buy + sell, "pressure_total_invalid")
        return {"version": TRADE_PRESSURE_VERSION, "ok": True, "reason": None,
                "buy_ratio": buy / total, "buy_quote": buy, "sell_quote": sell,
                "trade_count": len(trades), "market_timestamp": latest,
                "oldest_trade_at": trades[0]["timestamp"], "fetched_at": fetched_at,
                "checked_at": now, "trades": trades}
    except (ValueError, TypeError, OverflowError) as exc:
        return _pressure_unavailable(str(exc), now, fetched_at)


def _fetch_strict_trade_pressure(symbol: str, limit: int) -> dict:
    inst_id = to_okx_symbol(symbol)
    cache_key = f"{inst_id}:{limit}"
    now = time.time()
    cached = _strict_trade_pressure_cache.get(cache_key)
    if cached and 0 <= now - cached["fetched_at"] < _CACHE_TTL:
        _FETCH_STATS["cache_hits"] += 1
    else:
        data = _http_get(f"/api/v5/market/trades?instId={inst_id}&limit={limit}")
        fetched_at = time.time()
        cached = {"fetched_at": fetched_at, "rows": data.get("data") if isinstance(data, dict) else None}
        _strict_trade_pressure_cache[cache_key] = cached
    now = time.time()
    rows = cached["rows"]
    if not isinstance(rows, list):
        return _pressure_unavailable("pressure_response_invalid", now, cached["fetched_at"])
    canonical = []
    try:
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("pressure_trade_invalid")
            canonical.append({"trade_id": row.get("tradeId"), "inst_id": row.get("instId"),
                              "side": row.get("side"), "price": row.get("px"), "size": row.get("sz"),
                              "timestamp": _pressure_number(row.get("ts"), "pressure_timestamp_invalid") / 1000})
    except ValueError as exc:
        return _pressure_unavailable(str(exc), now, cached["fetched_at"])
    return revalidate_trade_pressure({"version": TRADE_PRESSURE_VERSION,
                                     "fetched_at": cached["fetched_at"], "trades": canonical}, symbol, now)


def fetch_recent_trade_pressure(symbol: str, limit: int = 300, *, strict: bool = False) -> Optional[dict]:
    """Kaufdruck aus den juengsten oeffentlichen OKX-Trades (buy/sell-Seite, quote-gewichtet).

    Das ist eine kurzfristige Stichprobe und kein vollstaendiges Orderbuch. Sie wird deshalb
    nur als Bestaetigung verwendet, nicht als alleiniger Kaufgrund.
    """
    limit = max(1, min(int(limit), 500))
    if strict:
        return _fetch_strict_trade_pressure(symbol, limit)
    inst_id = to_okx_symbol(symbol)
    cache_key = f"{inst_id}:{limit}"
    now = time.time()
    cached = _trade_pressure_cache.get(cache_key)
    if cached and (now - cached["ts"]) < _CACHE_TTL:
        _FETCH_STATS["cache_hits"] += 1
        return cached["value"]

    data = _http_get(f"/api/v5/market/trades?instId={inst_id}&limit={limit}")
    if not data or not data.get("data"):
        return None

    buy_quote = 0.0
    sell_quote = 0.0
    parsed = 0
    for trade in data["data"]:
        try:
            notional = float(trade["px"]) * float(trade["sz"])
        except (KeyError, TypeError, ValueError):
            continue
        if trade.get("side") == "buy":
            buy_quote += notional
        elif trade.get("side") == "sell":
            sell_quote += notional
        else:
            continue
        parsed += 1

    total_quote = buy_quote + sell_quote
    if parsed == 0 or total_quote <= 0:
        return None
    result = {
        "buy_ratio": buy_quote / total_quote,
        "buy_quote": buy_quote,
        "sell_quote": sell_quote,
        "trade_count": parsed,
    }
    _trade_pressure_cache[cache_key] = {"ts": now, "value": result}
    return result


def fetch_history_days(symbol: str, days: int = 365, bar: str = "1D") -> Optional[dict]:
    """Holt moeglichst lange Historie fuer Backtesting (OKX begrenzt auf ~300 Kerzen/Request,
    daher ggf. mehrere Requests mit 'after'-Pagination).

    WICHTIG: 'days' meint tatsaechliche KALENDERTAGE, nicht Kerzen. Die benoetigte
    Kerzenanzahl wird aus der Bar-Groesse abgeleitet (z.B. 365 Tage bei bar='4H' =
    365*6 = 2190 Kerzen). Vorher wurde 'days' faelschlich als Kerzenanzahl behandelt,
    wodurch ein '365-Tage-Backtest' mit 4H-Kerzen real nur ~61 Tage abdeckte."""
    inst_id = to_okx_symbol(symbol)

    # Kerzen pro Tag je Bar-Groesse
    bars_per_day = {
        "1m": 1440, "3m": 480, "5m": 288, "15m": 96, "30m": 48,
        "1H": 24, "2H": 12, "4H": 6, "6H": 4, "12H": 2,
        "1D": 1, "1W": 1 / 7, "1M": 1 / 30,
    }
    per_day = bars_per_day.get(bar, 1)
    needed_candles = max(1, int(days * per_day))

    all_rows: List[list] = []
    after = None
    remaining = needed_candles
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
