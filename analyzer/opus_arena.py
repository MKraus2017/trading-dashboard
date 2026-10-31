"""Opus 5.5 Arena: isolated, deterministic PAPER competitor to Krypto and Astra.

Public OKX market data only. Never places orders, never calls an LLM, broker,
OKX private endpoint or Telegram. Never writes crypto_portfolios, settings,
users or any astra_ultra_* table. The background leader owns all writes to the
dedicated opus_arena_* tables; the HTTP report is strictly read-only.

Start capital is seeded ONCE as the mean of the fresh Krypto reference equity
(mark-to-market, same formula as Astra's seed) and the fresh Astra equity.
"""
import copy
import hashlib
import json
import math
import os
import sqlite3
import threading
import time
import urllib.request
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

VERSION = "opus_pullback_2h_v1"
RULES = dict(symbols=["BTC", "ETH", "SOL"], bar_seconds=7200, history_bars=400,
             ema_fast=50, ema_slow=200, rsi_period=14, rsi_pullback_max=40,
             pullback_lookback=4, atr_period=14, initial_stop_atr=1.5, trail_atr=3.0,
             max_hold_hours=120, risk_pct=0.005, max_open_risk_pct=0.015,
             max_positions=3, max_notional_pct=0.40, leverage=1,
             fee_pct=0.002, slippage_pct=0.0005, direction="LONG",
             seed="mean_of_krypto_and_astra_equity")
RULE_HASH = hashlib.sha256(json.dumps(RULES, sort_keys=True).encode()).hexdigest()
INTERVAL = 30
MAX_AGE = 90
ENTRY_WINDOW = 180
RETRY_SECONDS = 30
MAX_ATTEMPTS = 6
SAMPLE_SECONDS = 300
_thread = None
_start_lock = threading.Lock()
_stop = threading.Event()


class NotReady(Exception):
    """Krypto or Astra not yet fresh enough to seed the arena."""


def connect(path, readonly=False):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro" if readonly else str(path),
                           uri=readonly, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(path):
    with closing(connect(path)) as c, c:
        c.executescript("""
          CREATE TABLE IF NOT EXISTS opus_arena_runs(
            user_id INTEGER NOT NULL, version TEXT NOT NULL, data TEXT NOT NULL,
            PRIMARY KEY(user_id,version));
          CREATE TABLE IF NOT EXISTS opus_arena_decisions(
            user_id INTEGER NOT NULL, version TEXT NOT NULL, bar_end REAL NOT NULL,
            symbol TEXT NOT NULL, data TEXT NOT NULL,
            PRIMARY KEY(user_id,version,bar_end,symbol));
          CREATE TABLE IF NOT EXISTS opus_arena_equity(
            user_id INTEGER NOT NULL, version TEXT NOT NULL, observed_at REAL NOT NULL,
            data TEXT NOT NULL, PRIMARY KEY(user_id,version,observed_at));
        """)


def number(value, positive=False):
    v = float(value)
    if not math.isfinite(v) or (positive and v <= 0):
        raise ValueError("invalid_number")
    return v


# --------------------------------------------------------------------------
# Competitors (read-only)
# --------------------------------------------------------------------------

def _table_exists(c, name):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def krypto_equity(p):
    """Same mark-to-market formula Astra uses to seed from the Krypto account."""
    return number(p["cash"]) + sum(number(x["margin_eur"], True) +
           max(-number(x["margin_eur"], True), number(x.get("unrealized_eur", 0)))
           for x in p.get("positions", []))


def competitors(path, uid, now):
    """Return (krypto, astra) snapshots or None. Never writes anything."""
    krypto = astra = None
    with closing(connect(path, True)) as c:
        if _table_exists(c, "crypto_portfolios"):
            row = c.execute("SELECT data FROM crypto_portfolios WHERE user_id=?", (uid,)).fetchone()
            strategy = "classic"
            if _table_exists(c, "settings"):
                srow = c.execute("SELECT crypto_strategy_version FROM settings WHERE user_id=?", (uid,)).fetchone()
                strategy = (srow[0] if srow and srow[0] else "classic")
            if row:
                try:
                    p = json.loads(row[0])
                    stamp = number(p.get("monitor_success_at", 0))
                    krypto = dict(equity=krypto_equity(p), at=stamp, strategy=strategy,
                                  start_capital=p.get("start_capital"),
                                  fresh=0 <= now - stamp <= MAX_AGE and not p.get("monitor_missing_symbols"))
                except (KeyError, TypeError, ValueError):
                    krypto = None
        if _table_exists(c, "astra_ultra_runs"):
            rows = [json.loads(r[0]) for r in c.execute("SELECT data FROM astra_ultra_runs WHERE user_id=?", (uid,))]
            if rows:
                # The active Astra run is the one that started its version last.
                s = max(rows, key=lambda r: r.get("version_started_at") or r.get("started_at") or 0)
                try:
                    stamp = s.get("monitor_success_at", s.get("last_success_at"))
                    astra = dict(equity=number(s["equity"], True), at=stamp, version=s.get("version"),
                                 started_at=s.get("started_at"),
                                 fresh=bool(stamp) and 0 <= now - stamp <= MAX_AGE and not s.get("monitor_error"))
                except (KeyError, TypeError, ValueError):
                    astra = None
    return krypto, astra


def seed(krypto, astra, now):
    if not krypto or not astra or not krypto["fresh"] or not astra["fresh"] or krypto["equity"] <= 0:
        raise NotReady("krypto_or_astra_not_fresh")
    capital = (krypto["equity"] + astra["equity"]) / 2
    if not math.isfinite(capital) or capital <= 0:
        raise NotReady("invalid_seed_capital")
    bar = RULES["bar_seconds"]
    return dict(version=VERSION, rule_hash=RULE_HASH, rules=copy.deepcopy(RULES), started_at=now,
                initial_capital=capital, cash=capital, positions=[], trades=[], equity=capital,
                peak=capital, max_drawdown_pct=0, fees_paid=0,
                seed=dict(krypto_equity=krypto["equity"], astra_equity=astra["equity"],
                          astra_version=astra.get("version"), krypto_strategy=krypto.get("strategy"), at=now),
                # Seeding skips the current boundary: first decision is the next completed candle.
                last_bar_end=int(now // bar) * bar, pending=None, missed_bars=0, data_errors=0,
                observation_gaps=0, monitor_error=None, candle_error=None, last_error=None,
                monitor_success_at=None, last_candle_success_at=None, last_candle_bar_end=None,
                last_attempt_at=None, last_sample_at=None,
                competitors=dict(
                    krypto=dict(start_equity=krypto["equity"], equity=krypto["equity"], peak=krypto["equity"],
                                max_drawdown_pct=0, fresh=True, at=krypto["at"], strategy=krypto.get("strategy"),
                                start_capital=krypto.get("start_capital"), strategy_changes=[], invalid=[]),
                    astra=dict(start_equity=astra["equity"], equity=astra["equity"], peak=astra["equity"],
                               max_drawdown_pct=0, fresh=True, at=astra["at"], version=astra.get("version"),
                               started_at=astra.get("started_at"), invalid=[])))


def update_competitors(s, krypto, astra, now):
    comp = s["competitors"]
    k = comp["krypto"]
    k["fresh"] = bool(krypto and krypto["fresh"])
    if krypto:
        if krypto.get("start_capital") != k.get("start_capital") and "krypto_depot_reset" not in k["invalid"]:
            k["invalid"].append("krypto_depot_reset")
        if krypto.get("strategy") != k.get("strategy"):
            k["strategy_changes"].append(dict(at=now, strategy=krypto.get("strategy")))
            k["strategy"] = krypto.get("strategy")
    if krypto and krypto["fresh"]:
        k.update(equity=krypto["equity"], at=krypto["at"])
        k["peak"] = max(k["peak"], k["equity"])
        k["max_drawdown_pct"] = max(k["max_drawdown_pct"], (1 - k["equity"] / k["peak"]) * 100)
    a = comp["astra"]
    a["fresh"] = bool(astra and astra["fresh"])
    if astra and astra.get("started_at") != a.get("started_at") and "astra_restarted" not in a["invalid"]:
        a["invalid"].append("astra_restarted")
    if astra and astra["fresh"]:
        a.update(equity=astra["equity"], at=astra["at"], version=astra.get("version"))
        a["peak"] = max(a["peak"], a["equity"])
        a["max_drawdown_pct"] = max(a["max_drawdown_pct"], (1 - a["equity"] / a["peak"]) * 100)


# --------------------------------------------------------------------------
# Market data (public)
# --------------------------------------------------------------------------

def public_get(symbol, endpoint):
    if symbol not in RULES["symbols"]:
        raise ValueError("unsupported_symbol")
    request = urllib.request.Request("https://www.okx.com/api/v5/market/" + endpoint +
                                     "&instId=" + symbol + "-USDT", headers={"User-Agent": "OpusArenaPaper/1"})
    with urllib.request.urlopen(request, timeout=6) as response:
        result = json.load(response)
    if result.get("code") != "0":
        raise ValueError("market_api_error")
    return result["data"]


def fetch_bars(symbol):
    rows, cursor = {}, None
    bar = str(RULES["bar_seconds"] // 3600) + "H"
    for _ in range(6):
        batch = public_get(symbol, "history-candles?bar=" + bar + "&limit=100" + ("&after=" + str(cursor) if cursor else ""))
        if not batch:
            break
        for r in batch:
            if len(r) >= 9 and str(r[8]) == "1":
                rows[int(r[0])] = r
        oldest = min(int(r[0]) for r in batch)
        if len(rows) >= RULES["history_bars"]:
            break
        if cursor and oldest >= cursor:
            raise ValueError("pagination_not_advancing")
        cursor = oldest
    return [rows[t] for t in sorted(rows)[-RULES["history_bars"]:]]


def fetch_quote(symbol):
    t = public_get(symbol, "ticker?")[0]
    return dict(price=number(t["last"], True), market_at=number(t["ts"], True) / 1000)


def valid_quote(tick, now, not_before=0):
    price = number(tick["price"], True)
    if not max(now - 60, not_before) <= number(tick["market_at"], True) <= now + 2:
        raise ValueError("stale_quote")
    return price


# --------------------------------------------------------------------------
# Indicators and signal (pure)
# --------------------------------------------------------------------------

def ema(values, period):
    e = sum(values[:period]) / period
    k = 2 / (period + 1)
    for v in values[period:]:
        e += k * (v - e)
    return e


def rsi_series(closes, period):
    """Wilder RSI aligned to closes; None until enough history."""
    out = [None] * len(closes)
    if len(closes) <= period:
        return out
    deltas = [b - a for a, b in zip(closes, closes[1:])]
    gain = sum(max(d, 0) for d in deltas[:period]) / period
    loss = sum(max(-d, 0) for d in deltas[:period]) / period

    def value(g, l):
        if l == 0:
            return 100.0 if g > 0 else 50.0
        return 100 - 100 / (1 + g / l)
    out[period] = value(gain, loss)
    for i, d in enumerate(deltas[period:], start=period + 1):
        gain = (gain * (period - 1) + max(d, 0)) / period
        loss = (loss * (period - 1) + max(-d, 0)) / period
        out[i] = value(gain, loss)
    return out


def atr(bars, period):
    tr = [bars[0]["high"] - bars[0]["low"]] + [
        max(b["high"] - b["low"], abs(b["high"] - a["close"]), abs(b["low"] - a["close"]))
        for a, b in zip(bars, bars[1:])]
    value = sum(tr[:period]) / period
    for t in tr[period:]:
        value = (value * (period - 1) + t) / period
    return value


def parse_bars(rows, now):
    bar = RULES["bar_seconds"]
    bars = []
    for r in rows:
        if len(r) < 9 or str(r[8]) != "1":
            continue
        b = dict(start=number(r[0], True) / 1000, open=number(r[1], True), high=number(r[2], True),
                 low=number(r[3], True), close=number(r[4], True))
        if b["start"] + bar > now:
            continue
        if b["start"] % bar:
            raise ValueError("unaligned_bar")
        if not b["low"] <= min(b["open"], b["close"]) <= max(b["open"], b["close"]) <= b["high"]:
            raise ValueError("invalid_ohlc")
        bars.append(b)
    bars.sort(key=lambda b: b["start"])
    bars = bars[-RULES["history_bars"]:]
    if len(bars) < RULES["history_bars"]:
        raise ValueError("insufficient_warmup")
    if any(b["start"] - a["start"] != bar for a, b in zip(bars, bars[1:])):
        raise ValueError("non_contiguous_bars")
    bar_end = bars[-1]["start"] + bar
    if not 0 <= now - bar_end <= ENTRY_WINDOW:
        raise ValueError("stale_bar")
    return bars


def signal(rows, now):
    """Opus v1: buy a confirmed bounce after an RSI pullback inside an uptrend.

    Trend: close > EMA200 and EMA50 > EMA200.
    Pullback: min RSI14 of the previous 4 completed candles (signal candle
    excluded) <= 40.
    Reversal: the signal candle is green and closes above the previous high.
    Trend exit: a completed close below EMA200.
    """
    bars = parse_bars(rows, now)
    closes = [b["close"] for b in bars]
    ema_fast = ema(closes, RULES["ema_fast"])
    ema_slow = ema(closes, RULES["ema_slow"])
    rsis = rsi_series(closes, RULES["rsi_period"])
    window = rsis[-1 - RULES["pullback_lookback"]:-1]
    if any(v is None for v in window):
        raise ValueError("insufficient_rsi")
    pullback_min = min(window)
    a = atr(bars, RULES["atr_period"])
    if a <= 0:
        raise ValueError("zero_atr")
    last, prev = bars[-1], bars[-2]
    trend_ok = last["close"] > ema_slow and ema_fast > ema_slow
    pullback_ok = pullback_min <= RULES["rsi_pullback_max"]
    reversal_ok = last["close"] > prev["high"] and last["close"] > last["open"]
    return dict(bar_end=last["start"] + RULES["bar_seconds"], bar_seconds=RULES["bar_seconds"],
                open=last["open"], high=last["high"], close=last["close"], prev_high=prev["high"],
                ema50=ema_fast, ema200=ema_slow, rsi14=rsis[-1], rsi_pullback_min=pullback_min, atr14=a,
                trend_ok=trend_ok, pullback_ok=pullback_ok, reversal_ok=reversal_ok,
                enter=trend_ok and pullback_ok and reversal_ok, exit=last["close"] < ema_slow)


# --------------------------------------------------------------------------
# Paper accounting
# --------------------------------------------------------------------------

def liquidation_value(pos, price):
    return pos["quantity"] * price * (1 - RULES["slippage_pct"]) * (1 - RULES["fee_pct"])


def mark(s):
    s["equity"] = s["cash"] + sum(liquidation_value(p, p["last_price"]) for p in s["positions"])
    s["peak"] = max(s["peak"], s["equity"])
    s["max_drawdown_pct"] = max(s["max_drawdown_pct"], (1 - s["equity"] / s["peak"]) * 100)


def close_position(s, pos, price, now, reason):
    fill = price * (1 - RULES["slippage_pct"])
    gross = pos["quantity"] * fill
    fee = gross * RULES["fee_pct"]
    proceeds = gross - fee
    pnl = proceeds - pos["entry_total"]
    s["cash"] += proceeds
    s["fees_paid"] += fee
    s["trades"].append(dict(pos, exit_price=fill, exit_reference=price, exit_fee=fee, closed_at=now,
                            close_reason=reason, pnl_eur=pnl, pnl_pct=pnl / pos["entry_total"] * 100))
    s["positions"].remove(pos)


def open_position(s, symbol, sig, price, now, quote_at):
    if len(s["positions"]) >= RULES["max_positions"]:
        return "Positionslimit"
    if any(p["symbol"] == symbol for p in s["positions"]):
        return "Bereits offen"
    fill = price * (1 + RULES["slippage_pct"])
    stop = fill - RULES["initial_stop_atr"] * sig["atr14"]
    if stop <= 0 or abs(price / sig["close"] - 1) > 0.01:
        return "Kurs zu weit vom Signal / Stop ungueltig"
    equity = s["equity"]
    remaining = max(0, equity * RULES["max_open_risk_pct"] - sum(p["planned_risk"] for p in s["positions"]))
    risk = min(equity * RULES["risk_pct"], remaining)
    loss_per_unit = fill * (1 + RULES["fee_pct"]) - stop * (1 - RULES["slippage_pct"]) * (1 - RULES["fee_pct"])
    if loss_per_unit <= 0:
        return "Stop ungueltig"
    quantity = min(risk / loss_per_unit, equity * RULES["max_notional_pct"] / fill,
                   s["cash"] / (fill * (1 + RULES["fee_pct"])))
    if quantity * fill < 10:
        return "Budget zu klein"
    fee = quantity * fill * RULES["fee_pct"]
    total = quantity * fill + fee
    s["positions"].append(dict(
        id=f"{symbol}_{int(now * 1000)}", symbol=symbol, direction="LONG", leverage=1, quantity=quantity,
        entry_price=fill, entry_reference=price, entry_total=total, entry_fee=fee, margin_eur=quantity * fill,
        opened_at=now, initial_stop=stop, stop_loss=stop, peak_price=price, take_profit=None,
        planned_risk=quantity * loss_per_unit, last_price=price, quote_at=quote_at,
        signal=copy.deepcopy(sig), version=VERSION))
    s["cash"] -= total
    s["fees_paid"] += fee
    mark(s)
    return "Virtueller Kauf"


def _protective_reason(pos, price, at):
    if price <= pos["stop_loss"]:
        return "Trailing-Stop" if pos["stop_loss"] > pos["initial_stop"] else "Stop-Loss"
    if at - pos["opened_at"] >= RULES["max_hold_hours"] * 3600:
        return f"Zeit-Exit {RULES['max_hold_hours']}h"
    return None


def _evaluate_candle(s, target, bars, quote, clock, monitor_errors):
    """All-or-nothing: fetch everything first; act only on a complete universe."""
    signals, ticks, errors = {}, {}, {}
    with ThreadPoolExecutor(max_workers=len(RULES["symbols"])) as pool:
        futures = {sym: pool.submit(bars, sym) for sym in RULES["symbols"]}
    for sym in RULES["symbols"]:
        try:
            sig = signal(futures[sym].result(), clock())
            if sig["bar_end"] != target:
                raise ValueError("wrong_bar")
            tick = quote(sym)
            valid_quote(tick, clock(), target)
            signals[sym], ticks[sym] = sig, tick
        except Exception as exc:
            errors[sym] = f"{sym}: Kerzendaten/Kurs fehlen ({type(exc).__name__})"
    if errors or monitor_errors:
        errors.update({sym: sym + ": Schutzkurs fehlt/veraltet" for sym in monitor_errors})
        return None, errors
    at = clock()
    if not target <= at <= target + ENTRY_WINDOW:
        return None, {sym: sym + ": Kerzenfenster abgelaufen" for sym in RULES["symbols"]}
    decisions = {}
    # Exits, trailing updates and marks always precede entries.
    for sym in RULES["symbols"]:
        sig, tick = signals[sym], ticks[sym]
        price = number(tick["price"], True)
        pos = next((p for p in s["positions"] if p["symbol"] == sym), None)
        if not pos:
            continue
        pos.update(last_price=price, quote_at=tick["market_at"], peak_price=max(pos["peak_price"], price, sig["high"]))
        trail = pos["peak_price"] - RULES["trail_atr"] * sig["atr14"]
        if trail > pos["stop_loss"]:
            pos["stop_loss"] = trail
        reason = _protective_reason(pos, price, at) or ("Trend gebrochen: Schluss unter EMA200" if sig["exit"] else None)
        if reason:
            close_position(s, pos, price, at, reason)
            decisions[sym] = ("Virtueller Verkauf: " + reason, price)
        else:
            decisions[sym] = (f"Halten · Stop {pos['stop_loss']:.6g}", price)
    mark(s)
    for sym in RULES["symbols"]:
        if sym in decisions:
            continue
        sig = signals[sym]
        price = number(ticks[sym]["price"], True)
        if not sig["enter"]:
            decisions[sym] = ("Abwarten", price)
            continue
        if any(t["symbol"] == sym and t["closed_at"] >= target for t in s["trades"]):
            decisions[sym] = ("In dieser Kerze bereits verkauft; kein Wiedereinstieg", price)
            continue
        decisions[sym] = (open_position(s, sym, sig, price, clock(), ticks[sym]["market_at"]), price)
    return [dict(signals[sym], symbol=sym, version=VERSION, action=decisions[sym][0], reference=decisions[sym][1],
                 observed_at=at, final=True) for sym in RULES["symbols"]], {}


def step(s, bars, quote, clock=time.time):
    now = clock()
    if s.get("version") != VERSION or s.get("rule_hash") != RULE_HASH or s.get("rules") != RULES:
        raise ValueError("rules_changed_requires_new_version")
    if s.get("monitor_success_at") and now - s["monitor_success_at"] > MAX_AGE:
        s["observation_gaps"] += 1
    monitor_errors = []
    # Protection first, every cycle.
    for pos in list(s["positions"]):
        try:
            tick = quote(pos["symbol"])
            at = clock()
            price = valid_quote(tick, at, max(pos["opened_at"], pos.get("quote_at") or pos["opened_at"]))
            pos.update(last_price=price, quote_at=tick["market_at"], peak_price=max(pos["peak_price"], price))
            reason = _protective_reason(pos, price, at)
            if reason:
                close_position(s, pos, price, at, reason)
        except Exception:
            monitor_errors.append(pos["symbol"])
    mark(s)
    decisions = []
    bar = RULES["bar_seconds"]
    now = clock()
    target = int(now // bar) * bar
    if target > s["last_bar_end"]:
        pending = s.get("pending")
        if pending is None or pending["bar_end"] != target:
            # Every unfinished boundary between the last finished one and this
            # target (including an abandoned older pending bar) counts once.
            s["missed_bars"] += max(0, int((target - s["last_bar_end"]) / bar) - 1)
            pending = s["pending"] = dict(bar_end=target, attempts=0, next_retry_at=target + 5, errors={})
        if now > target + ENTRY_WINDOW or pending["attempts"] >= MAX_ATTEMPTS:
            s["missed_bars"] += 1
            s["candle_error"] = "Kerzenprüfung unvollständig; Fenster abgelaufen: " + "; ".join(pending["errors"].values())
            s["last_bar_end"], s["pending"] = target, None
        elif now >= pending["next_retry_at"]:
            pending["attempts"] += 1
            rows, errors = _evaluate_candle(s, target, bars, quote, clock, monitor_errors)
            if rows is not None:
                decisions = rows
                s.update(last_bar_end=target, pending=None, candle_error=None,
                         last_candle_success_at=clock(), last_candle_bar_end=target)
            else:
                pending.update(errors=errors, next_retry_at=clock() + RETRY_SECONDS)
                s["candle_error"] = "Kerzenprüfung wird wiederholt: " + "; ".join(errors.values())
                s["data_errors"] += 1
    mark(s)
    s["last_attempt_at"] = clock()
    s["monitor_error"] = "; ".join(sym + ": Kurs fehlt/veraltet" for sym in monitor_errors) or None
    if monitor_errors:
        s["data_errors"] += 1
    else:
        s["monitor_success_at"] = clock()
    s["last_error"] = "; ".join(e for e in (s["monitor_error"], s["candle_error"]) if e) or None
    return decisions


# --------------------------------------------------------------------------
# Persistence, report, worker
# --------------------------------------------------------------------------

def _stored(c, uid):
    if not _table_exists(c, "opus_arena_runs"):
        return None
    row = c.execute("SELECT data FROM opus_arena_runs WHERE user_id=? AND version=?", (uid, VERSION)).fetchone()
    return row[0] if row else None


def cycle(path, uid=1, bars=fetch_bars, quote=fetch_quote, clock=time.time):
    now = clock()
    krypto, astra = competitors(path, uid, now)
    with closing(connect(path, True)) as c:
        raw = _stored(c, uid)
    if raw is None:
        try:
            s = seed(krypto, astra, now)
        except NotReady:
            return None
    else:
        s = json.loads(raw)
    update_competitors(s, krypto, astra, now)
    decisions = step(s, bars, quote, clock)
    with closing(connect(path)) as c, c:
        c.execute("BEGIN IMMEDIATE")
        if _stored(c, uid) != raw:
            raise ValueError("run_changed_during_cycle")
        for d in decisions:
            c.execute("INSERT OR IGNORE INTO opus_arena_decisions VALUES(?,?,?,?,?)",
                      (uid, VERSION, d["bar_end"], d["symbol"], json.dumps(d, allow_nan=False)))
        if not s.get("last_sample_at") or now - s["last_sample_at"] >= SAMPLE_SECONDS or decisions:
            comp = s["competitors"]
            sample = dict(equity=s["equity"], krypto_equity=comp["krypto"]["equity"], krypto_fresh=comp["krypto"]["fresh"],
                          astra_equity=comp["astra"]["equity"], astra_fresh=comp["astra"]["fresh"],
                          data_ok=not s["last_error"])
            c.execute("INSERT OR IGNORE INTO opus_arena_equity VALUES(?,?,?,?)",
                      (uid, VERSION, now, json.dumps(sample, allow_nan=False)))
            s["last_sample_at"] = now
        c.execute("INSERT OR REPLACE INTO opus_arena_runs VALUES(?,?,?)", (uid, VERSION, json.dumps(s, allow_nan=False)))
    return s


def leaderboard(s):
    comp = s["competitors"]
    rows = [dict(key="opus", name="Opus 5.5", start_equity=s["initial_capital"], equity=s["equity"],
                 max_drawdown_pct=s["max_drawdown_pct"], fresh=True, valid=True)]
    rows.append(dict(key="astra", name="Astra Ultra", start_equity=comp["astra"]["start_equity"],
                     equity=comp["astra"]["equity"], max_drawdown_pct=comp["astra"]["max_drawdown_pct"],
                     fresh=comp["astra"]["fresh"], valid=not comp["astra"]["invalid"]))
    rows.append(dict(key="krypto", name="Krypto", start_equity=comp["krypto"]["start_equity"],
                     equity=comp["krypto"]["equity"], max_drawdown_pct=comp["krypto"]["max_drawdown_pct"],
                     fresh=comp["krypto"]["fresh"], valid=not comp["krypto"]["invalid"]))
    for r in rows:
        r["return_pct"] = (r["equity"] / r["start_equity"] - 1) * 100
    rows.sort(key=lambda r: (not r["valid"], -r["return_pct"]))
    for rank, r in enumerate(rows, 1):
        r["rank"] = rank
    return rows


def report(path, uid, now=None):
    now = time.time() if now is None else now
    with closing(connect(path, True)) as c:
        raw = _stored(c, uid)
        if raw is None:
            return dict(ok=True, started=False, version=VERSION, rules=RULES,
                        enabled=os.environ.get("OPUS_ARENA_ENABLED", "1") == "1")
        s = json.loads(raw)
        decisions = [json.loads(r[0]) for r in c.execute(
            "SELECT data FROM opus_arena_decisions WHERE user_id=? AND version=? ORDER BY bar_end DESC,symbol LIMIT 90",
            (uid, VERSION))]
        daily = [dict(json.loads(r["data"]), at=r["observed_at"]) for r in c.execute("""
            SELECT observed_at,data FROM (
              SELECT observed_at,data,ROW_NUMBER() OVER (
                PARTITION BY CAST(observed_at/86400 AS INTEGER) ORDER BY observed_at DESC) AS rn
              FROM opus_arena_equity WHERE user_id=? AND version=?)
            WHERE rn=1 ORDER BY observed_at DESC LIMIT 90""", (uid, VERSION))]
    trades = s["trades"]
    wins = [t["pnl_eur"] for t in trades if t["pnl_eur"] > 0]
    losses = [-t["pnl_eur"] for t in trades if t["pnl_eur"] < 0]
    s["closed_count"] = len(trades)
    s["realized_pnl"] = sum(t["pnl_eur"] for t in trades)
    s["win_rate"] = len(wins) / len(trades) * 100 if trades else None
    s["profit_factor"] = sum(wins) / sum(losses) if losses else None
    s["trades"] = list(reversed(trades[-100:]))
    stamp = s.get("monitor_success_at")
    monitor_healthy = bool(stamp and 0 <= now - stamp <= MAX_AGE and not s.get("monitor_error"))
    candle_healthy = not s.get("candle_error")
    return dict(ok=True, started=True, mode="paper_only", version=VERSION,
                enabled=os.environ.get("OPUS_ARENA_ENABLED", "1") == "1",
                healthy=monitor_healthy and candle_healthy, monitor_healthy=monitor_healthy,
                candle_healthy=candle_healthy, run=s, leaderboard=leaderboard(s),
                decisions=decisions, daily=daily)


def start(path, maintenance):
    global _thread
    if os.environ.get("OPUS_ARENA_ENABLED", "1") != "1":
        return
    from analyzer.crypto_runtime import file_lock
    with _start_lock:
        if _thread and _thread.is_alive():
            return
        _stop.clear()

        def worker():
            while not _stop.is_set():
                try:
                    with file_lock(str(path) + ".opus-arena.lock", blocking=False) as leader:
                        if leader:
                            init_schema(path)
                            while not _stop.is_set():
                                before = time.monotonic()
                                try:
                                    if not maintenance():
                                        result = cycle(path)
                                        state = ("waiting for fresh Krypto+Astra" if result is None else
                                                 "ok" if not result["last_error"] else "incomplete")
                                        print("[OpusArena] paper cycle " + state, flush=True)
                                except Exception as exc:
                                    print("[OpusArena] paper cycle failed: " + type(exc).__name__, flush=True)
                                _stop.wait(max(1, INTERVAL - (time.monotonic() - before)))
                except Exception as exc:
                    print("[OpusArena] worker failed: " + type(exc).__name__, flush=True)
                _stop.wait(INTERVAL)

        _thread = threading.Thread(target=worker, name="opus-arena-paper", daemon=True)
        _thread.start()
