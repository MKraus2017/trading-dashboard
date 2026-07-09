"""Krypto-Backtester: testet die Signal-Strategie auf historischen OKX-Daten
und schlägt Parameter-Verbesserungen vor (analog zum Aktien-Backtester).
"""
import copy
from typing import Dict, List

import config
from analyzer import indicators, okx_client


def _simulate(symbol: str, candles: dict, score_threshold: int, sl_atr_mult: float,
              rr_ratio: float, max_leverage: int) -> dict:
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

    trades = []
    position = None

    for i in range(55, len(closes)):
        price = closes[i]
        if ema20_all[i] is None or ema50_all[i] is None or rsi_all[i] is None or atr_all[i] is None:
            continue

        if position:
            direction = position["direction"]
            hit_sl = (direction == "LONG" and price <= position["sl"]) or (direction == "SHORT" and price >= position["sl"])
            hit_tp = (direction == "LONG" and price >= position["tp"]) or (direction == "SHORT" and price <= position["tp"])
            if hit_sl or hit_tp:
                move_pct = ((price - position["entry"]) / position["entry"]) if direction == "LONG" else ((position["entry"] - price) / position["entry"])
                pnl_pct_leveraged = move_pct * position["leverage"] * 100
                trades.append({"pnl_pct": pnl_pct_leveraged, "won": pnl_pct_leveraged > 0, "days_held": i - position["entry_idx"]})
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
                    "leverage": leverage, "entry_idx": i}

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

    return {
        "trades": len(trades),
        "win_rate": round(win_rate, 1),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "profit_factor": profit_factor,
    }


def run_crypto_backtest(days: int = 180) -> dict:
    """Testet mehrere Parameter-Varianten über alle Krypto-Symbole und liefert
    die beste Kombination + Vergleichstabelle."""
    variants = [
        {"name": "Baseline (Score 65, SL 1.5x ATR, RR 1.8, Lev 10)", "score_threshold": 65, "sl_atr_mult": 1.5, "rr_ratio": 1.8, "max_leverage": 10},
        {"name": "Konservativ (Score 70, SL 2x ATR, RR 2.0, Lev 5)", "score_threshold": 70, "sl_atr_mult": 2.0, "rr_ratio": 2.0, "max_leverage": 5},
        {"name": "Eng (Score 65, SL 1x ATR, RR 1.5, Lev 10)", "score_threshold": 65, "sl_atr_mult": 1.0, "rr_ratio": 1.5, "max_leverage": 10},
        {"name": "Hohe Schwelle (Score 75, SL 1.5x ATR, RR 2.5, Lev 8)", "score_threshold": 75, "sl_atr_mult": 1.5, "rr_ratio": 2.5, "max_leverage": 8},
        {"name": "Niedriger Hebel (Score 65, SL 1.5x ATR, RR 1.8, Lev 3)", "score_threshold": 65, "sl_atr_mult": 1.5, "rr_ratio": 1.8, "max_leverage": 3},
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
        gross_profit = 0.0
        gross_loss = 0.0001
        per_symbol = {}
        for sym, candles in candle_cache.items():
            r = _simulate(sym, candles, variant["score_threshold"], variant["sl_atr_mult"],
                          variant["rr_ratio"], variant["max_leverage"])
            per_symbol[sym] = r
            if r.get("trades", 0) > 0:
                agg_trades += r["trades"]
                agg_pnl += r["total_pnl_pct"]
                agg_wins += round(r["win_rate"] / 100 * r["trades"])

        win_rate = round(agg_wins / agg_trades * 100, 1) if agg_trades else 0
        results.append({
            **variant,
            "total_trades": agg_trades,
            "win_rate": win_rate,
            "total_pnl_pct": round(agg_pnl, 2),
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
            f"Win-Rate {best['win_rate']}%, Gesamt-PnL {best['total_pnl_pct']}%"
        ) if best and best["total_trades"] > 0 else "Nicht genug Trades für eine verlässliche Aussage.",
    }
