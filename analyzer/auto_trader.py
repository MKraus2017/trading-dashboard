"""Automatischer Handel im virtuellen Depot auf Basis von Empfehlungen."""
import subprocess
from typing import Dict

from analyzer import portfolio, signals, telegram


def _commit_portfolio() -> dict:
    """Versucht, portfolio.json nach einem Trade zu committen + pushen (Render-Persistenz)."""
    try:
        repo = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5
        )
        if repo.returncode != 0:
            return {"ok": False, "error": "Kein Git-Repo"}
        subprocess.run(
            ["git", "add", "data/portfolio.json", "data/recommendations.json"],
            capture_output=True, timeout=10
        )
        res = subprocess.run(
            ["git", "commit", "-m", "Auto-Trade Update"],
            capture_output=True, text=True, timeout=10
        )
        if res.returncode == 0:
            subprocess.run(
                ["git", "push"],
                capture_output=True, timeout=30
            )
            return {"ok": True}
        return {"ok": False, "error": res.stderr[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def run_auto_trading(dry_run: bool = False) -> dict:
    """
    Führt automatisch Kauf-/Verkaufsentscheidungen im virtuellen Depot aus.
    - Verkauft offene Positionen, wenn Richtung == VERKAUF
    - Kauft Top-KAUF-Empfehlungen, wenn Cash und Positionslimit es erlauben
    Rückgabe: Protokoll der ausgeführten Aktionen.
    """
    actions = []

    # 1. Depot bewerten (SL/TP/Trailing)
    p, alerts = portfolio.evaluate_portfolio()
    for alert in alerts:
        if alert.get("type") in ("sell",):
            actions.append({
                "time": alert.get("time"),
                "symbol": alert["symbol"],
                "action": "AUTO-SELL",
                "reason": alert["msg"],
                "price": alert.get("price"),
            })

    # 2. Empfehlungen generieren
    recs = signals.generate_recommendations()

    # 3. Verkäufe bei VERKAUF-Empfehlungen
    sell_recs = [r for r in recs.get("suggestions", []) if r["direction"] == "VERKAUF"]
    held_symbols = {pos["symbol"] for pos in p.get("positions", [])}
    for rec in sell_recs:
        symbol = rec["symbol"]
        if symbol in held_symbols:
            if dry_run:
                actions.append({
                    "symbol": symbol,
                    "action": "WOULD-SELL",
                    "reason": "Empfehlung VERKAUF (Dry-Run)",
                })
            else:
                res = portfolio.sell(symbol)
                if res.get("ok"):
                    actions.append({
                        "symbol": symbol,
                        "action": "AUTO-SELL",
                        "reason": "Empfehlung VERKAUF",
                    })
                    held_symbols.discard(symbol)
                else:
                    actions.append({
                        "symbol": symbol,
                        "action": "AUTO-SELL-FAILED",
                        "reason": res.get("error"),
                    })

    # 4. Käufe bei KAUF-Empfehlungen
    buy_recs = [r for r in recs.get("suggestions", []) if r["direction"] == "KAUF"]
    prices: Dict[str, float] = {}

    for rec in buy_recs:
        symbol = rec["symbol"]
        # Nicht doppelt kaufen / Positionslimit prüfen
        if symbol in held_symbols:
            continue
        if len(portfolio.get_portfolio().get("positions", [])) >= 5:
            actions.append({"symbol": symbol, "action": "SKIP", "reason": "Max. Positionen erreicht"})
            continue

        if dry_run:
            actions.append({
                "symbol": symbol,
                "action": "WOULD-BUY",
                "reason": f"Empfehlung KAUF, Score {rec['score']}",
            })
            held_symbols.add(symbol)
        else:
            res = portfolio.buy(symbol)
            if res.get("ok"):
                actions.append({
                    "symbol": symbol,
                    "action": "AUTO-BUY",
                    "reason": f"Empfehlung KAUF, Score {rec['score']}",
                    "invested": res["position"]["invested"],
                })
                held_symbols.add(symbol)
            else:
                actions.append({
                    "symbol": symbol,
                    "action": "AUTO-BUY-FAILED",
                    "reason": res.get("error"),
                })

    # Depot neu bewerten für Rückgabe
    p_final, _ = portfolio.evaluate_portfolio()

    if actions and not dry_run:
        try:
            summary_lines = [f"{'🟢' if a['action'] == 'AUTO-BUY' else '🔴'} {a['action']} {a['symbol']}: {a.get('reason', '')}" for a in actions if a['action'] in ('AUTO-BUY', 'AUTO-SELL')]
            if summary_lines:
                telegram._send_message("🤖 <b>Automatische Trades ausgeführt</b>\n\n" + "\n".join(summary_lines) + f"\n\nNeuer Depotwert: {telegram.fmt_eur(p_final.get('total_value', 0))}")
        except Exception:
            pass

    if not dry_run:
        commit_result = _commit_portfolio()
    else:
        commit_result = {"ok": True, "note": "Dry-Run, kein Commit"}

    return {
        "actions": actions,
        "portfolio": p_final,
        "recommendations": recs,
        "commit": commit_result,
    }
