from flask import Flask, jsonify, render_template, request, session, redirect, url_for
import json, os, requests
from datetime import datetime, timedelta
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-this-in-production")

API_SECRET = os.environ.get("API_SECRET", "")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
CACHE_FILE = "/tmp/dashboard_cache.json"
TRADE_FILE = "/tmp/pending_trades.json"

def load_cache():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except:
        return None

def load_trades():
    try:
        with open(TRADE_FILE) as f:
            return json.load(f)
    except:
        return []

def save_trades(trades):
    with open(TRADE_FILE, "w") as f:
        json.dump(trades, f)

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        if DASHBOARD_PASSWORD and password == DASHBOARD_PASSWORD:
            session["logged_in"] = True
            session.permanent = True
            app.permanent_session_lifetime = timedelta(days=7)
            return redirect(url_for("index"))
        else:
            error = "Falsches Passwort"
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@login_required
def index():
    return render_template("index.html")

@app.route("/api/update", methods=["POST"])
def api_update():
    secret = request.headers.get("X-API-Secret", "")
    if not API_SECRET or secret != API_SECRET:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json()
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f)
    return jsonify({"ok": True})

@app.route("/api/data")
@login_required
def api_data():
    cache = load_cache()
    if not cache:
        return jsonify({"error": "Keine Daten — Push-Script noch nicht gelaufen"}), 503
    # Pending trades mitsenden
    cache["pending_trades"] = load_trades()
    return jsonify(cache)

@app.route("/api/trade", methods=["POST"])
@login_required
def api_trade():
    data = request.get_json()
    required = ["broker", "action", "symbol", "menge"]
    for r in required:
        if r not in data:
            return jsonify({"error": f"Feld fehlt: {r}"}), 400

    broker = data["broker"]        # "okx" oder "tr"
    action = data["action"]        # "buy" oder "sell"
    symbol = data["symbol"]        # z.B. "NVDA", "BTC-USDT"
    menge = float(data["menge"])
    preis = data.get("preis")      # optional, sonst Marktkurs
    direction = data.get("direction", "long")  # "long" oder "short" (nur OKX)
    hebel = int(data.get("hebel", 1))          # nur OKX
    stop_loss = data.get("stop_loss")
    take_profit = data.get("take_profit")
    notiz = data.get("notiz", "")

    trade = {
        "id": datetime.utcnow().strftime("%Y%m%d%H%M%S"),
        "time": datetime.utcnow().isoformat(),
        "broker": broker,
        "action": action,
        "symbol": symbol,
        "menge": menge,
        "preis": preis,
        "direction": direction,
        "hebel": hebel,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "notiz": notiz,
        "status": "pending"
    }

    # Trade in pending liste speichern
    trades = load_trades()
    trades.append(trade)
    save_trades(trades)

    # Sofort an den Server pushen via API_SECRET
    server_url = os.environ.get("SERVER_PUSH_URL", "")
    if server_url:
        try:
            requests.post(
                server_url + "/execute_trade",
                json=trade,
                headers={"X-API-Secret": API_SECRET},
                timeout=10
            )
        except:
            pass

    return jsonify({"ok": True, "trade": trade})

@app.route("/api/trades/pending")
@login_required
def api_pending_trades():
    return jsonify(load_trades())

@app.route("/api/trades/clear", methods=["POST"])
@login_required
def api_clear_trades():
    save_trades([])
    return jsonify({"ok": True})

@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.utcnow().isoformat()})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
