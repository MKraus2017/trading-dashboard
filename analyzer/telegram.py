"""Telegram-Benachrichtigungen für Trades und Zusammenfassungen."""
import os
from typing import List, Optional

import requests

import config


def _telegram_creds(token: str = None, chat_id: str = None) -> tuple:
    """Token/Chat-ID kommen bevorzugt aus Umgebungsvariablen (sicher gegen Datenverlust)."""
    t = token or os.environ.get("TELEGRAM_BOT_TOKEN") or config.TELEGRAM_BOT_TOKEN
    c = chat_id or os.environ.get("TELEGRAM_CHAT_ID") or config.TELEGRAM_CHAT_ID
    return t, c


def _send_message(text: str, token: str = None, chat_id: str = None) -> dict:
    token, chat_id = _telegram_creds(token, chat_id)
    if not token or not chat_id:
        return {"ok": False, "error": "Telegram nicht konfiguriert"}
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        r = requests.post(url, json=payload, timeout=15)
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def fmt_eur(n) -> str:
    if n is None:
        return "-"
    return f"{n:,.2f} €".replace(",", " ")


def fmt_pct(n) -> str:
    if n is None:
        return "-"
    return f"{n:+.2f}%"


def notify_virtual_trade(action: str, symbol: str, shares: float, price: float, reason: str = "", profit: Optional[float] = None):
    """Benachrichtigung beim virtuellen Kauf oder Verkauf."""
    if action.upper() == "BUY":
        text = f"🟢 <b>Virtueller Kauf</b>\n\n<b>{symbol}</b> @ {fmt_eur(price)}\nStück: {shares:.4f}\nInvestition: {fmt_eur(shares * price)}"
        if reason:
            text += f"\nGrund: {reason}"
    else:
        text = f"🔴 <b>Virtueller Verkauf</b>\n\n<b>{symbol}</b> @ {fmt_eur(price)}\nStück: {shares:.4f}"
        if profit is not None:
            emoji = "🟢" if profit >= 0 else "🔴"
            text += f"\n{emoji} Gewinn/Verlust: {fmt_eur(profit)}"
        if reason:
            text += f"\nGrund: {reason}"
    return _send_message(text)


def notify_real_trade(action: str, symbol: str, shares: float, price: float, invested: float):
    """Benachrichtigung beim manuell gemeldeten realen Trade."""
    emoji = "🟢" if action.upper() == "BUY" else "🔴"
    text = (
        f"{emoji} <b>Realer {action.upper()} gemeldet</b>\n\n"
        f"<b>{symbol}</b> @ {fmt_eur(price)}\n"
        f"Stück: {shares:.4f}\n"
        f"Betrag: {fmt_eur(invested)}"
    )
    return _send_message(text)


def notify_daily_summary(portfolio: dict):
    """Tägliche Zusammenfassung des Depots."""
    total = portfolio.get("total_value", 0)
    cash = portfolio.get("cash", 0)
    return_pct = portfolio.get("total_return_pct", 0)
    positions = portfolio.get("positions", [])
    pos_texts = []
    for pos in positions[:5]:
        pnl = pos.get("unrealized_eur", 0)
        pnl_pct = pos.get("unrealized_pct", 0)
        pos_texts.append(
            f"• {pos['symbol']}: {fmt_eur(pos['last_price'])} ({fmt_pct(pnl_pct)})"
        )
    positions_block = "\n".join(pos_texts) if pos_texts else "Keine offenen Positionen"
    emoji = "🟢" if return_pct >= 0 else "🔴"
    text = (
        f"📊 <b>Tägliche Depot-Zusammenfassung</b>\n\n"
        f"Depotwert: <b>{fmt_eur(total)}</b>\n"
        f"Cash: {fmt_eur(cash)}\n"
        f"{emoji} Gesamtrendite: {fmt_pct(return_pct)}\n\n"
        f"<b>Offene Positionen:</b>\n{positions_block}"
    )
    return _send_message(text)


def test_message() -> dict:
    return _send_message("🧪 Testnachricht vom Trading Bot.")
