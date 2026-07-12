"""Krypto-Backtester: testet die Signal-Strategie auf historischen OKX-Daten
und schlägt Parameter-Verbesserungen vor (analog zum Aktien-Backtester).
"""
import copy
from typing import Dict, List

import config
from analyzer import indicators, okx_client


def _simulate(symbol: str, candles: dict, score_threshold: int, sl_atr_mult: float,
              rr_ratio: float, max_leverage: int, use_adx_filter: bool = True,
              use_trailing_stop: bool = False, use_time_exit: bool = False,
              bar_hours: float = 4.0) -> dict:
    closes = candles["closes"]
    highs = candles["highs"]
    lows = candles["lows"]
    if len(closes) < 60:
        return {"trades": 0}

    ema20_all = indicators.ema(closes, 20)
    ema50_all = indicators.ema(closes, 50)
    rsi_all = indicators.rsi(closes, 14)
    _macd = indicators.macd(closes)
    macd_line, signal_line = _macd["macd"], _macd["signal"]
    bb = indicators.bollinger(closes, 20, 2)
    atr_all = indicators.atr(highs, lows, closes, 14)
    adx_all = indicators.adx(highs, lows, closes, 14) if use_adx_filter else [None] * len(closes)

    trades = []
    position = None
    max_hold_bars = config.CRYPTO_MAX_HOLD_HOURS / bar_hours if bar_hours else None

    for i in range(55, len(closes)):
        price = closes[i]
        if ema20_all[i] is None or ema50_all[i] is None or rsi_all[i] is None or atr_all[i] is None:
            continue

        if position:
            direction = position["direction"]
            move_pct_now = ((price - position["entry"]) / position["entry"]) if direction == "LONG" else ((position["entry"] - price) / position["entry"])
            pnl_pct_now = move_pct_now * position["leverage"] * 100
            bars_held = i - position["entry_idx"]

            # Trailing-Stop: ab Aktivierungsschwelle SL nachziehen (nur in Gewinnrichtung)
            if use_trailing_stop and pnl_pct_now >= config.CRYPTO_TRAILING_ACTIVATE_PCT:
                trail_dist = position["sl_dist"] * config.CRYPTO_TRAILING_TIGHTEN_FACTOR
                if direction == "LONG":
                    new_sl = price - trail_dist
                    if new_sl > position["sl"]:
                        position["sl"] = new_sl
                else:
                    new_sl = price + trail_dist
                    if new_sl < position["sl"]:
                        position["sl"] = new_sl

            hit_sl = (direction == "LONG" and price <= position["sl"]) or (direction == "SHORT" and price >= position["sl"])
            hit_tp = (direction == "LONG" and price >= position["tp"]) or (direction == "SHORT" and price <= position["tp"])
            hit_time = use_time_exit and max_hold_bars and bars_held >= max_hold_bars and pnl_pct_now < config.CRYPTO_TIME_EXIT_MIN_PROFIT_PCT

            if hit_sl or hit_tp or hit_time:
                pnl_pct_leveraged = move_pct_now * position["leverage"] * 100
                trades.append({"pnl_pct": pnl_pct_leveraged, "won": pnl_pct_leveraged > 0, "days_held": bars_held,
                               "exit_reason": "time" if hit_time and not (hit_sl or hit_tp) else ("tp" if hit_tp else "sl")})
                position = None
            continue

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
        if use_adx_filter and adx_val is not None:
            if adx_val < 20:
                score = 50 + (score - 50) * 0.4
            elif adx_val >= 25:
                direction_sign = 1 if score >= 50 else -1
                score += direction_sign * min((adx_val - 25) * 0.2, 8)

        score = max(0, min(100, score))

        direction = None
        if score >= score_threshold:
            direction = "LONG"
        elif score <= (100 - score_threshold):
            direction = "SHORT"
        if not direction:
            continue

        confidence = min(abs(score - 50) / 50.0, 1.0)
        leverage = max(1, min(max_leverage, round(confidence * max_leverage)))

        atr = atr_all[i]
        sl_dist = atr * sl_atr_mult
        tp_dist = atr * sl_atr_mult * rr_ratio
        if direction == "LONG":
            sl = price - sl_dist
            tp = price + tp_dist
        else:
            sl = price + sl_dist
            tp = price - tp_dist

        position = {"direction": direction, "entry": price, "sl": sl, "tp": tp,
                    "leverage": leverage, "entry_idx": i, "sl_dist": sl_dist}

    if not trades:
        return {"trades": 0}

    wins = [t for t in trades if t["won"]]
    losses = [t for t in trades if not t["won"]]
    win_rate = len(wins) / len(trades) * 100
    avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
    total_pnl_pct = sum(t["pnl_pct"] for t in trades)
    gross_profit = sum(t["pnl_pct"] for t in wins) if wins else 0
    gross_loss = abs(sum(t["pnl_pct"] for t in losses)) if losses else 0.0001
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else 0
    avg_bars_held = sum(t["days_held"] for t in trades) / len(trades)
    time_exits = sum(1 for t in trades if t.get("exit_reason") == "time")

    return {
        "trades": len(trades),
        "win_rate": round(win_rate, 1),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "profit_factor": profit_factor,
        "avg_hold_bars": round(avg_bars_held, 1),
        "time_exits": time_exits,
    }


def run_crypto_backtest(days: int = 180) -> dict:
    """Testet mehrere Parameter-Varianten über alle Krypto-Symbole und liefert
    die beste Kombination + Vergleichstabelle."""
    variants = [
        {"name": "⭐ Aktueller Standard: SL 0.9x+ADX+Trailing+ZeitExit (Score 63, RR 1.0, Lev 10)", "score_threshold": 63, "sl_atr_mult": 0.9, "rr_ratio": 1.0, "max_leverage": 10, "use_adx_filter": True, "use_trailing_stop": True, "use_time_exit": True},
        {"name": "Ohne Trailing/ZeitExit (Score 63, SL 0.9x, RR 1.0, ADX)", "score_threshold": 63, "sl_atr_mult": 0.9, "rr_ratio": 1.0, "max_leverage": 10, "use_adx_filter": True, "use_trailing_stop": False, "use_time_exit": False},
        {"name": "Nur ZeitExit, ohne Trailing (Score 63, SL 0.9x, RR 1.0)", "score_threshold": 63, "sl_atr_mult": 0.9, "rr_ratio": 1.0, "max_leverage": 10, "use_adx_filter": True, "use_trailing_stop": False, "use_time_exit": True},
        {"name": "Vorheriger Standard: Eng + ADX (Score 65, SL 1x ATR, RR 1.5, kein Trailing/ZeitExit)", "score_threshold": 65, "sl_atr_mult": 1.0, "rr_ratio": 1.5, "max_leverage": 10, "use_adx_filter": True, "use_trailing_stop": False, "use_time_exit": False},
        {"name": "Baseline (Score 65, SL 1.5x ATR, RR 1.8, Lev 10, keine Filter)", "score_threshold": 65, "sl_atr_mult": 1.5, "rr_ratio": 1.8, "max_leverage": 10, "use_adx_filter": False, "use_trailing_stop": False, "use_time_exit": False},
        {"name": "Konservativ + alle Filter (Score 70, SL 1.2x, RR 1.2, Lev 5)", "score_threshold": 70, "sl_atr_mult": 1.2, "rr_ratio": 1.2, "max_leverage": 5, "use_adx_filter": True, "use_trailing_stop": True, "use_time_exit": True},
        {"name": "Niedriger Hebel + alle Filter (Score 63, SL 0.9x, RR 1.0, Lev 3)", "score_threshold": 63, "sl_atr_mult": 0.9, "rr_ratio": 1.0, "max_leverage": 3, "use_adx_filter": True, "use_trailing_stop": True, "use_time_exit": True},
    ]

    symbols = [item["symbol"] for item in config.get_crypto_universe()]
    candle_cache = {}
    for sym in symbols:
        c = okx_client.fetch_history_days(sym, days=days, bar="4H")
        if c:
            candle_cache[sym] = c

    results = []
    for variant in variants:
        agg_trades = 0
        agg_pnl = 0.0
        agg_wins = 0
        agg_bars = 0.0
        per_symbol = {}
        for sym, candles in candle_cache.items():
            r = _simulate(sym, candles, variant["score_threshold"], variant["sl_atr_mult"],
                          variant["rr_ratio"], variant["max_leverage"],
                          use_adx_filter=variant.get("use_adx_filter", False),
                          use_trailing_stop=variant.get("use_trailing_stop", False),
                          use_time_exit=variant.get("use_time_exit", False))
            per_symbol[sym] = r
            if r.get("trades", 0) > 0:
                agg_trades += r["trades"]
                agg_pnl += r["total_pnl_pct"]
                agg_wins += round(r["win_rate"] / 100 * r["trades"])
                agg_bars += r.get("avg_hold_bars", 0) * r["trades"]

        win_rate = round(agg_wins / agg_trades * 100, 1) if agg_trades else 0
        avg_hold_hours = round((agg_bars / agg_trades) * 4.0, 1) if agg_trades else 0  # 4H-Kerzen
        results.append({
            **variant,
            "total_trades": agg_trades,
            "win_rate": win_rate,
            "total_pnl_pct": round(agg_pnl, 2),
            "avg_hold_hours": avg_hold_hours,
            "per_symbol": per_symbol,
        })

    results.sort(key=lambda r: r["total_pnl_pct"], reverse=True)
    best = results[0] if results else None

    return {
        "days_tested": days,
        "symbols_tested": list(candle_cache.keys()),
        "variants": results,
        "best": best,
        "recommendation": (
            f"Beste Variante: {best['name']} — {best['total_trades']} Trades, "
            f"Win-Rate {best['win_rate']}%, Gesamt-PnL {best['total_pnl_pct']}%, "
            f"Ø Haltedauer {best['avg_hold_hours']}h"
        ) if best and best["total_trades"] > 0 else "Nicht genug Trades für eine verlässliche Aussage.",
    }
