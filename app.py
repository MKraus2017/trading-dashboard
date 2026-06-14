from flask import Flask, jsonify, render_template, request
import json, os, requests
from datetime import datetime

app = Flask(__name__)

# Pfade zu den Datendateien
OKX_PORTFOLIO   = "/opt/data/okx-bot/portfolio.json"
VIRT_DEPOT      = "/opt/data/virtuelles_depot.json"
REAL_DEPOT      = "/opt/data/depot.csv"
API_SECRET      = os.environ.get("DASHBOARD_SECRET", "okx-dashboard-2026")

# EUR/USD Kurs
def get_eur_usd():
    try:
        r = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X?interval=1m&range=1d", timeout=5)
        return r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"]
    except:
        return 1.08

# Live-Kurs von Yahoo Finance
def get_price_usd(ticker):
    try:
        r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1m&range=1d", timeout=5)
        return r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"]
    except:
        return None

# Crypto-Kurs in EUR von OKX
def get_crypto_price_eur(symbol):
    try:
        r = requests.get(f"https://www.okx.com/api/v5/market/ticker?instId={symbol}-USDT", timeout=5)
        price_usd = float(r.json()["data"][0]["last"])
        return price_usd / get_eur_usd()
    except:
        return None

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/okx")
def api_okx():
    try:
        with open(OKX_PORTFOLIO) as f:
            data = json.load(f)
    except:
        return jsonify({"error": "Portfolio nicht gefunden"}), 404

    positions = []
    total_pnl = 0.0
    total_margin = 0.0

    for symbol, pos_list in data.get("positions", {}).items():
        for pos in pos_list:
            if pos.get("status") != "open":
                continue
            current_price = get_crypto_price_eur(symbol)
            if current_price is None:
                current_price = pos["entry_price"]
            pnl = (current_price - pos["entry_price"]) * pos["quantity"]
            pnl_pct = ((current_price - pos["entry_price"]) / pos["entry_price"]) * 100 * pos["leverage"]
            total_pnl += pnl
            total_margin += pos["margin"]
            positions.append({
                "symbol": symbol,
                "direction": "LONG" if pos.get("action", "BUY") == "BUY" else "SHORT",
                "entry_price": round(pos["entry_price"], 6),
                "current_price": round(current_price, 6),
                "quantity": round(pos["quantity"], 4),
                "leverage": pos["leverage"],
                "margin": round(pos["margin"], 2),
                "exposure": round(pos["exposure"], 2),
                "stop_loss": round(pos["stop_loss"], 6),
                "take_profit": round(pos["take_profit"], 6),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "opened": pos.get("opened", "")[:16]
            })

    # Trade-Historie (letzte 20)
    trades = []
    for t in data.get("trades", [])[-20:]:
        trades.append({
            "time": t.get("time", "")[:16],
            "action": t.get("action"),
            "symbol": t.get("name"),
            "price": t.get("price"),
            "leverage": t.get("leverage"),
            "margin": t.get("margin"),
            "pnl": t.get("pnl"),
            "pnl_pct": t.get("pnl_pct"),
            "reason": t.get("reason", "")
        })

    depot_value = data.get("cash", 0) + total_margin + total_pnl
    start = data.get("start_capital", 1000)

    return jsonify({
        "cash": round(data.get("cash", 0), 2),
        "start_capital": start,
        "depot_value": round(depot_value, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(((depot_value - start) / start) * 100, 2),
        "open_positions": len(positions),
        "positions": positions,
        "trades": list(reversed(trades))
    })

@app.route("/api/tradereublic/virtual")
def api_tr_virtual():
    try:
        with open(VIRT_DEPOT) as f:
            data = json.load(f)
    except:
        return jsonify({"error": "Virtuelles Depot nicht gefunden"}), 404

    eur_usd = get_eur_usd()
    positions = []
    total_value = 0.0

    for pos in data.get("positionen", []):
        ticker = pos["ticker"]
        price_usd = get_price_usd(ticker)
        if price_usd:
            price_eur = price_usd / eur_usd
        else:
            price_eur = pos["kaufkurs"]
        current_value = price_eur * pos["menge"]
        invested = pos["investiert"]
        pnl = current_value - invested
        pnl_pct = (pnl / invested) * 100
        total_value += current_value
        positions.append({
            "asset": pos["asset"],
            "ticker": ticker,
            "menge": round(pos["menge"], 6),
            "kaufkurs": round(pos["kaufkurs"], 2),
            "current_price": round(price_eur, 2),
            "investiert": round(invested, 2),
            "current_value": round(current_value, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "kaufdatum": pos.get("kaufdatum", "")
        })

    cash = data.get("kassenstand", 0)
    start = data.get("startkapital", 10000)
    depot_value = total_value + cash

    return jsonify({
        "start_capital": start,
        "cash": round(cash, 2),
        "depot_value": round(depot_value, 2),
        "invested_value": round(total_value, 2),
        "total_pnl": round(depot_value - start, 2),
        "total_pnl_pct": round(((depot_value - start) / start) * 100, 2),
        "positions": positions,
        "transaktionen": list(reversed(data.get("transaktionen", [])[-10:]))
    })

@app.route("/api/tradereublic/real")
def api_tr_real():
    try:
        import csv
        transactions = []
        with open(REAL_DEPOT) as f:
            reader = csv.DictReader(f)
            for row in reader:
                transactions.append(row)
    except:
        return jsonify({"error": "Reales Depot nicht gefunden"}), 404

    eur_usd = get_eur_usd()
    # Nur NVIDIA offen
    nvda_price_usd = get_price_usd("NVDA")
    nvda_price_eur = nvda_price_usd / eur_usd if nvda_price_usd else 189.80

    nvda_qty = 5.269814
    nvda_entry = 189.80
    current_value = nvda_qty * nvda_price_eur
    invested = nvda_qty * nvda_entry
    pnl = current_value - invested
    pnl_pct = (pnl / invested) * 100

    return jsonify({
        "positions": [{
            "asset": "NVIDIA",
            "ticker": "NVDA",
            "menge": nvda_qty,
            "kaufkurs": nvda_entry,
            "current_price": round(nvda_price_eur, 2),
            "investiert": round(invested, 2),
            "current_value": round(current_value, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "kaufdatum": "2026-05-18",
            "boerse": "Lang & Schwarz"
        }],
        "transactions": transactions
    })

@app.route("/api/push", methods=["POST"])
def api_push():
    """Endpunkt für Push-Updates vom Server"""
    secret = request.headers.get("X-Secret", "")
    if secret != API_SECRET:
        return jsonify({"error": "Unauthorized"}), 401
    # Daten entgegennehmen und in lokale Datei speichern
    data = request.get_json()
    data_type = data.get("type")
    if data_type == "okx":
        with open("/tmp/okx_cache.json", "w") as f:
            json.dump(data, f)
    return jsonify({"ok": True})

@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.utcnow().isoformat()})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
