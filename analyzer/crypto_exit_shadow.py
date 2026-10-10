"""Read-only observer of the virtual ledger. Never trades or sends notifications.

The only writes are to crypto_exit_shadow_* tables. A separate thread/leader lock
keeps slow public market requests out of the existing 30-second stop watcher.
"""
import json
import math
import os
import re
import sqlite3
import threading
import time
import urllib.request
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

VERSION = "volume_exit_15m_v1"
BAR_SECONDS = 900
INTERVAL = 30
MAX_GAP = 90
FEE_BPS = 10.0
SLIPPAGE_BPS = 5.0
_thread = None
_start_lock = threading.Lock()
_stop = threading.Event()


def _connect(path, readonly=False):
    uri = Path(path).resolve().as_uri() + "?mode=ro" if readonly else str(path)
    conn = sqlite3.connect(uri, uri=readonly, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(path):
    with closing(_connect(path)) as conn, conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS crypto_exit_shadow_runs (
                user_id INTEGER NOT NULL, version TEXT NOT NULL, started_at REAL NOT NULL,
                last_attempt_at REAL, last_success_at REAL, last_error TEXT,
                PRIMARY KEY(user_id, version));
            CREATE TABLE IF NOT EXISTS crypto_exit_shadow_positions (
                user_id INTEGER NOT NULL, position_id TEXT NOT NULL, version TEXT NOT NULL,
                data TEXT NOT NULL, PRIMARY KEY(user_id, position_id, version));
            CREATE TABLE IF NOT EXISTS crypto_exit_shadow_bars (
                user_id INTEGER NOT NULL, position_id TEXT NOT NULL, version TEXT NOT NULL,
                bar_ts REAL NOT NULL, data TEXT NOT NULL,
                PRIMARY KEY(user_id, position_id, version, bar_ts));
        """)


def _number(value, positive=False):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("invalid_number") from None
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError("invalid_number")
    return number


def _stamp(value):
    if isinstance(value, (int, float)):
        return _number(value, positive=True)
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=timezone.utc).timestamp() if parsed.tzinfo is None else parsed.timestamp()


def _public_get(symbol, endpoint):
    if not re.fullmatch(r"[A-Z0-9]{2,15}", symbol):
        raise ValueError("unsupported_symbol")
    url = "https://www.okx.com/api/v5/market/" + endpoint.format(symbol=symbol)
    req = urllib.request.Request(url, headers={"User-Agent": "TradingDashboard-Shadow/1"})
    with urllib.request.urlopen(req, timeout=6) as response:
        payload = json.loads(response.read(250000))
    if payload.get("code") != "0" or not payload.get("data"):
        raise ValueError("market_data_unavailable")
    return payload["data"]


def fetch_bars(symbol):
    return _public_get(symbol, "candles?instId={symbol}-USDT&bar=15m&limit=100")


def fetch_quote(symbol):
    row = _public_get(symbol, "ticker?instId={symbol}-USDT")[0]
    return {"price": _number(row["last"], True), "market_at": _number(row["ts"], True) / 1000}


def volume_signal(rows, direction, now):
    """Closed, contiguous 15m bars only; current bar is never used.

    LONG: a bearish bar crosses from at/above EMA20 to below it. SHORT mirrors
    this. RVOL >=1.5 uses the previous 20 quote-volume bars, excluding this bar.
    """
    if direction not in ("LONG", "SHORT"):
        raise ValueError("unsupported_direction")
    bars = []
    for raw in rows:
        if len(raw) < 9 or str(raw[8]) != "1":
            continue
        start = _number(raw[0], True) / 1000
        if start + BAR_SECONDS > now:
            continue
        bars.append({"start": start, "open": _number(raw[1], True),
                     "close": _number(raw[4], True), "volume": _number(raw[7])})
    bars.sort(key=lambda b: b["start"])
    if len(bars) < 50:
        raise ValueError("insufficient_closed_bars")
    for a, b in zip(bars, bars[1:]):
        if b["start"] - a["start"] != BAR_SECONDS:
            raise ValueError("non_contiguous_bars")
    if any(b["volume"] < 0 for b in bars):
        raise ValueError("invalid_volume")
    latest = bars[-1]
    end = latest["start"] + BAR_SECONDS
    if not 0 <= now - end <= MAX_GAP:
        raise ValueError("stale_closed_bar")
    ema = sum(b["close"] for b in bars[:20]) / 20
    previous_ema = ema
    for b in bars[20:]:
        previous_ema = ema
        ema += (b["close"] - ema) * (2 / 21)
    average = sum(b["volume"] for b in bars[-21:-1]) / 20
    if average <= 0:
        raise ValueError("zero_volume_baseline")
    ratio = latest["volume"] / average
    before = bars[-2]["close"]
    if direction == "LONG":
        crossed = before >= previous_ema and latest["close"] < ema and latest["close"] < latest["open"]
    else:
        crossed = before <= previous_ema and latest["close"] > ema and latest["close"] > latest["open"]
    return {"bar_ts": latest["start"], "bar_end": end, "close": latest["close"],
            "ema20": ema, "previous_ema20": previous_ema, "quote_volume": latest["volume"],
            "mean_volume20": average, "relative_volume": ratio, "crossed": crossed,
            "triggered": bool(crossed and ratio >= 1.5), "observed_at": now}


def valuation(record, price):
    entry = _number(record["entry_price"], True)
    notional = _number(record["margin_eur"], True) * _number(record["leverage"], True)
    sign = 1 if record["direction"] == "LONG" else -1
    # Match the existing isolated-margin virtual ledger's gross-loss cap.
    gross = max(-record["margin_eur"], notional * sign * (price / entry - 1))
    costs = notional * (1 + price / entry) * (record["fee_bps"] + record["slippage_bps"]) / 10000
    return {"gross_eur": round(gross, 2), "costs_eur": round(costs, 2),
            "net_eur": round(gross - costs, 2)}


def _exit(record, price, at, reason):
    return {"price": price, "at": at, "reason": reason, **valuation(record, price)}


def _exclude(record, reason):
    if reason not in record["exclusions"]:
        record["exclusions"].append(reason)


def _new_record(pos, started, now, closed=False):
    opened = _stamp(pos.get("opened_at_ts") or pos.get("opened_at"))
    record = {k: pos[k] for k in ("id", "symbol", "direction", "leverage", "margin_eur", "entry_price")}
    for key in ("entry_price", "margin_eur", "leverage"):
        _number(record[key], True)
    if record["direction"] not in ("LONG", "SHORT"):
        raise ValueError("unsupported_direction")
    record.update(version=VERSION, opened_at=opened, observed_from=now,
                  fee_bps=FEE_BPS, slippage_bps=SLIPPAGE_BPS, exclusions=[],
                  baseline_exit=None, shadow_exit=None, last_signal=None,
                  baseline_peak=0, baseline_drawdown=0, shadow_peak=0, shadow_drawdown=0,
                  last_seen_at=now, last_bar_ts=None)
    if opened <= started:
        _exclude(record, "late_adoption")
    if closed or now - opened > MAX_GAP:
        _exclude(record, "entry_not_observed")
    if int(now // BAR_SECONDS) > int(opened // BAR_SECONDS):
        _exclude(record, "entry_bar_missed")
    return record


def _source(path, uid):
    with closing(_connect(path, True)) as conn:
        row = conn.execute("SELECT data FROM crypto_portfolios WHERE user_id=?", (uid,)).fetchone()
    return json.loads(row[0]) if row else {"positions": [], "trades": []}


def _save_record(path, uid, record, signal=None):
    with closing(_connect(path)) as conn, conn:
        conn.execute("INSERT OR REPLACE INTO crypto_exit_shadow_positions VALUES(?,?,?,?)",
                     (uid, record["id"], VERSION, json.dumps(record, allow_nan=False)))
        if signal:
            conn.execute("INSERT OR IGNORE INTO crypto_exit_shadow_bars VALUES(?,?,?,?,?)",
                         (uid, record["id"], VERSION, signal["bar_ts"], json.dumps(signal, allow_nan=False)))


def _baseline_close(record, trade):
    closed = _exit(record, _number(trade["exit_price"], True), _stamp(trade["closed_at"]),
                   trade.get("close_reason", "Baseline"))
    # This is a paired overlay: absent an EARLIER volume exit, copy the actual
    # baseline exit, never reconstruct an ideal candle/stop fill retrospectively.
    record["baseline_exit"] = closed
    if not record["shadow_exit"] or record["shadow_exit"]["at"] >= closed["at"]:
        record["shadow_exit"] = dict(closed)
    _mark(record, closed["price"], baseline=True, shadow=record["shadow_exit"]["at"] == closed["at"])


def _mark(record, price, baseline=True, shadow=True):
    pnl = valuation(record, price)["net_eur"]
    record["last_price"] = price
    record["mark_net_eur"] = pnl
    for branch, active in (("baseline", baseline), ("shadow", shadow)):
        if active:
            record[branch + "_peak"] = max(record[branch + "_peak"], pnl)
            record[branch + "_drawdown"] = max(record[branch + "_drawdown"], record[branch + "_peak"] - pnl)


def cycle(path, bars=fetch_bars, quote=fetch_quote, clock=time.time):
    now = clock()
    with closing(_connect(path, True)) as conn:
        users = [r[0] for r in conn.execute("SELECT p.user_id FROM crypto_portfolios p JOIN users u ON u.id=p.user_id")]
    errors = 0
    bar_cache = {}
    for uid in users:
        with closing(_connect(path)) as conn, conn:
            conn.execute("INSERT OR IGNORE INTO crypto_exit_shadow_runs(user_id,version,started_at) VALUES(?,?,?)", (uid, VERSION, now))
            run = dict(conn.execute("SELECT * FROM crypto_exit_shadow_runs WHERE user_id=? AND version=?", (uid, VERSION)).fetchone())
            records = {r[0]: json.loads(r[1]) for r in conn.execute("SELECT position_id,data FROM crypto_exit_shadow_positions WHERE user_id=? AND version=?", (uid, VERSION))}
        source = _source(path, uid)
        opened = {p["id"]: p for p in source.get("positions", [])}
        trades = {p["id"]: p for p in source.get("trades", [])}
        candidates = dict(opened)
        candidates.update({key: p for key, p in trades.items() if key not in records and _stamp(p["closed_at"]) > run["started_at"]})
        for key, pos in candidates.items():
            if key not in records:
                records[key] = _new_record(pos, run["started_at"], now, closed=key in trades)
        user_error = None
        for key, record in records.items():
            if record["baseline_exit"]:
                continue
            if now - record["last_seen_at"] > MAX_GAP:
                _exclude(record, "observation_gap")
            record["last_seen_at"] = now
            if key in trades:
                _baseline_close(record, trades[key])
                _save_record(path, uid, record)
                continue
            if key not in opened:
                _exclude(record, "source_position_missing")
                record["last_error"] = "source_position_missing"
                _save_record(path, uid, record)
                continue
            pos = opened[key]
            signal = None
            try:
                monitor_at = _number(source.get("monitor_success_at"), True)
                if not 0 <= now - monitor_at <= MAX_GAP or source.get("monitor_missing_symbols"):
                    raise ValueError("baseline_quote_stale")
                _mark(record, _number(pos["last_price"], True), shadow=not record["shadow_exit"])
                record["last_error"] = None
                # Once one decision has been captured, do not fetch/re-evaluate
                # it later with revised data. Only the first 90s after bar close.
                bar_end = int(now // BAR_SECONDS) * BAR_SECONDS
                candidate_ts = bar_end - BAR_SECONDS
                if (not record["shadow_exit"] and record["last_bar_ts"] != candidate_ts
                        and bar_end > record["observed_from"]):
                    if now - bar_end > MAX_GAP:
                        record["last_bar_ts"] = candidate_ts
                        _exclude(record, "missed_bar")
                    elif now - bar_end >= 5:
                        symbol = record["symbol"]
                        if symbol not in bar_cache:
                            bar_cache[symbol] = bars(symbol)
                        signal = volume_signal(bar_cache[symbol], record["direction"], now)
                        record["last_signal"] = signal
                        if signal["triggered"]:
                            tick = quote(symbol)
                            observed_at = clock()
                            market_at = _number(tick["market_at"], True)
                            if not max(signal["bar_end"], observed_at - 60) <= market_at <= observed_at + 2:
                                raise ValueError("exit_quote_stale")
                            if observed_at - signal["bar_end"] > MAX_GAP:
                                raise ValueError("late_exit_quote")
                            price = _number(tick["price"], True)
                            # The actual baseline may have closed during the HTTP
                            # requests. Resolve that race before recording a fill.
                            fresh = _source(path, uid)
                            closed = next((t for t in fresh.get("trades", []) if t["id"] == key), None)
                            if closed:
                                _baseline_close(record, closed)
                            elif any(p["id"] == key for p in fresh.get("positions", [])):
                                record["shadow_exit"] = _exit(record, price, observed_at, "Volumen-Trendbruch (15m)")
                                record["shadow_exit"]["signal"] = signal
                                _mark(record, price, baseline=False)
                            else:
                                raise ValueError("source_position_missing")
                        record["last_bar_ts"] = signal["bar_ts"]
            except Exception as exc:
                errors += 1
                user_error = type(exc).__name__
                record["last_error"] = str(exc) if isinstance(exc, ValueError) else "market_request_failed"
                # Errors are explicit and excluded; never quietly count incomplete
                # observations as an unchanged/successful strategy result.
                _exclude(record, "data_error")
            _save_record(path, uid, record, signal)
        with closing(_connect(path)) as conn, conn:
            conn.execute("UPDATE crypto_exit_shadow_runs SET last_attempt_at=?, last_success_at=CASE WHEN ? IS NULL THEN ? ELSE last_success_at END,last_error=? WHERE user_id=? AND version=?",
                         (now, user_error, now, user_error, uid, VERSION))
    return {"ok": errors == 0, "errors": errors}


def report(path, uid, now=None):
    """No schema creation, market requests or source-ledger writes from GET."""
    now = time.time() if now is None else now
    with closing(_connect(path, True)) as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='crypto_exit_shadow_runs'").fetchone()
        run = conn.execute("SELECT * FROM crypto_exit_shadow_runs WHERE user_id=? AND version=?", (uid, VERSION)).fetchone() if exists else None
        rows = [json.loads(r[0]) for r in conn.execute("SELECT data FROM crypto_exit_shadow_positions WHERE user_id=? AND version=?", (uid, VERSION))] if run else []
    paired = [r for r in rows if r["baseline_exit"] and r["shadow_exit"] and not r["exclusions"]]
    base = sum(r["baseline_exit"]["net_eur"] for r in paired)
    alternative = sum(r["shadow_exit"]["net_eur"] for r in paired)
    stamp = run["last_success_at"] if run else None
    healthy = bool(stamp and 0 <= now - stamp <= MAX_GAP and not run["last_error"])
    rows.sort(key=lambda r: r["observed_from"], reverse=True)
    return {"ok": True, "version": VERSION, "mode": "shadow_only", "healthy": healthy,
            "enabled": os.environ.get("CRYPTO_EXIT_SHADOW_ENABLED", "1") == "1",
            "started_at": run["started_at"] if run else None, "last_success_at": stamp,
            "last_error": run["last_error"] if run else None,
            "cost_model": {"fee_bps_per_side": FEE_BPS, "slippage_bps_per_side": SLIPPAGE_BPS,
                           "funding_included": False},
            "summary": {"completed_pairs": len(paired), "total_records": len(rows),
                        "excluded": sum(bool(r["exclusions"]) for r in rows),
                        "baseline_net_eur": round(base, 2), "shadow_net_eur": round(alternative, 2),
                        "difference_eur": round(alternative - base, 2),
                        "baseline_max_trade_drawdown_eur": round(max((r["baseline_drawdown"] for r in paired), default=0), 2),
                        "shadow_max_trade_drawdown_eur": round(max((r["shadow_drawdown"] for r in paired), default=0), 2)},
            "rows": rows[:100]}


def start(path, maintenance):
    global _thread
    if os.environ.get("CRYPTO_EXIT_SHADOW_ENABLED", "1") != "1":
        return
    # Import on the main startup thread, not while another trading module imports.
    from analyzer.crypto_runtime import file_lock
    with _start_lock:
        if _thread and _thread.is_alive():
            return
        _stop.clear()

        def worker():
            while not _stop.is_set():
                try:
                    with file_lock(str(path) + ".crypto-exit-shadow.lock", blocking=False) as leader:
                        if leader:
                            init_schema(path)
                            while not _stop.is_set():
                                before = time.monotonic()
                                try:
                                    if not maintenance():
                                        result = cycle(path)
                                        print("[CryptoExitShadow] cycle " + ("ok" if result["ok"] else "incomplete"), flush=True)
                                except Exception as exc:
                                    print("[CryptoExitShadow] cycle failed: " + type(exc).__name__, flush=True)
                                _stop.wait(max(1, INTERVAL - (time.monotonic() - before)))
                except Exception as exc:
                    print("[CryptoExitShadow] startup failed: " + type(exc).__name__, flush=True)
                _stop.wait(INTERVAL)

        _thread = threading.Thread(target=worker, name="crypto-exit-shadow", daemon=True)
        _thread.start()
