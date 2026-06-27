"""Flask-Dashboard für Trading-Bot (Multi-User)."""
import json
import os
import shutil
import tempfile
import time
from datetime import datetime, timedelta
from functools import wraps

import bcrypt
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

import config
from analyzer import auto_trader, db_store, portfolio, scheduler_tasks, signals, yahoo_client, telegram as telegram_client

app = Flask(__name__)
app.secret_key = config.FLASK_SECRET_KEY


def _hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def _check_password(pw: str, hashed: str) -> bool:
    return bcrypt.checkpw(pw.encode(), hashed.encode())


def get_current_user_id():
    return session.get("user_id")


def is_logged_in():
    return bool(get_current_user_id())


def ensure_user():
    """Holt user_id aus Session oder legt Default-User an (für Rückwärtskompatibilität)."""
    uid = get_current_user_id()
    if uid:
        return uid
    user = db_store.get_user_by_username("default")
    if not user:
        user_id = db_store.create_user("default", _hash_password(config.DASHBOARD_PASSWORD))
        user = db_store.get_user_by_id(user_id)
    elif not user["password_hash"]:
        db_store.set_user_password(user["id"], _hash_password(config.DASHBOARD_PASSWORD))
    return user["id"]


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_logged_in():
            if request.is_json:
                return jsonify({"ok": False, "error": "Nicht angemeldet"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated


# --- Auth Routes ---

@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            return render_template("login.html", error="Bitte Benutzername und Passwort eingeben")
        user = db_store.get_user_by_username(username)
        if not user or not _check_password(password, user["password_hash"]):
            return render_template("login.html", error="Login fehlgeschlagen")
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        return redirect(url_for("index"))
    return render_template("login.html", error=None)


@app.route("/register", methods=["GET", "POST"])
def register_page():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if not username or not password:
            return render_template("register.html", error="Benutzername und Passwort erforderlich")
        if password != confirm:
            return render_template("register.html", error="Passwörter stimmen nicht überein")
        if len(password) < 6:
            return render_template("register.html", error="Passwort muss mindestens 6 Zeichen haben")
        user_id = db_store.create_user(username, _hash_password(password))
        if not user_id:
            return render_template("register.html", error="Benutzername bereits vergeben")
        session["user_id"] = user_id
        session["username"] = username
        return redirect(url_for("index"))
    return render_template("register.html", error=None)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login_page"))


# --- Main ---

@app.route("/")
@login_required
def index():
    return render_template("index.html", username=session.get("username", ""))


@app.route("/settings")
@login_required
def settings_page():
    return render_template("settings.html", username=session.get("username", ""))


# --- API ---

@app.route("/api/portfolio")
@login_required
def api_portfolio():
    uid = get_current_user_id()
    p, alerts = portfolio.evaluate_portfolio(uid)
    # Preise für echte Positionen anreichern
    p = portfolio.enrich_real_positions(p)
    p = _calc_real_guv(p)
    # Vergleichs- & Backtest-Daten anreichern
    p["comparison"] = portfolio.calculate_comparison(p)
    p["backtest"] = portfolio.run_backtest(p)
    return jsonify({"portfolio": p, "alerts": alerts})


def _calc_real_guv(p: dict) -> dict:
    """Berechnet GuV-Zahlen für das reale TR-Depot."""
    positions = p.get("real_positions", [])
    trades = p.get("real_trades", [])
    total_invested = sum(pos.get("invested", 0) for pos in positions)
    current_value = sum(pos.get("current_value", 0) for pos in positions)
    unrealized = current_value - total_invested

    total_buy = sum(t.get("invested", t.get("shares", 0) * t.get("price", 0)) for t in trades if t.get("action") == "BUY")
    total_sell_proceeds = sum(t.get("shares", 0) * t.get("price", 0) for t in trades if t.get("action") == "SELL")
    realized = total_sell_proceeds - (total_buy - total_invested)

    p["real_guv"] = {
        "invested": round(total_invested, 2),
        "current_value": round(current_value, 2),
        "unrealized": round(unrealized, 2),
        "unrealized_pct": round(unrealized / total_invested * 100, 2) if total_invested else 0.0,
        "realized": round(realized, 2),
        "total_return": round(unrealized + realized, 2),
        "total_return_pct": round((unrealized + realized) / max(total_invested, 1) * 100, 2) if total_invested > 0 else 0.0,
    }
    return p


@app.route("/api/recommendations", methods=["GET", "POST"])
@login_required
def api_recommendations():
    uid = get_current_user_id()
    dry_run = request.args.get("dry_run", "false").lower() == "true"
    if request.method == "GET":
        return jsonify(signals.generate_recommendations())
    result = auto_trader.run_auto_trading(uid, dry_run=dry_run)
    return jsonify(result)


@app.route("/api/buy", methods=["POST"])
@login_required
def api_buy():
    uid = get_current_user_id()
    body = request.get_json() or {}
    symbol = body.get("symbol", "").upper().strip()
    amount = body.get("amount")
    if not symbol:
        return jsonify({"ok": False, "error": "Symbol fehlt"})
    amount = float(amount) if amount else None
    res = portfolio.buy(uid, symbol, amount_eur=amount)
    return jsonify(res)


@app.route("/api/sell", methods=["POST"])
@login_required
def api_sell():
    uid = get_current_user_id()
    body = request.get_json() or {}
    symbol = body.get("symbol", "").upper().strip()
    if not symbol:
        return jsonify({"ok": False, "error": "Symbol fehlt"})
    res = portfolio.sell(uid, symbol)
    return jsonify(res)


@app.route("/api/reset_portfolio", methods=["POST"])
@login_required
def api_reset_portfolio():
    uid = get_current_user_id()
    p = portfolio.reset_portfolio(uid)
    return jsonify(p)


@app.route("/api/real_position", methods=["POST"])
@login_required
def api_real_position():
    uid = get_current_user_id()
    body = request.get_json() or {}
    symbol = body.get("symbol", "").upper().strip()
    action = body.get("action")
    shares = float(body.get("shares", 0))
    price = float(body.get("price", 0))
    if not symbol or action not in ("buy", "sell") or shares <= 0 or price <= 0:
        return jsonify({"ok": False, "error": "Ungültige Eingabe"})

    p = portfolio.get_portfolio(uid)
    p.setdefault("real_positions", [])
    p.setdefault("real_trades", [])

    if action == "buy":
        existing = next((x for x in p["real_positions"] if x["symbol"] == symbol), None)
        invested = shares * price
        if existing:
            total_shares = existing["shares"] + shares
            total_invested = existing["invested"] + invested
            existing["shares"] = round(total_shares, 6)
            existing["invested"] = round(total_invested, 2)
            existing["entry_price"] = round(total_invested / total_shares, 4)
        else:
            p["real_positions"].append({
                "symbol": symbol,
                "shares": round(shares, 6),
                "entry_price": round(price, 4),
                "invested": round(invested, 2),
                "opened_at": datetime.utcnow().isoformat(),
            })
        p["real_trades"].append({
            "time": datetime.utcnow().isoformat(),
            "symbol": symbol,
            "action": "BUY",
            "shares": shares,
            "price": price,
            "invested": invested,
        })
    elif action == "sell":
        pos = next((x for x in p["real_positions"] if x["symbol"] == symbol), None)
        if not pos:
            return jsonify({"ok": False, "error": "Position nicht vorhanden"})
        close_time = datetime.utcnow().isoformat()
        if shares >= pos["shares"]:
            p["real_positions"] = [x for x in p["real_positions"] if x["symbol"] != symbol]
        else:
            ratio = 1 - shares / pos["shares"]
            pos["shares"] = round(pos["shares"] - shares, 6)
            pos["invested"] = round(pos["invested"] * ratio, 2)
            pos["entry_price"] = round(pos["invested"] / pos["shares"], 4)
        p["real_trades"].append({
            "time": close_time,
            "closed_at": close_time,
            "symbol": symbol,
            "action": "SELL",
            "shares": shares,
            "price": price,
        })

    portfolio._save(uid, p)
    return jsonify({"ok": True, "real_positions": p["real_positions"]})


@app.route("/api/universe")
@login_required
def api_universe():
    from config import get_universe
    return jsonify({"universe": get_universe()})


@app.route("/api/price")
@login_required
def api_price():
    symbol = request.args.get("symbol", "").upper().strip()
    if not symbol:
        return jsonify({"ok": False, "error": "Symbol fehlt"})
    price = yahoo_client.fetch_latest_price(symbol)
    return jsonify({"ok": bool(price), "symbol": symbol, "price": price})


@app.route("/api/settings", methods=["GET", "POST"])
@login_required
def api_settings():
    uid = get_current_user_id()
    if request.method == "GET":
        s = db_store.get_settings(uid)
        # Zeige Quelle der Telegram-Creds an
        s["telegram_bot_token_source"] = "Env" if os.environ.get("TELEGRAM_BOT_TOKEN") else ("DB" if s.get("telegram_bot_token") else "config")
        s["telegram_chat_id_source"] = "Env" if os.environ.get("TELEGRAM_CHAT_ID") else ("DB" if s.get("telegram_chat_id") else "config")
        return jsonify(s)
    body = request.get_json() or {}
    try:
        db_store.save_settings(uid, {
            "telegram_bot_token": body.get("telegram_bot_token", ""),
            "telegram_chat_id": body.get("telegram_chat_id", ""),
            "auto_trade_enabled": body.get("auto_trade_enabled", True),
            "report_enabled": body.get("report_enabled", True),
        })
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/telegram_test", methods=["POST"])
@login_required
def api_telegram_test():
    uid = get_current_user_id()
    settings = db_store.get_settings(uid)
    token = settings.get("telegram_bot_token") or config.TELEGRAM_BOT_TOKEN
    chat_id = settings.get("telegram_chat_id") or config.TELEGRAM_CHAT_ID
    res = telegram_client._send_message("🧪 Testnachricht vom Trading Bot Dashboard.", token=token, chat_id=chat_id)
    return jsonify(res)


@app.route("/api/telegram_status", methods=["GET"])
@login_required
def api_telegram_status():
    """Liefert Diagnose-Informationen zur Telegram-Konfiguration und führt einen echten API-Test aus."""
    try:
        uid = get_current_user_id()
        settings = db_store.get_settings(uid)
        env_token = os.environ.get("TELEGRAM_BOT_TOKEN") or config.TELEGRAM_BOT_TOKEN
        env_chat = os.environ.get("TELEGRAM_CHAT_ID") or config.TELEGRAM_CHAT_ID
        db_token = settings.get("telegram_bot_token", "")
        db_chat = settings.get("telegram_chat_id", "")

        active_token = db_token or env_token
        active_chat = db_chat or env_chat
        token_source = "DB" if db_token else ("Env" if env_token else "None")
        chat_source = "DB" if db_chat else ("Env" if env_chat else "None")

        result = {
            "token_source": token_source,
            "token_length": len(active_token),
            "token_prefix": active_token[:10] + "..." if len(active_token) > 10 else active_token,
            "chat_id": str(active_chat),
            "chat_id_source": chat_source,
        }

        if not active_token or not active_chat:
            result["test_result"] = {"ok": False, "error": "Token oder Chat-ID fehlen"}
            return jsonify(result)

        test_res = telegram_client._send_message("🧪 Testnachricht vom Trading Bot Dashboard.", token=active_token, chat_id=active_chat)
        result["test_result"] = test_res
        return jsonify(result)
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@app.errorhandler(Exception)
def handle_exception(e):
    """Globaler Fehler-Handler: jeden unerwarteten Fehler als JSON zurückgeben."""
    import traceback
    return jsonify({"ok": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@app.route("/api/db_status", methods=["GET"])
@login_required
def api_db_status():
    """Zeigt Diagnose-Informationen zur Datenbank und den Backups an."""
    uid = get_current_user_id()
    p = db_store.load_portfolio(uid) or {}
    real_positions = len(p.get("real_positions", []))
    real_trades = len(p.get("real_trades", []))
    virtual_positions = len(p.get("positions", []))
    virtual_trades = len(p.get("trades", []))

    db_path = db_store.DB_PATH
    backup_path = db_store._backup_db_path()

    status = {
        "db_path": db_path,
        "db_exists": os.path.exists(db_path),
        "db_size_bytes": db_store._db_size(db_path),
        "db_mtime": db_store._db_mtime(db_path),
        "repo_backup_path": backup_path,
        "repo_backup_exists": os.path.exists(backup_path),
        "repo_backup_size_bytes": db_store._db_size(backup_path),
        "repo_backup_mtime": db_store._db_mtime(backup_path),
        "users": db_store._count_users(db_path),
        "portfolios": db_store._count_portfolios(db_path),
        "real_positions": real_positions,
        "real_trades": real_trades,
        "virtual_positions": virtual_positions,
        "virtual_trades": virtual_trades,
        "newest_known": db_store._newest_existing(db_path, backup_path, db_store._newest_timestamped_backup() or ""),
    }
    return jsonify(status)


@app.route("/api/db_backup", methods=["GET"])
@login_required
def api_db_backup():
    """Liefert die aktuelle SQLite-DB als Download."""
    db_path = db_store.DB_PATH
    if not os.path.exists(db_path):
        return jsonify({"ok": False, "error": "Keine Datenbank vorhanden"}), 404
    from flask import send_file
    return send_file(db_path, as_attachment=True, download_name="trading_backup.db")


@app.route("/api/db_restore_upload", methods=["POST"])
@login_required
def api_db_restore_upload():
    """Empfängt eine SQLite-DB-Datei und spielt sie als neue Arbeits-DB ein."""
    from flask import request
    import sqlite3

    if "db_file" not in request.files:
        return jsonify({"ok": False, "error": "Keine Datei hochgeladen"}), 400

    file = request.files["db_file"]
    if file.filename == "":
        return jsonify({"ok": False, "error": "Leerer Dateiname"}), 400

    if not file.filename.endswith(".db"):
        return jsonify({"ok": False, "error": "Nur .db Dateien erlaubt"}), 400

    temp_path = os.path.join(tempfile.gettempdir(), f"restore_upload_{int(time.time())}.db")

    try:
        file.save(temp_path)

        # Validate SQLite and required tables
        with sqlite3.connect(temp_path) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "users" not in tables or "portfolios" not in tables:
                return jsonify({"ok": False, "error": "Ungültige DB: Tabellen users/portfolios fehlen"}), 400
            user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            portfolio_count = conn.execute("SELECT COUNT(*) FROM portfolios").fetchone()[0]

        # Backup current DB before replacing
        current_db = db_store.DB_PATH
        if os.path.exists(current_db):
            backup_name = f"trading_pre_restore_{int(time.time())}.db"
            backup_dir = os.path.join(os.path.dirname(current_db), "backups")
            os.makedirs(backup_dir, exist_ok=True)
            shutil.copy2(current_db, os.path.join(backup_dir, backup_name))

        # Replace current working DB
        os.makedirs(os.path.dirname(current_db), exist_ok=True)
        shutil.copy2(temp_path, current_db)

        # Also update repo backup so Git has the new state
        try:
            repo_backup = db_store._backup_db_path()
            os.makedirs(os.path.dirname(repo_backup), exist_ok=True)
            shutil.copy2(current_db, repo_backup)
            db_store.trigger_backup()
        except Exception:
            pass

        return jsonify({
            "ok": True,
            "message": "DB erfolgreich wiederhergestellt",
            "users": user_count,
            "portfolios": portfolio_count,
        })

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass


# --- Scheduler webhooks (external service, e.g. GitHub Actions) ---

def _scheduler_auth():
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else ""
    expected = os.environ.get("SCHEDULER_API_KEY", "")
    if not expected:
        print("[Scheduler Auth] SCHEDULER_API_KEY not set on server")
        return False
    if not token:
        print("[Scheduler Auth] Missing Authorization header")
        return False
    if token != expected:
        print(f"[Scheduler Auth] Invalid token received (len={len(token)}), expected len={len(expected)}")
        return False
    return True


@app.route("/api/scheduler/refresh_prices", methods=["POST"])
def api_scheduler_refresh_prices():
    if not _scheduler_auth():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    return jsonify(scheduler_tasks.refresh_prices())


@app.route("/api/scheduler/market_analysis", methods=["POST"])
def api_scheduler_market_analysis():
    if not _scheduler_auth():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    return jsonify(scheduler_tasks.market_analysis(notify=True))


@app.route("/api/scheduler/llm_analysis", methods=["POST"])
def api_scheduler_llm_analysis():
    if not _scheduler_auth():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    auto_trade = request.args.get("auto_trade", "true").lower() == "true"
    return jsonify(scheduler_tasks.llm_analysis(auto_trade=auto_trade, notify=True))


@app.route("/api/scheduler/daily_summary", methods=["POST"])
def api_scheduler_daily_summary():
    if not _scheduler_auth():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    return jsonify(scheduler_tasks.daily_summary())


@app.route("/api/scheduler/portfolio_report", methods=["POST"])
def api_scheduler_portfolio_report():
    if not _scheduler_auth():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    return jsonify(scheduler_tasks.portfolio_report(notify=True))


@app.route("/api/scheduler/real_positions_alert", methods=["POST"])
def api_scheduler_real_positions_alert():
    if not _scheduler_auth():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    return jsonify(scheduler_tasks.real_positions_report(only_urgent=True))


# --- Startup migration & default user safety ---
# Stelle sicher, dass Restore VOR dem potenziellen Anlegen eines Default-Users läuft.
# (restore_db_backup() wurde bereits beim Import von db_store ausgeführt.)
default_user = db_store.get_user_by_username("default")
if default_user and not default_user["password_hash"]:
    db_store.set_user_password(default_user["id"], _hash_password(config.DASHBOARD_PASSWORD))

# Direkt nach dem App-Start: lokale DB in Git-Backup spiegeln, falls der Container
# Schreibrechte auf das Repo hat (primäre Persistenz bleibt Render Disk).
db_store.backup_db(synchronous=True)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "status": "running"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
