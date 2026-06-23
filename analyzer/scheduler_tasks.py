"""Geplante Tasks: Preisaktualisierung, Marktanalyse, LLM-Analyse, Reports, Alerts."""
from datetime import datetime, timedelta, timezone

from analyzer import auto_trader, market_hours, portfolio, signals, telegram, yahoo_client


EUROPE_TZ_OFFSET = 2  # CEST (UTC+2). Bei Winterzeit (CET) auf 1 ändern.


def _now_cet() -> datetime:
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=EUROPE_TZ_OFFSET)))


def refresh_prices() -> dict:
    """Aktualisiert die Preise aller offenen Positionen und Watchlist."""
    p = portfolio.get_portfolio()
    symbols = {pos["symbol"] for pos in p.get("positions", [])}
    real_symbols = {pos["symbol"] for pos in p.get("real_positions", [])}
    from config import get_universe
    for item in get_universe():
        symbols.add(item["symbol"])
    symbols.update(real_symbols)

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
        return {"task": "market_analysis", "skipped": True, "reason": "Außerhalb der Handelszeiten"}

    recs = signals.generate_recommendations()

    if notify:
        try:
            if recs.get("suggestions"):
                lines = []
                for s in recs["suggestions"]:
                    lines.append(f"• {s['symbol']} ({s['direction']}) Score {s['score']}/100 @ {s['preis']:.2f}")
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
        return {"task": "llm_analysis", "skipped": True, "reason": "Außerhalb der Handelszeiten"}

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
                    actions = [a for a in result.get("actions", []) if a["action"] in ("AUTO-BUY", "AUTO-SELL")]
                    if actions:
                        text += "\n\n<b>Auto-Trades (virtuell):</b>\n" + "\n".join([f"• {a['action']} {a['symbol']}" for a in actions])
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
    return {"task": "daily_summary", "timestamp": datetime.now(timezone.utc).isoformat()}


def _analyze_symbol(symbol: str):
    """Analysiert ein einzelnes Symbol und gibt Einstieg/Verkauf zurück."""
    from analyzer import indicators
    data = yahoo_client.fetch_yahoo(symbol, interval="1d", range_="3mo")
    if not data.get("closes"):
        return None
    closes = data.get("closes", [])
    highs = data.get("highs", [])
    lows = data.get("lows", [])
    volumes = data.get("volumes", [])
    if len(closes) < 50:
        return None
    ema20 = indicators.ema(closes, 20)[-1]
    ema50 = indicators.ema(closes, 50)[-1]
    rsi = indicators.rsi(closes, 14)[-1]
    macd_line, signal_line, _ = indicators.macd(closes)
    bb_upper, bb_sma, bb_lower = indicators.bollinger_bands(closes, 20, 2)
    atr = indicators.atr(highs, lows, closes, 14)[-1]
    latest = closes[-1]

    details = []
    if ema20 > ema50:
        details.append("EMA20 > EMA50 (Trend +)")
    else:
        details.append("EMA20 < EMA50 (Trend -)")
    if rsi < 30:
        details.append("RSI überverkauft")
    elif rsi > 70:
        details.append("RSI überkauft")
    details.append(f"MACD {'bullish' if macd_line[-1] > signal_line[-1] else 'bearish'}")
    details.append(f"Bollinger: {'unten' if latest <= bb_lower[-1] else ('oben' if latest >= bb_upper[-1] else 'Mitte')}")

    score = 50
    if ema20 > ema50:
        score += 15
    if rsi < 45:
        score += 10
    if macd_line[-1] > signal_line[-1]:
        score += 10
    if latest <= bb_lower[-1]:
        score += 10

    if rsi > 65:
        score -= 10
    if macd_line[-1] < signal_line[-1]:
        score -= 10
    if latest >= bb_upper[-1]:
        score -= 10

    direction = "KAUF" if score >= 60 else ("VERKAUF" if score < 45 else "HALTEN")
    stop_loss = round(latest - 1.5 * atr, 4)
    take_profit = round(latest + 2 * atr, 4)
    return {
        "symbol": symbol,
        "direction": direction,
        "score": round(score, 1),
        "price": round(latest, 2),
        "currency": data.get("currency", "EUR"),
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "details": details,
    }


def real_positions_report(only_urgent: bool = False) -> dict:
    """Analysiert deine realen Positionen und sendet Empfehlungen."""
    p = portfolio.get_portfolio()
    real_positions = p.get("real_positions", [])
    if not real_positions:
        return {"task": "real_positions_report", "skipped": True, "reason": "Keine realen Positionen"}

    reports = []
    urgent = []
    for pos in real_positions:
        analysis = _analyze_symbol(pos["symbol"])
        if not analysis:
            reports.append(f"• {pos['symbol']}: Keine Analyse möglich")
            continue
        entry = pos.get("entry_price", 0)
        current = analysis["price"]
        pnl_pct = (current - entry) / entry * 100 if entry else 0
        emoji = "🟢" if analysis["direction"] == "KAUF" else ("🔴" if analysis["direction"] == "VERKAUF" else "🟡")
        lines = [
            f"{emoji} <b>{pos['symbol']}</b> — {analysis['direction']} (Score {analysis['score']})",
            f"Einstieg: {telegram.fmt_eur(entry)} | Aktuell: {telegram.fmt_eur(current)} ({pnl_pct:+.2f}%)",
            f"SL: {telegram.fmt_eur(analysis['stop_loss'])} | TP: {telegram.fmt_eur(analysis['take_profit'])}",
        ]
        if analysis["direction"] == "VERKAUF" and pnl_pct >= -2:
            urgent.append(pos["symbol"])
            lines.append("<b>⚠️ Dringend: Verkauf empfohlen!</b>")
        reports.append("\n".join(lines))

    if only_urgent and not urgent:
        return {"task": "real_positions_alert", "skipped": True, "reason": "Keine dringenden Alerts"}

    title = "🚨 Dringende Alerts (reale Positionen)" if only_urgent else "📋 Bericht (reale Positionen)"
    body = "\n\n".join(reports)
    try:
        telegram._send_message(f"{title}\n\n{body}")
    except Exception:
        pass

    return {"task": "real_positions_report", "timestamp": datetime.now(timezone.utc).isoformat(), "urgent": urgent}


def portfolio_report(notify: bool = True) -> dict:
    """Umfassender 3-Stunden-Report für virtuelles und reales Depot."""
    now_cet = _now_cet()
    hour = now_cet.hour
    # 7-23 Uhr deutscher Zeit, sonst überspringen
    if not (7 <= hour <= 23):
        return {"task": "portfolio_report", "skipped": True, "reason": "Außerhalb 7-23 Uhr"}

    p, _ = portfolio.evaluate_portfolio()
    total_virt = p.get("total_value", 0)
    cash = p.get("cash", 0)
    virt_return = p.get("total_return_pct", 0)
    virt_positions = p.get("positions", [])
    real_positions = p.get("real_positions", [])

    markets = []
    if market_hours.is_trading_hours("ASIA"):
        markets.append("🌏 Asien")
    if market_hours.is_trading_hours("EU"):
        markets.append("🇪🇺 Europa")
    if market_hours.is_trading_hours("US"):
        markets.append("🇺🇸 USA")
    market_str = ", ".join(markets) if markets else "🌙 Keine Börsen geöffnet"

    lines = [
        f"📊 <b>3-Stunden Report</b> ({now_cet.strftime('%d.%m.%Y %H:%M')} CEST)",
        f"Geöffnete Märkte: {market_str}",
        "",
        f"<b>VIRTUELLES DEPOT</b>",
        f"Wert: {telegram.fmt_eur(total_virt)} | Cash: {telegram.fmt_eur(cash)} | Rendite: {virt_return:+.2f}%",
    ]

    if virt_positions:
        lines.append("\n<b>Offene virtuelle Positionen:</b>")
        for pos in virt_positions:
            pnl_pct = pos.get("unrealized_pct", 0)
            lines.append(f"• {pos['symbol']}: {telegram.fmt_eur(pos['last_price'])} ({pnl_pct:+.2f}%)")
    else:
        lines.append("Keine offenen virtuellen Positionen.")

    lines.append("\n<b>REALES DEPOT</b>")
    if real_positions:
        real_reports = []
        for pos in real_positions:
            analysis = _analyze_symbol(pos["symbol"])
            entry = pos.get("entry_price", 0)
            current = analysis["price"] if analysis else pos.get("last_price", entry)
            pnl_pct = (current - entry) / entry * 100 if entry else 0
            emoji = "🟢" if analysis and analysis["direction"] == "KAUF" else ("🔴" if analysis and analysis["direction"] == "VERKAUF" else "🟡")
            rec = f"KAUF" if analysis and analysis["direction"] == "KAUF" else (f"VERKAUF" if analysis and analysis["direction"] == "VERKAUF" else f"HALTEN")
            real_reports.append(f"{emoji} {pos['symbol']} @ {telegram.fmt_eur(current)} ({pnl_pct:+.2f}%) → <b>{rec}</b>")
        lines.extend(real_reports)
    else:
        lines.append("Keine realen Positionen.")

    text = "\n".join(lines)
    if notify:
        try:
            telegram._send_message(text)
        except Exception:
            pass

    return {"task": "portfolio_report", "timestamp": datetime.now(timezone.utc).isoformat()}
