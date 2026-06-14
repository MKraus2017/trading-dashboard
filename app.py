from flask import Flask, jsonify, render_template
import json, os, requests
from datetime import datetime

app = Flask(__name__)

OKX_PORTFOLIO = "/opt/data/okx-bot/portfolio.json"
VIRTUAL_DEPOT = "/opt/data/virtuelles_depot.json"
REAL_DEPOT    = "/opt/data/depot.csv"

EUR_USD = 1.08  # fallback

def get_eur_usd():
    try:
        r = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        return 1 / r.json()["rates"]["EUR"]
    except:
        return EUR_USD

def get_price_usd(ticker):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1m&range=1d"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        d = r.json()
        return d["chart"]["result"][0]["meta"]["regularMarketPrice"]
    except:
        return None

def get_crypto_price_eur(symbol):
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={symbol}&vs_currencies=eur",
            timeout=8
        )
        data = r.json()
        return list(data.values())[0]["eur"]
    except:
        return None

COINGECKO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "DOGE": "dogecoin", "ADA": "cardano", "AVAX": "avalanche-2",
    "BNB": "binancecoin", "XRP": "ripple"
}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/okx")
def api_okx():
    with open(OKX_PORTFOLIO) as f:
        p = json.load(f)

    fx = get_eur_usd()
    open_positions = []
    closed_trades  = []

    for symbol, entries in p.get("positions", {}).items():
        cg_id = COINGECKO_IDS.get(symbol)
        live_price = get_crypto_price_eur(cg_id) if cg_id else None

        for pos in entries:
            if pos["status"] == "open":
                entry = pos["entry_price"]
                qty   = pos["quantity"]
                lev   = pos["leverage"]
                margin= pos["margin"]
                sl    = pos.get("stop_loss")
                tp    = pos.get("take_profit")
                action= pos.get("action", "BUY")
                direction = "LONG" if action == "BUY" else "SHORT"

                if live_price:
                    if direction == "LONG":
                        pnl = (live_price - entry) * qty
                    else:
                        pnl = (entry - live_price) * qty
                    pnl_pct = (pnl / margin) * 100
                else:
                    pnl = pnl_pct = None

                open_positions.append({
                    "symbol": symbol,
                    "direction": direction,
                    "leverage": lev,
                    "entry": entry,
                    "current": live_price,
                    "qty": round(qty, 4),
                    "margin": margin,
                    "exposure": pos.get("exposure"),
                    "stop_loss": sl,
                    "take_profit": tp,
                    "pnl": round(pnl, 2) if pnl is not None else None,
                    "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
                    "opened": pos.get("opened", "")[:16]
                })
            else:
                closed_trades.append({
                    "symbol": symbol,
                    "direction": "LONG" if pos.get("action","BUY") == "BUY" else "SHORT",
                    "leverage": pos.get("leverage"),
                    "entry": pos.get("entry_price"),
                    "exit": pos.get("exit_price"),
                    "pnl": round(pos.get("pnl", 0), 2),
                    "pnl_pct": round(pos.get("pnl", 0) / pos.get("margin", 1) * 100, 2),
                    "reason": pos.get("close_reason", ""),
                    "closed": pos.get("closed", "")[:16]
                })

    total_pnl = sum(t["pnl"] for t in closed_trades)
    open_pnl  = sum(p["pnl"] for p in open_positions if p["pnl"] is not None)

    return jsonify({
        "cash": p.get("cash", 0),
        "start_capital": p.get("start_capital", 1000),
        "open_positions": open_positions,
        "closed_trades": sorted(closed_trades, key=lambda x: x["closed"], reverse=True),
        "total_realized_pnl": round(total_pnl, 2),
        "total_open_pnl": round(open_pnl, 2),
        "updated": datetime.now().strftime("%H:%M:%S")
    })

@app.route("/api/virtual")
def api_virtual():
    with open(VIRTUAL_DEPOT) as f:
        v = json.load(f)

    fx = get_eur_usd()
    positions = []
    for pos in v.get("positionen", []):
        ticker = pos["ticker"]
        price_usd = get_price_usd(ticker)
        price_eur = price_usd / fx if price_usd else None
        kaufkurs  = pos["kaufkurs"]
        menge     = pos["menge"]
        investiert= pos["investiert"]

        if price_eur:
            aktuell = price_eur * menge
            pnl     = aktuell - investiert
            pnl_pct = (pnl / investiert) * 100
        else:
            aktuell = pnl = pnl_pct = None

        positions.append({
            "asset": pos["asset"],
            "ticker": ticker,
            "menge": menge,
            "kaufkurs": kaufkurs,
            "aktuell": round(price_eur, 2) if price_eur else None,
            "investiert": investiert,
            "aktuell_gesamt": round(aktuell, 2) if aktuell else None,
            "pnl": round(pnl, 2) if pnl else None,
            "pnl_pct": round(pnl_pct, 2) if pnl_pct else None,
            "kaufdatum": pos.get("kaufdatum", "")
        })

    return jsonify({
        "startkapital": v.get("startkapital", 10000),
        "kassenstand": v.get("kassenstand", 0),
        "positionen": positions,
        "updated": datetime.now().strftime("%H:%M:%S")
    })

@app.route("/api/real")
def api_real():
    import csv
    fx = get_eur_usd()

    # NVIDIA real position
    nvidia_price_usd = get_price_usd("NVDA")
    nvidia_price_eur = nvidia_price_usd / fx if nvidia_price_usd else None

    menge     = 5.269814
    kaufkurs  = 189.80
    investiert= 1001.21

    if nvidia_price_eur:
        aktuell = nvidia_price_eur * menge
        pnl     = aktuell - investiert
        pnl_pct = (pnl / investiert) * 100
    else:
        aktuell = pnl = pnl_pct = None

    # parse CSV for history
    transactions = []
    try:
        with open(REAL_DEPOT) as f:
            reader = csv.DictReader(f)
            for row in reader:
                transactions.append(dict(row))
    except:
        pass

    return jsonify({
        "positionen": [{
            "asset": "NVIDIA",
            "ticker": "NVDA",
            "menge": menge,
            "kaufkurs": kaufkurs,
            "kaufdatum": "2026-05-18",
            "boerse": "Lang & Schwarz",
            "investiert": investiert,
            "aktuell": round(nvidia_price_eur, 2) if nvidia_price_eur else None,
            "aktuell_gesamt": round(aktuell, 2) if aktuell else None,
            "pnl": round(pnl, 2) if pnl else None,
            "pnl_pct": round(pnl_pct, 2) if pnl_pct else None,
        }],
        "transaktionen": transactions,
        "updated": datetime.now().strftime("%H:%M:%S")
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
