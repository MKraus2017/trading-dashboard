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

VERSION = "astra_breakout_2h_v2"
RULES = dict(symbols=["BTC", "ETH", "SOL"], bar_seconds=7200, warmup_bars=500, entry_channel=24,
             exit_channel=12, ema_period=200, atr_period=14, stop_atr=2,
             risk_pct=0.005, max_open_risk_pct=0.01, max_positions=2,
             max_notional_pct=0.40, leverage=1, max_hold_hours=336,
             fee_pct=0.002, slippage_pct=0.0005, direction="LONG")
RULE_HASH = hashlib.sha256(json.dumps(RULES, sort_keys=True).encode()).hexdigest()
PREVIOUS_VERSION = "astra_breakout_4h_v1"
PREVIOUS_RULES = dict(copy.deepcopy(RULES), bar_seconds=14400)
PREVIOUS_RULE_HASH = hashlib.sha256(json.dumps(PREVIOUS_RULES, sort_keys=True).encode()).hexdigest()
INTERVAL = 30
MAX_AGE = 90
ENTRY_WINDOW = 180
CANDLE_RETRY_SECONDS = 30
MAX_CANDLE_ATTEMPTS = 6
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
                version_started_at=now, prior_versions=[],
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


def carry_forward(previous, now):
    """Explicit 4H -> 2H transition: same account, no reset or backdated fills.

    The old database row remains untouched. Entry facts and stops are retained;
    any inherited open position uses the new channel for future management.
    """
    if (previous.get("version") != PREVIOUS_VERSION
            or previous.get("rule_hash") != PREVIOUS_RULE_HASH
            or previous.get("rules") != PREVIOUS_RULES):
        raise ValueError("version_transition_requires_capital_carry_forward")
    s = copy.deepcopy(previous)
    s.setdefault("prior_versions", []).append(dict(
        version=previous["version"], started_at=previous.get("version_started_at", previous["started_at"]),
        ended_at=now, equity=previous["equity"], cash=previous["cash"],
        closed_count=len(previous["trades"]), positions_count=len(previous["positions"]),
        rule_hash=previous["rule_hash"], rules=copy.deepcopy(previous["rules"])))
    s.update(version=VERSION, rule_hash=RULE_HASH, rules=copy.deepcopy(RULES),
             version_started_at=now, version_change_reason="User-requested 4H to 2H transition",
             last_bar_end=int(now // RULES["bar_seconds"]) * RULES["bar_seconds"],
             last_sample_at=None)
    for pos in s["positions"]:
        pos.setdefault("rules", copy.deepcopy(PREVIOUS_RULES))
        pos.update(management_version=VERSION, management_rules=copy.deepcopy(RULES))
    return s


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
        bar = str(RULES["bar_seconds"] // 3600) + "H"
        batch = public_get(symbol, "history-candles?bar=" + bar + "&limit=300" + ("&after=" + str(cursor) if cursor else ""))
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
    return dict(bar_end=bar_end, bar_seconds=RULES["bar_seconds"], close=close, ema200=ema, atr14=atr,
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


def open_position(s, symbol, sig, price, now, quote_at=None):
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
               last_price=price, quote_at=now if quote_at is None else quote_at,
               signal=copy.deepcopy(sig), version=VERSION,
               rules=copy.deepcopy(RULES), management_version=VERSION, management_rules=copy.deepcopy(RULES))
    s["cash"] -= total
    s["fees_paid"] += fee
    s["positions"].append(pos)
    mark(s)
    return "Virtueller Kauf"


def _new_pending_bar(target, now, closed=()):
    return dict(bar_end=target, started_at=now, expires_at=target + ENTRY_WINDOW,
                attempts=0, max_attempts=MAX_CANDLE_ATTEMPTS, last_attempt_at=None,
                next_retry_at=max(now, target + 5), waiting_symbols=list(RULES["symbols"]),
                signals={}, completed={}, entered_symbols=[], closed_symbols=sorted(set(closed)),
                decision_records={}, executions={})


def _remember_closed(pending, symbol, price=None, at=None, reason=None):
    if pending is not None and symbol not in pending["closed_symbols"]:
        pending["closed_symbols"].append(symbol)
    if pending is not None and reason is not None:
        event = dict(action="Virtueller Verkauf: " + reason, observed_at=at, reference=price)
        pending.setdefault("executions", {}).setdefault(symbol, []).append(event)
        previous = pending["completed"].get(symbol, {})
        # Preserve an actual same-bar entry, with the later exit in its explicit
        # execution history; a preliminary Hold must never cover a real exit.
        if previous.get("action") != "Virtueller Kauf":
            pending["completed"][symbol] = event


def _candle_records(pending, now, errors=None, final=False, append_attempt=True):
    """Append attempts; retain the time/action of any already executed fill."""
    decisions = []
    errors = errors or {}
    for symbol in RULES["symbols"]:
        completed = pending["completed"].get(symbol)
        old = pending["decision_records"].get(symbol, {})
        action = completed["action"] if completed else errors.get(symbol,
            "Kerzenfenster beendet; keine vollstaendige Entscheidung" if final else "Wiederholung: vollstaendige Daten fehlen")
        attempt = dict(attempt=pending["attempts"], observed_at=now, action=action,
                       final=final, completed=bool(completed))
        if symbol in errors:
            attempt["data_error"] = errors[symbol]
        history = copy.deepcopy(old.get("attempt_history", []))
        if append_attempt:
            history.append(attempt)
        record = dict(pending["signals"].get(symbol, {}), symbol=symbol, bar_end=pending["bar_end"],
                      version=VERSION, timeframe_hours=RULES["bar_seconds"] // 3600,
                      decision_schema=2, final=final, completed=bool(completed), action=action,
                      observed_at=completed["observed_at"] if completed else now,
                      last_attempt_at=pending["last_attempt_at"], attempt_history=history,
                      execution_history=copy.deepcopy(pending.get("executions", {}).get(symbol, [])))
        if completed and "reference" in completed:
            record["reference"] = completed["reference"]
        pending["decision_records"][symbol] = record
        decisions.append(record)
    return decisions


def _finish_candle(s, pending, now, success, errors=None, attempted=True):
    errors = {} if success else (errors if errors is not None else pending.get("last_errors", {}))
    if success:
        s["last_candle_success_at"] = now
        s["last_candle_bar_end"] = pending["bar_end"]
        s["candle_error"] = None
    else:
        s["missed_hours"] += 1
        s["candle_error"] = "Kerzenpruefung unvollstaendig; Fenster abgelaufen/Versuche ausgeschoepft"
    s["last_bar_end"] = pending["bar_end"]
    decisions = _candle_records(pending, now, errors, final=True, append_attempt=attempted)
    s["pending_bar"] = None
    return decisions


def _attempt_candle(s, pending, bars, quote, clock, monitor_errors):
    pending["attempts"] += 1
    pending["last_attempt_at"] = clock()
    errors, ticks = {}, {}
    needed = [symbol for symbol in RULES["symbols"] if symbol not in pending["signals"]]
    # A validated completed signal is immutable within this window. Retry only
    # missing histories, but acquire/revalidate every quote before allocation.
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {symbol: pool.submit(bars, symbol) for symbol in needed}
    for symbol in RULES["symbols"]:
        try:
            if symbol in futures:
                sig = signal(futures[symbol].result(), clock())
                if sig["bar_end"] != pending["bar_end"]:
                    raise ValueError("wrong_bar")
                pending["signals"][symbol] = sig
            if not pending["bar_end"] <= clock() <= pending["expires_at"]:
                raise ValueError("outside_decision_window")
            tick = quote(symbol)
            valid_quote(tick, clock(), pending["bar_end"])
            ticks[symbol] = tick
        except Exception:
            errors[symbol] = symbol + ": Kerzendaten/Kurs fehlen oder Fenster abgelaufen"

    def completed(symbol, action, at, price):
        pending["completed"].setdefault(symbol, dict(action=action, observed_at=at, reference=price))

    # All exits/marks precede all entries. Protection still applies to a freshly
    # observed stop/time breach even if fetching crossed the candle deadline.
    ready = {}
    for symbol, tick in ticks.items():
        at = clock()
        existing = next((p for p in s["positions"] if p["symbol"] == symbol), None)
        try:
            not_before = pending["bar_end"]
            if existing:
                if at < existing["opened_at"]:
                    raise ValueError("clock_before_entry")
                not_before = max(not_before, existing["opened_at"], existing.get("quote_at") or existing["opened_at"])
            price = valid_quote(tick, at, not_before)
        except ValueError:
            errors[symbol] = symbol + ": Kurs vor Entscheidung veraltet"
            continue
        sig = pending["signals"][symbol]
        within = pending["bar_end"] <= at <= pending["expires_at"]
        if existing:
            existing.update(last_price=price, quote_at=tick["market_at"])
            reason = ("Stop-Loss" if price <= existing["stop_loss"] else
                      "Zeit-Exit 336h" if at - existing["opened_at"] >= RULES["max_hold_hours"] * 3600 else
                      f"12-Kerzen-Kanal ({RULES['exit_channel'] * RULES['bar_seconds'] // 3600}h) nach unten gebrochen" if within and sig["exit"] else None)
            if reason:
                close_position(s, existing, price, at, reason)
                if within:
                    _remember_closed(pending, symbol, price, at, reason)
                    completed(symbol, "Virtueller Verkauf: " + reason, at, price)
                else:
                    # The independent protection fill is in the trade journal,
                    # not a retrospectively successful expired candle decision.
                    _remember_closed(pending, symbol)
            elif within:
                completed(symbol, "Halten", at, price)
        if not within:
            errors[symbol] = symbol + ": Kerzenfenster abgelaufen"
            continue
        ready[symbol] = tick
        if symbol in pending["closed_symbols"] or symbol in pending["entered_symbols"]:
            completed(symbol, "In dieser Kerze bereits gehandelt; kein Wiedereinstieg", at, price)
        elif not existing and not sig["enter"]:
            completed(symbol, "Abwarten", at, price)
    mark(s)

    # Incomplete universe or an unresolved protection quote forbids new risk.
    if len(ready) == len(RULES["symbols"]) and not errors and not monitor_errors:
        for symbol in RULES["symbols"]:
            if symbol in pending["completed"]:
                continue
            at = clock()
            try:
                if not pending["bar_end"] <= at <= pending["expires_at"]:
                    raise ValueError("outside_entry_window")
                # Recheck the WHOLE universe at each fill, not only this symbol.
                for tick in ready.values():
                    valid_quote(tick, at, pending["bar_end"])
                price = number(ready[symbol]["price"], True)
                action = open_position(s, symbol, pending["signals"][symbol], price, at,
                                       quote_at=ready[symbol]["market_at"])
                completed(symbol, action, at, price)
                if action == "Virtueller Kauf":
                    pending["entered_symbols"].append(symbol)
                    pending["executions"].setdefault(symbol, []).append(
                        dict(action=action, observed_at=at, reference=price))
            except ValueError:
                errors[symbol] = symbol + ": Einstiegsfenster/Kurs abgelaufen"
                break
    elif monitor_errors:
        errors.update({symbol: symbol + ": Schutzkurs fehlt/veraltet" for symbol in monitor_errors})

    pending["waiting_symbols"] = [symbol for symbol in RULES["symbols"]
                                  if symbol not in pending["completed"] or symbol in errors]
    at = clock()
    pending["next_retry_at"] = min(max(at, pending["last_attempt_at"]) + CANDLE_RETRY_SECONDS,
                                   pending["expires_at"])
    complete = len(ready) == len(RULES["symbols"]) and not errors and not pending["waiting_symbols"]
    if complete:
        return _finish_candle(s, pending, at, True), False
    pending["last_errors"] = copy.deepcopy(errors)
    details = "; ".join(errors.values()) or ", ".join(pending["waiting_symbols"])
    s["candle_error"] = "Kerzenpruefung unvollstaendig; Wiederholung ausstehend: " + details
    if at > pending["expires_at"] or pending["attempts"] >= pending["max_attempts"]:
        return _finish_candle(s, pending, at, False, errors), bool(errors)
    return _candle_records(pending, at, errors), bool(errors)


def step(s, bars, quote, clock=time.time):
    now = clock()
    if s["rule_hash"] != RULE_HASH:
        raise ValueError("rules_changed_requires_new_version")
    for key in ("pending_bar", "candle_error", "last_candle_success_at", "last_candle_bar_end", "monitor_error"):
        s.setdefault(key, None)
    s.setdefault("monitor_success_at", s.get("last_success_at"))
    if s["monitor_success_at"] and now - s["monitor_success_at"] > MAX_AGE:
        s["observation_gaps"] += 1
    monitor_errors, decisions, closed_this_cycle = [], [], []
    # Always run protection, even during retry cooldown or after an expired bar.
    for pos in list(s["positions"]):
        try:
            tick = quote(pos["symbol"])
            at = clock()
            if at < pos["opened_at"]:
                raise ValueError("clock_before_entry")
            price = valid_quote(tick, at, max(pos["opened_at"], pos.get("quote_at") or pos["opened_at"]))
            pos.update(last_price=price, quote_at=tick["market_at"])
            reason = ("Stop-Loss" if price <= pos["stop_loss"] else
                      "Zeit-Exit 336h" if at - pos["opened_at"] >= RULES["max_hold_hours"] * 3600 else None)
            if reason:
                close_position(s, pos, price, at, reason)
                closed_this_cycle.append((pos["symbol"], price, at, reason))
                pending = s["pending_bar"]
                if pending and pending["bar_end"] <= at <= pending["expires_at"]:
                    _remember_closed(pending, pos["symbol"], price, at, reason)
        except Exception:
            monitor_errors.append(pos["symbol"])
    mark(s)
    now = clock()
    pending = s["pending_bar"]
    if pending and now > pending["expires_at"]:
        decisions.extend(_finish_candle(s, pending, now, False, attempted=False))
    target = int(now // RULES["bar_seconds"]) * RULES["bar_seconds"]
    if s["pending_bar"] is None and target > s["last_bar_end"]:
        # Charge intervening windows only when creating their successor, never
        # on every retry while last_bar_end deliberately remains unfinished.
        s["missed_hours"] += max(0, int((target - s["last_bar_end"]) / RULES["bar_seconds"]) - 1)
        s["pending_bar"] = _new_pending_bar(target, now)
        for event in closed_this_cycle:
            if target <= event[2] <= s["pending_bar"]["expires_at"]:
                _remember_closed(s["pending_bar"], *event)
    pending = s["pending_bar"]
    had_data_error = False
    if pending:
        if now > pending["expires_at"]:
            decisions.extend(_finish_candle(s, pending, now, False, attempted=False))
        elif now >= pending["next_retry_at"]:
            rows, had_data_error = _attempt_candle(s, pending, bars, quote, clock, monitor_errors)
            decisions.extend(rows)
    mark(s)
    s["last_attempt_at"] = clock()
    s["monitor_error"] = "; ".join(symbol + ": Kurs fehlt/veraltet" for symbol in monitor_errors) or None
    if not monitor_errors:
        s["monitor_success_at"] = s["last_success_at"] = clock()
    if monitor_errors or had_data_error:
        s["data_errors"] += 1
    s["last_error"] = "; ".join(error for error in (s["monitor_error"], s["candle_error"]) if error) or None
    return decisions


def _persist_decision(connection, uid, decision):
    key = (uid, VERSION, decision["bar_end"], decision["symbol"])
    row = connection.execute(
        "SELECT data FROM astra_ultra_decisions WHERE user_id=? AND version=? AND bar_end=? AND symbol=?", key
    ).fetchone()
    encoded = json.dumps(decision, allow_nan=False)
    if row is None:
        connection.execute("INSERT INTO astra_ultra_decisions VALUES(?,?,?,?,?)", (*key, encoded))
        return
    previous = json.loads(row[0])
    # Never reinterpret legacy rows or finalized history. Only the new protocol
    # can extend an unfinished record, and all old attempts must stay a prefix.
    if previous.get("decision_schema") != 2 or previous.get("final", True):
        return
    history = previous.get("attempt_history", [])
    if decision.get("attempt_history", [])[:len(history)] != history:
        raise ValueError("decision_retry_history_not_preserved")
    connection.execute(
        "UPDATE astra_ultra_decisions SET data=? WHERE user_id=? AND version=? AND bar_end=? AND symbol=?",
        (encoded, *key))


def cycle(path, uid=1, bars=fetch_bars, quote=fetch_quote, clock=time.time):
    now = clock()
    p, strategy = source(path, uid)
    if p is None:
        return
    with closing(connect(path, True)) as c:
        row = c.execute("SELECT data FROM astra_ultra_runs WHERE user_id=? AND version=?", (uid, VERSION)).fetchone()
        older = c.execute("SELECT version,data FROM astra_ultra_runs WHERE user_id=?", (uid,)).fetchall()
    if not row and older:
        if len(older) != 1 or older[0]["version"] != PREVIOUS_VERSION:
            raise ValueError("version_transition_requires_capital_carry_forward")
        s = carry_forward(json.loads(older[0]["data"]), now)
    else:
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
            _persist_decision(c, uid, d)
        if not s.get("last_sample_at") or now - s["last_sample_at"] >= 300 or decisions:
            sample = dict(equity=s["equity"], baseline_equity=s["baseline_equity"],
                          baseline_fresh=bool(source_fresh), data_ok=not s["last_error"] and not s.get("pending_bar"))
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
        versions = list(dict.fromkeys([v["version"] for v in s.get("prior_versions", [])] + [VERSION]))
        hours = {v["version"]: v["rules"]["bar_seconds"] // 3600 for v in s.get("prior_versions", [])}
        hours[VERSION] = RULES["bar_seconds"] // 3600
        where = "user_id=? AND version IN (" + ",".join("?" for _ in versions) + ")"
        args = (uid, *versions)
        decisions = [dict(json.loads(r["data"]), version=r["version"], timeframe_hours=hours[r["version"]])
                     for r in c.execute("SELECT version,data FROM astra_ultra_decisions WHERE " + where +
                                        " ORDER BY bar_end DESC,symbol LIMIT 100", args)]
        curve = [dict(json.loads(r["data"]), at=r["observed_at"], version=r["version"])
                 for r in c.execute("SELECT version,observed_at,data FROM astra_ultra_equity WHERE " + where +
                                    " ORDER BY observed_at DESC LIMIT 500", args)]
        daily = [dict(json.loads(r["data"]), at=r["observed_at"], version=r["version"])
                 for r in c.execute("""SELECT version,observed_at,data FROM (
                   SELECT version,observed_at,data,ROW_NUMBER() OVER (
                     PARTITION BY CAST(observed_at/86400 AS INTEGER) ORDER BY observed_at DESC,version DESC) AS rn
                   FROM astra_ultra_equity WHERE """ + where +
                                    ") WHERE rn=1 ORDER BY observed_at DESC LIMIT 90", args)]
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
    stamp = s.get("monitor_success_at", s.get("last_success_at"))
    monitor_healthy = bool(stamp and 0 <= now - stamp <= MAX_AGE and not s.get("monitor_error", s.get("last_error")))
    candle_healthy = not s.get("candle_error") and not s.get("pending_bar")
    return dict(ok=True, started=True, mode="paper_only", healthy=monitor_healthy and candle_healthy,
                monitor_healthy=monitor_healthy, candle_healthy=candle_healthy,
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
                                        print("[AstraUltra] paper cycle " + ("ok" if result and not result["last_error"] and not result.get("pending_bar") else "waiting/incomplete"), flush=True)
                                except Exception as exc:
                                    print("[AstraUltra] paper cycle failed: " + type(exc).__name__, flush=True)
                                _stop.wait(max(1, INTERVAL - (time.monotonic() - before)))
                except Exception as exc:
                    print("[AstraUltra] worker failed: " + type(exc).__name__, flush=True)
                _stop.wait(INTERVAL)

        _thread = threading.Thread(target=worker, name="astra-ultra-paper", daemon=True)
        _thread.start()
