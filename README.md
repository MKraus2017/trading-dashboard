# Trading Dashboard

Live-Dashboard für OKX virtuelles Hebel-Depot, Trade Republic virtuelles Depot und Trade Republic reales Depot.

## Features
- OKX virtuelle Positionen mit Hebel, LONG/SHORT, P&L live
- Trade Republic virtuelles Depot (NVDA)
- Trade Republic reales Depot mit Transaktionshistorie
- Dark Mode, Auto-Refresh alle 60 Sekunden
- Live-Kurse via Yahoo Finance & CoinGecko

## Deployment auf Render

1. Repo mit Render verbinden: https://render.com/docs/github
2. Service Type: Web Service
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `gunicorn app:app`
5. Umgebungsvariablen setzen (siehe unten)

## Umgebungsvariablen (Render)

Die Datenpfade müssen auf Render als Pfade zu Persistent Disks konfiguriert werden,
oder die JSON-Dateien werden per API-Endpunkt vom Heimserver synchronisiert.

## Lokaler Start

```bash
pip install -r requirements.txt
python app.py
```
