"""Historisches Backtesting der Signal-Strategie über die Watchlist.

Simuliert die technische Scoring-Logik (ohne News/LLM, da historisch nicht
verfügbar) über Tagesdaten und testet zusätzlich alternative Parameter,
um konkrete Verbesserungsvorschläge abzuleiten.
"""
import json
import os
from datetime import datetime
from typing import Dict, List, Optional

import config
from analyzer import indicators, yahoo_client

BACKTEST_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "backtest_result.json")


def _tech_score(closes: List[float], i: int, ema9, ema20, ema50, rsi14, macd_line, macd_signal, bb_lower) -> tuple:
    """Berechnet den technischen Score am Tag i (Index in closes). Gibt (score, trend) zurück."""
    latest = closes[i]
    score = 0
    trend = "seitwärts"

    e20, e50 = ema20[i], ema50[i]
    if e20 and e50 and latest > e20 > e50:
        trend = "aufwärts"
        score += 25
    elif e20 and e50 and latest < e20 < e50:
        trend = "abwärts"
        score += 5

    # EMA9/EMA20-Kreuzung
    if i >= 1 and ema9[i] and ema20[i] and ema9[i - 1] and ema20[i - 1]:
        if ema9[i - 1] <= ema20[i - 1] and ema9[i] > ema20[i]:
            score += 10
        elif ema9[i - 1] >= ema20[i - 1] and ema9[i] < ema20[i]:
            score -= 5

    r = rsi14[i]
    if r is not None:
        if 45 <= r <= 65:
            score += 15
        elif r < 35:
            score += 10
        elif r > 75:
            score -= 5

    m, s = macd_line[i], macd_signal[i]
    if m is not None and s is not None:
        score += 10 if m > s else -5

    # 3M-Momentum (63 Tage)
    if i > 63 and closes[i - 63]:
        roc = (latest - closes[i - 63]) / closes[i - 63] * 100
        if roc > 15:
            score += 10
        elif roc > 5:
            score += 6
        elif roc < -10:
            score -= 8

    # Breakout 20-Tage-Hoch/Tief
    if i >= 21:
        window = closes[i - 20:i]
        if latest >= max(window) * 0.995 and trend == "aufwärts":
            score += 8
        elif latest <= min(window) * 1.005:
            score -= 8

    # Bollinger Mean-Reversion im Aufwärtstrend
    if bb_lower[i] is not None and trend == "aufwärts" and latest <= bb_lower[i] * 1.01:
        score += 8

    # Volatilitätsbonus (vereinfacht, neutral ~ +9)
    score += 9 if trend != "abwärts" else 3

    return score, trend


def _simulate_symbol(closes: List[float], highs: List[float], lows: List[float],
                     buy_threshold: int, stop_pct: float, rr_ratio: float,
                     trailing_pct: float) -> List[dict]:
    """Simuliert Trades für ein Symbol. Liefert Liste abgeschlossener Trades."""
    n = len(closes)
    if n < 80:
        return []

    ema9 = indicators.ema(closes, 9)
    ema20 = indicators.ema(closes, 20)
    ema50 = indicators.ema(closes, 50)
    rsi14 = indicators.rsi(closes, 14)
    macd_data = indicators.macd(closes, 12, 26, 9)
    bb = indicators.bollinger(closes, 20, 2)

    trades = []
    pos = None  # {entry, stop, tp, trailing, highest, entry_i}

    for i in range(64, n - 1):
        price = closes[i]
        if pos is None:
            score, trend = _tech_score(closes, i, ema9, ema20, ema50, rsi14,
                                       macd_data["macd"], macd_data["signal"], bb["lower"])
            buy = (score >= buy_threshold and trend == "aufwärts") or \
                  (score >= buy_threshold + 8 and trend == "seitwärts")
            if buy:
                stop = price * (1 - stop_pct)
                tp = price + (price - stop) * rr_ratio
                pos = {"entry": price, "stop": stop, "tp": tp, "trailing": None,
                       "highest": price, "entry_i": i}
        else:
            lo, hi = lows[i], highs[i]
            exit_price = None
            reason = None

            # Trailing-Stop aktivieren ab +25 %
            gain_pct = (price - pos["entry"]) / pos["entry"] * 100
            if gain_pct >= 25 and pos["trailing"] is None:
                pos["trailing"] = price * (1 - trailing_pct)
            if pos["trailing"] is not None and price > pos["highest"]:
                pos["highest"] = price
                new_tr = price * (1 - trailing_pct)
                if new_tr > pos["trailing"]:
                    pos["trailing"] = new_tr

            effective_stop = max(pos["stop"], pos["trailing"] or 0)
            if lo is not None and lo <= effective_stop:
                exit_price = effective_stop
                reason = "SL/Trailing"
            elif hi is not None and hi >= pos["tp"] and pos["trailing"] is None:
                exit_price = pos["tp"]
                reason = "TP"

            if exit_price is not None:
                pnl_pct = (exit_price - pos["entry"]) / pos["entry"] * 100
                trades.append({
                    "pnl_pct": round(pnl_pct, 2),
                    "days": i - pos["entry_i"],
                    "reason": reason,
                })
                pos = None

    return trades


def _metrics(trades: List[dict]) -> dict:
    if not trades:
        return {"trades": 0, "win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                "profit_factor": 0.0, "total_pnl_pct": 0.0, "max_drawdown_pct": 0.0,
                "avg_days": 0}
    wins = [t["pnl_pct"] for t in trades if t["pnl_pct"] > 0]
    losses = [t["pnl_pct"] for t in trades if t["pnl_pct"] <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    equity = 100.0
    peak = equity
    max_dd = 0.0
    for t in trades:
        equity *= (1 + t["pnl_pct"] / 100 * 0.2)  # 20 % Positionsgröße
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100
        max_dd = max(max_dd, dd)
    return {
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else 99.0,
        "total_pnl_pct": round(sum(t["pnl_pct"] for t in trades), 2),
        "max_drawdown_pct": round(max_dd, 2),
        "avg_days": round(sum(t["days"] for t in trades) / len(trades), 1),
    }


def run_full_backtest(max_symbols: Optional[int] = None) -> dict:
    """Backtest der aktuellen Strategie + Parameter-Varianten über die Watchlist."""
    universe = config.get_universe()
    if max_symbols:
        universe = universe[:max_symbols]

    # Kursdaten einmalig laden
    data_cache = {}
    for item in universe:
        sym = item["symbol"]
        try:
            d = yahoo_client.fetch_yahoo(sym, interval="1d", range_="1y")
            if d and len(d.get("closes", [])) >= 80:
                data_cache[sym] = d
        except Exception:
            continue

    current = {
        "buy_threshold": 60,
        "stop_pct": config.DEFAULT_STOP_PCT,
        "rr_ratio": config.MIN_RR_RATIO,
        "trailing_pct": config.TRAILING_STOP_PCT,
    }

    variants = [
        {"name": "Aktuelle Strategie", **current},
        {"name": "Engerer Stop (3 %)", **{**current, "stop_pct": 0.03}},
        {"name": "Weiterer Stop (5 %)", **{**current, "stop_pct": 0.05}},
        {"name": "RR 1.5:1", **{**current, "rr_ratio": 1.5}},
        {"name": "RR 2.5:1", **{**current, "rr_ratio": 2.5}},
        {"name": "Selektiver (Score 65)", **{**current, "buy_threshold": 65}},
        {"name": "Aggressiver (Score 55)", **{**current, "buy_threshold": 55}},
        {"name": "Trailing 6 %", **{**current, "trailing_pct": 0.06}},
    ]

    results = []
    for v in variants:
        all_trades = []
        for sym, d in data_cache.items():
            trades = _simulate_symbol(
                d["closes"], d["highs"], d["lows"],
                v["buy_threshold"], v["stop_pct"], v["rr_ratio"], v["trailing_pct"],
            )
            all_trades.extend(trades)
        m = _metrics(all_trades)
        results.append({
            "name": v["name"],
            "params": {k: v[k] for k in ("buy_threshold", "stop_pct", "rr_ratio", "trailing_pct")},
            **m,
        })

    baseline = results[0]
    # Beste Variante nach Profit-Faktor (mind. 10 Trades)
    eligible = [r for r in results if r["trades"] >= 10]
    best = max(eligible, key=lambda r: r["profit_factor"]) if eligible else baseline

    improvements = []
    if baseline["trades"] == 0:
        improvements.append("Backtest fand keine Einstiegssignale im letzten Jahr – Schwellenwerte prüfen.")
    else:
        if baseline["win_rate"] < 45:
            improvements.append(
                f"Win-Rate der aktuellen Strategie nur {baseline['win_rate']} % – Einstiege selektiver wählen (höhere Score-Schwelle testen).")
        if baseline["profit_factor"] < 1.2:
            improvements.append(
                f"Profit-Faktor {baseline['profit_factor']} ist schwach (<1.2) – Verhältnis Gewinn/Verlust verbessern.")
        if baseline["max_drawdown_pct"] > 15:
            improvements.append(
                f"Max. Drawdown {baseline['max_drawdown_pct']} % ist hoch – Positionsgrößen oder Stop-Abstände überdenken.")
        if best["name"] != baseline["name"] and best["profit_factor"] > baseline["profit_factor"] * 1.1:
            p = best["params"]
            improvements.append(
                f"Beste getestete Variante: „{best['name']}“ (Profit-Faktor {best['profit_factor']} vs. {baseline['profit_factor']}, "
                f"Win-Rate {best['win_rate']} %, {best['trades']} Trades). "
                f"Parameter: Score≥{p['buy_threshold']}, SL {p['stop_pct']*100:.0f} %, RR {p['rr_ratio']}:1, Trailing {p['trailing_pct']*100:.0f} %.")
        if not improvements:
            improvements.append(
                f"Aktuelle Strategie ist solide (Profit-Faktor {baseline['profit_factor']}, Win-Rate {baseline['win_rate']} %). Keine Parameter-Änderung nötig.")

    result = {
        "updated": datetime.utcnow().isoformat(),
        "symbols_tested": len(data_cache),
        "period": "1 Jahr Tagesdaten",
        "baseline": baseline,
        "variants": results,
        "best_variant": best["name"],
        "improvements": improvements,
    }

    try:
        os.makedirs(os.path.dirname(BACKTEST_FILE), exist_ok=True)
        with open(BACKTEST_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str)
    except Exception:
        pass

    return result


def load_last_backtest() -> Optional[dict]:
    try:
        with open(BACKTEST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None
