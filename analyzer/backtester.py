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

# Kostenannahme pro Trade-Seite (Kauf ODER Verkauf), analog zur bereits fuer Krypto
# genutzten Modellierung (config.CRYPTO_TAKER_FEE_PCT/CRYPTO_SLIPPAGE_PCT). Bei
# Trade Republic fallen i.d.R. keine %-Ordergebuehren an, aber Spread + Slippage
# bei ETFs/Nebenwerten schon - bisher flossen diese Kosten NICHT in den Aktien-
# Backtest ein, wodurch Profit-Faktor/Win-Rate zu optimistisch ausfielen.
STOCK_SLIPPAGE_PCT = 0.0005  # 0,05 % pro Seite (konservative Spread/Slippage-Annahme)


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
                     trailing_pct: float,
                     breakeven_at: Optional[float] = None,
                     time_exit_days: Optional[int] = None,
                     use_chandelier: bool = False,
                     chandelier_period: int = 22,
                     chandelier_mult: float = 3.0,
                     use_adx_filter: bool = False,
                     adx_min: float = 20.0,
                     slippage_pct: float = 0.0) -> List[dict]:
    """Simuliert Trades für ein Symbol. Liefert Liste abgeschlossener Trades.

    use_chandelier=True ersetzt den festen Trailing-Stop (trailing_pct) durch einen
    ATR-basierten Chandelier Exit: Stop = höchstes Hoch seit Einstieg - ATR * Multiplikator.
    Passt sich damit automatisch an die Volatilität des jeweiligen Symbols/Zeitraums an,
    statt einen festen Prozentsatz zu nutzen.
    """
    n = len(closes)
    if n < 80:
        return []

    ema9 = indicators.ema(closes, 9)
    ema20 = indicators.ema(closes, 20)
    ema50 = indicators.ema(closes, 50)
    rsi14 = indicators.rsi(closes, 14)
    macd_data = indicators.macd(closes, 12, 26, 9)
    bb = indicators.bollinger(closes, 20, 2)
    atr_vals = indicators.atr(highs, lows, closes, chandelier_period) if use_chandelier else None
    adx_vals = indicators.adx(highs, lows, closes, 14) if use_adx_filter else None

    trades = []
    pos = None  # {entry, stop, tp, trailing, highest, entry_i}

    for i in range(64, n - 1):
        price = closes[i]
        if pos is None:
            score, trend = _tech_score(closes, i, ema9, ema20, ema50, rsi14,
                                       macd_data["macd"], macd_data["signal"], bb["lower"])
            buy = (score >= buy_threshold and trend == "aufwärts") or \
                  (score >= buy_threshold + 8 and trend == "seitwärts")
            if buy and use_adx_filter:
                a = adx_vals[i] if adx_vals else None
                if a is None or a < adx_min:
                    buy = False
            if buy:
                entry_price = price * (1 + slippage_pct)  # Kauf-Slippage: schlechterer Einstieg
                stop = entry_price * (1 - stop_pct)
                tp = entry_price + (entry_price - stop) * rr_ratio
                pos = {"entry": entry_price, "stop": stop, "tp": tp, "trailing": None,
                       "highest": entry_price, "entry_i": i}
        else:
            lo, hi = lows[i], highs[i]
            exit_price = None
            reason = None

            gain_pct = (price - pos["entry"]) / pos["entry"] * 100

            # Breakeven-Stop: ab +X % Stop auf Einstieg anheben
            if breakeven_at and gain_pct >= breakeven_at and pos["stop"] < pos["entry"]:
                pos["stop"] = pos["entry"]

            if price > pos["highest"]:
                pos["highest"] = price

            if use_chandelier:
                # ATR-basierter Chandelier Exit statt fixem Prozent-Trailing.
                a = atr_vals[i] if atr_vals else None
                if a is not None:
                    chandelier_stop = pos["highest"] - a * chandelier_mult
                    if pos["trailing"] is None or chandelier_stop > pos["trailing"]:
                        pos["trailing"] = chandelier_stop
            else:
                # Fixer Prozent-Trailing-Stop aktiviert ab +25 %
                if gain_pct >= 25 and pos["trailing"] is None:
                    pos["trailing"] = price * (1 - trailing_pct)
                if pos["trailing"] is not None:
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
            elif time_exit_days and (i - pos["entry_i"]) >= time_exit_days and gain_pct < 1:
                exit_price = price
                reason = "TimeExit"

            if exit_price is not None:
                exit_price_net = exit_price * (1 - slippage_pct)  # Verkauf-Slippage: schlechterer Ausstieg
                pnl_pct = (exit_price_net - pos["entry"]) / pos["entry"] * 100
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


def _load_data(symbols: List[str]) -> Dict[str, dict]:
    data_cache = {}
    for sym in symbols:
        try:
            d = yahoo_client.fetch_yahoo(sym, interval="1d", range_="1y")
            if d and len(d.get("closes", [])) >= 80:
                data_cache[sym] = d
        except Exception:
            continue
    return data_cache


def _load_data_period(symbols: List[str], range_: str) -> Dict[str, dict]:
    data_cache = {}
    for sym in symbols:
        try:
            d = yahoo_client.fetch_yahoo(sym, interval="1d", range_=range_)
            if d and len(d.get("closes", [])) >= 80:
                data_cache[sym] = d
        except Exception:
            continue
    return data_cache


def run_full_backtest(max_symbols: Optional[int] = None, periods: Optional[List[str]] = None,
                       apply_costs: bool = True) -> dict:
    """Backtest der aktuellen Strategie + Parameter-Varianten über die Watchlist.

    periods: Liste von Yahoo-Ranges, z.B. ["1y", "2y"] - Variante gilt nur als
    'best' wenn sie in ALLEN Perioden konsistent besser ist (schützt vor
    Überanpassung an ein einzelnes Zeitfenster).
    apply_costs: bezieht STOCK_SLIPPAGE_PCT pro Trade-Seite mit ein (realistischer,
    da vorher komplett kostenlos gerechnet wurde).
    """
    universe = config.get_universe()
    if max_symbols:
        universe = universe[:max_symbols]
    periods = periods or ["1y", "2y"]
    symbols = [item["symbol"] for item in universe]
    slippage = STOCK_SLIPPAGE_PCT if apply_costs else 0.0

    period_data = {rng: _load_data_period(symbols, rng) for rng in periods}

    current = {
        "buy_threshold": getattr(config, "BUY_SCORE_THRESHOLD", 65),
        "stop_pct": config.DEFAULT_STOP_PCT,
        "rr_ratio": config.MIN_RR_RATIO,
        "trailing_pct": config.TRAILING_STOP_PCT,
        "breakeven_at": getattr(config, "BREAKEVEN_AT_PCT", None),
        "time_exit_days": getattr(config, "TIME_EXIT_DAYS", None),
    }

    variants = [
        {"name": "Aktuelle Strategie", **current},
        {"name": "Engerer Stop (3 %)", **{**current, "stop_pct": 0.03}},
        {"name": "Weiterer Stop (5 %)", **{**current, "stop_pct": 0.05}},
        {"name": "RR 1.5:1", **{**current, "rr_ratio": 1.5}},
        {"name": "RR 2.5:1", **{**current, "rr_ratio": 2.5}},
        {"name": f"Selektiver (Score {current['buy_threshold']+5})", **{**current, "buy_threshold": current["buy_threshold"] + 5}},
        {"name": f"Deutlich selektiver (Score {current['buy_threshold']+10})", **{**current, "buy_threshold": current["buy_threshold"] + 10}},
        {"name": f"Aggressiver (Score {current['buy_threshold']-5})", **{**current, "buy_threshold": current["buy_threshold"] - 5}},
        {"name": "Trailing 6 %", **{**current, "trailing_pct": 0.06}},
        {"name": "Ohne Breakeven-Stop", **{**current, "breakeven_at": None}},
        {"name": "Ohne Time-Exit", **{**current, "time_exit_days": None}},
        {"name": "Time-Exit 15 Tage", **{**current, "time_exit_days": 15}},
        {"name": "Time-Exit 20 Tage", **{**current, "time_exit_days": 20}},
        {"name": "Chandelier Exit (ATR x3)", **current, "use_chandelier": True},
        {"name": "Chandelier Exit (ATR x2)", **current, "use_chandelier": True, "chandelier_mult": 2.0},
        {"name": "Chandelier x2 + ADX-Filter", **current, "use_chandelier": True, "chandelier_mult": 2.0,
         "use_adx_filter": True, "adx_min": 20.0},
        {"name": "ADX-Filter (>20)", **current, "use_adx_filter": True, "adx_min": 20.0},
        {"name": "ADX-Filter (>25)", **current, "use_adx_filter": True, "adx_min": 25.0},
    ]

    # Pro Variante: Metriken je Periode + gepoolte Gesamt-Metrik
    results = []
    for v in variants:
        per_period = {}
        for rng in periods:
            all_trades = []
            for sym, d in period_data[rng].items():
                trades = _simulate_symbol(
                    d["closes"], d["highs"], d["lows"],
                    v["buy_threshold"], v["stop_pct"], v["rr_ratio"], v["trailing_pct"],
                    breakeven_at=v.get("breakeven_at"), time_exit_days=v.get("time_exit_days"),
                    use_chandelier=v.get("use_chandelier", False),
                    chandelier_mult=v.get("chandelier_mult", 3.0),
                    use_adx_filter=v.get("use_adx_filter", False),
                    adx_min=v.get("adx_min", 20.0),
                    slippage_pct=slippage,
                )
                all_trades.extend(trades)
            per_period[rng] = _metrics(all_trades)

        # Konsistenz: Variante gilt nur als robust, wenn sie in JEDER Periode
        # mind. 5 Trades UND profit_factor >= 1.0 erreicht (kein reines
        # Overfitting auf ein einzelnes Fenster).
        consistent = all(per_period[rng]["trades"] >= 5 and per_period[rng]["profit_factor"] >= 1.0 for rng in periods)
        avg_pf = round(sum(per_period[rng]["profit_factor"] for rng in periods) / len(periods), 2)
        total_trades = sum(per_period[rng]["trades"] for rng in periods)

        results.append({
            "name": v["name"],
            "params": {k: v[k] for k in ("buy_threshold", "stop_pct", "rr_ratio", "trailing_pct")},
            "per_period": per_period,
            "avg_profit_factor": avg_pf,
            "total_trades": total_trades,
            "consistent_across_periods": consistent,
        })

    baseline = results[0]
    baseline_1y = baseline["per_period"].get(periods[0], {})

    # Beste Variante: unter den über ALLE Perioden konsistenten Varianten die mit
    # dem höchsten durchschnittlichen Profit-Faktor (mind. 10 Trades gesamt).
    eligible = [r for r in results if r["consistent_across_periods"] and r["total_trades"] >= 10]
    best = max(eligible, key=lambda r: r["avg_profit_factor"]) if eligible else baseline

    improvements = []
    if baseline_1y.get("trades", 0) == 0:
        improvements.append("Backtest fand keine Einstiegssignale – Schwellenwerte prüfen.")
    else:
        if baseline_1y.get("win_rate", 0) < 40 and baseline_1y.get("profit_factor", 0) < 1.2:
            improvements.append(
                f"Win-Rate {baseline_1y['win_rate']} % bei Profit-Faktor {baseline_1y['profit_factor']} (Kosten inkl.) – Einstiege selektiver wählen.")
        if baseline_1y.get("profit_factor", 0) < 1.2:
            improvements.append(
                f"Profit-Faktor {baseline_1y['profit_factor']} ist nach Kosten schwach (<1.2).")
        if baseline_1y.get("max_drawdown_pct", 0) > 15:
            improvements.append(
                f"Max. Drawdown {baseline_1y['max_drawdown_pct']} % ist hoch – Positionsgrößen oder Stop-Abstände überdenken.")
        if best["name"] != baseline["name"] and best["avg_profit_factor"] > baseline["avg_profit_factor"] * 1.1:
            p = best["params"]
            improvements.append(
                f"Beste ÜBER BEIDE PERIODEN konsistente Variante: „{best['name']}“ "
                f"(ø Profit-Faktor {best['avg_profit_factor']} vs. {baseline['avg_profit_factor']}, {best['total_trades']} Trades gesamt). "
                f"Parameter: Score≥{p['buy_threshold']}, SL {p['stop_pct']*100:.0f} %, RR {p['rr_ratio']}:1, Trailing {p['trailing_pct']*100:.0f} %.")
        elif not eligible:
            improvements.append("Keine Variante war über beide Perioden konsistent profitabel (PF≥1.0, ≥5 Trades je Periode) – Vorsicht vor Overfitting auf ein Zeitfenster.")
        if not improvements:
            improvements.append(
                f"Aktuelle Strategie ist solide (ø Profit-Faktor {baseline['avg_profit_factor']}). Keine Parameter-Änderung nötig.")

    result = {
        "updated": datetime.utcnow().isoformat(),
        "symbols_tested": {rng: len(period_data[rng]) for rng in periods},
        "periods": periods,
        "costs_applied_pct_per_side": slippage * 100,
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


def run_real_backtest(real_positions: List[dict]) -> dict:
    """Backtest der Strategie nur über die Symbole der realen TR-Positionen.

    Zeigt pro Symbol, wie die aktuelle Signal-Logik im letzten Jahr performt
    hätte — als Realitätscheck für die tatsächlich gehaltenen Titel.
    """
    symbols = sorted({p.get("symbol") for p in real_positions if p.get("symbol")})
    if not symbols:
        return {"ok": False, "error": "Keine realen TR-Positionen vorhanden"}

    data_cache = _load_data(symbols)

    params = {
        "buy_threshold": getattr(config, "BUY_SCORE_THRESHOLD", 65),
        "stop_pct": config.DEFAULT_STOP_PCT,
        "rr_ratio": config.MIN_RR_RATIO,
        "trailing_pct": config.TRAILING_STOP_PCT,
        "breakeven_at": getattr(config, "BREAKEVEN_AT_PCT", None),
        "time_exit_days": getattr(config, "TIME_EXIT_DAYS", None),
    }

    per_symbol = []
    all_trades = []
    for sym in symbols:
        d = data_cache.get(sym)
        if not d:
            per_symbol.append({"symbol": sym, "trades": 0, "note": "Keine ausreichenden Kursdaten"})
            continue
        trades = _simulate_symbol(
            d["closes"], d["highs"], d["lows"],
            params["buy_threshold"], params["stop_pct"], params["rr_ratio"], params["trailing_pct"],
            breakeven_at=params.get("breakeven_at"), time_exit_days=params.get("time_exit_days"),
        )
        all_trades.extend(trades)
        m = _metrics(trades)
        per_symbol.append({"symbol": sym, **m})

    overall = _metrics(all_trades)

    hints = []
    for s in per_symbol:
        if s.get("trades", 0) == 0:
            hints.append(f"{s['symbol']}: Strategie fand im letzten Jahr keinen Einstieg (oder keine Daten) — Position beruht nicht auf aktuellem Signal.")
        elif s.get("profit_factor", 0) < 1.0:
            hints.append(f"{s['symbol']}: Strategie war auf diesem Titel historisch unprofitabel (PF {s['profit_factor']}, Win-Rate {s['win_rate']} %) — Signale hier kritisch prüfen.")
        elif s.get("profit_factor", 0) >= 1.5:
            hints.append(f"{s['symbol']}: Strategie funktioniert auf diesem Titel gut (PF {s['profit_factor']}, Win-Rate {s['win_rate']} %).")
    if not hints:
        hints.append("Strategie zeigt auf deinen realen Titeln durchschnittliche Ergebnisse.")

    return {
        "ok": True,
        "updated": datetime.utcnow().isoformat(),
        "period": "1 Jahr Tagesdaten",
        "params": params,
        "symbols": symbols,
        "per_symbol": per_symbol,
        "overall": overall,
        "hints": hints,
    }
