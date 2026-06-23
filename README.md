# 🤖 Trading Bot Dashboard

Passwortgeschütztes Trading-Dashboard für Trade Republic, auf Render deploybar.

## Features
- **Marktanalyse:** Kostenlose Daten von Yahoo Finance + News-Sentiment (Yahoo/NewsAPI-Fallback)
- **Max. 5 Empfehlungen:** Mit Einstiegs-/Ausstiegsbereich, Stop-Loss, Take-Profit
- **Virtuelles Depot:** Startkapital 10.000 €, eigenständige Kauf-/Verkaufs-Entscheidungen
- **Reales TR-Depot:** Manuelle Sync, damit ich auf Real-Positionen besonders achte
- **Dashboard-Login:** Passwortgeschützt

## Lokaler Start
```bash
pip install -r requirements.txt
python app.py
```
Login-Passwort: `Martin.Kraus2026!`

## Render Deploy
1. Repo auf GitHub pushen
2. In Render: „New Web Service“ → GitHub-Repo verbinden
3. `PYTHON_VERSION=3.11.0` setzen
4. Umgebungsvariable `DASHBOARD_PASSWORD` setzen
5. Deploy

## Telegram (optional)
`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` als Render-Env-Variablen setzen.

## Dateien
- `analyzer/signals.py` → Bewertung & Empfehlungen
- `analyzer/portfolio.py` → Virtuelles Depot
- `analyzer/yahoo_client.py` → Kursdaten
- `analyzer/news_client.py` → News/Sentiment
- `app.py` → Flask-Server & API
- `templates/` → Dashboard UI
- `static/` → CSS/JS
