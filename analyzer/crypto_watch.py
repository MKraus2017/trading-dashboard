"""Server-side SL/TP checks for virtual crypto only. Never opens positions."""
import os
import math
import threading
import time
import storage_runtime

from analyzer.crypto_runtime import file_lock

INTERVAL_SECONDS = 30
STALE_SECONDS = 120
_thread = None
_start_lock = threading.Lock()
_stop = threading.Event()


def status(portfolio, now=None):
    now = time.time() if now is None else now
    stamp = portfolio.get("monitor_success_at")
    age = max(0, now - stamp) if isinstance(stamp, (int, float)) and 0 < stamp <= now else None
    stored_errors = portfolio.get("monitor_quote_errors", {})
    quote_errors = dict(stored_errors) if isinstance(stored_errors, dict) else {}
    missing = list(portfolio.get("monitor_missing_symbols", []))
    quote_ages = []
    if portfolio.get("monitor_quote_policy") == "fresh_exit_quotes_v1":
        for pos in portfolio.get("positions", []):
            symbol = pos.get("symbol", "?")
            accepted = pos.get("last_quote_at")
            error = None
            if isinstance(accepted, bool) or not isinstance(accepted, (int, float)) or not math.isfinite(accepted) or accepted <= 0:
                error = "missing_timestamp"
            elif accepted > now + 5:
                error = "future_quote"
            else:
                quote_ages.append(max(0, now - accepted))
                if now - accepted > 60:
                    error = "stale_quote"
            if error:
                quote_errors.setdefault(symbol, error)
                if symbol not in missing:
                    missing.append(symbol)
    return {
        "healthy": age is not None and age <= STALE_SECONDS and not missing and not quote_errors,
        "last_success_at": stamp,
        "age_seconds": round(age, 1) if age is not None else None,
        "missing_symbols": missing,
        "quote_errors": quote_errors,
        "oldest_quote_age_seconds": round(max(quote_ages), 1) if quote_ages else None,
        "quote_policy": portfolio.get("monitor_quote_policy"),
        "interval_seconds": INTERVAL_SECONDS,
        "stale_after_seconds": STALE_SECONDS,
        "scope": "virtual_crypto_only",
    }


def run_loop(check, stop, interval=INTERVAL_SECONDS, clock=time.monotonic):
    while not stop.is_set():
        started = clock()
        try:
            result = check()
            print("[CryptoWatch] cycle " + ("ok" if result.get("ok") else "incomplete"), flush=True)
        except Exception:
            print("[CryptoWatch] cycle failed; retrying", flush=True)
        stop.wait(max(1, interval - (clock() - started)))


def start():
    """Call only at web-worker startup, not from a backtest or module import."""
    global _thread
    if os.environ.get("CRYPTO_PAPER_MONITOR_ENABLED", "1") != "1":
        return
    with _start_lock:
        if _thread and _thread.is_alive():
            return
        _stop.clear()

        def worker():
            from analyzer import db_store, scheduler_tasks
            while not _stop.is_set():
                try:
                    with file_lock(str(db_store.DB_PATH) + ".crypto-monitor.lock", blocking=False) as leader:
                        if leader:
                            def check():
                                if storage_runtime.maintenance_required(db_store.DB_PATH):
                                    return {"ok": False}
                                try:
                                    from analyzer import crypto_paper_policy
                                    crypto_paper_policy.initialize(1)
                                except Exception:
                                    # A policy-enrollment failure must never stop
                                    # protection of already open paper positions.
                                    print("[CryptoPaperPolicy] initialization failed; exits remain monitored", flush=True)
                                result = scheduler_tasks.crypto_monitor_positions()
                                if storage_runtime.is_render():
                                    storage_runtime.private_backup(db_store.DB_PATH)
                                return result
                            run_loop(check, _stop)
                except Exception:
                    print("[CryptoWatch] startup failed; retrying", flush=True)
                _stop.wait(INTERVAL_SECONDS)

        _thread = threading.Thread(target=worker, name="virtual-crypto-watch", daemon=True)
        _thread.start()
