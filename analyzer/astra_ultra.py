"""Isolated, deterministic PAPER experiment. Public data only; no broker/Telegram.

Never writes crypto_portfolios, settings, users or the original strategy. The
separate leader owns all experiment writes; HTTP reporting is strictly read-only.
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

VERSION = "astra_breakout_4h_v1"
RULES = dict(symbols=["BTC", "ETH", "SOL"], bar_seconds=14400, warmup_bars=500, entry_channel=24,
             exit_channel=12, ema_period=200, atr_period=14, stop_atr=2,
             risk_pct=0.005, max_open_risk_pct=0.01, max_positions=2,
             max_notional_pct=0.40, leverage=1, max_hold_hours=336,
             fee_pct=0.002, slippage_pct=0.0005, direction="LONG")
RULE_HASH = hashlib.sha256(json.dumps(RULES, sort_keys=True).encode()).hexdigest()
INTERVAL = 30
MAX_AGE = 90
ENTRY_WINDOW = 180
_thread = None
_start_lock = threading.Lock()
_stop = threading.Event()


def connect(path, readonly=False):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro" if readonly else str(path),
                           uri=readonly, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(path):
    with closing(connect(path)) as c, c:
        c.executescript("""
          CREATE TABLE IF NOT EXISTS astra_ultra_runs(
            user_id INTEGER NOT NULL, version TEXT NOT NULL, data TEXT NOT NULL,
            PRIMARY KEY(user_id,version));
          CREATE TABLE IF NOT EXISTS astra_ultra_decisions(
            user_id INTEGER NOT NULL, version TEXT NOT NULL, bar_end REAL NOT NULL,
            symbol TEXT NOT NULL, data TEXT NOT NULL,
            PRIMARY KEY(user_id,version,bar_end,symbol));
          CREATE TABLE IF NOT EXISTS astra_ultra_equity(
            user_id INTEGER NOT NULL, version TEXT NOT NULL, observed_at REAL NOT NULL,
            data TEXT NOT NULL, PRIMARY KEY(user_id,version,observed_at));
        """)


def number(value, positive=False):
    v = float(value)
    if not math.isfinite(v) or (positive and v <= 0):
        raise ValueError("invalid_number")
    return v


def source(path, uid):
    with closing(connect(path, True)) as c:
        row = c.execute("SELECT data FROM crypto_portfolios WHERE user_id=?", (uid,)).fetchone()
        settings = c.execute("SELECT crypto_strategy_version FROM settings WHERE user_id=?", (uid,)).fetchone()
    return (json.loads(row[0]) if row else None), (settings[0] if settings else "classic")


def source_equity(p):
    return number(p["cash"]) + sum(number(x["margin_eur"], True) +
           max(-number(x["margin_eur"], True), number(x.get("unrealized_eur", 0)))
           for x in p.get("positions", []))


def seed(p, strategy, now):
    capital = source_equity(p)
    if capital <= 0 or not 0 <= now - number(p.get("monitor_success_at", 0)) <= MAX_AGE:
        raise ValueError("source_not_fresh_or_empty")
    return dict(version=VERSION, rule_hash=RULE_HASH, rules=copy.deepcopy(RULES), started_at=now,
                initial_capital=capital, cash=capital, positions=[], trades=[], equity=capital,
                peak=capital, max_drawdown_pct=0, fees_paid=0, last_attempt_at=None,
                last_success_at=None, last_bar_end=int(now // RULES["bar_seconds"]) * RULES["bar_seconds"],
                missed_hours=0, data_errors=0, observation_gaps=0, last_error=None,
                baseline_start_equity=capital, baseline_start_capital=p.get("start_capital"),
                baseline_old_trades=[t["id"] for t in p.get("trades", [])],
                baseline_inherited=[x["id"] for x in p.get("positions", [])],
                baseline_seen_trades=[t["id"] for t in p.get("trades", [])],
                baseline_seen_open=[x["id"] for x in p.get("positions", [])],
                baseline_initial_reserve=sum(exit_cost(x, x["last_price"]) for x in p.get("positions", [])),
                baseline_strategy=strategy, baseline_strategy_changes=[], baseline_invalid=[],
                baseline_equity=capital, baseline_peak=capital, baseline_max_drawdown_pct=0,
                baseline_costs=0, baseline_closed_count=0)


def exit_cost(pos, price):
    return pos["margin_eur"] * pos["leverage"] * price / pos["entry_price"] * (RULES["fee_pct"] + RULES["slippage_pct"])


def benchmark(s, p, strategy, now):
    """Rebased observed account, NOT a matched-risk randomized strategy test."""
    old = set(s["baseline_old_trades"])
    inherited = set(s["baseline_inherited"])
    current_trades = {t["id"] for t in p.get("trades", [])}
    current_open = {x["id"] for x in p.get("positions", [])}
    if (not set(s["baseline_seen_trades"]).issubset(current_trades)
            or not set(s["baseline_seen_open"]).issubset(current_open | current_trades)
            or p.get("start_capital") != s["baseline_start_capital"]):
        if "source_reset_or_changed_history" not in s["baseline_invalid"]:
            s["baseline_invalid"].append("source_reset_or_changed_history")
    s["baseline_seen_trades"] = list(current_trades)
    s["baseline_seen_open"] = list(current_open)
    if strategy != s.get("baseline_last_strategy", s["baseline_strategy"]):
        s["baseline_strategy_changes"].append(dict(at=now, strategy=strategy))
    s["baseline_last_strategy"] = strategy
    costs = 0
    count = 0
    for pos in p.get("positions", []) + p.get("trades", []):
        if pos["id"] in old:
            continue
        notional = pos["margin_eur"] * pos["leverage"]
        if pos["id"] not in inherited:
            costs += notional * (RULES["fee_pct"] + RULES["slippage_pct"])
        costs += exit_cost(pos, pos.get("exit_price", pos["last_price"]))
        count += int(pos["id"] in current_trades)
    s["baseline_costs"] = costs - s["baseline_initial_reserve"]
    s["baseline_equity"] = source_equity(p) - s["baseline_costs"]
    s["baseline_closed_count"] = count
    s["baseline_peak"] = max(s["baseline_peak"], s["baseline_equity"])
    s["baseline_max_drawdown_pct"] = max(s["baseline_max_drawdown_pct"],
        (1 - s["baseline_equity"] / s["baseline_peak"]) * 100)


def public_get(symbol, endpoint):
    if symbol not in RULES["symbols"]:
        raise ValueError("unsupported_symbol")
    request = urllib.request.Request("https://www.okx.com/api/v5/market/" + endpoint +
                                     "&instId=" + symbol + "-USDT", headers={"User-Agent": "AstraPaper/1"})
    with urllib.request.urlopen(request, timeout=6) as response:
        result = json.load(response)
    if result.get("code") != "0":
        raise ValueError("market_api_error")
    return result["data"]


def fetch_bars(symbol):
    rows = {}
    cursor = None
    # Bounded pagination. Public history may cap a response below the requested
    # limit. Six requests suffice for500confirmed bars at100/page; otherwise fail.
    for _ in range(6):
        batch = public_get(symbol, "history-candles?bar=4H&limit=300" + ("&after=" + str(cursor) if cursor else ""))
        if not batch:
            break
        for r in batch:
            if len(r) >= 9 and str(r[8]) == "1":
                rows[int(r[0])] = r
        oldest = min(int(r[0]) for r in batch)
        if len(rows) >= RULES["warmup_bars"]:
            break
        if cursor and oldest >= cursor:
            raise ValueError("pagination_not_advancing")
        cursor = oldest
    return [rows[t] for t in sorted(rows)[-RULES["warmup_bars"]:]]


def fetch_quote(symbol):
    t = public_get(symbol, "ticker?")[0]
    return dict(price=number(t["last"], True), market_at=number(t["ts"], True) / 1000)


def signal(rows, now):
    """Only completed contiguous bars; current bar never enters a channel."""
    bars = []
    for r in rows:
        if len(r) < 9 or str(r[8]) != "1":
            continue
        b = dict(start=number(r[0], True) / 1000, open=number(r[1], True),
                 high=number(r[2], True), low=number(r[3], True), close=number(r[4], True))
        if b["start"] + RULES["bar_seconds"] <= now:
            if not b["low"] <= min(b["open"], b["close"]) <= max(b["open"], b["close"]) <= b["high"]:
                raise ValueError("invalid_ohlc")
            bars.append(b)
    bars.sort(key=lambda b: b["start"])
    bars = bars[-RULES["warmup_bars"]:]
    if len(bars) < RULES["warmup_bars"]:
        raise ValueError("insufficient_warmup")
    if any(b["start"] - a["start"] != RULES["bar_seconds"] for a, b in zip(bars, bars[1:])):
        raise ValueError("non_contiguous_bars")
    bar_end = bars[-1]["start"] + RULES["bar_seconds"]
    if not 0 <= now - bar_end <= ENTRY_WINDOW:
        raise ValueError("stale_bar")
    ema = sum(b["close"] for b in bars[:200]) / 200
    for b in bars[200:]:
        ema += 2 / 201 * (b["close"] - ema)
    tr = [bars[0]["high"] - bars[0]["low"]] + [max(b["high"] - b["low"], abs(b["high"] - a["close"]), abs(b["low"] - a["close"]))
          for a, b in zip(bars, bars[1:])]
    atr = sum(tr[:14]) / 14
    for t in tr[14:]:
        atr = (atr * 13 + t) / 14
    if atr <= 0:
        raise ValueError("zero_atr")
    upper = max(b["high"] for b in bars[-25:-1])
    lower = min(b["low"] for b in bars[-13:-1])
    close = bars[-1]["close"]
    return dict(bar_end=bar_end, close=close, ema200=ema, atr14=atr,
                upper24=upper, lower12=lower, enter=close > upper and close > ema,
                exit=close < lower)


def valid_quote(tick, now, not_before=0):
    price = number(tick["price"], True)
    if not max(now - 60, not_before) <= number(tick["market_at"], True) <= now + 2:
        raise ValueError("stale_quote")
    return price


def liquidation_value(pos, price):
    return pos["quantity"] * price * (1 - RULES["slippage_pct"]) * (1 - RULES["fee_pct"])


def mark(s):
    s["equity"] = s["cash"] + sum(liquidation_value(p, p["last_price"]) for p in s["positions"])
    s["peak"] = max(s["peak"], s["equity"])
    s["max_drawdown_pct"] = max(s["max_drawdown_pct"], (1 - s["equity"] / s["peak"]) * 100)


def close_position(s, pos, price, now, reason):
    fill = price * (1 - RULES["slippage_pct"])
    gross_value = pos["quantity"] * fill
    fee = gross_value * RULES["fee_pct"]
    proceeds = gross_value - fee
    pnl = proceeds - pos["entry_total"]
    s["cash"] += proceeds
    s["fees_paid"] += fee
    s["trades"].append(dict(pos, exit_price=fill, exit_reference=price, exit_fee=fee,
                            closed_at=now, close_reason=reason, pnl_eur=pnl,
                            pnl_pct=pnl / pos["entry_total"] * 100))
    s["positions"].remove(pos)


def open_position(s, symbol, sig, price, now):
    if len(s["positions"]) >= RULES["max_positions"]:
        return "Positionslimit"
    if any(p["symbol"] == symbol for p in s["positions"]):
        return "Bereits offen"
    fill = price * (1 + RULES["slippage_pct"])
    stop = fill - RULES["stop_atr"] * sig["atr14"]
    if stop <= 0 or abs(price / sig["close"] - 1) > 0.01:
        return "Kurs zu weit vom Signal / Stop ungueltig"
    equity = s["equity"]
    remaining_risk = max(0, equity * RULES["max_open_risk_pct"] - sum(p["planned_risk"] for p in s["positions"]))
    risk = min(equity * RULES["risk_pct"], remaining_risk)
    loss_per_unit = fill * (1 + RULES["fee_pct"]) - stop * (1 - RULES["slippage_pct"]) * (1 - RULES["fee_pct"])
    quantity = min(risk / loss_per_unit, equity * RULES["max_notional_pct"] / fill,
                   s["cash"] / (fill * (1 + RULES["fee_pct"])))
    if quantity * fill < 10:
        return "Budget zu klein"
    fee = quantity * fill * RULES["fee_pct"]
    total = quantity * fill + fee
    pos = dict(id=f"{symbol}_{int(now * 1000)}", symbol=symbol, direction="LONG", leverage=1,
               quantity=quantity, entry_price=fill, entry_reference=price, entry_total=total,
               entry_fee=fee, margin_eur=quantity * fill, opened_at=now, stop_loss=stop,
               take_profit=None, planned_risk=quantity * loss_per_unit,
               last_price=price, quote_at=now, signal=copy.deepcopy(sig), version=VERSION)
    s["cash"] -= total
    s["fees_paid"] += fee
    s["positions"].append(pos)
    mark(s)
    return "Virtueller Kauf"


def step(s, bars, quote, clock=time.time):
    now = clock()
    if s["rule_hash"] != RULE_HASH:
        raise ValueError("rules_changed_requires_new_version")
    if s["last_success_at"] and now - s["last_success_at"] > MAX_AGE:
        s["observation_gaps"] += 1
    errors = []
    decisions = []
    ticks = {}
    closed_this_cycle = set()
    # Protect existing paper positions before considering new entries.
    for pos in list(s["positions"]):
        try:
            tick = quote(pos["symbol"])
            price = valid_quote(tick, clock())
            ticks[pos["symbol"]] = tick
            pos.update(last_price=price, quote_at=tick["market_at"])
            if price <= pos["stop_loss"]:
                close_position(s, pos, price, clock(), "Stop-Loss")
                closed_this_cycle.add(pos["symbol"])
            elif clock() - pos["opened_at"] >= RULES["max_hold_hours"] * 3600:
                close_position(s, pos, price, clock(), "Zeit-Exit 336h")
                closed_this_cycle.add(pos["symbol"])
        except Exception:
            errors.append(pos["symbol"] + ": Kurs fehlt/veraltet")
    mark(s)
    target = int(now // RULES["bar_seconds"]) * RULES["bar_seconds"]
    if target > s["last_bar_end"] and now - target >= 5:
        missed = max(0, int((target - s["last_bar_end"]) / RULES["bar_seconds"]) - 1)
        s["missed_hours"] += missed
        if now - target > ENTRY_WINDOW:
            s["missed_hours"] += 1
            decisions.append(dict(bar_end=target, symbol="SYSTEM", action="Stundenfenster verpasst; kein rueckdatierter Handel"))
        else:
            signals = []
            # Fetch all before opening: a partial universe cannot silently win a
            # ranking/capacity decision. Fixed symbol order is pre-registered.
            # Public candle histories are fetched in parallel, away from the
            # original watcher. All network calls have bounded timeouts.
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = {symbol: pool.submit(bars, symbol) for symbol in RULES["symbols"]}
            for symbol in RULES["symbols"]:
                try:
                    sig = signal(futures[symbol].result(), clock())
                    if sig["bar_end"] != target:
                        raise ValueError("wrong_bar")
                    tick = quote(symbol)
                    price = valid_quote(tick, clock(), target)
                    if clock() - target > ENTRY_WINDOW:
                        raise ValueError("late_decision")
                    signals.append((symbol, sig, price, tick["market_at"]))
                except Exception:
                    errors.append(symbol + ": Stundendaten fehlen/veraltet")
                    decisions.append(dict(bar_end=target, symbol=symbol, action="Datenfehler; keine neue Position"))
            # Apply ALL fresh marks/exits before considering any allocation.
            # A quote can become stale while the other symbols are fetched.
            ready = []
            actions = {}
            for symbol, sig, price, market_at in signals:
                try:
                    valid_quote(dict(price=price, market_at=market_at), clock(), target)
                except ValueError:
                    errors.append(symbol + ": Kurs vor Entscheidung veraltet")
                    actions[symbol] = "Kein Handel: Kurs vor Entscheidung veraltet"
                    continue
                existing = next((p for p in s["positions"] if p["symbol"] == symbol), None)
                ready.append((symbol, sig, price, market_at))
                if existing:
                    existing.update(last_price=price, quote_at=market_at)
                    reason = ("Stop-Loss" if price <= existing["stop_loss"] else
                              "Zeit-Exit 336h" if clock() - existing["opened_at"] >= RULES["max_hold_hours"] * 3600 else
                              "12-Kerzen-Kanal (48h) nach unten gebrochen" if sig["exit"] else None)
                    if reason:
                        close_position(s, existing, price, clock(), reason)
                        closed_this_cycle.add(symbol)
                        actions[symbol] = "Virtueller Verkauf: " + reason
                    else:
                        actions[symbol] = "Halten"
            mark(s)
            # Fixed BTC/ETH/SOL priority, now with released capital and all marks.
            for symbol, sig, price, market_at in ready:
                action = actions.get(symbol, "Abwarten")
                if symbol in actions:
                    continue
                if symbol in closed_this_cycle:
                    action = "Gerade geschlossen; kein sofortiger Wiedereinstieg"
                elif sig["enter"] and not errors:
                    try:
                        valid_quote(dict(price=price, market_at=market_at), clock(), target)
                        if clock() - target > ENTRY_WINDOW:
                            raise ValueError("late_entry")
                        action = open_position(s, symbol, sig, price, clock())
                    except ValueError:
                        errors.append(symbol + ": Einstiegsfenster/Kurs abgelaufen")
                        action = "Kein Einstieg: Zeitpunkt/Kurs abgelaufen"
                elif errors:
                    action = "Kein Einstieg: unvollstaendige Daten"
                actions[symbol] = action
            for symbol, sig, price, market_at in signals:
                decisions.append(dict(sig, symbol=symbol, reference=price, observed_at=clock(), action=actions[symbol]))
        s["last_bar_end"] = target
    mark(s)
    s["last_attempt_at"] = clock()
    s["last_error"] = "; ".join(errors) or None
    if errors:
        s["data_errors"] += 1
    else:
        s["last_success_at"] = clock()
    return decisions


def cycle(path, uid=1, bars=fetch_bars, quote=fetch_quote, clock=time.time):
    now = clock()
    p, strategy = source(path, uid)
    if p is None:
        return
    with closing(connect(path, True)) as c:
        row = c.execute("SELECT data FROM astra_ultra_runs WHERE user_id=? AND version=?", (uid, VERSION)).fetchone()
        older = c.execute("SELECT version FROM astra_ultra_runs WHERE user_id=?", (uid,)).fetchone()
    if not row and older:
        # A later code version must migrate/carry forward the same paper capital
        # explicitly. It must not erase a losing run by silently reseeding.
        raise ValueError("version_transition_requires_capital_carry_forward")
    s = json.loads(row[0]) if row else seed(p, strategy, now)
    source_fresh = 0 <= now - number(p.get("monitor_success_at", 0)) <= MAX_AGE and not p.get("monitor_missing_symbols")
    if source_fresh:
        benchmark(s, p, strategy, now)
        s["baseline_at"] = now
    s["baseline_fresh"] = bool(source_fresh)
    decisions = step(s, bars, quote, clock)
    # One short transaction persists fills, decisions and equity atomically.
    with closing(connect(path)) as c, c:
        c.execute("INSERT OR REPLACE INTO astra_ultra_runs VALUES(?,?,?)", (uid, VERSION, json.dumps(s, allow_nan=False)))
        for d in decisions:
            c.execute("INSERT OR IGNORE INTO astra_ultra_decisions VALUES(?,?,?,?,?)", (uid, VERSION, d["bar_end"], d["symbol"], json.dumps(d, allow_nan=False)))
        if not s.get("last_sample_at") or now - s["last_sample_at"] >= 300 or decisions:
            sample = dict(equity=s["equity"], baseline_equity=s["baseline_equity"],
                          baseline_fresh=bool(source_fresh), data_ok=not s["last_error"])
            c.execute("INSERT OR IGNORE INTO astra_ultra_equity VALUES(?,?,?,?)", (uid, VERSION, now, json.dumps(sample)))
            s["last_sample_at"] = now
            c.execute("UPDATE astra_ultra_runs SET data=? WHERE user_id=? AND version=?", (json.dumps(s, allow_nan=False), uid, VERSION))
    return s


def report(path, uid, now=None):
    now = time.time() if now is None else now
    with closing(connect(path, True)) as c:
        exists = c.execute("SELECT 1 FROM sqlite_master WHERE name='astra_ultra_runs'").fetchone()
        row = c.execute("SELECT data FROM astra_ultra_runs WHERE user_id=? AND version=?", (uid, VERSION)).fetchone() if exists else None
        if not row:
            return dict(ok=True, started=False, version=VERSION)
        s = json.loads(row[0])
        decisions = [json.loads(r[0]) for r in c.execute("SELECT data FROM astra_ultra_decisions WHERE user_id=? AND version=? ORDER BY bar_end DESC,symbol LIMIT 100", (uid, VERSION))]
        curve = [dict(json.loads(r[1]), at=r[0]) for r in c.execute("SELECT observed_at,data FROM astra_ultra_equity WHERE user_id=? AND version=? ORDER BY observed_at DESC LIMIT 500", (uid, VERSION))]
        daily = [dict(json.loads(r[1]), at=r[0]) for r in c.execute("""SELECT observed_at,data FROM astra_ultra_equity
                  WHERE user_id=? AND version=? AND observed_at IN (
                    SELECT MAX(observed_at) FROM astra_ultra_equity WHERE user_id=? AND version=?
                    GROUP BY CAST(observed_at/86400 AS INTEGER)) ORDER BY observed_at DESC LIMIT 90""", (uid, VERSION, uid, VERSION))]
    # Never expose snapshots of unrelated historical source trades to the client.
    for key in ("baseline_old_trades", "baseline_seen_trades", "baseline_seen_open", "baseline_inherited"):
        if key == "baseline_inherited":
            s["baseline_inherited_count"] = len(s[key])
        s.pop(key, None)
    s["closed_count"] = len(s["trades"])
    s["realized_pnl"] = sum(t["pnl_eur"] for t in s["trades"])
    wins = sum(t["pnl_eur"] for t in s["trades"] if t["pnl_eur"] > 0)
    losses = -sum(t["pnl_eur"] for t in s["trades"] if t["pnl_eur"] < 0)
    s["profit_factor"] = wins / losses if losses else None
    s["trades"] = list(reversed(s["trades"][-100:]))
    stamp = s.get("last_success_at")
    return dict(ok=True, started=True, mode="paper_only", healthy=bool(stamp and 0 <= now - stamp <= MAX_AGE and not s["last_error"]),
                enabled=os.environ.get("ASTRA_ULTRA_ENABLED", "1") == "1", run=s,
                decisions=decisions, curve=list(reversed(curve)), daily=daily)


def start(path, maintenance):
    global _thread
    if os.environ.get("ASTRA_ULTRA_ENABLED", "1") != "1":
        return
    from analyzer.crypto_runtime import file_lock
    with _start_lock:
        if _thread and _thread.is_alive():
            return
        _stop.clear()

        def worker():
            while not _stop.is_set():
                try:
                    with file_lock(str(path) + ".astra-ultra.lock", blocking=False) as leader:
                        if leader:
                            init_schema(path)
                            while not _stop.is_set():
                                before = time.monotonic()
                                try:
                                    if not maintenance():
                                        result = cycle(path)
                                        print("[AstraUltra] paper cycle " + ("ok" if result and not result["last_error"] else "waiting/incomplete"), flush=True)
                                except Exception as exc:
                                    print("[AstraUltra] paper cycle failed: " + type(exc).__name__, flush=True)
                                _stop.wait(max(1, INTERVAL - (time.monotonic() - before)))
                except Exception as exc:
                    print("[AstraUltra] worker failed: " + type(exc).__name__, flush=True)
                _stop.wait(INTERVAL)

        _thread = threading.Thread(target=worker, name="astra-ultra-paper", daemon=True)
        _thread.start()
