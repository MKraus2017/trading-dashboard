"""Serialization for the virtual crypto ledger, not broker orders."""
import os
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

_locks = {}
_registry_lock = threading.Lock()
_held = threading.local()


@contextmanager
def file_lock(path, blocking=True):
    """Never unlink a lock inode while another worker might be using it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0, 2)
            if not handle.tell():
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            except OSError:
                if blocking:
                    raise
                yield False
                return
            try:
                yield True
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def ledger_lock(path):
    key = str(Path(path).resolve())
    with _registry_lock:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_held, "paths", set())
        if key in held:
            yield
            return
        with file_lock(key):
            _held.paths = held | {key}
            try:
                yield
            finally:
                _held.paths = held


def serialized(fn):
    @wraps(fn)
    def wrapped(user_id, *args, **kwargs):
        from analyzer import db_store
        path = str(db_store.DB_PATH) + f".crypto-{int(user_id)}.lock"
        with ledger_lock(path):
            return fn(user_id, *args, **kwargs)
    return wrapped
