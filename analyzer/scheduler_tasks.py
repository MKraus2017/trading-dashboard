"""Geplante Tasks: Preisaktualisierung, Marktanalyse, LLM-Analyse."""
from datetime import datetime, timezone

from analyzer import auto_trader, market_hours, portfolio, signals, telegram, yahoo_client
from analyzer.db_store import save_portfolio


def refresh_prices() -> dict:
    """Aktualisiert die Preise aller offenen Positionen und Watchlist."""
    p = portfolio.get_portfolio()
    symbols = {pos["symbol"] for pos in p.get("positions", [])}
    # auch Watchlist-Symbole frisch holen, damit Cache warm bleibt
    from config import get_universe
    for item in get_universe():
        symbols.add(item["symbol"])

    updated = []
    failed = []
    for symbol in symbols:
        try:
            price = yahoo_client.fetch_latest_price(symbol)
            if price:
                updated.append({"symbol": symbol, "price": price})
            else:
                failed.append(symbol)
        except Exception:
            failed.append(symbol)

    # Portfolio neu bewerten
    p_evaluated, alerts = portfolio.evaluate_portfolio()

    return {
        "task": "refresh_prices",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "updated": len(updated),
        "failed": failed,
        "alerts": len(alerts),
        "portfolio_value": round(p_evaluated.get("total_value", 0), 2),
    }


def market_analysis(notify: bool = True) -> dict:
    """Führt die nicht-KI-Markanalyse aus (alle 30 min in Handelszeiten)."""
    if not market_hours.is_any_trading_hours():
        return {
            "task": "market_analysis",
            "skipped": True,
            "reason": "Außerhalb der Handelszeiten",
            "next_open": market_hours.next_trading_hours_info()["next_start"].isoformat(),
        }

    recs = signals.generate_recommendations()

    if notify:
        try:
            if recs.get("suggestions"):
                lines = []
                for s in recs["suggestions"]:
                    lines.append(f"• {s['symbol']} ({s['direction']}) Score {s['score']}/100 @ {s['preis']:.2f} {s['currency']}")
                telegram._send_message("📈 <b>Marktanalyse (30 Min)</b>\n\n" + "\n".join(lines))
            else:
                telegram._send_message("📈 <b>Marktanalyse (30 Min)</b>\n\nKeine klaren Handlungsempfehlungen.")
        except Exception:
            pass

    return {
        "task": "market_analysis",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "count": recs.get("count", 0),
        "suggestions": recs.get("suggestions", []),
    }


def llm_analysis(auto_trade: bool = True, notify: bool = True) -> dict:
    """Führt die LLM-Risikoanalyse aus (alle 2 Stunden in Handelszeiten). Optional Auto-Trade."""
    if not market_hours.is_any_trading_hours():
        return {
            "task": "llm_analysis",
            "skipped": True,
            "reason": "Außerhalb der Handelszeiten",
            "next_open": market_hours.next_trading_hours_info()["next_start"].isoformat(),
        }

    result = auto_trader.run_auto_trading(dry_run=not auto_trade)

    if notify:
        try:
            recs = result.get("recommendations", {})
            lines = [f"• {s['symbol']}: {s['direction']} (Score {s['score']})" for s in recs.get("suggestions", [])]
            if lines:
                llm_lines = []
                for s in recs.get("suggestions", []):
                    llm = s.get("llm_risk")
                    if llm and not llm.get("error"):
                        llm_lines.append(f"• {s['symbol']}: {llm['risk_level']} ({llm['risk_score']}/10) — {llm['verdict']}")
                text = "🧠 <b>LLM Analyse (2h)</b>\n\n<b>Empfehlungen:</b>\n" + "\n".join(lines)
                if llm_lines:
                    text += "\n\n<b>LLM Risiken:</b>\n" + "\n".join(llm_lines)
                if auto_trade:
                    actions = result.get("actions", [])
                    if actions:
                        text += "\n\n<b>Auto-Trades:</b>\n" + "\n".join([f"• {a['action']} {a['symbol']}" for a in actions if a['action'] in ('AUTO-BUY','AUTO-SELL')])
                telegram._send_message(text)
            else:
                telegram._send_message("🧠 <b>LLM Analyse (2h)</b>\n\nKeine klaren Empfehlungen.")
        except Exception:
            pass

    return {
        "task": "llm_analysis",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "auto_trade": auto_trade,
        "actions": result.get("actions", []),
        "portfolio_value": round(result.get("portfolio", {}).get("total_value", 0), 2),
    }


def daily_summary() -> dict:
    """Sendet täglich eine Depot-Zusammenfassung."""
    p, _ = portfolio.evaluate_portfolio()
    telegram.notify_daily_summary(p)
    return {
        "task": "daily_summary",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "portfolio_value": round(p.get("total_value", 0), 2),
    }
