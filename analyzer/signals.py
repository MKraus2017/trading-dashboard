"""Kerneinheit: Scoring + Trading-Signale + Empfehlungen."""
import json
import os
from datetime import datetime
from typing import Dict, List, Optional

import config
from analyzer import indicators, llm_risk, news_client, yahoo_client


REC_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "recommendations.json")


def analyze_symbol(item: dict) -> Optional[dict]:
    """Analysiert ein einzelnes Symbol und liefert Bewertung + Signal."""
    symbol = item["symbol"]
    name = item.get("name", symbol)
    data = yahoo_client.fetch_yahoo(symbol, interval="1d", range_="6mo")
    if not data or len(data.get("closes", [])) < 50:
        return None

    closes = data["closes"]
    highs = data["highs"]
    lows = data["lows"]
    volumes = data["volumes"]
    latest = data["latest"]
    previous = data["previous"]

    ema9 = indicators.ema(closes, 9)
    ema20 = indicators.ema(closes, 20)
    ema50 = indicators.ema(closes, 50)
    rsi14 = indicators.rsi(closes, 14)
    macd_data = indicators.macd(closes, 12, 26, 9)
    atr14 = indicators.atr(highs, lows, closes, 14)
    bb = indicators.bollinger(closes, 20, 2)

    # Sicherer Zugriff auf letzte Werte
    def last(seq):
        for v in reversed(seq):
            if v is not None:
                return v
        return None

    last_ema9 = last(ema9)
    last_ema20 = last(ema20)
    last_ema50 = last(ema50)
    last_rsi = last(rsi14)
    last_macd = last(macd_data["macd"])
    last_signal = last(macd_data["signal"])
    last_atr = last(atr14)
    last_bb_upper = last(bb["upper"])
    last_bb_lower = last(bb["lower"])
    last_bb_middle = last(bb["middle"])

    # --- Scoring ---
    details = []
    score = 0
    trend = "neutral"

    # 1. Trend (max 35)
    if last_ema20 and last_ema50 and latest > last_ema20 > last_ema50:
        trend = "aufwärts"
        score += 25
        details.append("Preis > EMA20 > EMA50 (bullish Trend)")
    elif last_ema20 and last_ema50 and latest < last_ema20 < last_ema50:
        trend = "abwärts"
        score += 5
        details.append("Preis < EMA20 < EMA50 (bärischer Trend)")
    else:
        trend = "seitwärts"
        details.append("Trend uneindeutig")

    if last_ema9 and last_ema20 and last_ema9 > last_ema20:
        # EMA9 über EMA20 = kurzfristiges Momentum
        pass

    # Golden/Death Cross letzte 3 Tage
    if last_ema9 and last_ema20:
        prev_ema9 = next((v for v in reversed(ema9[:-1]) if v is not None), None)
        prev_ema20 = next((v for v in reversed(ema20[:-1]) if v is not None), None)
        if prev_ema9 and prev_ema20 and prev_ema9 <= prev_ema20 and last_ema9 > last_ema20:
            score += 10
            details.append("EMA9 kreuzte EMA20 nach oben")
        elif prev_ema9 and prev_ema20 and prev_ema9 >= prev_ema20 and last_ema9 < last_ema20:
            score -= 5
            details.append("EMA9 kreuzte EMA20 nach unten")

    # 2. Momentum (max 25)
    if last_rsi is not None:
        if 45 <= last_rsi <= 65:
            score += 15
            details.append(f"RSI {last_rsi:.1f} im bullish Momentum-Bereich")
        elif last_rsi < 35:
            score += 10
            details.append(f"RSI {last_rsi:.1f} überverkauft (Mean-Reversion-Chance)")
        elif last_rsi > 75:
            score -= 5
            details.append(f"RSI {last_rsi:.1f} überkauft")

    if last_macd is not None and last_signal is not None:
        if last_macd > last_signal:
            score += 10
            details.append("MACD über Signal-Linie")
        else:
            score -= 5
            details.append("MACD unter Signal-Linie")

    # 3. Volatilität / Risiko (max 15) — nur belohnen, wenn kein Abwärtstrend
    atr_pct = (last_atr / latest * 100) if last_atr else None
    if atr_pct is not None:
        vol_bonus_factor = 0.4 if trend == "abwärts" else 1.0
        if atr_pct < 2.0:
            score += int(15 * vol_bonus_factor)
            details.append(f"ATR {atr_pct:.2f}% — niedrige Volatilität")
        elif atr_pct < 4.0:
            score += int(9 * vol_bonus_factor)
            details.append(f"ATR {atr_pct:.2f}% — moderate Volatilität")
        else:
            score += int(3 * vol_bonus_factor)
            details.append(f"ATR {atr_pct:.2f}% — hohe Volatilität")

    vol_trend = indicators.volume_trend(volumes, 20)
    if vol_trend > 1.3:
        score += 5
        details.append("Volumen über 20-Tage-Schnitt")

    # 3b. Momentum-Faktor: 3-Monats-Kursleistung (Rate of Change, 63 Handelstage)
    if len(closes) > 63 and closes[-64]:
        roc_63 = (latest - closes[-64]) / closes[-64] * 100
        if roc_63 > 15:
            score += 10
            details.append(f"Starkes 3M-Momentum ({roc_63:+.1f}%)")
        elif roc_63 > 5:
            score += 6
            details.append(f"Positives 3M-Momentum ({roc_63:+.1f}%)")
        elif roc_63 < -10:
            score -= 8
            details.append(f"Negatives 3M-Momentum ({roc_63:+.1f}%)")

    # 3c. Breakout-Signal: Ausbruch nahe 20-Tage-Hoch / neues 20-Tage-Tief
    if len(closes) >= 21:
        high_20 = max(closes[-21:-1])
        low_20 = min(closes[-21:-1])
        if latest >= high_20 * 0.995 and trend == "aufwärts":
            score += 8
            details.append("Ausbruch auf 20-Tage-Hoch (Breakout)")
        elif latest <= low_20 * 1.005:
            score -= 8
            details.append("Neues 20-Tage-Tief (Schwäche)")

    # 3d. Bollinger Mean-Reversion: Kurs am unteren Band im Aufwärtstrend = Einstiegschance
    if last_bb_lower is not None and trend == "aufwärts" and latest <= last_bb_lower * 1.01:
        score += 8
        details.append("Kurs am unteren Bollinger-Band im Aufwärtstrend (Rücksetzer-Chance)")

    # 4. News Sentiment (max 20)
    sentiment = news_client.get_news_sentiment(symbol, name)
    sent_score = sentiment.get("score", 0)
    score += int(sent_score * 20)
    if sent_score > 0.1:
        details.append(f"Neuigkeiten eher positiv ({sent_score:+.2f})")
    elif sent_score < -0.1:
        details.append(f"Neuigkeiten eher negativ ({sent_score:+.2f})")
    else:
        details.append("Neuigkeiten neutral")

    # --- Signal ableiten ---
    # Long nur bei Trend/Momentum positiv; Verkauf bei deutlichem Abwärtssignal
    if score >= 60 and trend == "aufwärts":
        direction = "KAUF"
    elif score >= 68 and trend == "seitwärts":
        direction = "KAUF"  # Seitwärtstrend braucht stärkere Bestätigung
    elif score <= 35 and trend == "abwärts":
        direction = "VERKAUF"
    else:
        direction = "HALTEN"

    # Für Short-fähige Broker könnte SHORT kommen, TR aber nur Long/Halten/Verkauf
    if not config.ALLOW_SHORT and direction == "SHORT":
        direction = "HALTEN"

    # Einstiegs-/Ausstiegsbereiche
    if direction == "KAUF":
        einstieg_von = round(latest * 0.985, 2)
        einstieg_bis = round(latest * 1.015, 2)
    else:
        einstieg_von = round(latest * 0.99, 2)
        einstieg_bis = round(latest * 1.01, 2)

    # Stop-Loss / Take-Profit
    atr_value = last_atr if last_atr else latest * config.DEFAULT_STOP_PCT
    if direction == "KAUF":
        stop_loss = round(max(latest * (1 - config.DEFAULT_STOP_PCT), latest - 2 * atr_value), 2)
        risk = latest - stop_loss
        take_profit = round(latest + risk * config.MIN_RR_RATIO, 2)
    else:
        stop_loss = None
        take_profit = None

    # 5. LLM Risikobewertung (nur für auffällige Kandidaten, reduzierte Kosten)
    llm_risk_result = None
    if config.OPENROUTER_API_KEY and direction == "KAUF" and score >= 65:
        llm_risk_result = llm_risk.assess_risk(
            symbol=symbol,
            name=name,
            price=latest,
            entry_low=einstieg_von,
            entry_high=einstieg_bis,
            stop_loss=stop_loss,
            take_profit=take_profit,
            indicators={
                "trend": trend,
                "rsi": round(last_rsi, 1) if last_rsi is not None else None,
                "macd": round(last_macd, 4) if last_macd is not None else None,
                "atr_pct": round(atr_pct, 2) if atr_pct is not None else None,
            },
            news=sentiment.get("headlines", []),
        )
        if llm_risk_result:
            # Max. Positionsgröße reduzieren, wenn LLM Risiko hoch sieht
            risk_val = llm_risk_result.get("risk_score", 5)
            if risk_val >= 8:
                score -= 10
            elif risk_val <= 3:
                score += 3

    his_low_20 = min(closes[-20:]) if len(closes) >= 20 else min(closes)
    his_high_20 = max(closes[-20:]) if len(closes) >= 20 else max(closes)

    return {
        "symbol": symbol,
        "name": name,
        "preis": round(latest, 2),
        "change_pct": round(data["change_pct"], 2),
        "currency": data.get("currency", "USD"),
        "direction": direction,
        "score": max(0, min(100, score)),
        "trend": trend,
        "rsi": round(last_rsi, 1) if last_rsi is not None else None,
        "macd": round(last_macd, 4) if last_macd is not None else None,
        "atr_pct": round(atr_pct, 2) if atr_pct is not None else None,
        "sentiment": round(sent_score, 2),
        "range_20d_low": round(his_low_20, 2),
        "range_20d_high": round(his_high_20, 2),
        "einstieg_von": einstieg_von,
        "einstieg_bis": einstieg_bis,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "risiko": "niedrig" if (atr_pct or 99) < 2 else ("mittel" if (atr_pct or 99) < 4 else "hoch"),
        "llm_risk": llm_risk_result,
        "begruendung": "; ".join(details),
        "headlines": sentiment.get("headlines", []),
        "timestamp": datetime.utcnow().isoformat(),
    }


def generate_recommendations(universe: Optional[List[dict]] = None, top_n: int = 5) -> dict:
    """Analysiert die gesamte Watchlist und liefert Top-N Empfehlungen."""
    universe = universe or config.get_universe()
    analyzed = []
    for item in universe:
        result = analyze_symbol(item)
        if result:
            analyzed.append(result)

    # Halten-Filter: Nur KAUF oder VERKAUF kommen in die Top-Empfehlungen,
    # außer die Absicht ist "alles anzeigen". Der User will max. 5 Empfehlungen,
    # wo ich wirklich empfehlen kann.
    actionable = [x for x in analyzed if x["direction"] != "HALTEN"]
    actionable.sort(key=lambda x: (x["direction"] == "VERKAUF", -x["score"]))

    # Wenn es weniger als 5 actionable gibt, zeige trotzdem keine Halten,
    # da der User nur echte Handlungsempfehlungen möchte.
    top = actionable[:top_n]

    result = {
        "updated": datetime.utcnow().isoformat(),
        "count": len(top),
        "suggestions": top,
        "all_analyzed": len(analyzed),
    }

    os.makedirs(os.path.dirname(REC_FILE), exist_ok=True)
    with open(REC_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str)

    return result


def load_recommendations() -> dict:
    try:
        with open(REC_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"updated": None, "count": 0, "suggestions": []}
