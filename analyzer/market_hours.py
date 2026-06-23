"""Hilfsfunktionen zur Prüfung von Handelszeiten."""
from datetime import datetime, timedelta, timezone


# Handelszeiten für unsere Märkte
# US: Mo-Fr 09:30-16:00 ET (15:30-22:00 UTC)
# EU (Xetra): Mo-Fr 09:00-17:30 CET (08:00-16:30 UTC)
TRADING_HOURS = {
    "US": {
        "weekdays": range(0, 5),  # Monday = 0 ... Friday = 4
        "start_utc": (15, 30),
        "end_utc": (22, 0),
    },
    "EU": {
        "weekdays": range(0, 5),
        "start_utc": (8, 0),
        "end_utc": (16, 30),
    },
}


def is_trading_hours(market: str = "US", utc_now: datetime = None) -> bool:
    """Prüft, ob aktuell Handelszeit für den angegebenen Markt ist."""
    if utc_now is None:
        utc_now = datetime.now(timezone.utc)
    cfg = TRADING_HOURS.get(market)
    if not cfg:
        return False
    if utc_now.weekday() not in cfg["weekdays"]:
        return False
    start_hour, start_min = cfg["start_utc"]
    end_hour, end_min = cfg["end_utc"]
    start = utc_now.replace(hour=start_hour, minute=start_min, second=0, microsecond=0)
    end = utc_now.replace(hour=end_hour, minute=end_min, second=0, microsecond=0)
    return start <= utc_now <= end


def is_any_trading_hours(utc_now: datetime = None) -> bool:
    """True, wenn entweder EU- oder US-Handelszeit läuft."""
    return is_trading_hours("US", utc_now) or is_trading_hours("EU", utc_now)


def next_trading_hours_info(utc_now: datetime = None) -> dict:
    """Info, wann der nächste Handelszeitraum beginnt."""
    if utc_now is None:
        utc_now = datetime.now(timezone.utc)
    markets = []
    for m in ["US", "EU"]:
        cfg = TRADING_HOURS[m]
        start_hour, start_min = cfg["start_utc"]
        start = utc_now.replace(hour=start_hour, minute=start_min, second=0, microsecond=0)
        if start < utc_now:
            start += timedelta(days=1)
        # Skip weekends; simplistic: add days until weekday
        while start.weekday() not in cfg["weekdays"]:
            start += timedelta(days=1)
        markets.append({"market": m, "next_start": start})
    markets.sort(key=lambda x: x["next_start"])
    return markets[0]
