"""OKX Spot Auto-Trader: automatisierte ECHTE Kaeufe/Verkaeufe basierend auf der
Krypto-Signal-Engine (crypto_signals.py) - aber SPOT statt Futures (kein Hebel,
kein Short-Selling, keine Liquidation), weil Perpetual-Futures fuer dieses OKX-
Konto regulatorisch gesperrt ist (Code 51155, verifiziert fuer alle 8 Symbole).

Sicherheits-Prinzipien:
- Nur LONG-Signale werden gehandelt (Spot kann nicht shorten)
- Positionsgroesse skaliert mit Signal-Konfidenz (Score), gedeckelt durch
  config.OKX_SPOT_MAX_TRADE_USDC pro Trade
- Hartes Gesamtlimit: config.OKX_SPOT_MAX_TOTAL_INVESTED_PCT des verfuegbaren
  USDC-Guthabens darf maximal gleichzeitig in offenen Positionen gebunden sein
- SL/TP-Distanz aus ATR (gleiche bewaehrte Logik wie virtuelles Krypto-Depot:
  SL 0.9x ATR, RR 1.0), Trailing-Stop ab Gewinnschwelle, Zeit-Exit nach X Stunden
- Alle Trades werden in der DB (okx_spot_positions) getrackt, damit SL/TP/Trailing
  ueber mehrere Scheduler-Laeufe hinweg konsistent nachverfolgt werden koennen
"""
import time
from datetime import datetime, timezone

import config
from analyzer import db_store, okx_client, okx_trading_client, crypto_signals


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _position_size_usdc(score: float, available_usdc: float) -> float:
    """Positionsgroesse skaliert mit Signal-Konfidenz (Score 63-100 -> 0..1),
    gedeckelt durch OKX_SPOT_MAX_TRADE_USDC und das verfuegbare Guthaben."""
    confidence = max(0.0, min(1.0, (score - config.CRYPTO_BUY_SCORE_THRESHOLD) / (100 - config.CRYPTO_BUY_SCORE_THRESHOLD)))
    size = config.OKX_SPOT_MIN_TRADE_USDC + confidence * (config.OKX_SPOT_MAX_TRADE_USDC - config.OKX_SPOT_MIN_TRADE_USDC)
    return round(min(size, available_usdc), 2)


def _total_invested_usdc(user_id: int) -> float:
    """Summe des urspruenglich investierten Betrags aller offenen Positionen."""
    positions = db_store.get_open_okx_spot_positions(user_id)
    return sum(p["amount_usdc"] for p in positions)


def _is_btc_risk_off() -> bool:
    """V2: BTC-Risk-off-Filter. Blockiert neue Kaeufe bei starkem BTC-Abwaertsmarkt,
    um Altcoin-Trades in breiten Markt-Selloffs zu vermeiden (externe Review-Empfehlung,
    per Backtest mit Fees verifiziert: 365T-Rendite +0.49% -> +1.28%)."""
    if not config.CRYPTO_BTC_RISKOFF_FILTER_ENABLED:
        return False
    ticker = okx_client.fetch_ticker("BTC")
    if not ticker:
        return False  # bei fehlenden Daten nicht blockieren (fail-open)
    change_24h = ticker.get("change_pct", 0) or 0
    return change_24h < config.CRYPTO_BTC_RISKOFF_THRESHOLD_PCT


def run_okx_spot_auto_trade(user_id: int, dry_run: bool = False) -> dict:
    """Fuehrt einen kompletten Zyklus aus: 1) offene Positionen bewerten (SL/TP/Trailing/
    Zeit-Exit pruefen, ggf. verkaufen), 2) neue LONG-Signale pruefen und ggf. kaufen."""
    actions = []

    # --- 1. Offene Positionen bewerten ---
    eval_result = evaluate_okx_spot_positions(user_id)
    actions.extend(eval_result.get("actions", []))

    if not okx_trading_client.has_trading_credentials():
        return {"ok": False, "error": "OKX-Trading-Credentials nicht gesetzt", "actions": actions}

    # NOTBREMSE: neue ECHTE Kaeufe koennen per Config komplett deaktiviert werden.
    # Positions-Monitoring/Verkaeufe (Schritt 1 oben) laufen bewusst WEITER, damit
    # bestehende Positionen weiterhin durch SL/TP/Trailing geschuetzt sind.
    if not getattr(config, "OKX_SPOT_AUTOTRADE_ENABLED", True):
        actions.append({"action": "DISABLED", "symbol": "SYSTEM",
                         "reason": "OKX_SPOT_AUTOTRADE_ENABLED=False - neue Kaeufe sind deaktiviert (Strategie-Edge nicht nachgewiesen). Monitoring/Verkaeufe laufen weiter."})
        return {"ok": True, "actions": actions, "autotrade_disabled": True}

    # --- 2. Neue Signale pruefen ---
    recs = crypto_signals.generate_crypto_recommendations()
    long_signals = [s for s in recs.get("suggestions", []) if s["direction"] == "LONG"]
    long_signals.sort(key=lambda s: s["score"], reverse=True)

    open_positions = db_store.get_open_okx_spot_positions(user_id)
    open_symbols = {p["symbol"] for p in open_positions}

    bal_res = okx_trading_client.get_balance("USDC")
    if not bal_res.get("ok"):
        return {"ok": False, "error": f"Balance-Abfrage fehlgeschlagen: {bal_res.get('error')}", "actions": actions}
    available_usdc = bal_res["available"]

    total_invested = _total_invested_usdc(user_id)
    # Referenzbasis fuer das Gesamtlimit: aktuelles Cash + bereits Investiertes
    reference_capital = available_usdc + total_invested
    max_total_allowed = reference_capital * config.OKX_SPOT_MAX_TOTAL_INVESTED_PCT

    btc_risk_off = _is_btc_risk_off()
    if btc_risk_off:
        actions.append({"action": "SKIP-BTC-RISKOFF", "symbol": "MARKET",
                         "reason": f"BTC 24h-Change unter {config.CRYPTO_BTC_RISKOFF_THRESHOLD_PCT}% - keine neuen Kaeufe"})

    for sig in long_signals:
        if btc_risk_off:
            break
        if sig["symbol"] in open_symbols:
            continue  # Kein Pyramiding - pro Symbol nur eine offene Position
        if len(open_positions) >= config.OKX_SPOT_MAX_POSITIONS:
            break
        if total_invested >= max_total_allowed:
            actions.append({"action": "SKIP-LIMIT-REACHED", "symbol": sig["symbol"],
                             "reason": f"Gesamtlimit erreicht ({round(total_invested,2)}/{round(max_total_allowed,2)} USDC)"})
            continue

        trade_size = _position_size_usdc(sig["score"], available_usdc)
        remaining_room = max_total_allowed - total_invested
        trade_size = min(trade_size, remaining_room)

        if trade_size < config.OKX_SPOT_MIN_TRADE_USDC:
            continue

        if dry_run:
            actions.append({"action": "WOULD-BUY", "symbol": sig["symbol"], "score": sig["score"],
                             "amount_usdc": trade_size})
            continue

        inst_id = okx_trading_client.to_spot_inst_id(sig["symbol"], quote="USDC")
        res = okx_trading_client.place_spot_order(inst_id, "buy", amount_quote=trade_size)
        if not res.get("ok"):
            actions.append({"action": "BUY-FAILED", "symbol": sig["symbol"], "error": res.get("error")})
            continue

        # Tatsaechlich erhaltene Menge ermitteln (Market-Order -> kurz warten und Bestand lesen)
        time.sleep(2)
        holding = okx_trading_client.get_spot_holdings(sig["symbol"])
        amount_base = holding.get("available", 0) if holding.get("ok") else 0
        entry_price = sig["price"]
        sl_dist = abs(entry_price - sig["stop_loss"]) if sig.get("stop_loss") else entry_price * config.CRYPTO_DEFAULT_STOP_PCT

        db_store.create_okx_spot_position(
            user_id=user_id, symbol=sig["symbol"], inst_id=inst_id, amount_base=amount_base,
            entry_price=entry_price, amount_usdc=trade_size, stop_loss=sig.get("stop_loss"),
            take_profit=sig.get("take_profit"), initial_sl_dist=sl_dist,
            buy_order_id=res.get("order_id"), reason=f"Score {sig['score']}/100 - {'; '.join(sig.get('details', []))}"
        )
        actions.append({"action": "BOUGHT", "symbol": sig["symbol"], "score": sig["score"],
                         "amount_usdc": trade_size, "order_id": res.get("order_id")})
        total_invested += trade_size
        available_usdc -= trade_size

    return {"ok": True, "actions": actions}


def evaluate_okx_spot_positions(user_id: int) -> dict:
    """Prueft alle offenen echten Spot-Positionen auf SL/TP/Trailing/Zeit-Exit und
    verkauft bei Bedarf. Analog zur virtuellen Krypto-Portfolio-Logik, aber ohne
    Hebel/Liquidation - reine Kauf/Verkauf-Entscheidung.

    Reconciliation: Der Nutzer handelt bewusst auch direkt auf OKX (z.B. manuelles
    Nachziehen von Stop-Loss-Orders). Bevor SL/TP/Trailing geprueft wird, wird daher
    zuerst verglichen, ob der getrackte Bestand noch tatsaechlich auf OKX vorhanden
    ist - falls die Position extern (ausserhalb unseres Systems) bereits verkauft
    wurde, wird sie hier automatisch als 'closed_external' markiert statt fehlerhaft
    weiterzulaufen oder eine bereits leere Position erneut verkaufen zu wollen."""
    positions = db_store.get_open_okx_spot_positions(user_id)
    actions = []

    for pos in positions:
        # --- Reconciliation: stimmt der getrackte Bestand noch mit OKX ueberein? ---
        holding = okx_trading_client.get_spot_holdings(pos["symbol"])
        actual_amount = holding.get("available", 0) if holding.get("ok") else None
        # Toleranz 5%: kleine Abweichungen durch Rundung/Gebuehren sind normal.
        # Fehlt der Bestand groesstenteils (< 10% des getrackten), wurde extern verkauft.
        if actual_amount is not None and pos["amount_base"] > 0 and actual_amount < pos["amount_base"] * 0.10:
            ticker = okx_client.fetch_ticker(pos["symbol"])
            last_price = ticker["last"] if ticker else pos["entry_price"]
            entry = pos["entry_price"]
            approx_pnl_pct = (last_price - entry) / entry * 100 if entry else None
            approx_pnl_usdc = pos["amount_base"] * (last_price - entry) if entry else None
            db_store.close_okx_spot_position(
                pos["id"], exit_price=last_price, sell_order_id="external",
                close_reason="Extern verkauft (Bestand auf OKX nicht mehr vorhanden - manueller Trade ausserhalb des Systems erkannt, PnL approximiert anhand letztem Live-Preis, da echter Verkaufspreis unbekannt)",
                pnl_usdc=round(approx_pnl_usdc, 4) if approx_pnl_usdc is not None else None,
                pnl_pct=round(approx_pnl_pct, 2) if approx_pnl_pct is not None else None
            )
            actions.append({"action": "RECONCILED-EXTERNAL-CLOSE", "symbol": pos["symbol"],
                             "reason": "Position wurde ausserhalb des Systems (z.B. manuell auf OKX) bereits verkauft - DB-Tracking korrigiert"})
            continue

        ticker = okx_client.fetch_ticker(pos["symbol"])
        if not ticker:
            continue
        price = ticker["last"]
        entry = pos["entry_price"]
        pnl_pct = (price - entry) / entry * 100 if entry else 0

        # Trailing-Stop: ab Aktivierungsschwelle SL nachziehen (nie zurueck)
        new_stop_loss = pos["stop_loss"]
        trailing_active = bool(pos.get("trailing_active"))
        if config.CRYPTO_USE_TRAILING_STOP and pnl_pct >= config.CRYPTO_TRAILING_ACTIVATE_PCT:
            trail_dist = (pos.get("initial_sl_dist") or price * config.CRYPTO_DEFAULT_STOP_PCT) * config.CRYPTO_TRAILING_TIGHTEN_FACTOR
            candidate_sl = price - trail_dist
            if new_stop_loss is None or candidate_sl > new_stop_loss:
                new_stop_loss = round(candidate_sl, 6)
                trailing_active = True

        if new_stop_loss != pos["stop_loss"] or trailing_active != bool(pos.get("trailing_active")):
            db_store.update_okx_spot_position(pos["id"], stop_loss=new_stop_loss, trailing_active=int(trailing_active))
            pos["stop_loss"] = new_stop_loss
            pos["trailing_active"] = trailing_active

        triggered, reason = False, None
        if pos.get("stop_loss") and price <= pos["stop_loss"]:
            sl_label = "Trailing-Stop" if pos.get("trailing_active") else "Stop-Loss"
            triggered, reason = True, f"{sl_label} {pos['stop_loss']} erreicht"
        elif pos.get("take_profit") and price >= pos["take_profit"]:
            triggered, reason = True, f"Take-Profit {pos['take_profit']} erreicht"
        elif (time.time() - pos["opened_at_ts"]) / 3600.0 >= config.CRYPTO_MAX_HOLD_HOURS and pnl_pct < config.CRYPTO_TIME_EXIT_MIN_PROFIT_PCT:
            hours_held = round((time.time() - pos["opened_at_ts"]) / 3600.0, 1)
            triggered, reason = True, f"Zeit-Exit nach {hours_held}h ({config.CRYPTO_MAX_HOLD_HOURS}h Limit, Gewinn {round(pnl_pct,1)}% < {config.CRYPTO_TIME_EXIT_MIN_PROFIT_PCT}%)"

        if triggered:
            res = okx_trading_client.sell_spot_all(pos["inst_id"], pos["symbol"])
            if res.get("ok"):
                pnl_usdc = pos["amount_base"] * (price - entry)
                db_store.close_okx_spot_position(
                    pos["id"], exit_price=price, sell_order_id=res.get("order_id"),
                    close_reason=reason, pnl_usdc=round(pnl_usdc, 4), pnl_pct=round(pnl_pct, 2)
                )
                actions.append({"action": "SOLD", "symbol": pos["symbol"], "reason": reason,
                                 "pnl_usdc": round(pnl_usdc, 4), "pnl_pct": round(pnl_pct, 2),
                                 "order_id": res.get("order_id")})
            else:
                actions.append({"action": "SELL-FAILED", "symbol": pos["symbol"], "error": res.get("error")})

    return {"ok": True, "actions": actions}
