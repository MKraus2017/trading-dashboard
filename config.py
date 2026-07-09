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


# --- Krypto-Testbereich (separates virtuelles Depot, OKX-Live-Preise) ---
CRYPTO_START_CAPITAL = 1_000.00
CRYPTO_MAX_LEVERAGE = 10           # Hartes Cap; Bot waehlt Hebel selbst je nach Signal-Konfidenz/Volatilitaet
CRYPTO_MAX_POSITIONS = 4
CRYPTO_MAX_POSITION_PCT = 0.30     # max. 30 % des Krypto-Depots pro Position (Margin-Einsatz)
CRYPTO_MIN_POSITION_EUR = 50.00
CRYPTO_CASH_RESERVE_PCT = 0.15
CRYPTO_DEFAULT_STOP_PCT = 0.04     # 4 % Gegenbewegung vom Entry (auf Basispreis, nicht auf Margin)
CRYPTO_SL_ATR_MULT = 1.0          # Backtest 180T/10 Symbole 4H: SL 1x ATR + ADX-Filter => PnL +80.1% (Win-Rate 46.6%)
CRYPTO_MIN_RR_RATIO = 1.5          # Backtest 180T: RR 1.5 mit engem SL + ADX schlaegt alle anderen Kombinationen
CRYPTO_USE_ADX_FILTER = True      # ADX-Trendfilter aktiv (bewaehrt: Eng+ADX = +80.1% vs. Eng ohne ADX = +26.3%)
CRYPTO_BUY_SCORE_THRESHOLD = 65
CRYPTO_LIQUIDATION_BUFFER_PCT = 0.02  # Sicherheitsabstand: Position wird VOR echter Liquidation geschlossen

# Totalverlust-Schutz (Circuit Breaker): stoppt neue Trades automatisch, wenn das
# Krypto-Depot zu tief faellt. Schuetzt vor Komplettverlust des virtuellen Kapitals.
CRYPTO_MAX_DRAWDOWN_PCT = 50.0    # Bei Depotwert <= 50% des Startkapitals: Handel pausieren
CRYPTO_CRITICAL_DRAWDOWN_PCT = 25.0  # Bei <= 25%: zusaetzlich alle offenen Positionen sofort schliessen

# Analyse-Fenster: alle 2h, nur 07-23 Uhr deutscher Zeit (Positions-Ueberwachung laeuft aber 24/7)
CRYPTO_ANALYSIS_START_HOUR = 7
CRYPTO_ANALYSIS_END_HOUR = 23

CRYPTO_UNIVERSE = [
    {"symbol": "BTC", "name": "Bitcoin"},
    {"symbol": "ETH", "name": "Ethereum"},
    {"symbol": "SOL", "name": "Solana"},
    {"symbol": "XRP", "name": "Ripple"},
    {"symbol": "BNB", "name": "BNB"},
    {"symbol": "ADA", "name": "Cardano"},
    {"symbol": "DOGE", "name": "Dogecoin"},
    {"symbol": "AVAX", "name": "Avalanche"},
    {"symbol": "LINK", "name": "Chainlink"},
    {"symbol": "DOT", "name": "Polkadot"},
]


def get_crypto_universe():
    return CRYPTO_UNIVERSE


def get_crypto_symbol_name(symbol: str) -> str:
    for item in CRYPTO_UNIVERSE:
        if item["symbol"] == symbol.upper():
            return item["name"]
    return symbol.upper()

