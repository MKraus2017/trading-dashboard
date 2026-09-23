"""Isolated Telegram interaction demo. No exchange API or order code is called."""
import secrets
import time
from contextlib import contextmanager


@contextmanager
def connection():
    from analyzer import db_store
    conn = db_store.get_conn()
    conn.execute('''CREATE TABLE IF NOT EXISTS telegram_demo_receipts (
        token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, chat_id TEXT NOT NULL,
        status TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
        received_at REAL
    )''')
    conn.commit()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def latest(user_id):
    with connection() as conn:
        conn.execute("UPDATE telegram_demo_receipts SET status='expired' WHERE status='pending' AND expires_at<=?", (time.time(),))
        row = conn.execute('SELECT * FROM telegram_demo_receipts WHERE user_id=? ORDER BY created_at DESC LIMIT 1', (user_id,)).fetchone()
        return dict(row) if row else None


def send(user_id, bot_token, chat_id):
    from analyzer import telegram
    # Only a private chat can acknowledge this personal demo.
    if not str(chat_id).isdigit() or int(chat_id) <= 0:
        return {"ok": False, "error": "Für diesen Test bitte einen privaten Telegram-Chat konfigurieren."}
    token = secrets.token_hex(8)
    now = time.time()
    with connection() as conn:
        conn.execute("INSERT INTO telegram_demo_receipts VALUES (?, ?, ?, 'pending', ?, ?, NULL)",
                     (token, user_id, str(chat_id), now, now + 300))
    text = (
        '🧪 <b>DEMO – Ordervorschlag DOGE</b>\n'
        '<b>Nur erfundene Beispielwerte. Keine echte Order.</b>\n\n'
        'Richtung: Kauf / Long\nBeispielkurs: 0,10000 USDC\n'
        'Signalhebel: 3× → Demo-Hebel: 2×\n'
        'Beispiel-Margin: 150 USDC\nPositionswert: 300 USDC\n'
        'Stop-Loss: 0,09700\nTake-Profit: 0,10500\n\n'
        'Geplanter Verlust am SL inkl. geschätzter Kosten: 9,78 USDC\n'
        'Beispiel-Risikobudget: 11,97 USDC (1,5 % von 798 USDC). '
        'Ein Stop garantiert keine Verlustobergrenze.\n\n'
        '⏳ Test-Schaltflächen sind 5 Minuten gültig.\n'
        'Tippe auf Freigeben oder Ablehnen. Der Bot bestätigt den Empfang; '
        'deine Antwort wird ausschließlich als Demo gespeichert. '
        'Die Hebelwerte sind keine Bestätigung der Konto-/API-Verfügbarkeit.'
    )
    result = telegram._send_message(text, token=bot_token, chat_id=chat_id, reply_markup={
        "inline_keyboard": [[
            {"text": "✅ Demo freigeben", "callback_data": "demo:approve:" + token},
            {"text": "❌ Demo ablehnen", "callback_data": "demo:reject:" + token},
        ]]
    })
    if not result.get('ok'):
        with connection() as conn:
            conn.execute("UPDATE telegram_demo_receipts SET status='send_failed' WHERE token=?", (token,))
        return {"ok": False, "error": "Telegram konnte die Demo nicht zustellen."}
    return {"ok": True, "demo": latest(user_id)}


def receive(user_id, update, bot_token, expected_chat):
    from analyzer import telegram
    callback = update.get('callback_query') or {}
    raw = str(callback.get('data') or '')
    if not raw.startswith('demo:'):
        return None
    chat = (callback.get('message') or {}).get('chat') or {}
    sender = callback.get('from') or {}
    if (str(chat.get('id')) != str(expected_chat) or chat.get('type') != 'private'
            or str(sender.get('id')) != str(expected_chat)):
        return {"ok": False, "error": "Nicht autorisierter Demo-Absender."}
    parts = raw.split(':')
    if len(parts) != 3 or parts[1] not in ('approve', 'reject'):
        return {"ok": False, "error": "Ungültige Demo-Antwort."}
    status = 'approved' if parts[1] == 'approve' else 'rejected'
    now = time.time()
    with connection() as conn:
        changed = conn.execute('''UPDATE telegram_demo_receipts SET status=?, received_at=?
            WHERE token=? AND user_id=? AND chat_id=? AND status='pending' AND expires_at>?''',
            (status, now, parts[2], user_id, str(expected_chat), now)).rowcount == 1
    summary = ('Demo-Freigabe empfangen ✅' if status == 'approved' else 'Demo-Ablehnung empfangen ✅') if changed else 'Demo bereits bearbeitet oder abgelaufen.'
    telegram.answer_callback_query(callback.get('id', ''), summary, token=bot_token)
    if changed:
        telegram._send_message(summary + '\nDein Klick ist im Dashboard angekommen und gespeichert.\n🧪 Es wurde keine Order ausgeführt.', token=bot_token, chat_id=expected_chat)
    return {"ok": True, "received": changed, "demo_only": True}
