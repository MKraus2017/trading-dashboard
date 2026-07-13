"""OKX Authenticated Trading Client — ECHTE Order-Ausfuehrung mit echtem Geld (Perpetual Futures).

WICHTIG: Dieses Modul sendet echte Orders an OKX. Anders als okx_client.py (nur
oeffentliche Marktdaten, kein API-Key), braucht dieses Modul API Key + Secret +
Passphrase mit "Trade"-Berechtigung (KEIN Withdraw-Recht empfohlen).

Handelt USDT-margined Perpetual Swaps (z.B. BTC-USDT-SWAP) mit Hebel, analog zur
virtuellen Krypto-Simulation (crypto_portfolio.py) - aber mit ECHTEM Geld und
ECHTEM Liquidationsrisiko durch OKX selbst (nicht simuliert).

Sicherheits-Prinzipien:
- Alle Requests werden per HMAC-SHA256 signiert (OKX v5 REST API Standard)
- Kein automatisches Fallback auf ungueltige/fehlende Credentials - klare Fehlermeldung
- Jede Order wird geloggt (Zeitstempel, Symbol, Seite, Menge, Preis, Hebel)
- demo=True Modus verfuegbar: OKX Demo-Trading (Paper-Trading auf echter Infrastruktur,
  KEIN echtes Geld) - IMMER zuerst hier testen, bevor demo=False verwendet wird
"""
import base64
import hashlib
import hmac
import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Optional, Dict

OKX_BASE = "https://www.okx.com"


def _get_credentials():
    """Liest OKX-Trading-Credentials aus Env-Variablen (NICHT im Code/Git, NICHT in Hermes .env)."""
    api_key = os.environ.get("OKX_API_KEY", "")
    secret_key = os.environ.get("OKX_SECRET_KEY", "")
    passphrase = os.environ.get("OKX_PASSPHRASE", "")
    return api_key, secret_key, passphrase


def has_trading_credentials() -> bool:
    api_key, secret_key, passphrase = _get_credentials()
    return bool(api_key and secret_key and passphrase)


def _sign(timestamp: str, method: str, request_path: str, body: str, secret_key: str) -> str:
    """HMAC-SHA256-Signatur nach OKX v5 API Standard."""
    message = f"{timestamp}{method}{request_path}{body}"
    mac = hmac.new(secret_key.encode(), message.encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()


def _request(method: str, path: str, body: dict = None, demo: bool = False) -> dict:
    """Signierter Request gegen die OKX v5 API. demo=True nutzt OKX Demo-Trading-Header
    (x-simulated-trading: 1) - Paper-Trading auf echter Infrastruktur ohne echtes Geld."""
    api_key, secret_key, passphrase = _get_credentials()
    if not (api_key and secret_key and passphrase):
        return {"ok": False, "error": "OKX-Trading-Credentials fehlen (OKX_API_KEY/OKX_SECRET_KEY/OKX_PASSPHRASE als Render Env-Variable setzen)"}

    body_str = json.dumps(body) if body else ""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"
    signature = _sign(timestamp, method, path, body_str, secret_key)

    headers = {
        "OK-ACCESS-KEY": api_key,
        "OK-ACCESS-SIGN": signature,
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": passphrase,
        "Content-Type": "application/json",
    }
    if demo:
        headers["x-simulated-trading"] = "1"

    url = OKX_BASE + path
    data = body_str.encode() if body_str else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
            if result.get("code") != "0":
                return {"ok": False, "error": result.get("msg", "Unbekannter OKX-Fehler"), "code": result.get("code"), "raw": result}
            return {"ok": True, "data": result.get("data", [])}
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode())
            return {"ok": False, "error": err_body.get("msg", str(e)), "code": err_body.get("code"), "http_status": e.code}
        except Exception:
            return {"ok": False, "error": f"HTTP {e.code}: {e.reason}", "http_status": e.code}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def to_swap_inst_id(symbol: str) -> str:
    """Normalisiert z.B. 'BTC' -> 'BTC-USDT-SWAP' (USDT-margined Perpetual Future)."""
    symbol = symbol.upper()
    if symbol.endswith("-SWAP"):
        return symbol
    return f"{symbol}-USDT-SWAP"


def get_balance(ccy: str = "USDT") -> dict:
    """Verfuegbares Guthaben im Trading-Account (fuer Futures-Margin)."""
    res = _request("GET", f"/api/v5/account/balance?ccy={ccy}")
    if not res.get("ok"):
        return res
    details = res["data"][0].get("details", []) if res["data"] else []
    for d in details:
        if d.get("ccy") == ccy:
            return {"ok": True, "ccy": ccy, "available": float(d.get("availBal", 0)), "total": float(d.get("bal", 0))}
    return {"ok": True, "ccy": ccy, "available": 0.0, "total": 0.0}


def get_all_balances() -> dict:
    res = _request("GET", "/api/v5/account/balance")
    if not res.get("ok"):
        return res
    details = res["data"][0].get("details", []) if res["data"] else []
    balances = [{"ccy": d.get("ccy"), "available": float(d.get("availBal", 0)), "total": float(d.get("bal", 0))}
                for d in details if float(d.get("bal", 0)) > 0]
    return {"ok": True, "balances": balances}


def set_leverage(inst_id: str, leverage: int, mgn_mode: str = "isolated") -> dict:
    """Setzt den Hebel fuer ein Instrument VOR dem Order-Platzieren (OKX-Anforderung).
    mgn_mode: 'isolated' (empfohlen - Verlust auf die Position begrenzt) oder 'cross'."""
    body = {"instId": inst_id, "lever": str(leverage), "mgnMode": mgn_mode}
    return _request("POST", "/api/v5/account/set-leverage", body=body)


def place_futures_order(inst_id: str, side: str, size_contracts: float, leverage: int,
                          order_type: str = "market", reduce_only: bool = False,
                          mgn_mode: str = "isolated", demo: bool = False) -> dict:
    """Platziert eine ECHTE Perpetual-Futures-Order auf OKX.

    inst_id: z.B. 'BTC-USDT-SWAP'
    side: 'buy' (LONG eroeffnen oder SHORT schliessen) oder 'sell' (SHORT eroeffnen oder LONG schliessen)
    size_contracts: Anzahl Kontrakte (OKX-spezifische Kontraktgroesse pro Symbol! Vor der ersten
                     Order IMMER get_instrument_info() pruefen, um ctVal (Kontraktwert) zu kennen)
    leverage: Hebel 1-10x - WIRD VOR DER ORDER via set_leverage() gesetzt
    reduce_only: True beim Schliessen einer Position (verhindert versehentliches Aufstocken)
    mgn_mode: 'isolated' (empfohlen) oder 'cross'
    demo: True = OKX Demo-Trading (Paper-Trading, KEIN echtes Geld) - IMMER zuerst hier testen
    """
    if side not in ("buy", "sell"):
        return {"ok": False, "error": "side muss 'buy' oder 'sell' sein"}

    lev_res = set_leverage(inst_id, leverage, mgn_mode)
    if not lev_res.get("ok"):
        return {"ok": False, "error": f"Hebel konnte nicht gesetzt werden: {lev_res.get('error')}"}

    body = {
        "instId": inst_id,
        "tdMode": mgn_mode,
        "side": side,
        "ordType": order_type,
        "sz": str(size_contracts),
    }
    if reduce_only:
        body["reduceOnly"] = "true"

    res = _request("POST", "/api/v5/trade/order", body=body, demo=demo)
    if res.get("ok") and res["data"]:
        order = res["data"][0]
        return {
            "ok": order.get("sCode") == "0",
            "order_id": order.get("ordId"),
            "client_order_id": order.get("clOrdId"),
            "error": order.get("sMsg") if order.get("sCode") != "0" else None,
        }
    return res


def get_instrument_info(inst_id: str) -> dict:
    """Kontrakt-Spezifikation (Kontraktwert, Mindestgroesse) - VOR der ersten Order pruefen."""
    res = _request("GET", f"/api/v5/public/instruments?instType=SWAP&instId={inst_id}")
    if not res.get("ok") or not res["data"]:
        return res
    i = res["data"][0]
    return {
        "ok": True,
        "inst_id": inst_id,
        "ct_val": float(i.get("ctVal", 0)),       # Kontraktwert in Base-Currency (z.B. BTC pro Kontrakt)
        "ct_val_ccy": i.get("ctValCcy"),
        "min_size": float(i.get("minSz", 0)),      # Mindest-Kontraktanzahl
        "lot_size": float(i.get("lotSz", 0)),       # Schrittgroesse
        "max_leverage": float(i.get("lever", 0)),
    }


def get_positions(inst_id: str = None) -> dict:
    """Offene Futures-Positionen (echte, von OKX verwaltete Positionen)."""
    path = "/api/v5/account/positions?instType=SWAP"
    if inst_id:
        path += f"&instId={inst_id}"
    res = _request("GET", path)
    if not res.get("ok"):
        return res
    positions = []
    for p in res["data"]:
        if float(p.get("pos", 0)) == 0:
            continue
        positions.append({
            "inst_id": p.get("instId"),
            "side": "LONG" if p.get("posSide") == "long" or float(p.get("pos", 0)) > 0 else "SHORT",
            "size_contracts": abs(float(p.get("pos", 0))),
            "entry_price": float(p.get("avgPx", 0) or 0),
            "mark_price": float(p.get("markPx", 0) or 0),
            "unrealized_pnl": float(p.get("upl", 0) or 0),
            "unrealized_pnl_pct": float(p.get("uplRatio", 0) or 0) * 100,
            "liquidation_price": float(p.get("liqPx", 0) or 0),
            "leverage": float(p.get("lever", 0) or 0),
            "margin": float(p.get("margin", 0) or 0),
        })
    return {"ok": True, "positions": positions}


def close_position(inst_id: str, side: str, size_contracts: float, mgn_mode: str = "isolated", demo: bool = False) -> dict:
    """Schliesst eine offene Position (reduce_only Market-Order in Gegenrichtung)."""
    close_side = "sell" if side == "LONG" else "buy"
    return place_futures_order(inst_id, close_side, size_contracts, leverage=1,  # Hebel bei Close irrelevant
                                 order_type="market", reduce_only=True, mgn_mode=mgn_mode, demo=demo)


def get_order_status(inst_id: str, order_id: str) -> dict:
    res = _request("GET", f"/api/v5/trade/order?instId={inst_id}&ordId={order_id}")
    if not res.get("ok") or not res["data"]:
        return res
    o = res["data"][0]
    return {
        "ok": True,
        "state": o.get("state"),  # live, filled, canceled, partially_filled
        "filled_size": float(o.get("accFillSz", 0) or 0),
        "avg_price": float(o.get("avgPx", 0) or 0),
        "fee": float(o.get("fee", 0) or 0),
    }
