"""Autopilot: Wandelt Analyse-Empfehlungen in Depot-Aktionen um."""
from datetime import datetime
from typing import Dict, List, Tuple

import config
from analyzer import portfolio, signals
from analyzer.portfolio import evaluate_portfolio
from analyzer.signals import generate_recommendations


def _current_position_symbols(p: dict) -> set:
    return {pos["symbol"] for pos in p.get("positions", [])}


def run_autopilot(dry_run: bool = True) -> dict:
    """
    Führt den virtuellen Depot-Autopiloten aus.
    Bei dry_run=True werden keine Trades ausgeführt, nur ein Plan zurückgegeben.
    """
    p, alerts = evaluate_portfolio()  # SL/TP auslösen lassen
    recommendations = generate_recommendations()

    plan = {
        "timestamp": datetime.utcnow().isoformat(),
        "dry_run": dry_run,
        "actions": [],
        "skipped": [],
        "alerts": alerts,
    }

    held_symbols = _current_position_symbols(p)

    # 1. VERKAUF-Empfehlungen für gehaltene Positionen prüfen
    for rec in recommendations.get("suggestions", []):
        if rec["direction"] == "VERKAUF" and rec["symbol"] in held_symbols:
            pos = next(x for x in p["positions"] if x["symbol"] == rec["symbol"])
            action = {
                "symbol": rec["symbol"],
                "action": "SELL",
                "reason": rec["begruendung"],
                "shares": pos["shares"],
                "expected_price": rec["preis"],
                "pnl_eur_so_far": pos.get("unrealized_eur", 0),
            }
            plan["actions"].append(action)
            if not dry_run:
                portfolio.sell(rec["symbol"], price=rec["preis"], shares=pos["shares"])

    # 2. KAUF-Empfehlungen umsetzen
    buys = [r for r in recommendations.get("suggestions", []) if r["direction"] == "KAUF"]
    for rec in buys:
        symbol = rec["symbol"]
        if symbol in _current_position_symbols(portfolio.get_portfolio()):
            plan["skipped"].append({"symbol": symbol, "reason": "Bereits im Depot"})
            continue

        total_value = portfolio._total_value(portfolio.get_portfolio(), {})
        max_amount = total_value * config.MAX_POSITION_PCT
        available = portfolio.get_portfolio().get("cash", 0) - (total_value * config.CASH_RESERVE_PCT)
        amount = min(max_amount, available)

        if amount <= 0:
            plan["skipped"].append({"symbol": symbol, "reason": "Nicht genug Cash"})
            continue

        if len(portfolio.get_portfolio().get("positions", [])) >= config.MAX_POSITIONS:
            plan["skipped"].append({"symbol": symbol, "reason": "Max. Anzahl Positionen erreicht"})
            break

        action = {
            "symbol": symbol,
            "action": "BUY",
            "amount_eur": round(amount, 2),
            "expected_price": rec["preis"],
            "stop_loss": rec.get("stop_loss"),
            "take_profit": rec.get("take_profit"),
            "reason": rec["begruendung"],
        }
        plan["actions"].append(action)
        if not dry_run:
            portfolio.buy(symbol, price=rec["preis"], amount_eur=amount)

    # 3. Depot neu bewerten
    if not dry_run:
        p, _ = evaluate_portfolio()
    else:
        p = portfolio.get_portfolio()

    plan["portfolio_after"] = {
        "cash": round(p.get("cash", 0), 2),
        "total_value": p.get("total_value", 0),
        "total_return_pct": p.get("total_return_pct", 0),
        "positions_count": len(p.get("positions", [])),
    }
    return plan
