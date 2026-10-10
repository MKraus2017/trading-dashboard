"""Fail-closed Render storage migration and private SQLite snapshots."""
import os
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

_backup_lock = threading.Lock()
_last_backup = 0.0


def is_render():
    return os.environ.get("RENDER") == "true" or bool(os.environ.get("RENDER_SERVICE_ID"))


def marker(db_path):
    return Path(db_path).parent / ".storage-migration-complete"


def maintenance_required(db_path):
    return (os.environ.get("TRADING_MAINTENANCE") == "1" or
            (is_render() and not marker(db_path).is_file()))


def mark_restored(db_path):
    if is_render() and not os.path.ismount(str(Path(db_path).parent.resolve())):
        raise RuntimeError("Wiederherstellung erst nach Einbindung der dauerhaften Datenplatte möglich.")
    marker(db_path).write_text("SQLite backup restored and verified\n", encoding="utf-8")


def snapshot(source, destination):
    """SQLite's backup API includes committed WAL data and avoids torn copies."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("SQLite backup integrity check failed")


def private_backup(db_path, force=False):
    global _last_backup
    if maintenance_required(db_path) or not Path(db_path).is_file():
        return
    if not _backup_lock.acquire(blocking=False):
        return
    try:
        now = time.time()
        if not force and now - _last_backup < 3600:
            return
        folder = Path(db_path).parent / "backups"
        target = folder / ("trading_private_" + time.strftime("%Y%m%d_%H%M%S", time.gmtime(now)) + ".db")
        snapshot(db_path, target)
        _last_backup = now
        # Only this routine's snapshots; retain pre-migration and manual backups.
        for old in sorted(folder.glob("trading_private_*.db"))[:-48]:
            old.unlink()
    finally:
        _backup_lock.release()
