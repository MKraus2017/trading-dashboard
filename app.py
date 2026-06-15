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

# Aktuelles Depot (nur TradeRepublic)
TR_DEPOT_POSITIONEN = ["NVDA", "LSMC"]

DEPOT = [
    {
        "symbol": "NVDA",
        "name": "NVIDIA",
        "isin": "US67066G1040",
        "stueck": 5.269814,
        "kaufkurs": 189.80,
        "investiert": 1000.21,
        "kaufdatum": "2026-05-18",
        "broker": "TradeRepublic"
    },
    {
        "symbol": "LSMC",
        "name": "Amundi MSCI Semiconductors ESG UCITS ETF ACC",
        "isin": "LU1900066033",
        "stueck": 22.198543,
        "kaufkurs": 112.54,
        "investiert": 2498.26,
        "kaufdatum": "2026-05-27",
        "broker": "TradeRepublic"
    },
]

def load_cache():
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
            # Cache nur verwenden wenn nicht aelter als 15 Minuten
            if data.get("_timestamp"):
                ts = datetime.fromisoformat(data["_timestamp"])
                age = (datetime.utcnow() - ts).total_seconds()
                if age < 900:
                    return data
    except:
        pass
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

def fetch_live_price(symbol):
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=2d",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8
        )
        result = r.json().get("chart", {}).get("result", [{}])[0]
        closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        closes = [c for c in closes if c is not None]
        if len(closes) >= 2:
            return closes[-1], ((closes[-1] - closes[-2]) / closes[-2]) * 100
        elif len(closes) == 1:
            return closes[-1], 0.0
    except:
        pass
    return None, None

@app.route("/api/data")
@login_required
def api_data():
    # Cache pruefen (max 15 Min alt)
    cache = load_cache()
    if cache:
        cache["pending_trades"] = load_trades()
        return jsonify(cache)

    # Live-Daten holen
    positionen = []
    gesamt_wert = 0.0
    gesamt_investiert = 0.0
    gesamt_gewinn = 0.0

    for pos in DEPOT:
        preis, change_pct = fetch_live_price(pos["symbol"])
        if preis is None:
            preis = pos["kaufkurs"]
            change_pct = 0.0
        aktuell = preis * pos["stueck"]
        gewinn = aktuell - pos["investiert"]
        gewinn_pct = (gewinn / pos["investiert"]) * 100

        positionen.append({
            "symbol": pos["symbol"],
            "name": pos["name"],
            "isin": pos["isin"],
            "stueck": pos["stueck"],
            "kaufkurs": pos["kaufkurs"],
            "aktuell_preis": round(preis, 4),
            "aktuell_wert": round(aktuell, 2),
            "investiert": pos["investiert"],
            "gewinn_abs": round(gewinn, 2),
            "gewinn_pct": round(gewinn_pct, 2),
            "change_24h": round(change_pct, 2) if change_pct else 0.0,
            "kaufdatum": pos["kaufdatum"],
            "broker": pos["broker"],
        })
        gesamt_wert += aktuell
        gesamt_investiert += pos["investiert"]
        gesamt_gewinn += gewinn

    data = {
        "positionen": positionen,
        "gesamt": {
            "wert": round(gesamt_wert, 2),
            "investiert": round(gesamt_investiert, 2),
            "gewinn_abs": round(gesamt_gewinn, 2),
            "gewinn_pct": round((gesamt_gewinn / gesamt_investiert) * 100, 2) if gesamt_investiert else 0,
        },
        "updated": datetime.utcnow().isoformat(),
        "_timestamp": datetime.utcnow().isoformat(),
        "pending_trades": load_trades()
    }

    # In Cache schreiben
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(data, f)
    except:
        pass

    return jsonify(data)

@app.route("/api/refresh", methods=["POST"])
@login_required
def api_refresh():
    """Cache loeschen und Live-Daten neu holen"""
    try:
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)
    except:
        pass
    return api_data()

@app.route("/api/trade", methods=["POST"])
@login_required
def api_trade():
    data = request.get_json()
    required = ["broker", "action", "symbol", "menge"]
    for r in required:
        if r not in data:
            return jsonify({"error": f"Feld fehlt: {r}"}), 400

    broker = data["broker"]
    action = data["action"]
    symbol = data["symbol"]
    menge = float(data["menge"])
    preis = data.get("preis")
    direction = data.get("direction", "long")
    hebel = int(data.get("hebel", 1))
    stop_loss = data.get("stop_loss")
    take_profit = data.get("take_profit")
    notiz = data.get("notiz", "")

    # TradeRepublic: kein SHORT, kein VERKAUF fuer nicht-gehaltene Positionen
    if broker == "tr" or broker == "TradeRepublic":
        if action == "sell" and symbol not in TR_DEPOT_POSITIONEN:
            return jsonify({"error": f"Verkauf nicht moeglich: {symbol} nicht im Depot"}), 400
        direction = "long"  # TR hat kein SHORT

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

    trades = load_trades()
    trades.append(trade)
    save_trades(trades)

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
    # Zuerst LLM-Analyse aus suggestions.json laden
    suggestions_path = "/opt/data/okx-bot/suggestions.json"
    if os.path.exists(suggestions_path):
        try:
            with open(suggestions_path) as f:
                data = json.load(f)
            suggestions = data.get("suggestions", [])
            if suggestions:
                normalized = []
                for s in suggestions:
                    broker = s.get("platform", "OKX")
                    symbol = s.get("symbol", "")
                    direction = s.get("direction", "LONG").upper()
                    # TradeRepublic: VERKAUF nur fuer Depot-Positionen
                    if broker == "TradeRepublic":
                        if direction in ["VERKAUF", "SELL", "SHORT"] and symbol not in TR_DEPOT_POSITIONEN:
                            continue
                        if direction == "SHORT":
                            direction = "HALTEN"
                    einstieg_von = s.get("einstieg_von", round(s.get("entry_price", 0) * 0.98, 4))
                    einstieg_bis = s.get("einstieg_bis", round(s.get("entry_price", 0) * 1.02, 4))
                    normalized.append({
                        "broker": broker,
                        "symbol": symbol,
                        "name": s.get("symbol", ""),
                        "preis": s.get("entry_price", 0),
                        "direction": direction,
                        "hebel": s.get("leverage", 1),
                        "risiko": s.get("risk_level", "MITTEL").capitalize(),
                        "change_24h": 0,
                        "score": s.get("score", 50),
                        "stop_loss": s.get("stop_loss", 0),
                        "take_profit": s.get("take_profit", 0),
                        "einstieg_von": einstieg_von,
                        "einstieg_bis": einstieg_bis,
                        "begruendung": s.get("reason", ""),
                        "timeframe": s.get("timeframe", ""),
                        "llm": True,
                        "model": data.get("model", "claude-sonnet-4-5"),
                        "updated": data.get("timestamp", "")
                    })
                normalized.sort(key=lambda x: x["score"], reverse=True)
                return jsonify({"suggestions": normalized, "updated": data.get("timestamp", ""), "source": "claude-sonnet-4-5"})
        except:
            pass

    # Fallback: algorithmische Berechnung
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
            einstieg_von = round(preis * 0.98, 6) if direction == "LONG" else round(preis * 1.00, 6)
            einstieg_bis = round(preis * 1.01, 6) if direction == "LONG" else round(preis * 1.03, 6)

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
                "einstieg_von": einstieg_von,
                "einstieg_bis": einstieg_bis,
                "begruendung": f"{'+' if change_pct > 0 else ''}{change_pct:.1f}% in 24h, {'nahe Tagestief' if pos_in_range < 35 else 'nahe Tageshoch' if pos_in_range > 65 else 'in Mitte'}, Range {range_pct:.1f}%"
            })
        except:
            continue

    # Trade Republic Aktien - NUR NVDA und LSMC (gehaltene Positionen)
    tr_symbols = [
        {"symbol": "NVDA", "name": "NVIDIA"},
        {"symbol": "LSMC", "name": "Amundi MSCI Semiconductors ETF"},
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

            # Richtung bestimmen: bei TR nur KAUF oder HALTEN
            # VERKAUF nur wenn nahe Tageshoch UND Position gehalten (was hier immer der Fall ist)
            if pos_in_range < 40:
                direction = "KAUF"
            elif pos_in_range > 75:
                direction = "VERKAUF"  # erlaubt, da NVDA und LSMC im Depot
            else:
                direction = "HALTEN"

            score = min(100, abs(change_pct) * 4 + range_pct * 1.5)

            if direction == "KAUF":
                einstieg_von = round(preis * 0.98, 2)
                einstieg_bis = round(preis * 1.02, 2)
            elif direction == "VERKAUF":
                einstieg_von = round(preis * 0.99, 2)
                einstieg_bis = round(preis * 1.01, 2)
            else:
                einstieg_von = round(preis * 0.97, 2)
                einstieg_bis = round(preis * 1.03, 2)

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
                "einstieg_von": einstieg_von,
                "einstieg_bis": einstieg_bis,
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
