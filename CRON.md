# Cron-Konfiguration

Da der kostenlose Render Free Web Service nach 15 Minuten Inaktivität in den Ruhezustand geht, 
wird für die regelmäßigen Aufrufe ein externer Cron-Dienst benötigt (z. B. cron-job.org, UptimeRobot, Easycron).

## Umgebungsvariable

Lege auf Render die folgende Environment Variable an: `SCHEDULER_API_KEY` = ein zufälliger langer Schlüssel

## Endpunkte

Alle Endpunkte erwarten den Header: `Authorization: Bearer <SCHEDULER_API_KEY>`

| Aufgabe | Intervall | URL |
|---|---|---|
| Preise aktualisieren | alle 5 Min | `POST /api/scheduler/refresh_prices` |
| Marktanalyse (ohne KI) | alle 30 Min in Handelszeiten | `POST /api/scheduler/market_analysis` |
| LLM-Analyse + Auto-Trade | alle 2 Std in Handelszeiten | `POST /api/scheduler/llm_analysis?auto_trade=true` |
| Tägliche Zusammenfassung | einmal täglich | `POST /api/scheduler/daily_summary` |

## Render-Service wachhalten

Zusätzlich sollte ein Ping-Endpunkt alle 10 Minuten aufgerufen werden, damit der kostenlose Plan nicht einschläft:
`GET https://trading-dashboard-6n5w.onrender.com/health`

## Beispiel curl

```bash
curl -X POST \
  https://trading-dashboard-6n5w.onrender.com/api/scheduler/refresh_prices \
  -H "Authorization: Bearer DEIN_SCHEDULER_API_KEY"
```
