# 🤖 Trading Bot Dashboard

Passwortgeschütztes Trading-Dashboard für Trade Republic, auf Render deploybar.

## Features
- **Marktanalyse:** Kostenlose Daten von Yahoo Finance + News-Sentiment
- **Max. 5 Empfehlungen:** Mit Einstiegs-/Ausstiegsbereich, Stop-Loss, Take-Profit
- **Virtuelles Depot:** 10.000 € startet, handelt jetzt **automatisch** auf Basis der besten Empfehlungen
- **Reales TR-Depot:** Manuelle Sync für besondere Beobachtung
- **Dashboard-Login:** Passwortgeschützt

## Auto-Handel im virtuellen Depot
Wenn du im Dashboard auf **„Analyse & Auto-Handel“** klickst:
1. Alle Symbole der Watchlist werden analysiert
2. Bestehende Positionen werden bei Stop-Loss/Take-Profit/Trailing-Stop verkauft
3. Top-Kaufempfehlungen werden automatisch mit ca. 2.000 € gekauft
4. Depot wird gespeichert und (falls möglich) nach GitHub gepusht

## Regeln des virtuellen Depots
- Max. 5 offene Positionen
- Max. 20 % des Depotwerts pro Position
- 3 % Stop-Loss
- 10 % Trailing-Stop ab +25 % Gewinn
- 1,5:1 Mindest-Reward/Risk
- 20 % Cash-Reserve

## Lokaler Start
```bash
pip install -r requirements.txt
python app.py
```
Login-Passwort: `Martin.Kraus2026!`

## Render Deploy
1. Repo auf GitHub pushen
2. In Render: „New Web Service“ → GitHub-Repo verbinden
3. Render erkennt `render.yaml` automatisch
4. Umgebungsvariable `DASHBOARD_PASSWORD` setzen
5. Deploy

## Telegram (optional)
`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` als Render-Env-Variablen setzen.
