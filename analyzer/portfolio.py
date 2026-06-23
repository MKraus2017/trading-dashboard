"""Virtuelles Depot: Kaufen, Verkaufen, SL/TP, Bewertung (Multi-User)."""
import json
import os
from datetime import datetime
from typing import Dict, Optional

import config
from analyzer import db_store, telegram, yahoo_client

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
        for key in ("real_positions", "real_trades"):
            if key not in p:
                p[key] = []
        return p
    return _default_portfolio()


def _save(user_id: int, p: dict):
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


def evaluate_portfolio(user_id: int) -> dict:
    p = _load(user_id)
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
        invested = pos.get("invested", 0) or (shares * entry)
        unrealized_pct = ((current_value - invested) / invested * 100) if invested else 0.0
        pos["unrealized_pct"] = round(unrealized_pct, 2)
        pos["unrealized_eur"] = round(current_value - invested, 2)

        # Trailing-Stop
        if unrealized_pct >= 25 and "trailing_stop" not in pos:
            pos["trailing_stop"] = round(price * (1 - config.TRAILING_STOP_PCT), 2)
            alerts.append({"type": "info", "symbol": symbol, "msg": f"Trailing-Stop aktiviert bei {pos['trailing_stop']} €"})

        if "trailing_stop" in pos and price > pos.get("highest_price", entry) * (1 + config.TRAILING_STOP_PCT * 0.5):
            pos["highest_price"] = max(pos.get("highest_price", entry), price)
            new_trailing = round(pos["highest_price"] * (1 - config.TRAILING_STOP_PCT), 2)
            if new_trailing > pos["trailing_stop"]:
                pos["trailing_stop"] = new_trailing
                alerts.append({"type": "info", "symbol": symbol, "msg": f"Trailing-Stop angehoben auf {new_trailing} €"})

        triggered = False
        reason = None
        sl = pos.get("stop_loss")
        tp = pos.get("take_profit")
        tsl = pos.get("trailing_stop")

        if sl and price <= sl:
            triggered = True; reason = f"Stop-Loss {sl} € erreicht"
        elif tp and price >= tp:
            triggered = True; reason = f"Take-Profit {tp} € erreicht"
        elif tsl and price <= tsl:
            triggered = True; reason = f"Trailing-Stop {tsl} € erreicht"

        if triggered:
            _sell_position_logic(p, pos, price, reason, auto=True)
            alerts.append({"type": "sell", "symbol": symbol, "msg": reason, "price": price})
        else:
            new_positions.append(pos)

    p["positions"] = new_positions

    total = _total_value(p, prices)
    p.setdefault("value_history", []).append({"date": datetime.utcnow().isoformat(), "value": round(total, 2)})
    if len(p["value_history"]) > 365:
        p["value_history"] = p["value_history"][-365:]

    _save(user_id, p)
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
        "invested": pos.get("invested", 0),
        "pnl_eur": round(proceeds - pos.get("invested", 0), 2),
        "reason": reason,
        "auto": auto,
    })


def buy(user_id: int, symbol: str, price: Optional[float] = None, amount_eur: Optional[float] = None, notify: bool = True) -> dict:
    p = _load(user_id)
    total_value = _total_value(p, {})
    max_per_position = total_value * config.MAX_POSITION_PCT
    available_for_trade = p["cash"] - (total_value * config.CASH_RESERVE_PCT)

    if amount_eur is None:
        amount_eur = min(max_per_position, available_for_trade)

    if amount_eur < 100:
        return {"ok": False, "error": "Kaufbetrag zu niedrig (< €100) oder Reserve überschritten"}

    if len(p.get("positions", [])) >= config.MAX_POSITIONS:
        return {"ok": False, "error": "Maximale Anzahl Positionen erreicht"}

    if price is None:
        price = yahoo_client.fetch_latest_price(symbol)
    if not price or price <= 0:
        return {"ok": False, "error": f"Kein gültiger Kurs für {symbol}"}

    amount_eur = min(amount_eur, p["cash"])
    if amount_eur < 100:
        return {"ok": False, "error": "Nicht genug Cash für Mindestkauf"}

    shares = amount_eur / price
    invested = shares * price
    if invested < 100:
        return {"ok": False, "error": "Investition zu gering"}

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
        existing["stop_loss"] = round(avg_price * (1 - config.DEFAULT_STOP_PCT), 2)
        existing["take_profit"] = round(avg_price + (avg_price - existing["stop_loss"]) * config.MIN_RR_RATIO, 2)
        updated_position = existing
    else:
        new_pos = {
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


def sell(user_id: int, symbol: str, price: Optional[float] = None, shares: Optional[float] = None, notify: bool = True) -> dict:
    p = _load(user_id)
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

    _save(user_id, p)
    if notify:
        try:
            telegram.notify_virtual_trade("SELL", symbol, sell_shares, price, reason="manuell", profit=round(proceeds - cost_basis, 2))
        except Exception:
            pass
    return {"ok": True, "cash": round(p["cash"], 2)}
