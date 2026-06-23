"""Zentrale Konfiguration für das Trading-Dashboard."""
import os

from dotenv import load_dotenv

load_dotenv()


# --- Dashboard ---
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "Martin.Kraus2026!")
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "change-me-in-production")

# --- Datenquellen ---
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "")
YAHOO_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# --- LLM Risikoanalyse (optional) ---
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")

# --- Virtuelles Depot ---
START_CAPITAL = 10_000.00
MAX_POSITIONS = 5
MAX_POSITION_PCT = 0.20          # max. 20 % des Depotwerts pro Position
CASH_RESERVE_PCT = 0.20          # min. 20 % Cash behalten
DEFAULT_STOP_PCT = 0.03          # 3 % harter Stop-Loss
TRAILING_STOP_PCT = 0.10         # 10 % Trailing-Stop bei Gewinn
MIN_RR_RATIO = 1.5               # Mindestens 1,5:1 Reward/Risk

# Für Trade Republic greifen ausschließlich Long-Positionen.
ALLOW_SHORT = False

# --- Telegram (optional) ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


# --- Watchlist (deutsche/amerikanische Aktien & ETFs, handelbar auf TR) ---
DEFAULT_UNIVERSE = [
    # US Tech / Growth
    {"symbol": "NVDA", "name": "NVIDIA"},
    {"symbol": "MSFT", "name": "Microsoft"},
    {"symbol": "AAPL", "name": "Apple"},
    {"symbol": "GOOGL", "name": "Alphabet A"},
    {"symbol": "AMZN", "name": "Amazon"},
    {"symbol": "META", "name": "Meta Platforms"},
    {"symbol": "TSLA", "name": "Tesla"},
    {"symbol": "AMD", "name": "AMD"},
    {"symbol": "PLTR", "name": "Palantir"},
    {"symbol": "AVGO", "name": "Broadcom"},
    # Europa
    {"symbol": "ASML", "name": "ASML Holding"},
    {"symbol": "SAP", "name": "SAP SE"},
    {"symbol": "NESN.SW", "name": "Nestlé"},
    # Diversifikation / Finanz / ETFs
    {"symbol": "JPM", "name": "JPMorgan Chase"},
    {"symbol": "V", "name": "Visa"},
    {"symbol": "MA", "name": "Mastercard"},
    {"symbol": "LMT", "name": "Lockheed Martin"},
    {"symbol": "SMH", "name": "VanEck Semiconductors UCITS ETF"},
    {"symbol": "VUSA.AS", "name": "Vanguard S&P 500 UCITS ETF"},
]


def get_universe():
    """Liefert die aktive Watchlist."""
    return DEFAULT_UNIVERSE


def get_symbol_name(symbol: str) -> str:
    for item in DEFAULT_UNIVERSE:
        if item["symbol"] == symbol:
            return item["name"]
    return symbol
