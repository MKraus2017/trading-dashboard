"""Portfolio-Backtest fuer OKX Spot Auto-Trading (KEIN Hebel).

Im Unterschied zu crypto_backtester.py (pro-Symbol isoliert, mit Hebel) simuliert
dieses Modul ein ECHTES Portfolio mit begrenztem Startkapital ueber mehrere Symbole
gleichzeitig, zeitlich synchronisiert - genau wie der echte okx_spot_autotrader.

Beantwortet die Frage: Sind wenige, grosse Positionen (Konzentration) oder viele,
kleine Positionen (Diversifikation) profitabler, wenn ohne Hebel gehandelt wird?
"""
from typing import Dict, List

import config
from analyzer import indicators, okx_client


def _compute_signals_for_symbol(symbol: str, candles: dict, score_threshold: int) -> List[dict]:
    """Berechnet fuer jede Kerze das Signal (Score/Richtung/SL/TP), analog zur
    Live-Signal-Engine. Gibt eine Liste synchron zu den Kerzen zurueck (None wenn
    kein Signal an dieser Stelle)."""
    closes = candles["closes"]
    highs = candles["highs"]
    lows = candles["lows"]
    timestamps = candles["timestamps"]
    n = len(closes)
    if n < 60:
        return []

    ema20_all = indicators.ema(closes, 20)
    ema50_all = indicators.ema(closes, 50)
    rsi_all = indicators.rsi(closes, 14)
    _macd = indicators.macd(closes)
    macd_line, signal_line = _macd["macd"], _macd["signal"]
    bb = indicators.bollinger(closes, 20, 2)
    atr_all = indicators.atr(highs, lows, closes, 14)
    adx_all = indicators.adx(highs, lows, closes, 14)

    signals = [None] * n
    for i in range(55, n):
        if ema20_all[i] is None or ema50_all[i] is None or rsi_all[i] is None or atr_all[i] is None:
            continue
        price = closes[i]
        score = 50
        if ema20_all[i] > ema50_all[i]:
            score += 15
        else:
            score -= 5
        if rsi_all[i] < 40:
            score += 12
        elif rsi_all[i] > 68:
            score -= 15
        if macd_line[i] > signal_line[i]:
            score += 10
        else:
            score -= 10
        if price <= bb["lower"][i]:
            score += 10
        elif price >= bb["upper"][i]:
            score -= 10

        adx_val = adx_all[i]
        if adx_val is not None:
            if adx_val < 20:
                score = 50 + (score - 50) * 0.4
            elif adx_val >= 25:
                direction_sign = 1 if score >= 50 else -1
                score += direction_sign * min((adx_val - 25) * 0.2, 8)
        score = max(0, min(100, score))

        # Spot: nur LONG handelbar
        if score < score_threshold:
            continue

        atr = atr_all[i]
        sl_dist = atr * config.CRYPTO_SL_ATR_MULT
        tp_dist = atr * config.CRYPTO_SL_ATR_MULT * config.CRYPTO_MIN_RR_RATIO
        signals[i] = {
            "score": round(score, 1),
            "price": price,
            "stop_loss": price - sl_dist,
            "take_profit": price + tp_dist,
            "sl_dist": sl_dist,
            "ts": timestamps[i],
        }
    return signals


def _run_portfolio_sim(all_candles: Dict[str, dict], all_signals: Dict[str, List[dict]],
                        start_capital: float, max_positions: int, max_total_invested_pct: float,
                        min_trade_usdc: float, max_trade_usdc: float, score_threshold: int,
                        bar_hours: float = 4.0) -> dict:
    """Simuliert echtes Portfolio-Verhalten: begrenztes Kapital, mehrere Symbole
    gleichzeitig, Positionsgroesse skaliert mit Score, hartes Gesamtlimit."""
    cash = start_capital
    open_positions = {}  # symbol -> {entry, sl, tp, sl_dist, amount_usdc, amount_base, trailing_active, entry_idx}
    closed_trades = []
    value_history = []

    # Zeitlich synchronisierte Iteration ueber alle Symbole (gleiche Kerzenanzahl vorausgesetzt)
    max_len = max(len(c["closes"]) for c in all_candles.values())

    for i in range(max_len):
        # 1. Offene Positionen bewerten (SL/TP/Trailing/Zeit-Exit)
        for symbol in list(open_positions.keys()):
            candles = all_candles[symbol]
            if i >= len(candles["closes"]):
                continue
            price = candles["closes"][i]
            pos = open_positions[symbol]
            pnl_pct = (price - pos["entry"]) / pos["entry"] * 100
            bars_held = i - pos["entry_idx"]

            if config.CRYPTO_USE_TRAILING_STOP and pnl_pct >= config.CRYPTO_TRAILING_ACTIVATE_PCT:
                trail_dist = pos["sl_dist"] * config.CRYPTO_TRAILING_TIGHTEN_FACTOR
                new_sl = price - trail_dist
                if new_sl > pos["sl"]:
                    pos["sl"] = new_sl

            hit_sl = price <= pos["sl"]
            hit_tp = price >= pos["tp"]
            max_hold_bars = config.CRYPTO_MAX_HOLD_HOURS / bar_hours
            hit_time = bars_held >= max_hold_bars and pnl_pct < config.CRYPTO_TIME_EXIT_MIN_PROFIT_PCT

            if hit_sl or hit_tp or hit_time:
                proceeds = pos["amount_usdc"] * (1 + pnl_pct / 100.0)
                cash += proceeds
                closed_trades.append({
                    "symbol": symbol, "pnl_pct": round(pnl_pct, 2), "pnl_usdc": round(proceeds - pos["amount_usdc"], 2),
                    "won": pnl_pct > 0, "bars_held": bars_held,
                    "exit_reason": "time" if hit_time and not (hit_sl or hit_tp) else ("tp" if hit_tp else "sl"),
                    "amount_usdc": pos["amount_usdc"],
                })
                del open_positions[symbol]

        # 2. Neue Signale pruefen (Kauf-Entscheidung)
        total_invested = sum(p["amount_usdc"] for p in open_positions.values())
        reference_capital = cash + total_invested
        max_total_allowed = reference_capital * max_total_invested_pct

        candidates = []
        for symbol, signals in all_signals.items():
            if symbol in open_positions or i >= len(signals) or signals[i] is None:
                continue
            candidates.append((symbol, signals[i]))
        candidates.sort(key=lambda x: x[1]["score"], reverse=True)

        for symbol, sig in candidates:
            if len(open_positions) >= max_positions:
                break
            if total_invested >= max_total_allowed:
                break
            confidence = max(0.0, min(1.0, (sig["score"] - score_threshold) / (100 - score_threshold)))
            trade_size = min_trade_usdc + confidence * (max_trade_usdc - min_trade_usdc)
            trade_size = min(trade_size, cash, max_total_allowed - total_invested)
            if trade_size < min_trade_usdc:
                continue

            cash -= trade_size
            open_positions[symbol] = {
                "entry": sig["price"], "sl": sig["stop_loss"], "tp": sig["take_profit"],
                "sl_dist": sig["sl_dist"], "amount_usdc": trade_size, "entry_idx": i,
            }
            total_invested += trade_size

        # Portfoliowert tracken
        unrealized = 0.0
        for symbol, pos in open_positions.items():
            candles = all_candles[symbol]
            if i < len(candles["closes"]):
                price = candles["closes"][i]
                pnl_pct = (price - pos["entry"]) / pos["entry"]
                unrealized += pos["amount_usdc"] * pnl_pct
        total_value = cash + sum(p["amount_usdc"] for p in open_positions.values()) + unrealized
        value_history.append(total_value)

    # Offene Positionen am Ende zum letzten Preis schliessen (fuer fairen Vergleich)
    for symbol, pos in list(open_positions.items()):
        candles = all_candles[symbol]
        price = candles["closes"][-1]
        pnl_pct = (price - pos["entry"]) / pos["entry"] * 100
        proceeds = pos["amount_usdc"] * (1 + pnl_pct / 100.0)
        cash += proceeds
        closed_trades.append({"symbol": symbol, "pnl_pct": round(pnl_pct, 2), "pnl_usdc": round(proceeds - pos["amount_usdc"], 2),
                               "won": pnl_pct > 0, "bars_held": max_len - pos["entry_idx"], "exit_reason": "end_of_test",
                               "amount_usdc": pos["amount_usdc"]})

    final_value = cash
    total_return_pct = (final_value - start_capital) / start_capital * 100
    wins = [t for t in closed_trades if t["won"]]
    win_rate = len(wins) / len(closed_trades) * 100 if closed_trades else 0
    max_dd = 0.0
    peak = start_capital
    for v in value_history:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100 if peak else 0
        max_dd = max(max_dd, dd)

    return {
        "start_capital": start_capital,
        "final_value": round(final_value, 2),
        "total_return_pct": round(total_return_pct, 2),
        "total_trades": len(closed_trades),
        "win_rate": round(win_rate, 1),
        "max_drawdown_pct": round(max_dd, 2),
        "avg_trade_usdc": round(sum(t["amount_usdc"] for t in closed_trades) / len(closed_trades), 2) if closed_trades else 0,
        "trades": closed_trades,
    }


def run_okx_spot_portfolio_backtest(days: int = 180, start_capital: float = 848.0) -> dict:
    """Vergleicht verschiedene Positionsgroessen-/Konzentrations-Strategien fuer das
    echte OKX Spot-Portfolio (kein Hebel) auf historischen Daten."""
    symbols = [item["symbol"] for item in config.get_crypto_universe()]
    all_candles = {}
    for sym in symbols:
        c = okx_client.fetch_history_days(sym, days=days, bar="4H")
        if c and len(c["closes"]) >= 60:
            all_candles[sym] = c

    if not all_candles:
        return {"error": "Keine historischen Daten verfuegbar"}

    score_threshold = config.CRYPTO_BUY_SCORE_THRESHOLD
    all_signals = {sym: _compute_signals_for_symbol(sym, candles, score_threshold)
                   for sym, candles in all_candles.items()}

    strategies = [
        {"name": "⭐ Aktuell: 4 Positionen, 50-250 USDC (diversifiziert, klein)",
         "max_positions": 4, "min_trade": 50.0, "max_trade": 250.0, "max_pct": 0.70},
        {"name": "3 Positionen, 75-350 USDC (mittel)",
         "max_positions": 3, "min_trade": 75.0, "max_trade": 350.0, "max_pct": 0.70},
        {"name": "2 Positionen, 100-450 USDC (konzentriert)",
         "max_positions": 2, "min_trade": 100.0, "max_trade": 450.0, "max_pct": 0.70},
        {"name": "1 Position, 200-600 USDC (maximal konzentriert)",
         "max_positions": 1, "min_trade": 200.0, "max_trade": 600.0, "max_pct": 0.70},
        {"name": "4 Positionen, 50-250 USDC, 100% Limit (kein Cash-Puffer)",
         "max_positions": 4, "min_trade": 50.0, "max_trade": 250.0, "max_pct": 1.0},
        {"name": "6 Positionen, 40-150 USDC (sehr diversifiziert)",
         "max_positions": 6, "min_trade": 40.0, "max_trade": 150.0, "max_pct": 0.70},
    ]

    results = []
    for strat in strategies:
        res = _run_portfolio_sim(
            all_candles, all_signals, start_capital=start_capital,
            max_positions=strat["max_positions"], max_total_invested_pct=strat["max_pct"],
            min_trade_usdc=strat["min_trade"], max_trade_usdc=strat["max_trade"],
            score_threshold=score_threshold,
        )
        res["name"] = strat["name"]
        res["config"] = strat
        results.append(res)

    results.sort(key=lambda r: r["total_return_pct"], reverse=True)
    best = results[0] if results else None

    return {
        "days_tested": days,
        "symbols_tested": list(all_candles.keys()),
        "start_capital": start_capital,
        "strategies": results,
        "best": best,
        "recommendation": (
            f"Beste Strategie: {best['name']} — {best['total_trades']} Trades, "
            f"Win-Rate {best['win_rate']}%, Gesamtrendite {best['total_return_pct']}%, "
            f"Max. Drawdown {best['max_drawdown_pct']}%"
        ) if best else "Keine Ergebnisse",
    }
