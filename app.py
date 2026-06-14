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

@app.route("/api/suggestions")
@login_required
def api_suggestions():
    suggestions = []

    # OKX Krypto-Symbole
    okx_symbols = [
        {"symbol": "BTC-USDT", "name": "Bitcoin"},
        {"symbol": "ETH-USDT", "name": "Ethereum"},
        {"symbol": "SOL-USDT", "name": "Solana"},
        {"symbol": "BNB-USDT", "name": "BNB"},
        {"symbol": "DOGE-USDT", "name": "Dogecoin"},
        {"symbol": "AVAX-USDT", "name": "Avalanche"},
        {"symbol": "XRP-USDT", "name": "XRP"},
        {"symbol": "ADA-USDT", "name": "Cardano"},
        {"symbol": "LINK-USDT", "name": "Chainlink"},
        {"symbol": "DOT-USDT", "name": "Polkadot"},
    ]

    for item in okx_symbols:
        try:
            r = requests.get(
                f"https://www.okx.com/api/v5/market/ticker?instId={item['symbol']}",
                timeout=5
            )
            d = r.json().get("data", [{}])[0]
            if not d:
                continue
            preis = float(d.get("last", 0))
            open24 = float(d.get("open24h", preis))
            high24 = float(d.get("high24h", preis))
            low24 = float(d.get("low24h", preis))
            vol = float(d.get("volCcy24h", 0))
            if preis == 0 or open24 == 0:
                continue
            change_pct = ((preis - open24) / open24) * 100
            range_pct = ((high24 - low24) / low24) * 100 if low24 > 0 else 0
            pos_in_range = ((preis - low24) / (high24 - low24) * 100) if (high24 - low24) > 0 else 50

            if pos_in_range < 35:
                direction = "LONG"
            elif pos_in_range > 65:
                direction = "SHORT"
            else:
                direction = "LONG" if change_pct > 2 else ("SHORT" if change_pct < -2 else "LONG")

            if range_pct > 15:
                hebel, risiko = 5, "Hoch"
            elif range_pct > 8:
                hebel, risiko = 10, "Mittel"
            else:
                hebel, risiko = 20, "Niedrig"

            score = min(100, abs(change_pct) * 3 + range_pct * 2 + (100 - pos_in_range if direction == "LONG" else pos_in_range) * 0.5)

            sl = round(preis * 0.95, 6) if direction == "LONG" else round(preis * 1.05, 6)
            tp = round(preis * 1.08, 6) if direction == "LONG" else round(preis * 0.92, 6)

            suggestions.append({
                "broker": "OKX",
                "symbol": item["symbol"],
                "name": item["name"],
                "preis": preis,
                "direction": direction,
                "hebel": hebel,
                "risiko": risiko,
                "change_24h": round(change_pct, 2),
                "range_24h": round(range_pct, 2),
                "volumen": vol,
                "score": round(score, 1),
                "stop_loss": sl,
                "take_profit": tp,
                "begruendung": f"{'+' if change_pct > 0 else ''}{change_pct:.1f}% in 24h, {'nahe Tagestief' if pos_in_range < 35 else 'nahe Tageshoch' if pos_in_range > 65 else 'in Mitte'}, Range {range_pct:.1f}%"
            })
        except:
            continue

    # Trade Republic Aktien
    tr_symbols = [
        {"symbol": "NVDA", "name": "NVIDIA"},
        {"symbol": "AAPL", "name": "Apple"},
        {"symbol": "TSLA", "name": "Tesla"},
        {"symbol": "MSFT", "name": "Microsoft"},
        {"symbol": "AMZN", "name": "Amazon"},
        {"symbol": "ASML", "name": "ASML"},
        {"symbol": "AMD", "name": "AMD"},
        {"symbol": "META", "name": "Meta"},
    ]

    for item in tr_symbols:
        try:
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{item['symbol']}?interval=1d&range=5d",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=5
            )
            result = r.json().get("chart", {}).get("result", [{}])[0]
            closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
            closes = [c for c in closes if c is not None]
            if len(closes) < 2:
                continue
            preis = closes[-1]
            prev = closes[-2]
            low5 = min(closes)
            high5 = max(closes)
            change_pct = ((preis - prev) / prev) * 100
            range_pct = ((high5 - low5) / low5) * 100 if low5 > 0 else 0
            pos_in_range = ((preis - low5) / (high5 - low5) * 100) if (high5 - low5) > 0 else 50

            if pos_in_range < 40:
                direction = "KAUF"
            elif pos_in_range > 75:
                direction = "VERKAUF"
            else:
                direction = "HALTEN"

            score = min(100, abs(change_pct) * 4 + range_pct * 1.5)

            suggestions.append({
                "broker": "TradeRepublic",
                "symbol": item["symbol"],
                "name": item["name"],
                "preis": round(preis, 2),
                "direction": direction,
                "hebel": 1,
                "risiko": "Niedrig" if range_pct < 5 else "Mittel",
                "change_24h": round(change_pct, 2),
                "range_5d": round(range_pct, 2),
                "score": round(score, 1),
                "stop_loss": round(preis * 0.94, 2),
                "take_profit": round(preis * 1.10, 2),
                "begruendung": f"{'+' if change_pct > 0 else ''}{change_pct:.1f}% heute, 5d-Range {range_pct:.1f}%, {'nahe 5d-Tief' if pos_in_range < 40 else 'nahe 5d-Hoch' if pos_in_range > 75 else 'neutral'}"
            })
        except:
            continue

    suggestions.sort(key=lambda x: x["score"], reverse=True)
    return jsonify({"suggestions": suggestions, "updated": datetime.utcnow().isoformat()})

@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.utcnow().isoformat()})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
