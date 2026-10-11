"""Serialize whole read/modify/write operations on the stock paper/manual ledger."""
from functools import wraps
from analyzer.crypto_runtime import ledger_lock


def user_lock(user_id):
    from analyzer import db_store
    return ledger_lock(str(db_store.DB_PATH) + f".stock-{int(user_id)}.lock")


def serialized(fn):
    @wraps(fn)
    def wrapped(user_id, *args, **kwargs):
        with user_lock(user_id):
            return fn(user_id, *args, **kwargs)
    return wrapped


def serialized_request(get_user_id):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            with user_lock(get_user_id()):
                return fn(*args, **kwargs)
        return wrapped
    return decorate
