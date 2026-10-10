"""Match the intended timeout from render.yaml for `gunicorn app:app`.

The default 30 seconds was killing the worker during analysis. Keep one HTTP
worker; virtual crypto checks run in their own thread with locked ledger writes.
"""
workers = 1
timeout = 240
preload_app = False
