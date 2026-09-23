"""Telegram-Freigaben fuer vorbereitete OKX-Ordertickets.

Dieses Modul fuehrt absichtlich KEINE Order aus. Es berechnet ein risikobegrenztes
Spot-Ticket, verschickt es an den konfigurierten Telegram-Chat und nimmt eine
einmalige, kurzlebige Freigabe oder Ablehnung entgegen. Die eigentliche Order
muss der Nutzer anschliessend selbst bei OKX platzieren.
"""
import hashlib
import hmac
import math
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

import config
from analyzer import db_store, okx_client, okx_trading_client, telegram


RISK_PCT = 0.015
MAX_CAPITAL_PCT = 0.25
MAX_ACTIVE_POSITIONS = 2
TICKET_TTL_MINUTES = 5
MAX_PRICE_DEVIATION_PCT = 0.75
MIN_TICKET_USDC = 10.0
OKX_AVAILABLE_LEVERAGES = (1, 2, 3, 5, 10, 50)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def reduced_leverage(signal_leverage: int) -> int:
    """Etwa zwei Drittel, abgerundet auf eine tatsaechlich angebotene OKX-Stufe."""
    target = max(1, math.floor(max(1, int(signal_leverage)) * 2 / 3))
    return max(level for level in OKX_AVAILABLE_LEVERAGES if level <= target)


def webhook_secret(bot_token: str) -> str:
    key = str(config.FLASK_SECRET_KEY).encode("utf-8")
    return hmac.new(key, bot_token.encode("utf-8"), hashlib.sha256).hexdigest()


def calculate_ticket_size(balance_usdc: float, entry_price: float, stop_loss: float) -> dict:
    if balance_usdc <= 0 or entry_price <= 0 or stop_loss <= 0 or stop_loss >= entry_price:
        raise ValueError("Balance, Einstieg und Stop-Loss sind fuer ein LONG-Ticket ungueltig.")
    stop_pct = (entry_price - stop_loss) / entry_price
    round_trip_cost_pct = 2 * (config.CRYPTO_TAKER_FEE_PCT + config.CRYPTO_SLIPPAGE_PCT)
    risk_budget = balance_usdc * RISK_PCT
    amount_by_risk = risk_budget / (stop_pct + round_trip_cost_pct)
    amount_usdc = min(amount_by_risk, balance_usdc * MAX_CAPITAL_PCT)
    risk_usdc = amount_usdc * (stop_pct + round_trip_cost_pct)
    return {
        "amount_usdc": round(amount_usdc, 2),
        "risk_usdc": round(risk_usdc, 2),
        "risk_budget_usdc": round(risk_budget, 2),
        "stop_distance_pct": round(stop_pct * 100, 2),
        "cost_buffer_pct": round(round_trip_cost_pct * 100, 2),
    }


def _credentials(user_id: int) -> tuple:
    settings = db_store.get_settings(user_id)
    token = settings.get("telegram_bot_token") or config.TELEGRAM_BOT_TOKEN
    chat_id = settings.get("telegram_chat_id") or config.TELEGRAM_CHAT_ID
    return token, str(chat_id or "")


def create_and_send_ticket(user_id: int, signal: dict) -> dict:
    settings = db_store.get_settings(user_id)
    if not settings.get("telegram_trade_confirmation_enabled"):
        return {"ok": False, "error": "Telegram-Handyfreigabe ist nicht aktiviert."}
    token, chat_id = _credentials(user_id)
    if not token or not chat_id:
        return {"ok": False, "error": "Telegram Bot-Token oder Chat-ID fehlt."}
    if signal.get("direction") != "LONG":
        return {"ok": False, "error": "Reale OKX-Tickets werden nur fuer LONG/Spot erstellt."}

    symbol = str(signal.get("symbol", "")).upper().strip()
    entry = float(signal.get("price") or 0)
    stop_loss = float(signal.get("stop_loss") or 0)
    take_profit = float(signal.get("take_profit") or 0) or None
    if not symbol:
        return {"ok": False, "error": "Symbol fehlt."}

    open_count = len(db_store.get_open_okx_spot_positions(user_id))
    active_tickets = db_store.count_active_okx_order_tickets(user_id)
    if open_count + active_tickets >= MAX_ACTIVE_POSITIONS:
        return {"ok": False, "error": "Limit von zwei offenen/freigegebenen Positionen erreicht."}

    since = (_now() - timedelta(hours=2)).isoformat()
    if db_store.has_recent_okx_order_ticket(user_id, symbol, since):
        return {"ok": False, "error": f"Fuer {symbol} gibt es bereits ein aktuelles Ticket."}

    balance = okx_trading_client.get_balance("USDC")
    if not balance.get("ok"):
        return {"ok": False, "error": f"OKX-Balance nicht lesbar: {balance.get('error')}"}
    capital = float(balance.get("total") or balance.get("available") or 0)
    sizing = calculate_ticket_size(capital, entry, stop_loss)
    available = float(balance.get("available") or 0)
    sizing["amount_usdc"] = round(min(sizing["amount_usdc"], available * MAX_CAPITAL_PCT), 2)
    stop_and_cost_pct = ((entry - stop_loss) / entry) + 2 * (config.CRYPTO_TAKER_FEE_PCT + config.CRYPTO_SLIPPAGE_PCT)
    sizing["risk_usdc"] = round(sizing["amount_usdc"] * stop_and_cost_pct, 2)
    if sizing["amount_usdc"] < MIN_TICKET_USDC:
        return {"ok": False, "error": "Der risikogerechte Ticketbetrag liegt unter 10 USDC."}

    signal_leverage = int(signal.get("leverage") or 1)
    suggested = reduced_leverage(signal_leverage)
    created = _now()
    expires = created + timedelta(minutes=TICKET_TTL_MINUTES)
    ticket_token = secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12]
    reason = "; ".join((signal.get("details") or [])[:3])
    ticket = {
        "token": ticket_token,
        "user_id": user_id,
        "chat_id": chat_id,
        "symbol": symbol,
        "inst_id": okx_trading_client.to_spot_inst_id(symbol, "USDC"),
        "direction": "LONG",
        "signal_leverage": signal_leverage,
        "suggested_leverage": suggested,
        "execution_leverage": 1,
        "score": signal.get("score"),
        "entry_price": entry,
        "amount_usdc": sizing["amount_usdc"],
        "risk_usdc": sizing["risk_usdc"],
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "max_price_deviation_pct": MAX_PRICE_DEVIATION_PCT,
        "reason": reason,
        "created_at": created.isoformat(),
        "expires_at": expires.isoformat(),
    }
    db_store.create_okx_order_ticket(**ticket)

    text = (
        f"📱 <b>OKX-Orderticket #{ticket_token}</b>\n\n"
        f"🟢 {symbol}-USDC · Spot LONG\n"
        f"Signal: {signal_leverage}x → vorsichtiger Vorschlag: {suggested}x\n"
        f"<b>Auf diesem Konto ausführbar: Spot 1x (kein Hebel)</b>\n"
        f"Betrag: <b>{ticket['amount_usdc']:.2f} USDC</b>\n"
        f"Referenzkurs: {entry:.8g}\nSL: {stop_loss:.8g} · TP: {take_profit or '-'}\n"
        f"Max. Risiko inkl. Kostenpuffer: {ticket['risk_usdc']:.2f} USDC "
        f"(≤ {RISK_PCT * 100:.1f}% des Kontos)\n\n"
        f"Gültig: {TICKET_TTL_MINUTES} Minuten. Freigabe erzeugt nur ein manuelles "
        f"Orderticket; <b>es wird keine Order automatisch gesendet.</b>"
    )
    keyboard = {"inline_keyboard": [[
        {"text": "✅ Ticket freigeben", "callback_data": f"ticket_approve:{ticket_token}"},
        {"text": "❌ Ablehnen", "callback_data": f"ticket_reject:{ticket_token}"},
    ]]}
    sent = telegram._send_message(text, token=token, chat_id=chat_id, reply_markup=keyboard)
    if not sent.get("ok"):
        db_store.review_okx_order_ticket(ticket_token, chat_id, "expired")
        return {"ok": False, "error": f"Telegram-Versand fehlgeschlagen: {sent.get('description') or sent.get('error')}"}
    from analyzer import signal_watch
    signal_watch.enroll(ticket_token, signal.get('strategy_version') or settings.get('crypto_strategy_version', 'classic'))
    return {"ok": True, "ticket": ticket}


def _review_ticket(user_id: int, chat_id: str, ticket_token: str, approve: bool) -> dict:
    ticket = db_store.get_okx_order_ticket(ticket_token)
    if not ticket or ticket["user_id"] != user_id or str(ticket["chat_id"]) != str(chat_id):
        return {"ok": False, "message": "Ticket nicht gefunden oder falscher Chat."}
    if ticket["status"] != "pending":
        return {"ok": False, "message": f"Ticket ist bereits {ticket['status']}."}
    expires = datetime.fromisoformat(ticket["expires_at"])
    if _now() >= expires:
        db_store.review_okx_order_ticket(ticket_token, chat_id, "expired")
        return {"ok": False, "message": "Ticket ist abgelaufen. Bitte neu berechnen lassen."}
    if not approve:
        db_store.review_okx_order_ticket(ticket_token, chat_id, "rejected")
        return {"ok": True, "message": "Ticket abgelehnt. Es wurde keine Order gesendet."}

    ticker = okx_client.fetch_ticker(ticket["symbol"])
    current = float(ticker.get("last") or 0) if ticker else 0
    if not current:
        return {"ok": False, "message": "Live-Kurs nicht verfügbar; Ticket nicht freigegeben."}
    deviation = abs(current - ticket["entry_price"]) / ticket["entry_price"] * 100
    if deviation > ticket["max_price_deviation_pct"]:
        db_store.review_okx_order_ticket(ticket_token, chat_id, "expired", current)
        return {"ok": False, "message": f"Kurs hat sich um {deviation:.2f}% bewegt; Ticket verworfen."}

    balance = okx_trading_client.get_balance("USDC")
    if not balance.get("ok") or float(balance.get("available") or 0) < ticket["amount_usdc"]:
        return {"ok": False, "message": "Verfügbares USDC-Guthaben reicht nicht; keine Freigabe."}
    capital = float(balance.get("total") or balance.get("available") or 0)
    if ticket["stop_loss"] >= current:
        db_store.review_okx_order_ticket(ticket_token, chat_id, "expired", current)
        return {"ok": False, "message": "Der Live-Kurs liegt bereits am/unter dem Stop-Loss; Ticket verworfen."}
    cost_pct = 2 * (config.CRYPTO_TAKER_FEE_PCT + config.CRYPTO_SLIPPAGE_PCT)
    reviewed_risk = ticket["amount_usdc"] * ((current - ticket["stop_loss"]) / current + cost_pct)
    if reviewed_risk > capital * RISK_PCT or ticket["amount_usdc"] > capital * MAX_CAPITAL_PCT:
        db_store.review_okx_order_ticket(ticket_token, chat_id, "expired", current)
        return {"ok": False, "message": "Risiko-/Kapitalgrenze ist nicht mehr eingehalten; Ticket verworfen."}
    if not db_store.review_okx_order_ticket(ticket_token, chat_id, "approved", current):
        return {"ok": False, "message": "Ticket wurde bereits anderweitig bearbeitet."}
    link = f"https://my.okx.com/de/trade-spot/{ticket['symbol'].lower()}-usdc"
    return {
        "ok": True,
        "message": (
            f"✅ Ticket #{ticket_token} freigegeben. Keine Order wurde gesendet.\n\n"
            f"Manuell auf OKX eingeben: {ticket['symbol']}-USDC, Kauf {ticket['amount_usdc']:.2f} USDC, "
            f"SL {ticket['stop_loss']:.8g}, TP {ticket['take_profit'] or '-'}\n"
            f"<a href=\"{link}\">OKX Spot öffnen</a>"
        ),
    }


def handle_update(user_id: int, update: dict) -> dict:
    token, expected_chat = _credentials(user_id)
    callback = update.get("callback_query") or {}
    message = update.get("message") or {}
    callback_id = callback.get("id")
    if callback:
        chat_id = str((callback.get("message") or {}).get("chat", {}).get("id", ""))
        raw = str(callback.get("data") or "")
    else:
        chat_id = str(message.get("chat", {}).get("id", ""))
        raw = str(message.get("text") or "").strip()
    if chat_id != str(expected_chat):
        if callback_id:
            telegram.answer_callback_query(callback_id, "Nicht autorisierter Chat.", token=token)
        return {"ok": False, "error": "Nicht autorisierter Chat."}

    approve: Optional[bool] = None
    ticket_token = ""
    if raw.startswith("ticket_approve:"):
        approve, ticket_token = True, raw.split(":", 1)[1]
    elif raw.startswith("ticket_reject:"):
        approve, ticket_token = False, raw.split(":", 1)[1]
    elif raw.lower().startswith("/confirm "):
        approve, ticket_token = True, raw.split(None, 1)[1].strip()
    elif raw.lower().startswith("/reject "):
        approve, ticket_token = False, raw.split(None, 1)[1].strip()
    else:
        return {"ok": True, "ignored": True}

    result = _review_ticket(user_id, chat_id, ticket_token, approve)
    if callback_id:
        telegram.answer_callback_query(callback_id, result["message"].split("\n", 1)[0], token=token)
    telegram._send_message(result["message"], token=token, chat_id=chat_id)
    return result


def register_webhook(user_id: int, public_base_url: str) -> dict:
    token, chat_id = _credentials(user_id)
    if not token or not chat_id:
        return {"ok": False, "error": "Telegram Bot-Token oder Chat-ID fehlt."}
    secret = webhook_secret(token)
    url = public_base_url.rstrip("/") + "/api/telegram/order_ticket/webhook"
    try:
        current = requests.post(f"https://api.telegram.org/bot{token}/getWebhookInfo", timeout=15).json()
        if not current.get('ok'):
            return {'ok': False, 'error': 'Bestehender Telegram-Empfang konnte nicht geprüft werden.'}
        existing_url = current.get('result', {}).get('url', '')
        if existing_url and existing_url != url:
            return {'ok': False, 'error': 'Der Bot hat bereits einen anderen Webhook. Er wurde nicht überschrieben.'}
        response = requests.post(
            f"https://api.telegram.org/bot{token}/setWebhook",
            json={"url": url, "secret_token": secret, "allowed_updates": ["message", "callback_query"]},
            timeout=15,
        ).json()
        if not response.get("ok"):
            return {"ok": False, "error": response.get("description", "Webhook konnte nicht registriert werden")}
        return {"ok": True, "webhook_url": url}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def webhook_is_authorized(user_id: int, supplied_secret: str) -> bool:
    token, _ = _credentials(user_id)
    return bool(token and supplied_secret and hmac.compare_digest(webhook_secret(token), supplied_secret))
