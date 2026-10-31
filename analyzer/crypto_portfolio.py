"""Virtuelles Krypto-Depot: Hebel-Positionen (long/short), Liquidation-Simulation, P&L.

Komplett getrennt vom Aktien-Portfolio. Nutzt OKX Live-Preise (Spot-Ticker als
Referenzkurs für die Perp-Simulation). Liquidation wird nach Standard-Margin-Formel
simuliert: bei isolierter Margin liquidiert eine Position, wenn der Verlust die
gesamte eingesetzte Margin aufzehrt (abzgl. Sicherheitspuffer).
"""
import time
import math
from datetime import datetime, timezone
from typing import Dict, List, Optional

import config
from analyzer import db_store, okx_client
from analyzer.crypto_runtime import serialized


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_crypto_portfolio(user_id: int) -> dict:
    p = db_store.load_crypto_portfolio(user_id)
    if p:
        p.setdefault("positions", [])
        p.setdefault("trades", [])
        p.setdefault("value_history", [])
        p.setdefault("start_capital", config.CRYPTO_START_CAPITAL)
        return p
    return db_store._default_crypto_portfolio()


@serialized
def reset_crypto_portfolio(user_id: int) -> dict:
    p = db_store._default_crypto_portfolio()
    db_store.save_crypto_portfolio(user_id, p)
    return p


def _liquidation_price(entry: float, leverage: int, direction: str, buffer_pct: float) -> float:
    """Preis, bei dem die Margin (abzgl. Sicherheitspuffer) aufgezehrt ist.
    Bei Hebel L wird die Position liquidiert, wenn sich der Preis um ~1/L (abzgl. Puffer)
    gegen die Position bewegt (vereinfachte isolierte Margin-Formel, keine Fees)."""
    if leverage <= 0:
        leverage = 1
    move_pct = (1.0 / leverage) * (1 - buffer_pct)
    if direction == "LONG":
        return round(entry * (1 - move_pct), 6)
    else:
        return round(entry * (1 + move_pct), 6)


@serialized
def open_position(user_id: int, symbol: str, direction: str, margin_eur: float,
                   leverage: int, entry_price: float, stop_loss: float, take_profit: float,
                   reason: str = "", *, paper_metadata: Optional[dict] = None) -> dict:
    if paper_metadata is not None:
        allowed = {"paper_policy_version", "paper_policy_rule_hash", "planned_risk_eur",
                   "estimated_entry_cost_eur", "estimated_exit_cost_eur", "signal_bar_end",
                   "entry_quote_at", "signal_price", "signal_strategy_version", "risk_equity_eur"}
        extra = {"signal_data_version", "pressure_oldest_at", "pressure_latest_at",
                 "pressure_trade_count", "pressure_buy_ratio"}
        if (user_id != 1 or not isinstance(paper_metadata, dict)
                or not allowed <= set(paper_metadata) or set(paper_metadata) - allowed - extra):
            return {"ok": False, "error": "Ungültige Papertrade-Metadaten"}
        pressure_fields = extra - {"signal_data_version"}
        if ((set(paper_metadata) & pressure_fields and not pressure_fields <= set(paper_metadata))
                or (set(paper_metadata) & pressure_fields and paper_metadata.get("signal_data_version") != "fresh_trade_pressure_v1")):
            return {"ok": False, "error": "Ungültige Papertrade-Metadaten"}
        for name, value in paper_metadata.items():
            if name == "pressure_buy_ratio":
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                    return {"ok": False, "error": "Ungültige Papertrade-Metadaten"}
            elif name in {"paper_policy_version", "paper_policy_rule_hash", "signal_strategy_version", "signal_data_version"}:
                if not isinstance(value, str) or not value or len(value) > 128:
                    return {"ok": False, "error": "Ungültige Papertrade-Metadaten"}
            elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                return {"ok": False, "error": "Ungültige Papertrade-Metadaten"}
        if pressure_fields <= set(paper_metadata):
            if (paper_metadata["pressure_trade_count"] != int(paper_metadata["pressure_trade_count"])
                    or paper_metadata["pressure_oldest_at"] > paper_metadata["pressure_latest_at"]):
                return {"ok": False, "error": "Ungültige Papertrade-Metadaten"}
    p = get_crypto_portfolio(user_id)
    if any(pos["symbol"] == symbol.upper() for pos in p["positions"]):
        return {"ok": False, "error": "Krypto-Position auf diesem Symbol bereits offen"}
    leverage = max(1, min(int(leverage), config.CRYPTO_MAX_LEVERAGE))
    margin_eur = min(margin_eur, p["cash"])
    if margin_eur < config.CRYPTO_MIN_POSITION_EUR:
        return {"ok": False, "error": f"Margin zu klein (min {config.CRYPTO_MIN_POSITION_EUR} EUR)"}
    if len(p["positions"]) >= config.CRYPTO_MAX_POSITIONS:
        return {"ok": False, "error": "Maximale Anzahl offener Krypto-Positionen erreicht"}

    notional = margin_eur * leverage
    size = notional / entry_price  # Menge in Coin

    liq_price = _liquidation_price(entry_price, leverage, direction, config.CRYPTO_LIQUIDATION_BUFFER_PCT)
    sl_dist = abs(entry_price - stop_loss) if stop_loss else entry_price * config.CRYPTO_DEFAULT_STOP_PCT

    pos = {
        "id": f"{symbol}_{int(time.time()*1000)}",
        "symbol": symbol.upper(),
        "direction": direction,  # LONG oder SHORT
        "leverage": leverage,
        "margin_eur": round(margin_eur, 2),
        "notional_eur": round(notional, 2),
        "size": size,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "initial_sl_dist": sl_dist,     # fuer Trailing-Stop-Berechnung, aendert sich nicht mehr
        "take_profit": take_profit,
        "liquidation_price": liq_price,
        "opened_at": _now(),
        "opened_at_ts": time.time(),
        "reason": reason,
        "last_price": entry_price,
        "unrealized_pct": 0.0,
        "unrealized_eur": 0.0,
        "trailing_active": False,
    }
    p["cash"] = round(p["cash"] - margin_eur, 2)
    if paper_metadata is not None:
        pos.update(paper_metadata)
        p.setdefault("paper_policy_quote_times", {})[symbol.upper()] = paper_metadata["entry_quote_at"]
    p["positions"].append(pos)
    db_store.save_crypto_portfolio(user_id, p, backup=paper_metadata is None)
    return {"ok": True, "position": pos}


def _pnl_pct(direction: str, entry: float, current: float) -> float:
    if direction == "LONG":
        return (current - entry) / entry * 100
    else:
        return (entry - current) / entry * 100


def _close_position(p: dict, pos: dict, price: float, reason: str, liquidated: bool = False):
    direction = pos["direction"]
    price_move_pct = _pnl_pct(direction, pos["entry_price"], price) / 100.0
    pnl_eur = pos["margin_eur"] * pos["leverage"] * price_move_pct
    # Bei Liquidation wird die gesamte Margin verloren (kein negativer Rest, keine Nachschusspflicht)
    if liquidated or pnl_eur <= -pos["margin_eur"]:
        pnl_eur = -pos["margin_eur"]
    payout = round(pos["margin_eur"] + pnl_eur, 2)
    p["cash"] = round(p["cash"] + max(payout, 0), 2)

    trade = {
        **pos,
        "exit_price": price,
        "closed_at": _now(),
        "pnl_eur": round(pnl_eur, 2),
        "pnl_pct": round(price_move_pct * 100 * pos["leverage"], 2),  # PnL relativ zur Margin (gehebelt)
        "close_reason": reason,
        "liquidated": liquidated,
    }
    p.setdefault("trades", []).append(trade)
    return trade


def _owner_quote_time(pos: dict, ticker: dict, now: float):
    """Validate the actual exchange time before owner-1 marks or exits.

    A local fetch/cache timestamp is never evidence of a fresh market price.
    Existing malformed timestamps fail closed; ISO fallback is only for an
    absent legacy opened_at_ts and must contain an explicit timezone.
    """
    def positive(value):
        return (not isinstance(value, bool) and isinstance(value, (int, float))
                and math.isfinite(value) and value > 0)

    if not positive(now):
        return None, "invalid_clock"
    if not isinstance(ticker, dict):
        return None, "missing_quote"
    if "market_timestamp" not in ticker or ticker["market_timestamp"] is None:
        return None, "missing_timestamp"
    stamp = ticker["market_timestamp"]
    if not positive(stamp):
        return None, "invalid_market_timestamp"
    if stamp < now - 60:
        return None, "stale_quote"
    if stamp > now + 5:
        return None, "future_quote"
    if "opened_at_ts" in pos:
        opened = pos["opened_at_ts"]
        if not positive(opened):
            return None, "invalid_entry_time"
    else:
        raw = pos.get("opened_at")
        if not isinstance(raw, str) or not raw:
            return None, "missing_entry_time"
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return None, "invalid_entry_time"
            opened = parsed.timestamp()
        except (ValueError, TypeError, OverflowError, OSError):
            return None, "invalid_entry_time"
        if not positive(opened):
            return None, "invalid_entry_time"
    if stamp < opened:
        return None, "before_entry"
    if "entry_quote_at" in pos:
        if not positive(pos["entry_quote_at"]):
            return None, "invalid_entry_quote_time"
        if stamp < pos["entry_quote_at"]:
            return None, "before_entry"
    if "last_quote_at" in pos:
        if not positive(pos["last_quote_at"]):
            return None, "invalid_previous_quote_time"
        if stamp < pos["last_quote_at"]:
            return None, "time_regression"
    return stamp, None


@serialized
def evaluate_crypto_portfolio(user_id: int, *, background: bool = False) -> dict:
    """Bewertet alle offenen Positionen mit Live-Preisen: SL/TP/Liquidation pruefen.
    Enthaelt einen Totalverlust-Schutz (Circuit Breaker): bei zu hohem Drawdown werden
    neue Trades gesperrt bzw. bei kritischem Drawdown alle offenen Positionen notfallgeschlossen."""
    p = get_crypto_portfolio(user_id)
    positions = p.get("positions", [])
    remaining = []
    events = []
    missing_symbols = []
    quote_errors = {}
    checked_quotes = {}

    for pos in positions:
        original = pos
        ticker = okx_client.fetch_ticker(pos["symbol"])
        price = ((ticker if isinstance(ticker, dict) else {}) if user_id == 1 else (ticker or {})).get("last")
        if (not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0
                or (user_id == 1 and isinstance(price, bool))):
            missing_symbols.append(pos["symbol"])
            if user_id == 1:
                quote_errors[pos["symbol"]] = "missing_quote" if ticker is None else "invalid_price"
            remaining.append(pos)
            continue
        if user_id == 1:
            quote_at, error = _owner_quote_time(pos, ticker, time.time())
            if error:
                missing_symbols.append(pos["symbol"])
                quote_errors[pos["symbol"]] = error
                remaining.append(pos)
                continue
            # Work on a copy so a quote expiring during this check cannot leave
            # a changed mark or trailing stop behind when the final guard fails.
            pos = dict(pos)
            checked_quotes[pos["symbol"]] = ticker
        pos["last_price"] = price
        direction = pos["direction"]
        pnl_pct_leveraged = _pnl_pct(direction, pos["entry_price"], price) / 100.0 * pos["leverage"] * 100
        pos["unrealized_pct"] = round(pnl_pct_leveraged, 2)
        pos["unrealized_eur"] = round(pos["margin_eur"] * (pnl_pct_leveraged / 100.0), 2)

        # Trailing-Stop: ab Aktivierungsschwelle SL zugunsten der Position nachziehen (nie zurueck).
        # Sichert gehebelte Gewinne aktiv, statt nur auf ein fixes Take-Profit zu warten.
        if config.CRYPTO_USE_TRAILING_STOP and pnl_pct_leveraged >= config.CRYPTO_TRAILING_ACTIVATE_PCT:
            trail_dist = pos.get("initial_sl_dist", price * config.CRYPTO_DEFAULT_STOP_PCT) * config.CRYPTO_TRAILING_TIGHTEN_FACTOR
            if direction == "LONG":
                new_sl = price - trail_dist
                if pos.get("stop_loss") is None or new_sl > pos["stop_loss"]:
                    pos["stop_loss"] = round(new_sl, 6)
                    pos["trailing_active"] = True
            else:
                new_sl = price + trail_dist
                if pos.get("stop_loss") is None or new_sl < pos["stop_loss"]:
                    pos["stop_loss"] = round(new_sl, 6)
                    pos["trailing_active"] = True

        triggered = False
        reason = None
        liquidated = False

        # Liquidation hat Vorrang
        if direction == "LONG" and price <= pos["liquidation_price"]:
            triggered, reason, liquidated = True, f"LIQUIDATION bei {round(price,4)}", True
        elif direction == "SHORT" and price >= pos["liquidation_price"]:
            triggered, reason, liquidated = True, f"LIQUIDATION bei {round(price,4)}", True
        elif pos.get("stop_loss") and (
            (direction == "LONG" and price <= pos["stop_loss"]) or
            (direction == "SHORT" and price >= pos["stop_loss"])
        ):
            sl_label = "Trailing-Stop" if pos.get("trailing_active") else "Stop-Loss"
            triggered, reason = True, f"{sl_label} {pos['stop_loss']} erreicht"
        elif pos.get("take_profit") and (
            (direction == "LONG" and price >= pos["take_profit"]) or
            (direction == "SHORT" and price <= pos["take_profit"])
        ):
            triggered, reason = True, f"Take-Profit {pos['take_profit']} erreicht"
        elif (
            (time.time() - pos.get("opened_at_ts", time.time())) / 3600.0 >= config.CRYPTO_MAX_HOLD_HOURS
            and pnl_pct_leveraged < config.CRYPTO_TIME_EXIT_MIN_PROFIT_PCT
        ):
            # Zeit-Exit: verhindert totes Kapital in Hebel-Positionen ohne klare Bewegung
            hours_held = round((time.time() - pos.get("opened_at_ts", time.time())) / 3600.0, 1)
            triggered, reason = True, f"Zeit-Exit nach {hours_held}h ({config.CRYPTO_MAX_HOLD_HOURS}h Limit, Gewinn {round(pnl_pct_leveraged,1)}% < {config.CRYPTO_TIME_EXIT_MIN_PROFIT_PCT}%)"

        if user_id == 1:
            quote_at, error = _owner_quote_time(original, ticker, time.time())
            if error:
                missing_symbols.append(pos["symbol"])
                quote_errors[pos["symbol"]] = error
                remaining.append(original)
                continue
            pos["last_quote_at"] = quote_at
        if triggered:
            trade = _close_position(p, pos, price, reason, liquidated=liquidated)
            events.append({"type": "liquidation" if liquidated else "close", "symbol": pos["symbol"],
                            "msg": reason, "trade": trade})
        else:
            remaining.append(pos)

    p["positions"] = remaining
    total_margin_in_positions = sum(pos["margin_eur"] for pos in remaining)
    unrealized_total = sum(pos.get("unrealized_eur", 0) for pos in remaining)
    total_value = round(p["cash"] + total_margin_in_positions + unrealized_total, 2)
    p["total_value"] = total_value
    start_capital = p.get("start_capital", config.CRYPTO_START_CAPITAL)
    p["total_return_pct"] = round((total_value - start_capital) / start_capital * 100, 2)

    # --- Totalverlust-Schutz (Circuit Breaker) ---
    drawdown_pct = round((start_capital - total_value) / start_capital * 100, 2) if start_capital else 0
    p["drawdown_pct"] = max(drawdown_pct, 0)
    was_halted = p.get("trading_halted", False)

    if user_id == 1:
        # Later symbols can take time to fetch. Revalidate EVERY remaining quote
        # before reporting success or taking an emergency action. A previously
        # valid mark/exit is not rolled back; expiry now makes this cycle stale.
        # Never refetch or invent a fallback market timestamp.
        final_quote_now = time.time()
        for pos in p["positions"]:
            if pos["symbol"] in quote_errors:
                continue
            _, error = _owner_quote_time(pos, checked_quotes.get(pos["symbol"]), final_quote_now)
            if error:
                missing_symbols.append(pos["symbol"])
                quote_errors[pos["symbol"]] = error

    if not missing_symbols and drawdown_pct >= config.CRYPTO_CRITICAL_DRAWDOWN_PCT:
        # Kritischer Drawdown: alle verbleibenden Positionen notfallschliessen
        if p["positions"]:
            for pos in list(p["positions"]):
                # Reuse the valid quote already checked above; never invent an
                # emergency fill after a failed second market-data request.
                close_price = pos["last_price"]
                trade = _close_position(p, pos, close_price,
                                         f"NOTFALL-STOPP: Depot-Drawdown {drawdown_pct}% >= kritischer Schwelle {config.CRYPTO_CRITICAL_DRAWDOWN_PCT}%",
                                         liquidated=False)
                events.append({"type": "emergency_close", "symbol": pos["symbol"],
                                "msg": trade["close_reason"], "trade": trade})
            p["positions"] = []
        p["trading_halted"] = True
        if not was_halted:
            events.append({"type": "circuit_breaker_critical", "symbol": "DEPOT",
                            "msg": f"🚨 NOTFALL-STOPP: Krypto-Depot bei {drawdown_pct}% Drawdown. Alle Positionen geschlossen, Handel pausiert.",
                            "trade": None})
    elif not missing_symbols and drawdown_pct >= config.CRYPTO_MAX_DRAWDOWN_PCT:
        p["trading_halted"] = True
        if not was_halted:
            events.append({"type": "circuit_breaker", "symbol": "DEPOT",
                            "msg": f"⚠️ Krypto-Handel PAUSIERT: Depot bei {drawdown_pct}% Drawdown (Schwelle {config.CRYPTO_MAX_DRAWDOWN_PCT}%). Keine neuen Positionen, bestehende laufen normal weiter.",
                            "trade": None})
    elif not missing_symbols:
        p["trading_halted"] = False

    # Nach Notfallschliessung Depotwert neu berechnen
    if not missing_symbols and drawdown_pct >= config.CRYPTO_CRITICAL_DRAWDOWN_PCT:
        total_value = p["cash"]
        p["total_value"] = round(total_value, 2)
        p["total_return_pct"] = round((total_value - start_capital) / start_capital * 100, 2)

    # Keep existing history. Faster checks must not evict it from a ring buffer.
    now = time.time()
    history = p.setdefault("value_history", [])
    try:
        previous = datetime.fromisoformat(history[-1]["date"])
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=timezone.utc)
        last_sample = previous.timestamp()
    except (IndexError, KeyError, TypeError, ValueError):
        last_sample = 0
    if events or now - last_sample >= 300:
        history.append({"date": _now(), "value": total_value})
    if background:
        # A page refresh must not make a failed background watcher look healthy.
        p["monitor_attempt_at"] = now
        p["monitor_missing_symbols"] = missing_symbols
        if user_id == 1:
            p["monitor_quote_errors"] = quote_errors
            p["monitor_quote_policy"] = "fresh_exit_quotes_v1"
        if not missing_symbols:
            p["monitor_success_at"] = now

    # Do not run synchronous Git commit/push for each price tick. Durable storage
    # and separate regular backups are prerequisites for the server-side watcher.
    db_store.save_crypto_portfolio(user_id, p, backup=False)
    return {"portfolio": p, "events": events, "missing_symbols": missing_symbols,
            **({"quote_errors": quote_errors} if user_id == 1 else {})}
