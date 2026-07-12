"""Virtuelles Krypto-Depot: Hebel-Positionen (long/short), Liquidation-Simulation, P&L.

Komplett getrennt vom Aktien-Portfolio. Nutzt OKX Live-Preise (Spot-Ticker als
Referenzkurs für die Perp-Simulation). Liquidation wird nach Standard-Margin-Formel
simuliert: bei isolierter Margin liquidiert eine Position, wenn der Verlust die
gesamte eingesetzte Margin aufzehrt (abzgl. Sicherheitspuffer).
"""
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import config
from analyzer import db_store, okx_client


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


def open_position(user_id: int, symbol: str, direction: str, margin_eur: float,
                   leverage: int, entry_price: float, stop_loss: float, take_profit: float,
                   reason: str = "") -> dict:
    p = get_crypto_portfolio(user_id)
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
    p["positions"].append(pos)
    db_store.save_crypto_portfolio(user_id, p)
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


def evaluate_crypto_portfolio(user_id: int) -> dict:
    """Bewertet alle offenen Positionen mit Live-Preisen: SL/TP/Liquidation pruefen.
    Enthaelt einen Totalverlust-Schutz (Circuit Breaker): bei zu hohem Drawdown werden
    neue Trades gesperrt bzw. bei kritischem Drawdown alle offenen Positionen notfallgeschlossen."""
    p = get_crypto_portfolio(user_id)
    positions = p.get("positions", [])
    remaining = []
    events = []

    for pos in positions:
        ticker = okx_client.fetch_ticker(pos["symbol"])
        if not ticker:
            remaining.append(pos)
            continue
        price = ticker["last"]
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

    if drawdown_pct >= config.CRYPTO_CRITICAL_DRAWDOWN_PCT:
        # Kritischer Drawdown: alle verbleibenden Positionen notfallschliessen
        if p["positions"]:
            for pos in list(p["positions"]):
                ticker = okx_client.fetch_ticker(pos["symbol"])
                close_price = ticker["last"] if ticker else pos.get("last_price", pos["entry_price"])
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
    elif drawdown_pct >= config.CRYPTO_MAX_DRAWDOWN_PCT:
        p["trading_halted"] = True
        if not was_halted:
            events.append({"type": "circuit_breaker", "symbol": "DEPOT",
                            "msg": f"⚠️ Krypto-Handel PAUSIERT: Depot bei {drawdown_pct}% Drawdown (Schwelle {config.CRYPTO_MAX_DRAWDOWN_PCT}%). Keine neuen Positionen, bestehende laufen normal weiter.",
                            "trade": None})
    else:
        p["trading_halted"] = False

    # Nach Notfallschliessung Depotwert neu berechnen
    if drawdown_pct >= config.CRYPTO_CRITICAL_DRAWDOWN_PCT:
        total_value = p["cash"]
        p["total_value"] = round(total_value, 2)
        p["total_return_pct"] = round((total_value - start_capital) / start_capital * 100, 2)

    p.setdefault("value_history", []).append({"date": _now(), "value": total_value})
    if len(p["value_history"]) > 2000:
        p["value_history"] = p["value_history"][-2000:]

    db_store.save_crypto_portfolio(user_id, p)
    return {"portfolio": p, "events": events}
