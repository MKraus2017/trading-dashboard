"""Browser-independent monitoring of existing PAPER stock positions only.

Importing this module does not bootstrap the app, open storage or start threads.
The portfolio evaluator owns quote validation and persisted heartbeat fields.
This monitor never opens positions, sends messages or calls a broker.
"""
import math
import os
import threading
import time


INTERVAL_SECONDS = 60
STALE_SECONDS = 180
_thread = None
_start_lock = threading.Lock()
_stop = threading.Event()


def _enabled():
    return os.environ.get("STOCK_PAPER_MONITOR_ENABLED", "1") == "1"


def _timestamp(value, now):
    if (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and 0 < value <= now):
        return value
    return None


def status(portfolio, now=None):
    """Heartbeat health is separate from missing quotes and closed markets.

    ``closed_symbols`` means market-closed symbols, not sold positions. A normal
    weekend/holiday evaluation can be healthy without any tradable quotes.
    """
    now = time.time() if now is None else now
    stamp = _timestamp(portfolio.get("stock_monitor_success_at"), now)
    attempt = _timestamp(portfolio.get("stock_monitor_attempt_at"), now)
    age = now - stamp if stamp is not None else None
    missing = list(portfolio.get("stock_monitor_missing_symbols") or [])
    closed = list(portfolio.get("stock_monitor_closed_symbols") or [])
    enabled = _enabled()
    return {
        "enabled": enabled,
        "healthy": bool(enabled and age is not None and age <= STALE_SECONDS),
        "last_attempt_at": attempt,
        "last_success_at": stamp,
        "age_seconds": round(age, 1) if age is not None else None,
        "missing_symbols": missing,
        "closed_symbols": closed,
        "data_complete": not missing,
        "interval_seconds": INTERVAL_SECONDS,
        "stale_after_seconds": STALE_SECONDS,
        "scope": "virtual_stocks_only",
    }


def cycle():
    """Evaluate each user's existing paper ledger; isolate per-user failures."""
    if not _enabled():
        return {"ok": False, "skipped": "disabled", "checked": 0, "failed_users": []}
    import storage_runtime
    from analyzer import db_store, portfolio

    if storage_runtime.maintenance_required(db_store.DB_PATH):
        return {"ok": False, "skipped": "maintenance", "checked": 0, "failed_users": []}

    users = db_store.list_users()
    results = []
    failed = []
    for row in users:
        if storage_runtime.maintenance_required(db_store.DB_PATH):
            return {"ok": False, "skipped": "maintenance", "checked": len(results),
                    "failed_users": failed, "results": results}
        uid = row["id"]
        try:
            current, _alerts = portfolio.evaluate_portfolio(uid)
            result = status(current)
            results.append({"user_id": uid, "healthy": result["healthy"],
                            "data_complete": result["data_complete"],
                            "missing_symbols": result["missing_symbols"],
                            "closed_symbols": result["closed_symbols"]})
        except Exception as exc:
            # Do not expose exception messages, account data or credentials.
            failed.append({"user_id": uid, "error_type": type(exc).__name__})
    return {"ok": not failed, "checked": len(results), "users": len(users),
            "failed_users": failed, "results": results}


def run_loop(check, stop, interval=INTERVAL_SECONDS, clock=time.monotonic):
    while not stop.is_set():
        started = clock()
        try:
            result = check()
            outcome = "paused" if result.get("skipped") else ("ok" if result.get("ok") else "incomplete")
            incomplete = sum(not row.get("data_complete", True) for row in result.get("results", []))
            print(f"[StockWatch] paper cycle {outcome}; checked={result.get('checked', 0)}, missing-data users={incomplete}", flush=True)
        except Exception:
            print("[StockWatch] paper cycle failed; retrying", flush=True)
        stop.wait(max(1, interval - (clock() - started)))


def start():
    """Call only after web-worker fork; leadership prevents duplicate monitors."""
    global _thread
    if not _enabled():
        return
    with _start_lock:
        if _thread and _thread.is_alive():
            return
        _stop.clear()

        def worker():
            while not _stop.is_set():
                try:
                    import storage_runtime
                    from analyzer import db_store
                    from analyzer.crypto_runtime import file_lock

                    if not storage_runtime.maintenance_required(db_store.DB_PATH):
                        with file_lock(str(db_store.DB_PATH) + ".stock-monitor.lock", blocking=False) as leader:
                            if leader:
                                run_loop(cycle, _stop)
                except Exception:
                    print("[StockWatch] worker unavailable; retrying", flush=True)
                _stop.wait(INTERVAL_SECONDS)

        _thread = threading.Thread(target=worker, name="virtual-stock-watch", daemon=True)
        _thread.start()
