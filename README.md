# Trading Dashboard

Live-Dashboard für OKX (Hebel-Depot) und Trade Republic (virtuell + real).

## Features
- OKX virtuelles Hebel-Depot: offene Positionen, P&L, Trade-Historie
- Trade Republic virtuell: NVDA und weitere Positionen
- Trade Republic real: NVDA Echtgeld-Position
- Live-Kurse über Yahoo Finance + OKX API
- Auto-Refresh alle 60 Sekunden
- Dark-Theme, responsive

## Lokaler Start
```bash
pip install -r requirements.txt
python app.py
```

## Render Deploy
1. GitHub Repo verbinden: https://github.com/MKraus2017/trading-dashboard
2. Build: `pip install -r requirements.txt`
3. Start: `gunicorn app:app`

## Wichtig
Die App liest Datendateien von `/opt/data/` — diese liegen auf dem Server.
Render greift über die Push-API darauf zu (POST /api/push).
