"""Match the intended timeout from render.yaml for `gunicorn app:app`.

The default 30 seconds was killing the worker during analysis. Keep one HTTP
worker; virtual crypto checks run in their own thread with locked ledger writes.
"""
workers = 1
timeout = 240
preload_app = False


def pre_fork(server, worker):
    # Environment/CLI flags can override preload_app. Collect legacy SQLite
    # connection cycles in the master before any descriptors are inherited.
    import gc
    gc.collect()


def post_worker_init(worker):
    # Start after fork, even when Render overrides preloading. The master must
    # never own background threads or their database/leader locks.
    from app import start_background_watchers
    start_background_watchers()
    worker.log.info("Virtual monitors initialized in worker pid=%s", worker.pid)
