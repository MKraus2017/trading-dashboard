"""Bounded, read-only Yahoo reference quotes for the virtual stock monitor.

These are delayed reference prices, not executable Trade Republic quotes.
Only the source venue's reported regular session permits simulated fills.
No portfolio, account, cache file, or notification is written by this module.
"""
import copy
import math
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Optional

import requests

QUOTE_MAX_AGE = 20 * 60
FX_MAX_AGE = 24 * 60 * 60
FUTURE_TOLERANCE = 60
CACHE_SECONDS = 60
CACHE_LIMIT = 256
HISTORY_CACHE_SECONDS = 15 * 60
HTTP_TIMEOUT = 6
FX_PAIRS = {"USD": "EURUSD=X", "CHF": "EURCHF=X", "GBP": "EURGBP=X"}

_cache = OrderedDict()
_history_cache = OrderedDict()
_cache_lock = threading.RLock()


def clear_cache():
    """Clear only this process's public quote cache (also useful for tests)."""
    with _cache_lock:
        _cache.clear()
        _history_cache.clear()


def _positive(value, field):
    if isinstance(value, bool):
        raise ValueError(f"invalid_{field}")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"invalid_{field}") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"invalid_{field}")
    return result


def _check_age(timestamp, now, max_age, label):
    if timestamp > now + FUTURE_TOLERANCE:
        raise ValueError(f"future_{label}")
    if now - timestamp > max_age:
        raise ValueError(f"stale_{label}")


def is_tradable(tick, now: Optional[float] = None) -> bool:
    """Revalidate a fetched quote immediately before a simulated mutation.

    Pure predicate: no network, cache access, mutation, or exceptions for bad
    quote data. In particular, an ATR request may have aged a previously usable
    quote or crossed the source venue's regular closing time.
    """
    if not isinstance(tick, dict) or tick.get("tradable") is not True:
        return False
    try:
        observed = _positive(time.time() if now is None else now, "now")
        if tick.get("currency") != "EUR":
            return False
        _positive(tick.get("price"), "eur_price")
        start = _positive(tick.get("regular_start"), "regular_start")
        end = _positive(tick.get("regular_end"), "regular_end")
        if end <= start or end - start > 24 * 60 * 60 or not start <= observed < end:
            return False
        market_at = _positive(tick.get("market_at"), "market_time")
        if not start <= market_at < end:
            return False
        _check_age(market_at, observed, QUOTE_MAX_AGE, "quote")
        source_currency = tick.get("source_currency")
        if source_currency != "EUR":
            if not isinstance(source_currency, str) or source_currency not in FX_PAIRS:
                return False
            _positive(tick.get("fx_rate"), "fx_rate")
            fx_at = _positive(tick.get("fx_market_at"), "fx_time")
            _check_age(fx_at, observed, FX_MAX_AGE, "fx")
        return True
    except (ValueError, TypeError, OverflowError):
        return False


def _metadata(symbol, clock):
    # One shared lock also deduplicates concurrent requests across users. The
    # HTTP request is bounded and makes no retries or stale-cache fallback.
    with _cache_lock:
        now = clock()
        cached = _cache.get(symbol)
        if cached and 0 <= now - cached["received_at"] < CACHE_SECONDS:
            _cache.move_to_end(symbol)
            return copy.deepcopy(cached)
        _cache.pop(symbol, None)
        try:
            response = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                params={"interval": "1m", "range": "1d"},
                headers={"User-Agent": "Mozilla/5.0 DepotLab-Paper-Monitor"},
                timeout=HTTP_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
            chart = payload.get("chart", {})
            results = chart.get("result")
            if chart.get("error") or not isinstance(results, list) or len(results) != 1:
                raise ValueError("missing_chart_result")
            meta = results[0].get("meta")
            if not isinstance(meta, dict):
                raise ValueError("missing_quote_metadata")
            # A symbol mismatch must never price another user's instrument.
            if str(meta.get("symbol", "")).upper() != symbol:
                raise ValueError("quote_symbol_mismatch")
        except Exception as exc:
            if isinstance(exc, ValueError):
                raise
            raise ValueError("quote_fetch_failed") from exc
        result = {"meta": copy.deepcopy(meta), "received_at": clock()}
        _cache[symbol] = result
        while len(_cache) > CACHE_LIMIT:
            _cache.popitem(last=False)
        return copy.deepcopy(result)


def _daily_history(symbol, clock):
    """Fetch/cache raw daily bars only; FX is never cached in this result."""
    with _cache_lock:
        now = clock()
        cached = _history_cache.get(symbol)
        if cached and 0 <= now - cached["received_at"] < HISTORY_CACHE_SECONDS:
            _history_cache.move_to_end(symbol)
            return copy.deepcopy(cached)
        _history_cache.pop(symbol, None)
        try:
            response = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                params={"interval": "1d", "range": "2mo"},
                headers={"User-Agent": "Mozilla/5.0 DepotLab-Paper-Monitor"},
                timeout=HTTP_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
            chart = payload.get("chart", {})
            results = chart.get("result")
            if chart.get("error") or not isinstance(results, list) or len(results) != 1:
                raise ValueError("missing_history_result")
            data = results[0]
            if not isinstance(data, dict):
                raise ValueError("invalid_history_result")
        except Exception as exc:
            if isinstance(exc, ValueError):
                raise
            raise ValueError("history_fetch_failed") from exc
        result = {"data": copy.deepcopy(data), "received_at": clock()}
        _history_cache[symbol] = result
        while len(_history_cache) > CACHE_LIMIT:
            _history_cache.popitem(last=False)
        return copy.deepcopy(result)


def fetch_completed_history(symbol: str, tick: dict) -> dict:
    """Return completed EUR daily OHLC bars suitable for a current ATR.

    Only bars strictly before the current source regular-session UTC date are
    included. The newest completed bar must be at most seven calendar days old.
    Bars must be ordered and valid; no missing values, stale FX fallback or
    current-day candle is silently used. Minimum ATR sample count is the caller's
    responsibility. This is current-unit ATR input, not historical EUR backtest
    data: every raw OHLC price uses exactly the fresh FX rate from ``tick``.
    """
    if not isinstance(symbol, str):
        raise ValueError("invalid_symbol")
    symbol = symbol.strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,23}", symbol):
        raise ValueError("invalid_symbol")
    if not is_tradable(tick):
        raise ValueError("history_quote_not_tradable")
    raw = _daily_history(symbol, time.time)
    if not is_tradable(tick):
        raise ValueError("history_quote_no_longer_tradable")
    observed = time.time()
    data = raw["data"]
    meta = data.get("meta")
    if not isinstance(meta, dict) or str(meta.get("symbol", "")).upper() != symbol:
        raise ValueError("history_symbol_mismatch")
    if meta.get("instrumentType") not in ("EQUITY", "ETF"):
        raise ValueError("history_unsupported_instrument")
    currency = tick.get("source_currency")
    if meta.get("currency") != currency:
        raise ValueError("history_currency_mismatch")
    fx_rate = 1.0 if currency == "EUR" else _positive(tick.get("fx_rate"), "fx_rate")
    session_date = datetime.fromtimestamp(tick["regular_start"], timezone.utc).date()
    timestamps = data.get("timestamp")
    indicators = data.get("indicators")
    quote_rows = indicators.get("quote") if isinstance(indicators, dict) else None
    if (not isinstance(timestamps, list) or not timestamps
            or not isinstance(quote_rows, list) or len(quote_rows) != 1
            or not isinstance(quote_rows[0], dict)):
        raise ValueError("missing_history_ohlc")
    ohlc = quote_rows[0]
    for key in ("open", "high", "low", "close"):
        if not isinstance(ohlc.get(key), list) or len(ohlc[key]) != len(timestamps):
            raise ValueError("misaligned_history_ohlc")

    result = {"symbol": symbol, "currency": "EUR", "source_currency": currency,
              "timestamps": [], "opens": [], "highs": [], "lows": [], "closes": [],
              "received_at": raw["received_at"], "fx_rate": fx_rate}
    if currency != "EUR":
        result["fx_market_at"] = tick["fx_market_at"]
    previous = 0
    last_date = None
    for index, stamp in enumerate(timestamps):
        stamp = _positive(stamp, "history_timestamp")
        if stamp <= previous or stamp > observed + FUTURE_TOLERANCE:
            raise ValueError("invalid_history_chronology")
        previous = stamp
        try:
            date = datetime.fromtimestamp(stamp, timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise ValueError("invalid_history_timestamp") from exc
        if date.date() >= session_date:
            continue
        values = {key: _positive(ohlc[key][index], "history_" + key)
                  for key in ("open", "high", "low", "close")}
        if (values["high"] < max(values["open"], values["close"], values["low"])
                or values["low"] > min(values["open"], values["close"], values["high"])):
            raise ValueError("invalid_history_ohlc_bounds")
        for key, output in (("open", "opens"), ("high", "highs"),
                            ("low", "lows"), ("close", "closes")):
            result[output].append(_positive(values[key] / fx_rate, "history_eur_price"))
        result["timestamps"].append(date.isoformat())
        result["last_completed_at"] = stamp
        last_date = date.date()
    if last_date is None:
        raise ValueError("missing_completed_history")
    if (session_date - last_date).days > 7:
        raise ValueError("stale_completed_history")
    # Also guard a session/age boundary crossed during validation itself.
    if not is_tradable(tick):
        raise ValueError("history_quote_no_longer_tradable")
    return result


def fetch_quote(symbol: str, now: Optional[float] = None) -> dict:
    """Return a validated EUR reference quote; raise ValueError if unusable.

    Keys: price (EUR, nullable only when market_closed), market_at,
    received_at (both UTC epoch seconds), currency='EUR', source_currency,
    market_open, tradable, reason, quote_age_seconds, regular_start/end.
    Foreign-currency results also include fx_rate and fx_market_at.

    Closed venues return tradable=False. When a closed venue's price or FX is
    stale, price=None and data_reason explains why; callers retain their last
    mark instead of presenting old FX as current. Freshness and the session
    window are rechecked on every call, including process-cache hits.
    """
    if not isinstance(symbol, str):
        raise ValueError("invalid_symbol")
    symbol = symbol.strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,23}", symbol):
        raise ValueError("invalid_symbol")
    if now is not None:
        fixed_now = _positive(now, "now")
        clock = lambda: fixed_now
    else:
        clock = time.time

    raw = _metadata(symbol, clock)
    meta = raw["meta"]
    if meta.get("instrumentType") not in ("EQUITY", "ETF"):
        raise ValueError("unsupported_instrument")
    market_at = _positive(meta.get("regularMarketTime"), "market_time")
    periods = meta.get("currentTradingPeriod")
    if not isinstance(periods, dict) or not isinstance(periods.get("regular"), dict):
        raise ValueError("invalid_regular_start")
    period = periods["regular"]
    start = _positive(period.get("start"), "regular_start")
    end = _positive(period.get("end"), "regular_end")
    if end <= start or end - start > 24 * 60 * 60:
        raise ValueError("invalid_regular_session")
    observed = clock()
    if market_at > observed + FUTURE_TOLERANCE:
        raise ValueError("future_quote")
    market_open = start <= observed < end
    currency = meta.get("currency")
    result = {
        "price": None, "market_at": market_at, "received_at": raw["received_at"],
        "currency": "EUR", "source_currency": currency,
        "market_open": market_open, "tradable": False,
        "reason": "market_closed" if not market_open else "quote_unusable",
        "quote_age_seconds": max(0, observed - market_at),
        "regular_start": start, "regular_end": end,
    }
    try:
        _check_age(market_at, observed, QUOTE_MAX_AGE, "quote")
        price = _positive(meta.get("regularMarketPrice"), "price")
        if currency != "EUR":
            # Do not upper-case currency: Yahoo's GBp means pence, NOT GBP.
            pair = FX_PAIRS.get(currency) if isinstance(currency, str) else None
            if not pair:
                raise ValueError("unsupported_currency")
            fx = _metadata(pair, clock)
            fx_meta = fx["meta"]
            if fx_meta.get("currency") != currency:
                raise ValueError("fx_currency_mismatch")
            rate = _positive(fx_meta.get("regularMarketPrice"), "fx_rate")
            fx_at = _positive(fx_meta.get("regularMarketTime"), "fx_time")
            observed = clock()
            _check_age(fx_at, observed, FX_MAX_AGE, "fx")
            # Recheck the stock quote after the additional FX request.
            _check_age(market_at, observed, QUOTE_MAX_AGE, "quote")
            price /= rate  # EURXXX=X is XXX per EUR, never multiply.
            result.update(fx_rate=rate, fx_market_at=fx_at,
                          fx_received_at=fx["received_at"])
        result["price"] = _positive(price, "eur_price")
    except ValueError as exc:
        # Closed markets are normal (especially weekends), not a broken feed.
        # Retain no synthetic price when the underlying price/FX is unusable.
        if not market_open:
            result["data_reason"] = str(exc)
            return result
        raise

    # A slow fetch can cross the session boundary, so use completion time.
    observed = clock()
    result["market_open"] = start <= observed < end
    result["quote_age_seconds"] = max(0, observed - market_at)
    if not result["market_open"]:
        result["reason"] = "market_closed"
    elif not start <= market_at < end + FUTURE_TOLERANCE:
        result["reason"] = "previous_regular_session"
    else:
        result["tradable"] = True
        result["reason"] = "fresh_delayed_reference_quote"
    return result
