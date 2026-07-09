"""Automatischer Krypto-Handel im virtuellen Depot (getrennt vom Aktien-Bot).

Öffnet Positionen basierend auf crypto_signals-Empfehlungen. Hebel wird vom
Signal-Modul selbst bestimmt (0-10x, gecappt). Positionsgröße = fester Prozentsatz
des verfügbaren Cash (CRYPTO_MAX_POSITION_PCT).
"""
from typing import Dict

import config
from analyzer import crypto_portfolio, crypto_signals, telegram


def run_crypto_auto_trading(user_id: int, dry_run: bool = False) -> dict:
    actions = []

    # 1. Bestehende Positionen bewerten (SL/TP/Liquidation) - IMMER, unabhaengig vom Analyse-Fenster
    eval_res = crypto_portfolio.evaluate_crypto_portfolio(user_id)
    p = eval_res["portfolio"]
    for ev in eval_res["events"]:
        actions.append({
            "symbol": ev["symbol"],
            "action": "LIQUIDATED" if ev["type"] == "liquidation" else "AUTO-CLOSE",
            "reason": ev["msg"],
            "pnl_eur": ev["trade"].get("pnl_eur"),
        })

    # 2. Neue Signale generieren (nur wenn im Analyse-Fenster - Aufrufer entscheidet das via scheduler)
    recs = crypto_signals.generate_crypto_recommendations()
    held_symbols = {pos["symbol"] for pos in p.get("positions", [])}

    for sig in recs.get("suggestions", []):
        symbol = sig["symbol"]
        if symbol in held_symbols:
            continue  # bereits offene Position auf diesem Symbol - kein Pyramiding
        if len(p.get("positions", [])) >= config.CRYPTO_MAX_POSITIONS:
            break
        if sig["direction"] not in ("LONG", "SHORT"):
            continue

        margin = round(p["cash"] * config.CRYPTO_MAX_POSITION_PCT, 2)
        cash_reserve = p.get("start_capital", config.CRYPTO_START_CAPITAL) * config.CRYPTO_CASH_RESERVE_PCT
        if p["cash"] - margin < cash_reserve or margin < config.CRYPTO_MIN_POSITION_EUR:
            continue

        if dry_run:
            actions.append({
                "symbol": symbol, "action": f"WOULD-OPEN-{sig['direction']}",
                "leverage": sig["leverage"], "score": sig["score"], "margin_eur": margin,
            })
        else:
            res = crypto_portfolio.open_position(
                user_id, symbol, sig["direction"], margin_eur=margin,
                leverage=sig["leverage"], entry_price=sig["price"],
                stop_loss=sig["stop_loss"], take_profit=sig["take_profit"],
                reason=f"Score {sig['score']}/100 - {', '.join(sig['details'][:2])}",
            )
            if res.get("ok"):
                actions.append({
                    "symbol": symbol, "action": f"AUTO-OPEN-{sig['direction']}",
                    "leverage": sig["leverage"], "score": sig["score"], "margin_eur": margin,
                })
                held_symbols.add(symbol)
                p = crypto_portfolio.get_crypto_portfolio(user_id)

    return {"actions": actions, "recommendations": recs, "portfolio": p}


def notify_crypto_actions(user_id: int, result: dict, token: str = None, chat_id: str = None):
    if not chat_id:
        return
    actions = result.get("actions", [])
    if not actions:
        return
    lines = ["🪙 <b>Krypto-Bot Update</b>", ""]
    for a in actions:
        emoji = "🟢" if "OPEN-LONG" in a["action"] else ("🔴" if "OPEN-SHORT" in a["action"] else
                ("💥" if a["action"] == "LIQUIDATED" else "✅"))
        if "OPEN" in a["action"]:
            lines.append(f"{emoji} {a['action']} {a['symbol']} — Hebel {a.get('leverage')}x, Score {a.get('score')}/100, Einsatz {a.get('margin_eur')} €")
        else:
            pnl = a.get("pnl_eur")
            pnl_str = f" ({pnl:+.2f} €)" if pnl is not None else ""
            lines.append(f"{emoji} {a['action']} {a['symbol']}{pnl_str} — {a.get('reason','')}")
    telegram._send_message("\n".join(lines), token=token, chat_id=chat_id)
