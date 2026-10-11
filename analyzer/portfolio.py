"""Virtuelles Depot: Kaufen, Verkaufen, SL/TP, Bewertung (Multi-User)."""
import json
import math
import os
import time
from datetime import datetime, timezone
from typing import Dict, Optional

import config
from analyzer import db_store, indicators, telegram, yahoo_client
from analyzer import stock_quotes
from analyzer.stock_runtime import serialized

PORTFOLIO_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "portfolio.json")


def _default_portfolio():
    return {
        "cash": config.START_CAPITAL,
        "positions": [],
        "trades": [],
        "value_history": [{"date": datetime.utcnow().isoformat(), "value": config.START_CAPITAL}],
        "real_positions": [],
        "real_trades": [],
    }


def _load(user_id: int) -> dict:
    p = db_store.load_portfolio(user_id)
    if p:
        # stelle sicher, dass neue Felder existieren
        for key in ("real_positions", "real_trades", "value_history"):
            if key not in p:
                p[key] = [] if key != "value_history" else [{"date": datetime.utcnow().isoformat(), "value": config.START_CAPITAL}]
        return p
    return _default_portfolio()


def _save(user_id: int, p: dict, *, background=False):
    if background:
        # Monitoring never publishes database backups or overwrites the legacy
        # shared JSON file. Keep private SQLite snapshots, also outside Render.
        db_store.save_portfolio(user_id, p, automatic_backup=False)
        try:
            import storage_runtime
            storage_runtime.private_backup(db_store.DB_PATH)
        except Exception:
            print("[StockWatch] private snapshot unavailable", flush=True)
        return
    db_store.save_portfolio(user_id, p)
    # Lokales Backup für Entwicklung
    try:
        os.makedirs(os.path.dirname(PORTFOLIO_FILE), exist_ok=True)
        with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
            json.dump(p, f, indent=2, default=str)
    except Exception:
        pass


def get_portfolio(user_id: int) -> dict:
    return _load(user_id)


@serialized
def reset_portfolio(user_id: int):
    p = _default_portfolio()
    _save(user_id, p)
    return p


def _current_position(portfolio: dict, symbol: str) -> Optional[dict]:
    for pos in portfolio.get("positions", []):
        if pos["symbol"] == symbol:
            return pos
    return None


def _total_value(portfolio: dict, prices: Dict[str, float]) -> float:
    total = portfolio.get("cash", 0)
    for pos in portfolio.get("positions", []):
        price = prices.get(pos["symbol"], pos.get("last_price", pos["entry_price"]))
        total += pos["shares"] * price
    return total


def _chandelier_stop(symbol: str, highest_price: float, tick: dict) -> Optional[float]:
    """Berechnet den ATR-basierten Chandelier-Exit-Stop für ein Symbol.

    Stop = höchster Kurs seit Einstieg - ATR(CHANDELIER_PERIOD) * CHANDELIER_MULT.
    Holt dafür kurze Tageshistorie (2 Monate reichen für ATR-22). Gibt None zurück,
    falls keine ausreichenden Daten verfügbar sind (dann bleibt der bisherige Stop aktiv).
    """
    try:
        hist = stock_quotes.fetch_completed_history(symbol, tick)
        if len(hist["closes"]) < config.CHANDELIER_PERIOD:
            return None
        highs, lows, closes = (hist[key] for key in ("highs", "lows", "closes"))
        if not all(_positive(v) for series in (highs, lows, closes) for v in series):
            return None
        atr_vals = indicators.atr(highs, lows, closes, config.CHANDELIER_PERIOD)
        latest_atr = next((a for a in reversed(atr_vals) if a is not None), None)
        if not _positive(latest_atr):
            return None
        return highest_price - latest_atr * config.CHANDELIER_MULT
    except Exception:
        return None


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def effective_stop(pos):
    """The strongest long stop wins; a lower trailing stop never loosens SL."""
    sl, trail = pos.get("stop_loss"), pos.get("trailing_stop")
    if _positive(trail) and (not _positive(sl) or trail >= sl):
        return trail, "trailing"
    if _positive(sl):
        return sl, "breakeven" if pos.get("breakeven_set") else "stop_loss"
    return None, None


def stock_rules():
    return dict(version="stock_trailing_monitor_v1", chandelier_enabled=config.USE_CHANDELIER_EXIT,
                atr_period=config.CHANDELIER_PERIOD, atr_multiple=config.CHANDELIER_MULT,
                trailing_pct=config.TRAILING_STOP_PCT, fixed_activation_pct=25,
                breakeven_at_pct=config.BREAKEVEN_AT_PCT, time_exit_days=config.TIME_EXIT_DAYS,
                tp_policy="always_active")


def portfolio_snapshot(user_id):
    """Display stored observations without sending messages, trading or saving."""
    p = _load(user_id)
    total = _total_value(p, {})
    p["total_value"] = round(total, 2)
    p["total_return_pct"] = round((total / config.START_CAPITAL - 1) * 100, 2)
    p["stock_rules"] = stock_rules()
    for pos in p.get("positions", []):
        pos["effective_stop_loss"], pos["effective_stop_kind"] = effective_stop(pos)
    return p


@serialized
def evaluate_portfolio(user_id: int) -> dict:
    started = time.time()
    print(f"[Portfolio] evaluate_portfolio started for user {user_id}", flush=True)
    p = _load(user_id)
    prices, quotes = {}, {}
    missing, closed = [], []
    positions = p.get("positions", [])
    print(f"[Portfolio] {len(positions)} open virtual positions", flush=True)
    for pos in positions:
        t0 = time.time()
        try:
            tick = stock_quotes.fetch_quote(pos["symbol"])
            quotes[pos["symbol"]] = tick
            price = tick.get("price")
            pos["quote_at"] = tick.get("market_at")
            pos["quote_checked_at"] = time.time()
            pos["quote_tradable"] = bool(tick.get("tradable"))
            pos["quote_status"] = "ok" if tick.get("tradable") else "market_closed" if not tick.get("market_open") else "unavailable"
            if not tick.get("market_open"):
                closed.append(pos["symbol"])
            elif not tick.get("tradable"):
                missing.append(pos["symbol"])
        except Exception:
            price = None
            pos["quote_tradable"] = False
            pos["quote_status"] = "unavailable"
            missing.append(pos["symbol"])
        print(f"[Portfolio] price fetch {pos['symbol']} took {round(time.time()-t0,2)}s -> {price}", flush=True)
        if _positive(price):
            prices[pos["symbol"]] = price

    new_positions = []
    alerts = []

    def tradable_now(pos):
        tick = quotes.get(pos["symbol"], {})
        if stock_quotes.is_tradable(tick):
            return True
        pos["quote_tradable"] = False
        now = time.time()
        ended = tick.get("regular_end", float("inf")) <= now
        pos["quote_status"] = "market_closed" if ended or not tick.get("market_open") else "unavailable"
        bucket = closed if pos["quote_status"] == "market_closed" else missing
        if pos["symbol"] not in bucket:
            bucket.append(pos["symbol"])
        return False

    for pos in p.get("positions", []):
        symbol = pos["symbol"]
        price = prices.get(symbol)
        if price is None:
            new_positions.append(pos)
            continue

        pos["last_price"] = price
        entry = pos["entry_price"]
        shares = pos["shares"]
        current_value = shares * price
        invested = pos.get("invested", 0) or (shares * entry)
        unrealized_pct = ((current_value - invested) / invested * 100) if invested else 0.0
        pos["unrealized_pct"] = round(unrealized_pct, 2)
        pos["unrealized_eur"] = round(current_value - invested, 2)

        # Closed venues can supply last-close valuations, never synthetic fills.
        if not tradable_now(pos):
            new_positions.append(pos)
            continue

        # Honour already active protection before recalculating from a new quote.
        stop, kind = effective_stop(pos)
        tp = pos.get("take_profit")
        reason = None
        if stop is not None and price <= stop:
            reason = f"{'Trailing-Stop' if kind == 'trailing' else 'Stop-Loss'} {stop} € erreicht"
        elif _positive(tp) and price >= tp:
            reason = f"Take-Profit {tp} € erreicht"
        if reason:
            _sell_position_logic(p, pos, price, reason, auto=True)
            alerts.append({"type": "sell", "symbol": symbol, "msg": reason, "price": price})
            continue

        # Fetch slow ATR data BEFORE any protection mutation; another quote or
        # this request may cross the session end or freshness limit.
        highest = max(pos.get("highest_price", entry), price)
        new_trailing = _chandelier_stop(symbol, highest, quotes[symbol]) if config.USE_CHANDELIER_EXIT else None
        if not tradable_now(pos):
            new_positions.append(pos)
            continue
        previous_effective, _ = effective_stop(pos)

        # Breakeven-Stop: ab +4 % Gewinn Stop-Loss auf Einstiegskurs anheben
        be_at = getattr(config, "BREAKEVEN_AT_PCT", 4.0)
        if unrealized_pct >= be_at and pos.get("stop_loss") and pos["stop_loss"] < entry and not pos.get("breakeven_set"):
            pos["stop_loss"] = round(entry, 4)
            pos["breakeven_set"] = True
            alerts.append({"type": "info", "symbol": symbol, "msg": f"Breakeven-Stop: Stop-Loss auf Einstieg {round(entry, 2)} € angehoben"})

        # Time-Exit: nach N Handelstagen ohne nennenswerten Gewinn schließen
        time_exit_days = getattr(config, "TIME_EXIT_DAYS", 0)
        if time_exit_days and pos.get("opened_at") and unrealized_pct < 1.0:
            try:
                opened = datetime.fromisoformat(pos["opened_at"])
                if opened.tzinfo is None:
                    opened = opened.replace(tzinfo=timezone.utc)
                cal_days = (datetime.now(timezone.utc) - opened).days
                # ~10 Handelstage entsprechen ~14 Kalendertagen
                if cal_days >= round(time_exit_days * 1.4):
                    _sell_position_logic(p, pos, price, f"Time-Exit: {cal_days} Tage ohne Gewinn (<+1 %)", auto=True)
                    alerts.append({"type": "sell", "symbol": symbol, "msg": f"Time-Exit nach {cal_days} Tagen ohne Gewinn", "price": price})
                    continue
            except Exception:
                pass

        # Trailing-Stop: Chandelier Exit (ATR-basiert, Standard) oder fester Prozentsatz
        pos["highest_price"] = highest

        if getattr(config, "USE_CHANDELIER_EXIT", False):
            # Aktiv ab Einstieg (nicht erst ab +25 %). Existing TP stays active.
            if _positive(new_trailing):
                old = pos.get("trailing_stop")
                if old is None or new_trailing > old:
                    pos["trailing_stop"] = round(new_trailing, 2)
                    pos["trailing_method"] = "chandelier"
                    msg = f"Chandelier-Exit aktiv bei {pos['trailing_stop']} €" if old is None \
                        else f"Chandelier-Exit angehoben auf {pos['trailing_stop']} €"
                    alerts.append({"type": "info", "symbol": symbol, "msg": msg})
        else:
            if unrealized_pct >= 25 and not _positive(pos.get("trailing_stop")):
                pos["trailing_stop"] = round(price * (1 - config.TRAILING_STOP_PCT), 2)
                pos["trailing_method"] = "fixed_pct"
                alerts.append({"type": "info", "symbol": symbol, "msg": f"Trailing-Stop aktiviert bei {pos['trailing_stop']} €"})

            if _positive(pos.get("trailing_stop")):
                new_trailing = round(pos["highest_price"] * (1 - config.TRAILING_STOP_PCT), 2)
                if new_trailing > pos["trailing_stop"]:
                    pos["trailing_stop"] = new_trailing
                    alerts.append({"type": "info", "symbol": symbol, "msg": f"Trailing-Stop angehoben auf {new_trailing} €"})

        effective, kind = effective_stop(pos)
        pos["effective_stop_loss"], pos["effective_stop_kind"] = effective, kind
        if effective is not None and (previous_effective is None or effective > previous_effective):
            pos["stop_last_raised_at"] = time.time()

        triggered = False
        reason = None
        tp = pos.get("take_profit")

        if effective is not None and price <= effective:
            triggered = True; reason = f"{'Trailing-Stop' if kind == 'trailing' else 'Stop-Loss'} {effective} € erreicht"
        elif _positive(tp) and price >= tp:
            triggered = True; reason = f"Take-Profit {tp} € erreicht"

        if triggered:
            _sell_position_logic(p, pos, price, reason, auto=True)
            alerts.append({"type": "sell", "symbol": symbol, "msg": reason, "price": price})
        else:
            new_positions.append(pos)

    p["positions"] = new_positions

    total = _total_value(p, prices)
    # Preserve every existing point. Sample future history at most every5min,
    # plus exits; do not truncate365 observations on each refresh.
    history = p.setdefault("value_history", [])
    try:
        last_date = datetime.fromisoformat(history[-1]["date"])
        if last_date.tzinfo is None:
            last_date = last_date.replace(tzinfo=timezone.utc)
        last_sample = last_date.timestamp()
    except (ValueError, KeyError, IndexError, TypeError):
        last_sample = 0
    if time.time() - last_sample >= 300 or any(a["type"] == "sell" for a in alerts):
        history.append({"date": datetime.utcnow().isoformat(), "value": round(total, 2)})

    p["total_value"] = round(total, 2)
    p["total_return_pct"] = round((total - config.START_CAPITAL) / config.START_CAPITAL * 100, 2)
    p.update(stock_monitor_attempt_at=started, stock_monitor_success_at=time.time(),
             stock_monitor_missing_symbols=missing, stock_monitor_closed_symbols=closed,
             stock_rules=stock_rules())
    _save(user_id, p, background=True)
    elapsed = round(time.time()-started, 2)
    print(f"[Portfolio] evaluate_portfolio finished in {elapsed}s", flush=True)
    p["_diag"] = {
        "eval_time_seconds": elapsed,
        "yahoo_fetches": yahoo_client.get_fetch_stats(),
        "open_positions_count": len(positions),
        "fetched_prices": prices,
    }
    return p, alerts


def enrich_real_positions(p: dict) -> dict:
    """Holt aktuelle Kurse für echte Positionen und berechnet P&L."""
    for pos in p.get("real_positions", []):
        price = yahoo_client.fetch_latest_price(pos["symbol"])
        if price:
            pos["last_price"] = round(price, 4)
            pos["current_value"] = round(pos["shares"] * price, 2)
            pos["unrealized_eur"] = round(pos["current_value"] - pos.get("invested", 0), 2)
            pos["unrealized_pct"] = round(pos["unrealized_eur"] / pos.get("invested", 1) * 100, 2) if pos.get("invested") else 0.0
        else:
            pos["last_price"] = pos.get("entry_price")
            pos["current_value"] = pos.get("invested", 0)
            pos["unrealized_eur"] = 0.0
            pos["unrealized_pct"] = 0.0
    return p


def _sell_position_logic(portfolio: dict, pos: dict, price: float, reason: str, auto: bool):
    proceeds = pos["shares"] * price
    portfolio["cash"] += proceeds
    portfolio["trades"].append({
        "time": datetime.utcnow().isoformat(),
        "symbol": pos["symbol"],
        "action": "SELL",
        "shares": pos["shares"],
        "price": round(price, 4),
        "proceeds": round(proceeds, 2),
        "invested": pos.get("invested", 0),
        "pnl_eur": round(proceeds - pos.get("invested", 0), 2),
        "reason": reason,
        "auto": auto,
        "opened_at": pos.get("opened_at"),
        "stop_loss": pos.get("stop_loss"), "trailing_stop": pos.get("trailing_stop"),
        "effective_stop_loss": effective_stop(pos)[0], "take_profit": pos.get("take_profit"),
        "quote_at": pos.get("quote_at"), "exit_rules_version": "stock_trailing_monitor_v1",
    })


@serialized
def buy(user_id: int, symbol: str, price: Optional[float] = None, amount_eur: Optional[float] = None, notify: bool = True) -> dict:
    p = _load(user_id)
    total_value = _total_value(p, {})
    max_per_position = total_value * config.MAX_POSITION_PCT
    available_for_trade = p["cash"] - (total_value * config.CASH_RESERVE_PCT)

    if amount_eur is None:
        amount_eur = min(max_per_position, available_for_trade)

    if amount_eur < config.MIN_POSITION_EUR:
        return {"ok": False, "error": f"Kaufbetrag zu niedrig (< €{config.MIN_POSITION_EUR:.0f}) oder Cash-Reserve überschritten"}

    if len(p.get("positions", [])) >= config.MAX_POSITIONS:
        return {"ok": False, "error": "Maximale Anzahl Positionen erreicht"}

    if price is None:
        try:
            tick = stock_quotes.fetch_quote(symbol)
            price = tick.get("price") if tick.get("tradable") else None
        except Exception:
            price = None
    if not _positive(price):
        return {"ok": False, "error": f"Kein gültiger Kurs für {symbol}"}

    amount_eur = min(amount_eur, p["cash"])
    if amount_eur < config.MIN_POSITION_EUR:
        return {"ok": False, "error": f"Nicht genug Cash für Mindestkauf (min. €{config.MIN_POSITION_EUR:.0f})"}

    shares = amount_eur / price
    invested = shares * price
    if invested < config.MIN_POSITION_EUR:
        return {"ok": False, "error": f"Investition zu gering (min. €{config.MIN_POSITION_EUR:.0f})"}

    stop_loss = round(price * (1 - config.DEFAULT_STOP_PCT), 2)
    take_profit = round(price + (price - stop_loss) * config.MIN_RR_RATIO, 2)

    existing = _current_position(p, symbol)
    if existing:
        old_invested = existing["invested"]
        old_shares = existing["shares"]
        total_shares = old_shares + shares
        avg_price = (old_invested + invested) / total_shares
        existing["shares"] = total_shares
        existing["entry_price"] = round(avg_price, 4)
        existing["invested"] = round(old_invested + invested, 2)
        old_stop = effective_stop(existing)[0] or 0
        existing["stop_loss"] = max(old_stop, round(avg_price * (1 - config.DEFAULT_STOP_PCT), 2))
        existing["take_profit"] = existing.get("take_profit") or take_profit
        updated_position = existing
    else:
        new_pos = {
            "symbol": symbol,
            "shares": round(shares, 6),
            "entry_price": round(price, 4),
            "invested": round(invested, 2),
            "last_price": round(price, 4),
            "stop_loss": stop_loss,
            "initial_stop_loss": stop_loss,
            "take_profit": take_profit,
            "opened_at": datetime.utcnow().isoformat(),
            "unrealized_pct": 0.0,
            "unrealized_eur": 0.0,
        }
        p["positions"].append(new_pos)
        updated_position = new_pos

    p["cash"] -= invested
    p["trades"].append({
        "time": datetime.utcnow().isoformat(),
        "symbol": symbol,
        "action": "BUY",
        "shares": round(shares, 6),
        "price": round(price, 4),
        "invested": round(invested, 2),
        "stop_loss": stop_loss,
        "take_profit": take_profit,
    })
    _save(user_id, p)
    if notify:
        try:
            telegram.notify_virtual_trade("BUY", symbol, shares, price)
        except Exception:
            pass
    return {"ok": True, "position": updated_position, "cash": round(p["cash"], 2)}


@serialized
def sell(user_id: int, symbol: str, price: Optional[float] = None, shares: Optional[float] = None, notify: bool = True) -> dict:
    p = _load(user_id)
    pos = _current_position(p, symbol)
    if not pos:
        return {"ok": False, "error": f"{symbol} nicht im Depot"}

    if price is None:
        try:
            tick = stock_quotes.fetch_quote(symbol)
            price = tick.get("price") if tick.get("tradable") else None
        except Exception:
            price = None
    if not _positive(price):
        return {"ok": False, "error": f"Kein gültiger Kurs für {symbol}"}

    sell_shares = shares if shares is not None else pos["shares"]
    if sell_shares > pos["shares"]:
        sell_shares = pos["shares"]

    proceeds = sell_shares * price
    cost_basis = (sell_shares / pos["shares"]) * pos["invested"]

    p["cash"] += proceeds
    p["trades"].append({
        "time": datetime.utcnow().isoformat(),
        "symbol": symbol,
        "action": "SELL",
        "shares": round(sell_shares, 6),
        "price": round(price, 4),
        "proceeds": round(proceeds, 2),
        "invested": round(cost_basis, 2),
        "pnl_eur": round(proceeds - cost_basis, 2),
        "reason": "manuell",
        "auto": False,
    })

    if sell_shares >= pos["shares"]:
        p["positions"] = [x for x in p["positions"] if x["symbol"] != symbol]
    else:
        ratio = 1 - sell_shares / pos["shares"]
        pos["shares"] -= sell_shares
        pos["invested"] *= ratio
        pos["shares"] = round(pos["shares"], 6)
        pos["invested"] = round(pos["invested"], 2)

    _save(user_id, p)
    if notify:
        try:
            telegram.notify_virtual_trade("SELL", symbol, sell_shares, price, reason="manuell", profit=round(proceeds - cost_basis, 2))
        except Exception:
            pass
    return {"ok": True, "cash": round(p["cash"], 2)}


# --- Vergleich & Backtest Hilfsfunktionen ---

def calculate_comparison(p: dict) -> dict:
    """Berechnet Kennzahlen für virtuell vs. reales Depot."""
    virt_positions = p.get("positions", [])
    real_positions = p.get("real_positions", [])
    virt_trades = [t for t in p.get("trades", []) if t.get("action") in ("BUY", "SELL")]
    real_trades = [t for t in p.get("real_trades", []) if t.get("action") in ("BUY", "SELL")]

    virt_invested = sum(pos.get("invested", 0) for pos in virt_positions)
    real_invested = sum(pos.get("invested", 0) for pos in real_positions)
    virt_value = sum(pos.get("shares", 0) * pos.get("last_price", pos.get("entry_price", 0)) for pos in virt_positions)
    real_value = sum(pos.get("current_value", 0) for pos in real_positions)

    virt_closed_sells = [t for t in virt_trades if t.get("action") == "SELL"]
    real_closed_sells = [t for t in real_trades if t.get("action") == "SELL"]
    virt_realized = sum(t.get("pnl_eur", (t.get("proceeds", 0) - t.get("invested", 0))) for t in virt_closed_sells)
    real_realized = sum((t.get("shares", 0) * t.get("price", 0)) - (t.get("invested", 0)) for t in real_closed_sells)

    virt_unrealized = virt_value - virt_invested
    real_unrealized = real_value - real_invested

    return {
        "virtual": {
            "open_positions": len(virt_positions),
            "invested": round(virt_invested, 2),
            "current_value": round(virt_value, 2),
            "unrealized": round(virt_unrealized, 2),
            "realized": round(virt_realized, 2),
            "total_return": round(virt_unrealized + virt_realized, 2),
            "trades_count": len(virt_trades),
        },
        "real": {
            "open_positions": len(real_positions),
            "invested": round(real_invested, 2),
            "current_value": round(real_value, 2),
            "unrealized": round(real_unrealized, 2),
            "realized": round(real_realized, 2),
            "total_return": round(real_unrealized + real_realized, 2),
            "trades_count": len(real_trades),
        },
        "diff": {
            "total_return": round((virt_unrealized + virt_realized) - (real_unrealized + real_realized), 2),
        },
    }


def run_backtest(p: dict) -> dict:
    """Einfaches internes Backtesting: Wie hätte die aktuelle Strategie historisch performt?

    Wir vergleichen alle abgeschlossenen virtuellen Trades mit einem simplen
    Buy-&-Hold-Gegenwert über die gleiche Haltedauer.
    """
    trades = p.get("trades", [])
    buy_events = [t for t in trades if t.get("action") == "BUY"]
    sell_events = [t for t in trades if t.get("action") == "SELL"]

    completed_pairs = []
    strategy_pnl = 0.0
    buy_hold_pnl = 0.0

    for sell in sell_events:
        symbol = sell.get("symbol")
        matching_buys = [b for b in buy_events if b.get("symbol") == symbol and b.get("time", "") < sell.get("time", "")]
        if not matching_buys:
            continue
        buy = matching_buys[-1]
        sell_price = sell.get("price", 0)
        buy_price = buy.get("price", 0)
        shares = min(sell.get("shares", 0), buy.get("shares", 0))
        if shares <= 0 or buy_price <= 0:
            continue

        period_return_pct = ((sell_price - buy_price) / buy_price) * 100
        # Approximation Buy-and-Hold über gleiche Haltedauer: was hätte ein S&P500-ETF gemacht?
        # Wir nehmen einen angenommenen Markt-Return von 8 % p.a. / 252 Handelstage pro Tag.
        try:
            from datetime import datetime
            days_held = max(1, (datetime.fromisoformat(sell["time"]) - datetime.fromisoformat(buy["time"])).days)
        except Exception:
            days_held = 30
        market_daily_return = 0.08 / 252
        market_period_return_pct = market_daily_return * days_held * 100

        pair_pnl = shares * (sell_price - buy_price)
        pair_buy_hold = shares * buy_price * market_period_return_pct / 100
        strategy_pnl += pair_pnl
        buy_hold_pnl += pair_buy_hold

        strategy_pnl_pct = ((sell_price - buy_price) / buy_price * 100) if buy_price else 0.0
        completed_pairs.append({
            "symbol": symbol,
            "buy_price": round(buy_price, 4),
            "sell_price": round(sell_price, 4),
            "shares": round(shares, 4),
            "days_held": days_held,
            "strategy_pnl": round(pair_pnl, 2),
            "strategy_pnl_pct": round(strategy_pnl_pct, 2),
            "buyhold_pnl": round(pair_buy_hold, 2),
        })

    alpha = round(strategy_pnl - buy_hold_pnl, 2)

    # Verbesserungs-Vorschläge generieren
    improvements = []
    win_trades = [x for x in completed_pairs if x["strategy_pnl"] > 0]
    loss_trades = [x for x in completed_pairs if x["strategy_pnl"] <= 0]
    win_rate = round(len(win_trades) / len(completed_pairs) * 100, 1) if completed_pairs else 0.0

    if win_rate < 50 and completed_pairs:
        improvements.append(f"Win-Rate nur {win_rate} % – Stop-Loss enger oder Filter für Einstieg verschärfen.")
    if len(loss_trades) > len(win_trades):
        avg_loss = sum(t["strategy_pnl"] for t in loss_trades) / max(len(loss_trades), 1)
        improvements.append(f"Durchschnittsverlust {fmtEur(avg_loss)} pro Trade – Risiko pro Trade reduzieren.")
    if alpha < 0:
        improvements.append(f"Strategie lag {fmtEur(abs(alpha))} hinter Buy-&-Hold zurück – Haltezeiten / Take-Profit anpassen.")
    # Trailing-Stop-Hinweis nur sinnvoll: es gab starke Gewinner (>25 %), aber
    # bisher wurde bei keinem abgeschlossenen Trade ein Trailing-Stop genutzt.
    if completed_pairs:
        big_winners = [t for t in win_trades if t.get("strategy_pnl_pct", 0) > 25]
        has_active_trailing = any(pos.get("trailing_stop") for pos in p.get("positions", []))
        if len(big_winners) >= 1 and not has_active_trailing:
            improvements.append("Trailing-Stop wurde bei großen Gewinnern (>25 %) noch nicht genutzt – bei >+25 % Gewinn automatisch aktivieren.")
    if not improvements and completed_pairs:
        improvements.append("Strategie performt gut. Fokus auf Disziplin und Einhaltung der Regeln.")
    if not completed_pairs:
        improvements.append("Noch nicht genug abgeschlossene virtuelle Trades für Backtesting. Mindestens einen Kauf und Verkauf durchführen.")

    return {
        "completed_trades": len(completed_pairs),
        "strategy_pnl": round(strategy_pnl, 2),
        "buyhold_pnl": round(buy_hold_pnl, 2),
        "alpha": alpha,
        "win_rate": win_rate,
        "details": completed_pairs,
        "improvements": improvements,
    }


def fmtEur(n) -> str:
    return f"€{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
