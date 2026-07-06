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
MIN_POSITION_EUR = 500.00        # Mindest-Kaufbetrag pro Trade (kleinere Käufe lohnen nicht)
CASH_RESERVE_PCT = 0.20          # min. 20 % Cash behalten
DEFAULT_STOP_PCT = 0.03          # Backtest 1J/28 Symbole: SL 3 % + Score>=70 => Profit-Faktor 1.57, Win-Rate 44 %
TRAILING_STOP_PCT = 0.08         # 8 % Trailing-Stop bei Gewinn (Gewinne enger sichern)
MIN_RR_RATIO = 2.0               # Mindestens 2:1 Reward/Risk (bessere Trade-Qualität)
BUY_SCORE_THRESHOLD = 70         # Backtest 1J/28 Symbole: Score>=70 => Profit-Faktor 1.23, Win-Rate 38.1 % (beste Variante)
BREAKEVEN_AT_PCT = 4.0           # Ab +4 % Gewinn Stop auf Einstiegskurs anheben (Backtest: PF 1.89 statt 1.57)
TIME_EXIT_DAYS = 10              # Position nach 10 Handelstagen schließen, wenn Gewinn < +1 % (totes Kapital freigeben)

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
    # Europa (EUR-Notierungen für TR)
    {"symbol": "ASML", "name": "ASML Holding"},
    {"symbol": "SAP", "name": "SAP SE"},
    {"symbol": "NSRGY", "name": "Nestlé ADR (USD)"},

    {"symbol": "JPM", "name": "JPMorgan Chase"},
    {"symbol": "V", "name": "Visa"},
    {"symbol": "MA", "name": "Mastercard"},
    {"symbol": "LMT", "name": "Lockheed Martin"},
    {"symbol": "SMH", "name": "VanEck Semiconductors UCITS ETF"},
    {"symbol": "VUSA.AS", "name": "Vanguard S&P 500 UCITS ETF"},
    # Neue Titel (alle auf Trade Republic handelbar)
    {"symbol": "LLY", "name": "Eli Lilly"},
    {"symbol": "NVO", "name": "Novo Nordisk ADR"},
    {"symbol": "NFLX", "name": "Netflix"},
    {"symbol": "COST", "name": "Costco"},
    {"symbol": "SIE.DE", "name": "Siemens"},
    {"symbol": "ALV.DE", "name": "Allianz"},
    {"symbol": "AIR.PA", "name": "Airbus"},
    {"symbol": "MC.PA", "name": "LVMH"},
]


def get_universe():
    """Liefert die aktive Watchlist."""
    return DEFAULT_UNIVERSE


def get_symbol_name(symbol: str) -> str:
    for item in DEFAULT_UNIVERSE:
        if item["symbol"] == symbol:
            return item["name"]
    return symbol
