#!/usr/bin/env python3
"""Push aktuelle Depot-Daten alle 5 Minuten zu Render Dashboard."""

import json, os, sys
import urllib.request
from datetime import datetime

RENDER_URL = "https://trading-dashboard-6n5w.onrender.com"
API_SECRET = os.environ.get("API_SECRET", "OKX2024secure!")

def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except:
        return default or {}

def get_live_price_usd(ticker):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1m&range=1d"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
            return data["chart"]["result"][0]["meta"]["regularMarketPrice"]
    except:
        return None

def get_eur_usd():
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X?interval=1m&range=1d"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
            return data["chart"]["result"][0]["meta"]["regularMarketPrice"]
    except:
        return 1.08

def get_crypto_price_eur(symbol, eur_usd):
    try:
        url = f"https://www.okx.com/api/v5/market/ticker?instId={symbol}-USDT"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
            price_usd = float(data["data"][0]["last"])
            return price_usd / eur_usd
    except:
        return None

def build_payload():
    eur_usd = get_eur_usd()

    # OKX virtuell
    okx = load_json("/opt/data/okx-bot/portfolio.json", {"cash": 2000, "positions": {}, "trades": []})

    # TR virtuell
    tr_virt = load_json("/opt/data/virtuelles_depot.json", {})

    # Live-Kurse für OKX Positionen
    live_prices = {}
    for sym in okx.get("positions", {}).keys():
        price = get_crypto_price_eur(sym, eur_usd)
        if price:
            live_prices[sym] = price

    # NVDA Live-Kurs
    nvda_usd = get_live_price_usd("NVDA")
    nvda_eur = nvda_usd / eur_usd if nvda_usd else None

    # TR real
    tr_real_positions = []
    try:
        import csv
        with open("/opt/data/depot.csv") as f:
            reader = csv.DictReader(f)
            for row in reader:
                tr_real_positions.append(row)
    except:
        pass

    return {
        "timestamp": datetime.now().isoformat(),
        "eur_usd": eur_usd,
        "okx_virtuell": okx,
        "tr_virtuell": tr_virt,
        "tr_real": {
            "positionen": tr_real_positions,
            "nvda_live_usd": nvda_usd,
            "nvda_live_eur": nvda_eur,
            "eur_usd": eur_usd
        },
        "live_prices": live_prices
    }

def push():
    payload = build_payload()
    payload_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{RENDER_URL}/api/update",
        data=payload_bytes,
        headers={
            "Content-Type": "application/json",
            "X-API-Secret": API_SECRET
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read())
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] Push erfolgreich: {resp}")
    except Exception as e:
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] Push Fehler: {e}", file=sys.stderr)

if __name__ == "__main__":
    push()
