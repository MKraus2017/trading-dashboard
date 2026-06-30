"""Geplante Tasks: Preisaktualisierung, Marktanalyse, LLM-Analyse, Reports, Alerts (Multi-User)."""
from datetime import datetime, timedelta, timezone
from typing import List

from analyzer import auto_trader, db_store, indicators, market_hours, portfolio, signals, telegram, yahoo_client


EUROPE_TZ_OFFSET = 2  # CEST (UTC+2)


def _now_cet() -> datetime:
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=EUROPE_TZ_OFFSET)))


def _get_active_users() -> List[int]:
    """Holt alle User-IDs, die Reports/Alerts erhalten möchten."""
    rows = db_store.list_users()
    return [r["id"] for r in rows]


def _user_telegram_cfg(user_id: int):
    settings = db_store.get_settings(user_id)
    return settings.get("telegram_bot_token"), settings.get("telegram_chat_id")


def refresh_prices() -> dict:
    """Aktualisiert Preise für alle Symbole aller Nutzer."""
    all_symbols = set()
    from config import get_universe
    for item in get_universe():
        all_symbols.add(item["symbol"])
    for user_id in _get_active_users():
        p = portfolio.get_portfolio(user_id)
        all_symbols.update(pos["symbol"] for pos in p.get("positions", []))
        all_symbols.update(pos["symbol"] for pos in p.get("real_positions", []))

    updated = []
    failed = []
    for symbol in all_symbols:
        try:
            price = yahoo_client.fetch_latest_price(symbol)
            if price:
                updated.append({"symbol": symbol, "price": price})
            else:
                failed.append(symbol)
        except Exception:
            failed.append(symbol)

    return {
        "task": "refresh_prices",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "updated": len(updated),
        "failed": failed,
    }


def evaluate_all_users() -> dict:
    """Bewertet die Depots aller Nutzer (SL/TP/Trailing)."""
    alerts_total = 0
    for user_id in _get_active_users():
        _, alerts = portfolio.evaluate_portfolio(user_id)
        alerts_total += len(alerts)
    return {"task": "evaluate_all", "users": len(_get_active_users()), "alerts": alerts_total}


def market_analysis(notify: bool = True) -> dict:
    if not market_hours.is_any_trading_hours():
        return {"task": "market_analysis", "skipped": True, "reason": "Außerhalb der Handelszeiten"}

    recs = signals.generate_recommendations()

    if notify:
        for user_id in _get_active_users():
            try:
                token, chat_id = _user_telegram_cfg(user_id)
                if not chat_id:
                    print(f"[market_analysis] user_id={user_id}: no chat_id configured")
                    continue

                lines = []
                if recs.get("suggestions"):
                    lines.append("<b>Kaufempfehlungen (virtuelles Depot):</b>")
                    lines += [f"• {s['symbol']} ({s['direction']}) Score {s['score']}/100 @ {s['preis']:.2f}" for s in recs["suggestions"]]

                # Reale TR-Positionen auswerten
                p = portfolio.get_portfolio(user_id)
                real_positions = p.get("real_positions", [])
                if real_positions:
                    if lines:
                        lines.append("")
                    lines.append("<b>Deine realen TR-Positionen:</b>")
                    for pos in real_positions:
                        symbol = pos["symbol"]
                        analysis = _analyze_symbol(symbol)
                        entry = pos.get("entry_price", 0)
                        current = analysis["price"] if analysis else pos.get("last_price", entry)
                        pnl_pct = (current - entry) / entry * 100 if entry else 0
                        if analysis:
                            direction = analysis["direction"]
                            score = analysis["score"]
                        else:
                            direction = "HALTEN"
                            score = "n/a"
                        if direction == "VERKAUF":
                            action_emoji = "🔴"
                            action_text = "VERKAUFEN"
                        elif direction == "KAUF":
                            action_emoji = "🟢"
                            action_text = "HALTEN (kein Verkauf)"
                        else:
                            action_emoji = "🟡"
                            action_text = "HALTEN"
                        lines.append(f"{action_emoji} <b>{symbol}</b> — {action_text} (Score {score}/100)")
                        lines.append(f"   Einstieg: {telegram.fmt_eur(entry)} | Aktuell: {telegram.fmt_eur(current)} ({pnl_pct:+.2f}%)")
                        if analysis:
                            lines.append(f"   SL: {telegram.fmt_eur(analysis['stop_loss'])} | TP: {telegram.fmt_eur(analysis['take_profit'])}")
                            if direction == "VERKAUF":
                                lines.append(f"   ⚠️ Dringend: Verkauf empfohlen!")
                else:
                    if lines:
                        lines.append("")
                    lines.append("<b>Reale TR-Positionen:</b> Keine")

                if lines:
                    text = "📈 <b>Marktanalyse (30 Min)</b>\n\n" + "\n".join(lines)
                else:
                    text = "📈 <b>Marktanalyse (30 Min)</b>\n\nKeine klaren Handlungsempfehlungen."
                res = telegram._send_message(text, token=token, chat_id=chat_id)
                print(f"[market_analysis] user_id={user_id}: {res}")
            except Exception as e:
                print(f"[market_analysis] user_id={user_id} error: {e}")

    return {
        "task": "market_analysis",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "count": recs.get("count", 0),
        "suggestions": recs.get("suggestions", []),
    }


def llm_analysis(auto_trade: bool = True, notify: bool = True) -> dict:
    if not market_hours.is_any_trading_hours():
        return {"task": "llm_analysis", "skipped": True, "reason": "Außerhalb der Handelszeiten"}

    total_actions = []
    for user_id in _get_active_users():
        settings = db_store.get_settings(user_id)
        do_trade = auto_trade and settings.get("auto_trade_enabled", True)
        result = auto_trader.run_auto_trading(user_id, dry_run=not do_trade)
        total_actions.extend({"user_id": user_id, **a} for a in result.get("actions", []))

    if notify:
        for user_id in _get_active_users():
            try:
                token, chat_id = _user_telegram_cfg(user_id)
                if not chat_id:
                    continue
                result = auto_trader.run_auto_trading(user_id, dry_run=True)
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
                    telegram._send_message(text, token=token, chat_id=chat_id)
                else:
                    telegram._send_message("🧠 <b>LLM Analyse (2h)</b>\n\nKeine klaren Empfehlungen.", token=token, chat_id=chat_id)
            except Exception:
                pass

    return {
        "task": "llm_analysis",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "auto_trade": auto_trade,
        "actions": total_actions,
    }


def daily_summary() -> dict:
    for user_id in _get_active_users():
        try:
            token, chat_id = _user_telegram_cfg(user_id)
            if not chat_id:
                print(f"[daily_summary] user_id={user_id}: no chat_id configured")
                continue
            p, _ = portfolio.evaluate_portfolio(user_id)
            res = telegram.notify_daily_summary(p, token=token, chat_id=chat_id)
            print(f"[daily_summary] user_id={user_id}: {res}")
        except Exception as e:
            print(f"[daily_summary] user_id={user_id} error: {e}")
    return {"task": "daily_summary", "timestamp": datetime.now(timezone.utc).isoformat()}


def _analyze_symbol(symbol: str):
    data = yahoo_client.fetch_yahoo(symbol, interval="1d", range_="3mo")
    if not data.get("closes"):
        return None
    closes = data["closes"]
    highs = data["highs"]
    lows = data["lows"]
    if len(closes) < 50:
        return None
    ema20 = indicators.ema(closes, 20)[-1]
    ema50 = indicators.ema(closes, 50)[-1]
    rsi = indicators.rsi(closes, 14)[-1]
    macd_line, signal_line, _ = indicators.macd(closes)
    bb = indicators.bollinger(closes, 20, 2)
    bb_upper = bb["upper"]
    bb_lower = bb["lower"]
    atr = indicators.atr(highs, lows, closes, 14)[-1]
    latest = closes[-1]

    details = []
    if ema20 > ema50:
        details.append("EMA20 > EMA50 (Trend +)")
    else:
        details.append("EMA20 < EMA50 (Trend -)")
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


def analyze_real_positions(user_id: int, notify: bool = True) -> dict:
    """Analysiert alle realen TR-Positionen eines Users und gibt konkrete Handlungsempfehlungen."""
    p = portfolio.get_portfolio(user_id)
    real_positions = p.get("real_positions", [])
    if not real_positions:
        return {"ok": False, "error": "Keine realen TR-Positionen vorhanden"}

    token, chat_id = _user_telegram_cfg(user_id)
    lines = [f"📊 <b>Manuelle Analyse: Reale TR-Positionen</b> ({datetime.now().strftime('%d.%m.%Y %H:%M')})", ""]
    results = []
    sell_count = 0
    partial_count = 0
    hold_count = 0
    add_count = 0

    for pos in real_positions:
        symbol = pos["symbol"]
        analysis = _analyze_symbol(symbol)
        entry = pos.get("entry_price", 0)
        shares = pos.get("shares", 0)
        current = analysis["price"] if analysis else pos.get("last_price", entry)
        pnl_pct = (current - entry) / entry * 100 if entry else 0

        if not analysis:
            advice = "HALTEN"
            emoji = "🟡"
            reason = "Keine aktuelle Analyse verfügbar"
        else:
            score = analysis["score"]
            if score >= 70 and pnl_pct > -5:
                advice = "NACHKAUFEN"
                emoji = "🟢🟢"
                reason = "Starkes Signal – bestehender Trend intakt"
            elif score >= 60:
                advice = "HALTEN"
                emoji = "🟢"
                reason = "Kaufsignal – Position weiterhalten"
            elif score < 45 and pnl_pct > 25:
                advice = "TEILVERKAUFEN"
                emoji = "🟠"
                reason = "Schwäche – Teilverkauf zur Gewinn-Sicherung"
                partial_count += 1
            elif score < 45:
                advice = "VERKAUFEN"
                emoji = "🔴"
                reason = "Verkaufssignal – Risiko reduzieren"
                sell_count += 1
            else:
                advice = "HALTEN"
                emoji = "🟡"
                reason = "Neutrales Signal – abwarten"

        if advice == "HALTEN":
            hold_count += 1
        elif advice == "NACHKAUFEN":
            add_count += 1

        results.append({
            "symbol": symbol,
            "advice": advice,
            "score": analysis["score"] if analysis else None,
            "price": current,
            "entry": entry,
            "pnl_pct": round(pnl_pct, 2),
            "shares": shares,
            "reason": reason,
            "stop_loss": analysis["stop_loss"] if analysis else None,
            "take_profit": analysis["take_profit"] if analysis else None,
        })

        lines.append(f"{emoji} <b>{symbol}</b> → <u>{advice}</u>")
        lines.append(f"   Score: {analysis['score'] if analysis else 'n/a'}/100 | Aktuell: {telegram.fmt_eur(current)} ({pnl_pct:+.2f}%)")
        lines.append(f"   Einstieg: {telegram.fmt_eur(entry)} | Stücke: {shares}")
        if analysis:
            lines.append(f"   SL: {telegram.fmt_eur(analysis['stop_loss'])} | TP: {telegram.fmt_eur(analysis['take_profit'])}")
        lines.append(f"   Begründung: {reason}")
        lines.append("")

    summary = f"Zusammenfassung: {sell_count}× Verkaufen, {partial_count}× Teil-Verkauf, {hold_count}× Halten, {add_count}× Nachkaufen"
    lines.insert(2, f"<i>{summary}</i>")

    telegram_sent = False
    if notify and chat_id:
        try:
            res = telegram._send_message("\n".join(lines), token=token, chat_id=chat_id)
            telegram_sent = res.get("ok", False)
        except Exception as e:
            print(f"[analyze_real_positions] Telegram error: {e}")

    return {
        "ok": True,
        "count": len(results),
        "summary": summary,
        "results": results,
        "telegram_sent": telegram_sent,
    }


def real_positions_report(only_urgent: bool = False) -> dict:
    sent_any = False
    for user_id in _get_active_users():
        settings = db_store.get_settings(user_id)
        if not settings.get("report_enabled", True):
            continue
        token, chat_id = _user_telegram_cfg(user_id)
        if not chat_id:
            continue
        p = portfolio.get_portfolio(user_id)
        real_positions = p.get("real_positions", [])
        if not real_positions:
            continue

        reports = []
        urgent = []
        for pos in real_positions:
            analysis = _analyze_symbol(pos["symbol"])
            entry = pos.get("entry_price", 0)
            current = analysis["price"] if analysis else pos.get("last_price", entry)
            pnl_pct = (current - entry) / entry * 100 if entry else 0
            emoji = "🟢" if analysis and analysis["direction"] == "KAUF" else ("🔴" if analysis and analysis["direction"] == "VERKAUF" else "🟡")
            title = "🚨 Dringende Alerts (reale Positionen)" if only_urgent else "📋 Bericht (reale Positionen)"
            lines = [
                f"{emoji} <b>{pos['symbol']}</b> — {analysis['direction'] if analysis else 'HALTEN'} (Score {analysis['score'] if analysis else 'n/a'})",
                f"Einstieg: {telegram.fmt_eur(entry)} | Aktuell: {telegram.fmt_eur(current)} ({pnl_pct:+.2f}%)",
            ]
            if analysis:
                lines.append(f"SL: {telegram.fmt_eur(analysis['stop_loss'])} | TP: {telegram.fmt_eur(analysis['take_profit'])}")
            if analysis and analysis["direction"] == "VERKAUF" and pnl_pct >= -2:
                urgent.append(pos["symbol"])
                lines.append("<b>⚠️ Dringend: Verkauf empfohlen!</b>")
            reports.append("\n".join(lines))

        if only_urgent and not urgent:
            continue

        try:
            title = "🚨 Dringende Alerts (reale Positionen)" if only_urgent else "📋 Bericht (reale Positionen)"
            telegram._send_message(f"{title}\n\n" + "\n\n".join(reports), token=token, chat_id=chat_id)
            sent_any = True
        except Exception:
            pass

    if not sent_any:
        return {"task": "real_positions_report", "skipped": True, "reason": "Nichts zu senden"}
    return {"task": "real_positions_report", "timestamp": datetime.now(timezone.utc).isoformat()}


def portfolio_report(notify: bool = True) -> dict:
    now_cet = _now_cet()
    hour = now_cet.hour
    if not (7 <= hour <= 23):
        return {"task": "portfolio_report", "skipped": True, "reason": "Außerhalb 7-23 Uhr"}

    markets = []
    if market_hours.is_trading_hours("ASIA"):
        markets.append("🌏 Asien")
    if market_hours.is_trading_hours("EU"):
        markets.append("🇪🇺 Europa")
    if market_hours.is_trading_hours("US"):
        markets.append("🇺🇸 USA")
    market_str = ", ".join(markets) if markets else "🌙 Keine Börsen geöffnet"

    # Empfehlungen einmal generieren und allen Nutzern mitteilen
    top_recs = signals.generate_recommendations().get("suggestions", [])[:5]

    for user_id in _get_active_users():
        settings = db_store.get_settings(user_id)
        token, chat_id = _user_telegram_cfg(user_id)
        if not chat_id or not settings.get("report_enabled", True):
            continue
        try:
            p, _ = portfolio.evaluate_portfolio(user_id)
            total_virt = p.get("total_value", 0)
            cash = p.get("cash", 0)
            virt_return = p.get("total_return_pct", 0)
            virt_positions = p.get("positions", [])
            real_positions = p.get("real_positions", [])

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
                for pos in real_positions:
                    analysis = _analyze_symbol(pos["symbol"])
                    entry = pos.get("entry_price", 0)
                    current = analysis["price"] if analysis else pos.get("last_price", entry)
                    pnl_pct = (current - entry) / entry * 100 if entry else 0
                    emoji = "🟢" if analysis and analysis["direction"] == "KAUF" else ("🔴" if analysis and analysis["direction"] == "VERKAUF" else "🟡")
                    rec = analysis["direction"] if analysis else "HALTEN"
                    lines.append(f"{emoji} {pos['symbol']} @ {telegram.fmt_eur(current)} ({pnl_pct:+.2f}%) → <b>{rec}</b>")
            else:
                lines.append("Keine realen Positionen.")

            lines.append("\n<b>KAUFEMPFEHLUNGEN</b>")
            if top_recs:
                buy_recs = [r for r in top_recs if r["direction"] == "KAUF"]
                if buy_recs:
                    for s in buy_recs:
                        lines.append(f"🟢 {s['symbol']} ({s['name']}) @ {telegram.fmt_eur(s['preis'])} — Score {s['score']}/100")
                        if s.get("stop_loss") and s.get("take_profit"):
                            lines.append(f"   SL: {telegram.fmt_eur(s['stop_loss'])} | TP: {telegram.fmt_eur(s['take_profit'])}")
                else:
                    lines.append("Aktuell keine Kaufempfehlungen.")
            else:
                lines.append("Keine Empfehlungen verfügbar.")

            telegram._send_message("\n".join(lines), token=token, chat_id=chat_id)
        except Exception:
            pass

    return {"task": "portfolio_report", "timestamp": datetime.now(timezone.utc).isoformat()}


def morning_report(notify: bool = True) -> dict:
    """Morgendlicher 7-Uhr Report mit Kaufempfehlungen für den Handelstag."""
    now_cet = _now_cet()
    if now_cet.hour != 7:
        return {"task": "morning_report", "skipped": True, "reason": "Nicht 7 Uhr CET"}

    recs = signals.generate_recommendations().get("suggestions", [])
    buy_recs = [r for r in recs if r["direction"] == "KAUF"][:5]

    if notify:
        for user_id in _get_active_users():
            settings = db_store.get_settings(user_id)
            token, chat_id = _user_telegram_cfg(user_id)
            if not chat_id or not settings.get("report_enabled", True):
                continue
            try:
                lines = [
                    f"🌅 <b>Guten Morgen!Handelstag Report ({now_cet.strftime('%d.%m.%Y')})</b>",
                    "",
                    "<b>TOP KAUFEMPFEHLUNGEN</b>",
                ]
                if buy_recs:
                    for s in buy_recs:
                        lines.append(
                            f"🟢 <b>{s['symbol']}</b> ({s['name']}) @ {telegram.fmt_eur(s['preis'])} — Score {s['score']}/100"
                        )
                        lines.append(f"   Einstieg: {telegram.fmt_eur(s['einstieg_von'])} – {telegram.fmt_eur(s['einstieg_bis'])}")
                        if s.get("stop_loss"):
                            lines.append(f"   SL: {telegram.fmt_eur(s['stop_loss'])}")
                        if s.get("take_profit"):
                            lines.append(f"   TP: {telegram.fmt_eur(s['take_profit'])}")
                        lines.append(f"   Begründung: {s['begruendung'][:120]}...")
                else:
                    lines.append("Aktuell keine klaren Kaufempfehlungen.")

                telegram._send_message("\n".join(lines), token=token, chat_id=chat_id)
            except Exception as e:
                print(f"[morning_report] user_id={user_id} error: {e}")

    return {
        "task": "morning_report",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "buy_recommendations": len(buy_recs),
    }
