"""Watch manual entry tickets and deliver invalidation notices; never place orders."""
import math
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from html import escape

_thread = None


@contextmanager
def connection():
    from analyzer import db_store
    conn = db_store.get_conn()
    try:
        with conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS signal_watches (
                token TEXT PRIMARY KEY, strategy TEXT NOT NULL,
                reason TEXT, sent_at REAL, lease_until REAL NOT NULL DEFAULT 0
            )''')
            yield conn
    finally:
        conn.close()


def enroll(token, strategy):
    with connection() as conn:
        conn.execute('INSERT OR IGNORE INTO signal_watches(token,strategy) VALUES (?,?)', (token, strategy))


def invalid_reason(ticket, ticker=None, analysis=None, now=None):
    now = now or datetime.now(timezone.utc)
    expires = datetime.fromisoformat(ticket['expires_at'])
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if now >= expires or ticket['status'] == 'expired':
        return 'Die fünfminütige Gültigkeit ist abgelaufen.'
    price = float((ticker or {}).get('last') or 0)
    if not math.isfinite(price) or price <= 0:
        return 'Aktuelle Marktdaten fehlen; der Einstieg ist nicht mehr verifizierbar.'
    if price <= ticket['stop_loss']:
        return 'Der Kurs hat den geplanten Stop-Loss erreicht oder unterschritten.'
    if ticket.get('take_profit') and price >= ticket['take_profit']:
        return 'Der Kurs hat das geplante Take-Profit bereits erreicht.'
    deviation = abs(price / ticket['entry_price'] - 1) * 100
    if deviation > ticket['max_price_deviation_pct']:
        return f"Kursabweichung {deviation:.2f}% überschreitet {ticket['max_price_deviation_pct']:.2f}%."
    if not analysis:
        return 'Die Strategie kann das Einstiegssignal aktuell nicht mehr bestätigen.'
    if analysis.get('direction') != 'LONG':
        return 'Die Strategie liefert kein LONG-Einstiegssignal mehr.'
    return None


def invalidate(token, reason):
    with connection() as conn:
        row = conn.execute('SELECT status FROM okx_order_tickets WHERE token=?', (token,)).fetchone()
        if not row or row['status'] not in ('pending', 'approved', 'expired'):
            return
        conn.execute("UPDATE okx_order_tickets SET status='expired' WHERE token=?", (token,))
        conn.execute('UPDATE signal_watches SET reason=COALESCE(reason, ?) WHERE token=?', (reason, token))


def deliver(ticket, send, bot_token):
    # A lease avoids concurrent sends; unsuccessful deliveries are retried.
    now = time.time()
    with connection() as conn:
        claimed = conn.execute('''UPDATE signal_watches SET lease_until=?
            WHERE token=? AND sent_at IS NULL AND reason IS NOT NULL AND lease_until<=?''',
            (now + 120, ticket['token'], now)).rowcount
        row = conn.execute('SELECT reason FROM signal_watches WHERE token=?', (ticket['token'],)).fetchone()
    if not claimed:
        return False
    message = (f"⛔ <b>Signal ungültig – nicht mehr einsteigen</b>\n"
               f"{escape(ticket['inst_id'])} · Ticket #{escape(ticket['token'])}\n\n"
               f"{escape(row['reason'])}\nBitte auf ein neues, geprüftes Ticket warten.\n"
               'Falls du bereits gekauft hast: Dies ist keine Verkaufsanweisung. '
               'Prüfe deine bestehende Position und die gesetzten SL/TP separat.')
    try:
        result = send(message, token=bot_token, chat_id=ticket['chat_id'])
        if not result.get('ok'):
            return False
        with connection() as conn:
            conn.execute('UPDATE signal_watches SET sent_at=? WHERE token=?', (time.time(), ticket['token']))
        return True
    except Exception:
        return False  # retry after the lease, without logging credentials


def monitor():
    from analyzer import db_store, okx_client, crypto_signals, telegram
    with connection() as conn:
        rows = conn.execute('''SELECT t.*, w.strategy, w.reason AS invalidation_reason
            FROM signal_watches w JOIN okx_order_tickets t ON t.token=w.token
            WHERE w.sent_at IS NULL AND t.status IN ('pending','approved','expired')''').fetchall()
        tickets = [dict(row) for row in rows]
    sent = 0
    for ticket in tickets:
        try:
            reason = ticket['invalidation_reason']
            settings = db_store.get_settings(ticket['user_id'])
            if not reason:
                expires = datetime.fromisoformat(ticket['expires_at'])
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if ticket['status'] == 'expired' or datetime.now(timezone.utc) >= expires:
                    reason = 'Die fünfminütige Gültigkeit ist abgelaufen.'
                elif settings.get('crypto_strategy_version', 'classic') != ticket['strategy']:
                    reason = 'Die Strategie wurde seit Erstellung des Tickets gewechselt.'
                else:
                    ticker = okx_client.fetch_ticker(ticket['inst_id'])
                    analysis = crypto_signals.analyze_crypto_symbol(ticket['symbol'], strategy_version=ticket['strategy'])
                    reason = invalid_reason(ticket, ticker, analysis)
                if reason:
                    invalidate(ticket['token'], reason)
            if reason and settings.get('telegram_bot_token'):
                sent += int(deliver(ticket, telegram._send_message, settings['telegram_bot_token']))
        except Exception:
            # An unverified ticket must not remain a usable entry suggestion.
            invalidate(ticket['token'], 'Überprüfung fehlgeschlagen; bitte nicht mehr auf dieses Ticket einsteigen.')
    return {'ok': True, 'checked': len(tickets), 'notifications_sent': sent}


def start():
    global _thread
    if _thread and _thread.is_alive():
        return
    def run():
        while True:
            try:
                monitor()
            except Exception:
                print('[SignalWatch] Überprüfung fehlgeschlagen; nächster Versuch in 20 Sekunden.')
            time.sleep(20)
    _thread = threading.Thread(target=run, daemon=True, name='signal-watch')
    _thread.start()
