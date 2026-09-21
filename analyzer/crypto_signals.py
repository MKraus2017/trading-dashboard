"""Krypto-Signal-Engine: technische Analyse + Score + selbstbestimmter Hebel.

Wiederverwendet die vorhandenen Indikatoren (EMA/RSI/MACD/Bollinger/ATR), aber mit
Krypto-spezifischer Volatilitäts-Anpassung: der Hebel wird NICHT fix vorgegeben,
sondern aus Score-Konfidenz und annualisierter Volatilität abgeleitet (hart gecappt
bei config.CRYPTO_MAX_LEVERAGE).
"""
import math
from typing import Optional

import config
from analyzer import indicators, okx_client

STRATEGY_CLASSIC = "classic"
STRATEGY_VOLUME_CONFIRMED = "volume_confirmed"
VALID_STRATEGY_VERSIONS = {STRATEGY_CLASSIC, STRATEGY_VOLUME_CONFIRMED}
RELATIVE_VOLUME_CONFIRMATION = 1.25
BUY_PRESSURE_CONFIRMATION = 0.55


def normalize_strategy_version(value: str) -> str:
    return value if value in VALID_STRATEGY_VERSIONS else STRATEGY_CLASSIC


def _volume_metrics(candles: dict, symbol: str) -> dict:
    # Quote-Volumen (USDT) ist ueber verschiedene Preisniveaus besser vergleichbar.
    volumes = candles.get("quote_volumes") or candles.get("volumes") or []
    confirmed = candles.get("confirmed") or []
    confirmed_indexes = [i for i, value in enumerate(confirmed) if value]
    # Alte/mocked Daten ohne Confirm-Feld: letzte Kerze kann noch laufen, daher vorletzte.
    current_index = confirmed_indexes[-1] if confirmed_indexes else len(volumes) - 2
    relative_volume = None
    if current_index > 0:
        baseline = [v for v in volumes[max(0, current_index - 20):current_index] if v > 0]
        if baseline:
            relative_volume = volumes[current_index] / (sum(baseline) / len(baseline))

    pressure = okx_client.fetch_recent_trade_pressure(symbol, limit=300)
    buy_ratio = pressure.get("buy_ratio") if pressure else None
    is_confirmed = (
        relative_volume is not None and relative_volume >= RELATIVE_VOLUME_CONFIRMATION
        and buy_ratio is not None and buy_ratio >= BUY_PRESSURE_CONFIRMATION
    )
    return {
        "relative_volume": relative_volume,
        "buy_pressure": buy_ratio,
        "trade_count": pressure.get("trade_count", 0) if pressure else 0,
        "confirmed": is_confirmed,
    }


def _volatility_pct(closes: list) -> float:
    """Annualisierte Tages-Volatilität (Std-Abw. der Log-Returns) in Prozent, grob geschätzt."""
    if len(closes) < 20:
        return 5.0
    rets = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            rets.append(math.log(closes[i] / closes[i - 1]))
    if len(rets) < 10:
        return 5.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    daily_std = math.sqrt(var)
    return daily_std * 100  # taegliche Volatilitaet in %


def _leverage_from_signal(score: float, volatility_pct: float) -> int:
    """Hebel-Heuristik: hohe Konfidenz (Score weit über/unter Schwelle) + niedrige
    Volatilität -> höherer Hebel erlaubt. Hart gecappt bei CRYPTO_MAX_LEVERAGE."""
    confidence = min(abs(score - 50) / 50.0, 1.0)  # 0..1
    vol_factor = max(0.15, min(1.0, 3.0 / max(volatility_pct, 0.5)))  # hohe Vola -> kleiner Faktor
    raw = confidence * vol_factor * config.CRYPTO_MAX_LEVERAGE
    leverage = max(1, min(config.CRYPTO_MAX_LEVERAGE, round(raw)))
    return leverage


def analyze_crypto_symbol(symbol: str, bar: str = "1H", limit: int = 200,
                          strategy_version: str = STRATEGY_CLASSIC) -> Optional[dict]:
    strategy_version = normalize_strategy_version(strategy_version)
    candles = okx_client.fetch_candles(symbol, bar=bar, limit=limit)
    if not candles or len(candles["closes"]) < 50:
        return None

    # Volumen-Filter: Symbole mit zu geringem 24h-Handelsvolumen ausschliessen (breite
    # Spreads, "gestaute" ATR-basierte SL/TP-Levels werden selten erreicht - erzeugt
    # kaum echte Bewegung trotz messbarer statistischer Volatilitaet). Verifiziert per
    # Backtest: Ausschluss von Symbolen < 5 Mio USDT/Tag verbessert Win-Rate + Rendite
    # konsistent ueber 180/365 Tage (AVAX/LINK/DOT waren die betroffenen Symbole).
    if config.CRYPTO_MIN_VOLUME_USDT_24H > 0:
        ticker = okx_client.fetch_ticker(symbol)
        vol24h_usdt = (ticker.get("vol24h", 0) or 0) if ticker else 0
        if vol24h_usdt < config.CRYPTO_MIN_VOLUME_USDT_24H:
            return None

    closes = candles["closes"]
    highs = candles["highs"]
    lows = candles["lows"]
    latest = closes[-1]

    ema20 = indicators.ema(closes, 20)[-1]
    ema50 = indicators.ema(closes, 50)[-1]
    rsi = indicators.rsi(closes, 14)[-1]
    _macd = indicators.macd(closes)
    macd_line, signal_line = _macd["macd"], _macd["signal"]
    bb = indicators.bollinger(closes, 20, 2)
    bb_upper = bb["upper"][-1]
    bb_lower = bb["lower"][-1]
    atr = indicators.atr(highs, lows, closes, 14)[-1]
    adx_val = indicators.adx(highs, lows, closes, 14)[-1]

    if ema20 is None or ema50 is None or rsi is None or atr is None:
        return None

    vola = _volatility_pct(closes[-60:] if len(closes) >= 60 else closes)

    score = 50
    details = []
    if ema20 > ema50:
        score += 15; details.append("EMA20 > EMA50 (Trend +)")
    else:
        score -= 5; details.append("EMA20 < EMA50 (Trend -)")
    if rsi < 40:
        score += 12; details.append(f"RSI {round(rsi,1)} (ueberverkauft)")
    elif rsi > 68:
        score -= 15; details.append(f"RSI {round(rsi,1)} (ueberkauft)")
    if macd_line[-1] > signal_line[-1]:
        score += 10; details.append("MACD bullish")
    else:
        score -= 10; details.append("MACD bearish")
    if latest <= bb_lower:
        score += 10; details.append("Preis an unterem Bollinger-Band")
    elif latest >= bb_upper:
        score -= 10; details.append("Preis an oberem Bollinger-Band")

    # ADX-Trendfilter (bewaehrter Baustein, z.B. Supertrend+ADX auf TradingView):
    # bei schwachem/seitwaertsgerichtetem Markt (ADX < 20) werden Trendfolge-Signale
    # gedaempft, weil EMA/MACD dort am haeufigsten Fehlsignale produzieren.
    if adx_val is not None:
        if adx_val < 20:
            # Signal Richtung 50 (neutral) ziehen -> schwaechere Konfidenz, kleinerer Hebel
            score = 50 + (score - 50) * 0.4
            details.append(f"ADX {round(adx_val,1)} (schwacher Trend, Signal gedaempft)")
        elif adx_val >= 25:
            # Klarer Trend bestaetigt das Signal -> leichter Bonus in Signal-Richtung
            direction_sign = 1 if score >= 50 else -1
            score += direction_sign * min((adx_val - 25) * 0.2, 8)
            details.append(f"ADX {round(adx_val,1)} (starker Trend, Signal bestaetigt)")

    volume_metrics = None
    volume_filter_blocked = False
    if strategy_version == STRATEGY_VOLUME_CONFIRMED:
        volume_metrics = _volume_metrics(candles, symbol)
        relative_volume = volume_metrics["relative_volume"]
        buy_pressure = volume_metrics["buy_pressure"]
        details.append(
            "Relatives 1H-Volumen " + (f"{relative_volume:.2f}x" if relative_volume is not None else "nicht verfuegbar")
        )
        details.append(
            "Kaufanteil (juengste OKX-Trades) " + (f"{buy_pressure * 100:.1f}%" if buy_pressure is not None else "nicht verfuegbar")
        )
        if volume_metrics["confirmed"]:
            score += 5
            details.append("Volumen und Kaufdruck bestaetigen das Signal")
        elif buy_pressure is not None and buy_pressure < 0.45:
            score -= 5
            details.append("Verkaufsdruck schwaecht das Signal")

    score = max(0, min(100, score))

    if score >= config.CRYPTO_BUY_SCORE_THRESHOLD:
        direction = "LONG"
    elif score <= (100 - config.CRYPTO_BUY_SCORE_THRESHOLD):
        direction = "SHORT"
    else:
        direction = "HALTEN"

    # Ein ueberkaufter LONG ist in der neuen Variante nur mit echtem Volumenanstieg
    # UND positivem Taker-Kaufdruck erlaubt. Fehlende Daten werden konservativ blockiert.
    if (strategy_version == STRATEGY_VOLUME_CONFIRMED and direction == "LONG"
            and rsi > 68 and not volume_metrics["confirmed"]):
        direction = "HALTEN"
        volume_filter_blocked = True
        details.append("LONG blockiert: RSI ueberkauft, Volumenbestaetigung fehlt")

    leverage = _leverage_from_signal(score, vola) if direction != "HALTEN" else 1

    sl_distance = atr * config.CRYPTO_SL_ATR_MULT
    tp_distance = atr * config.CRYPTO_SL_ATR_MULT * config.CRYPTO_MIN_RR_RATIO
    if direction == "LONG":
        stop_loss = round(latest - sl_distance, 6)
        take_profit = round(latest + tp_distance, 6)
    elif direction == "SHORT":
        stop_loss = round(latest + sl_distance, 6)
        take_profit = round(latest - tp_distance, 6)
    else:
        stop_loss = None
        take_profit = None

    return {
        "symbol": symbol.upper(),
        "name": config.get_crypto_symbol_name(symbol),
        "direction": direction,
        "score": round(score, 1),
        "price": round(latest, 6),
        "leverage": leverage,
        "volatility_pct": round(vola, 2),
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "details": details,
        "strategy_version": strategy_version,
        "relative_volume": round(volume_metrics["relative_volume"], 2) if volume_metrics and volume_metrics["relative_volume"] is not None else None,
        "buy_pressure_pct": round(volume_metrics["buy_pressure"] * 100, 1) if volume_metrics and volume_metrics["buy_pressure"] is not None else None,
        "volume_confirmed": bool(volume_metrics and volume_metrics["confirmed"]),
        "volume_filter_blocked": volume_filter_blocked,
    }


def generate_crypto_recommendations(strategy_version: str = STRATEGY_CLASSIC) -> dict:
    strategy_version = normalize_strategy_version(strategy_version)
    suggestions = []
    blocked = []
    for item in config.get_crypto_universe():
        try:
            analysis = analyze_crypto_symbol(item["symbol"], strategy_version=strategy_version)
            if analysis and analysis["direction"] != "HALTEN":
                suggestions.append(analysis)
            elif analysis and analysis.get("volume_filter_blocked"):
                blocked.append(analysis)
        except Exception as e:
            print(f"[CryptoSignals] Fehler bei {item['symbol']}: {e}")
    suggestions.sort(key=lambda s: abs(s["score"] - 50), reverse=True)
    return {"count": len(suggestions), "suggestions": suggestions, "blocked": blocked,
            "strategy_version": strategy_version}
