"""Virtuelles Depot: Kaufen, Verkaufen, SL/TP, Bewertung."""
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import config
from analyzer import yahoo_client


PORTFOLIO_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "portfolio.json")


def _load() -> dict:
    try:
        with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return _default_portfolio()


def _save(p: dict):
    os.makedirs(os.path.dirname(PORTFOLIO_FILE), exist_ok=True)
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(p, f, indent=2, default=str)


def _default_portfolio():
    return {
        "cash": config.START_CAPITAL,
        "positions": [],
        "trades": [],
        "value_history": [{"date": datetime.utcnow().isoformat(), "value": config.START_CAPITAL}],
    }


def get_portfolio() -> dict:
    return _load()


def reset_portfolio():
    p = _default_portfolio()
    _save(p)
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


def evaluate_portfolio() -> dict:
    """Bewertet aktuelles Depot, prüft SL/TP, aktualisiert Trailing-Stops."""
    p = _load()
    prices = {}
    for pos in p.get("positions", []):
        price = yahoo_client.fetch_latest_price(pos["symbol"])
        if price:
            prices[pos["symbol"]] = price

    new_positions = []
    alerts = []

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
        invested = pos["invested"]
        unrealized_pct = (current_value - invested) / invested * 100
        pos["unrealized_pct"] = round(unrealized_pct, 2)
        pos["unrealized_eur"] = round(current_value - invested, 2)

        # Trailing-Stop hochziehen bei Gewinn
        if unrealized_pct >= 25 and "trailing_stop" not in pos:
            pos["trailing_stop"] = round(price * (1 - config.TRAILING_STOP_PCT), 2)
            alerts.append({"type": "info", "symbol": symbol, "msg": f"Trailing-Stop aktiviert bei {pos['trailing_stop']} €"})

        if "trailing_stop" in pos and price > pos.get("highest_price", entry) * (1 + config.TRAILING_STOP_PCT * 0.5):
            # Nur hochziehen, wenn Kurs merklich gestiegen
            pos["highest_price"] = max(pos.get("highest_price", entry), price)
            new_trailing = round(pos["highest_price"] * (1 - config.TRAILING_STOP_PCT), 2)
            if new_trailing > pos["trailing_stop"]:
                pos["trailing_stop"] = new_trailing
                alerts.append({"type": "info", "symbol": symbol, "msg": f"Trailing-Stop angehoben auf {new_trailing} €"})

        # Prüfe Stop-Loss
        triggered = False
        reason = None
        sl = pos.get("stop_loss")
        tp = pos.get("take_profit")
        tsl = pos.get("trailing_stop")

        if sl and price <= sl:
            triggered = True
            reason = f"Stop-Loss {sl} € erreicht"
        elif tp and price >= tp:
            triggered = True
            reason = f"Take-Profit {tp} € erreicht"
        elif tsl and price <= tsl:
            triggered = True
            reason = f"Trailing-Stop {tsl} € erreicht"

        if triggered:
            _sell_position_logic(p, pos, price, reason, auto=True)
            alerts.append({"type": "sell", "symbol": symbol, "msg": reason, "price": price})
        else:
            new_positions.append(pos)

    p["positions"] = new_positions

    # Depotwert protokollieren
    total = _total_value(p, prices)
    p.setdefault("value_history", []).append({"date": datetime.utcnow().isoformat(), "value": round(total, 2)})
    if len(p["value_history"]) > 365:
        p["value_history"] = p["value_history"][-365:]

    _save(p)
    p["total_value"] = round(total, 2)
    p["total_return_pct"] = round((total - config.START_CAPITAL) / config.START_CAPITAL * 100, 2)
    return p, alerts


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
        "invested": pos["invested"],
        "pnl_eur": round(proceeds - pos["invested"], 2),
        "reason": reason,
        "auto": auto,
    })


def buy(symbol: str, price: Optional[float] = None, amount_eur: Optional[float] = None) -> dict:
    """Kauft eine Position im virtuellen Depot."""
    p = _load()
    total_value = _total_value(p, {})
    max_per_position = total_value * config.MAX_POSITION_PCT
    available_for_trade = p["cash"] - (total_value * config.CASH_RESERVE_PCT)

    if amount_eur is None:
        amount_eur = min(max_per_position, available_for_trade)

    if amount_eur <= 0:
        return {"ok": False, "error": "Nicht genug Cash oder Positionslimit erreicht"}

    if len(p.get("positions", [])) >= config.MAX_POSITIONS:
        return {"ok": False, "error": "Maximale Anzahl Positionen erreicht"}

    if price is None:
        price = yahoo_client.fetch_latest_price(symbol)
    if not price or price <= 0:
        return {"ok": False, "error": f"Kein gültiger Kurs für {symbol}"}

    shares = amount_eur / price
    if shares <= 0:
        return {"ok": False, "error": "Berechnete Stückzahl ungültig"}

    invested = shares * price
    if invested > p["cash"]:
        return {"ok": False, "error": "Nicht genügend Cash"}

    # Stop-Loss 3 % unter Einstieg
    stop_loss = round(price * (1 - config.DEFAULT_STOP_PCT), 2)
    # Take-Profit mind. 1,5:1
    take_profit = round(price + (price - stop_loss) * config.MIN_RR_RATIO, 2)

    # Bestehende Position aufstocken erlauben
    existing = _current_position(p, symbol)
    if existing:
        old_invested = existing["invested"]
        old_shares = existing["shares"]
        total_shares = old_shares + shares
        avg_price = (old_invested + invested) / total_shares
        existing["shares"] = total_shares
        existing["entry_price"] = round(avg_price, 4)
        existing["invested"] = round(old_invested + invested, 2)
        existing["stop_loss"] = round(avg_price * (1 - config.DEFAULT_STOP_PCT), 2)
        existing["take_profit"] = round(avg_price + (avg_price - existing["stop_loss"]) * config.MIN_RR_RATIO, 2)
    else:
        p["positions"].append({
            "symbol": symbol,
            "shares": round(shares, 6),
            "entry_price": round(price, 4),
            "invested": round(invested, 2),
            "last_price": round(price, 4),
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "opened_at": datetime.utcnow().isoformat(),
            "unrealized_pct": 0.0,
            "unrealized_eur": 0.0,
        })

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
    _save(p)
    return {"ok": True, "position": p["positions"][-1], "cash": round(p["cash"], 2)}


def sell(symbol: str, price: Optional[float] = None, shares: Optional[float] = None) -> dict:
    """Verkauft eine Position (oder einen Anteil)."""
    p = _load()
    pos = _current_position(p, symbol)
    if not pos:
        return {"ok": False, "error": f"{symbol} nicht im Depot"}

    if price is None:
        price = yahoo_client.fetch_latest_price(symbol)
    if not price or price <= 0:
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

    _save(p)
    return {"ok": True, "cash": round(p["cash"], 2)}
