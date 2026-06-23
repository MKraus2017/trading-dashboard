"""LLM-basierte Risikobewertung mit OpenRouter."""
import json
import os
from typing import Dict, List, Optional

import requests

import config


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _system_prompt() -> str:
    return (
        "Du bist ein risikobewusster Trading-Experte. "
        "Bewerte das kurzfristige Handelsrisiko (1-5 Tage) einer Aktie "
        "anhand aktueller Nachrichten, technischer Lage und Marktumfeld. "
        "Gib ausschließlich ein gültiges JSON-Objekt zurück. Kein Markdown, keine Erklärung."
    )


def _user_prompt(symbol: str, name: str, price: float, entry_low: float, entry_high: float,
                 stop_loss: float, take_profit: float, indicators: Dict, news: List[str]) -> str:
    news_text = "\n".join(f"- {n}" for n in news[:10]) or "Keine aktuellen Nachrichten verfügbar."
    return f"""Bewerte das Risiko für {name} ({symbol}).

Aktueller Kurs: {price:.2f} €
Empfohlener Einstiegsbereich: {entry_low:.2f} - {entry_high:.2f} €
Stop-Loss: {stop_loss:.2f} €
Take-Profit: {take_profit:.2f} €

Technische Kennzahlen:
{json.dumps(indicators, indent=2, ensure_ascii=False)}

Aktuelle Schlagzeilen:
{news_text}

Gib dieses JSON zurück:
{{
  "risk_score": 1-10,
  "risk_level": "niedrig|moderat|hoch|sehr hoch",
  "max_position_pct": "max empfohlener Positionsanteil, z.B. 5-20%",
  "main_risks": ["Kurze Aufzählung der größten Risiken"],
  "catalyst": "Wichtigster Kurstreiber / Ereignis in den nächsten Tagen",
  "verdict": "buy|hold|avoid",
  "summary": "1-2 Sätze zusammenfassende Einschätzung"
}}
"""


def assess_risk(symbol: str, name: str, price: float, entry_low: float, entry_high: float,
                stop_loss: float, take_profit: float, indicators: Dict,
                news: List[str]) -> Optional[Dict]:
    """Fragt OpenRouter nach einer Risikobewertung ab."""
    api_key = config.OPENROUTER_API_KEY
    if not api_key:
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": _user_prompt(
                symbol, name, price, entry_low, entry_high, stop_loss, take_profit,
                indicators, news
            )},
        ],
        "max_tokens": 600,
        "temperature": 0.3,
    }
    try:
        r = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        content = data["choices"][0]["message"]["content"]
        # Bereinige Markdown-JSON
        content = content.strip()
        if content.startswith("```"):
            content = "\n".join(content.split("\n")[1:-1]).strip()
        risk = json.loads(content)
        risk["llm_model"] = config.OPENROUTER_MODEL
        return risk
    except Exception as e:
        return {"error": str(e), "llm_model": config.OPENROUTER_MODEL}


if __name__ == "__main__":
    print(assess_risk(
        "ASML", "ASML Holding", 1783.0, 1753.0, 1807.0, 1727.0, 1860.0,
        {"rsi": 53.5, "macd": 1.2, "trend": "bullish"},
        ["ASML liefert EUV-Maschinen aus", "Chip-Markt zeigt Erholung"]
    ))
