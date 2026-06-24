"""Persistente Speicherung von Usern, Portfolio und Einstellungen in SQLite."""
import json
import os
import shutil
import sqlite3
import subprocess
import threading
from datetime import datetime
from typing import Dict, List, Optional

import config


# Lokale Arbeits-DB. Auf Render sollte RENDER_DISK_PATH gesetzt sein (z.B. /data).
DB_DIR = os.environ.get("RENDER_DISK_PATH", os.path.join(os.path.dirname(__file__), "..", "data"))
DB_PATH = os.path.join(DB_DIR, "trading.db")

# Backup-DB im Git-Repository (für Persistenz über Deploys hinweg).
REPO_BACKUP_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "backup", "trading.db")

_RESTORE_DONE = False


def _repo_root() -> Optional[str]:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5, shell=False
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return None


def _backup_db_path() -> str:
    """Liefert den Pfad zur Backup-DB im Git-Repo."""
    root = _repo_root()
    if root:
        return os.path.join(root, "data", "backup", "trading.db")
    return REPO_BACKUP_PATH


def _copy_file(src: str, dst: str):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)


def _db_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except Exception:
        return 0


def _db_has_users(path: str) -> bool:
    try:
        with sqlite3.connect(path) as conn:
            cur = conn.execute("SELECT COUNT(*) FROM users")
            return cur.fetchone()[0] > 0
    except Exception:
        return False


def _count_users(path: str) -> int:
    try:
        with sqlite3.connect(path) as conn:
            cur = conn.execute("SELECT COUNT(*) FROM users")
            return cur.fetchone()[0]
    except Exception:
        return 0


def restore_db_backup():
    """Stellt die DB aus dem Git-Backup wieder her, falls lokale DB leer/fehlend."""
    global _RESTORE_DONE
    if _RESTORE_DONE:
        return
    _RESTORE_DONE = True

    local_exists = os.path.exists(DB_PATH)
    local_usable = local_exists and _db_size(DB_PATH) > 0 and _db_has_users(DB_PATH)
    backup_path = _backup_db_path()
    backup_exists = os.path.exists(backup_path)

    print(f"[DB] Working DB: {DB_PATH} (exists={local_exists}, usable={local_usable})")
    print(f"[DB] Backup DB: {backup_path} (exists={backup_exists})")

    if local_usable:
        local_users = _count_users(DB_PATH)
        if backup_exists and _db_has_users(backup_path):
            backup_users = _count_users(backup_path)
            print(f"[DB] Local users: {local_users}, backup users: {backup_users}")
        return

    if backup_exists and _db_has_users(backup_path):
        print(f"[DB] Local DB empty -> restoring from backup")
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _copy_file(backup_path, DB_PATH)
        print("[DB] Restore complete")
    else:
        print("[DB] No usable backup found, starting fresh")


def get_conn():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS portfolios (
                user_id INTEGER PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)
        # Telegram-Creds werden bewusst nicht hier gespeichert (Render Env vars).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                user_id INTEGER PRIMARY KEY,
                auto_trade_enabled INTEGER DEFAULT 1,
                report_enabled INTEGER DEFAULT 1,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)


def _now() -> str:
    return datetime.utcnow().isoformat()


# --- Backups ---

def _backup_async():
    """Kopiert DB ins Git-Repo und committet asynchron."""
    try:
        if not os.path.exists(DB_PATH):
            return
        backup_path = _backup_db_path()
        os.makedirs(os.path.dirname(backup_path), exist_ok=True)
        _copy_file(DB_PATH, backup_path)

        root = _repo_root()
        if not root:
            return
        rel = os.path.relpath(backup_path, root)
        # Für Sicherheit: niemals Plaintext-Tokens committen.
        subprocess.run(["git", "add", rel], capture_output=True, cwd=root, timeout=10)
        subprocess.run(
            ["git", "commit", "-m", f"Auto-DB-Backup {_now()}"],
            capture_output=True, cwd=root, timeout=10
        )
        subprocess.run(["git", "push"], capture_output=True, cwd=root, timeout=30)
    except Exception as e:
        print(f"[DB Backup] Warning: {e}")


def trigger_backup():
    """Startet Backup in separatem Thread."""
    try:
        t = threading.Thread(target=_backup_async, daemon=True)
        t.start()
    except Exception as e:
        print(f"[DB Backup] Could not start thread: {e}")


# --- Users ---

def create_user(username: str, password_hash: str) -> Optional[int]:
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, password_hash, _now())
            )
            user_id = cur.lastrowid
            _init_user_data(conn, user_id)
            return user_id
    except sqlite3.IntegrityError:
        return None
    finally:
        trigger_backup()


def get_user_by_username(username: str) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return row


def get_user_by_id(user_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def list_users() -> List[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT id FROM users ORDER BY id").fetchall()


def set_user_password(user_id: int, password_hash: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (password_hash, user_id)
        )
    trigger_backup()


# --- Portfolio ---

def _default_portfolio():
    return {
        "cash": config.START_CAPITAL,
        "positions": [],
        "trades": [],
        "value_history": [{"date": _now(), "value": config.START_CAPITAL}],
        "real_positions": [],
        "real_trades": [],
        "watchlist": [],
    }


def _init_user_data(conn, user_id: int):
    conn.execute(
        "INSERT OR IGNORE INTO portfolios (user_id, data, updated_at) VALUES (?, ?, ?)",
        (user_id, json.dumps(_default_portfolio()), _now())
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings (user_id, updated_at) VALUES (?, ?)",
        (user_id, _now())
    )


def load_portfolio(user_id: int) -> Optional[dict]:
    if not user_id:
        return None
    with get_conn() as conn:
        row = conn.execute("SELECT data FROM portfolios WHERE user_id = ?", (user_id,)).fetchone()
        if row and row["data"]:
            return json.loads(row["data"])
    return None


def save_portfolio(user_id: int, p: dict):
    if not user_id:
        return
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO portfolios (user_id, data, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
            (user_id, json.dumps(p, default=str), _now())
        )
    trigger_backup()


def save_settings(user_id: int, settings: dict):
    """Speichert Settings ohne Telegram-Credentials (kommen aus Env/Config)."""
    if not user_id:
        return
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO settings (user_id, auto_trade_enabled, report_enabled, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 auto_trade_enabled=excluded.auto_trade_enabled,
                 report_enabled=excluded.report_enabled,
                 updated_at=excluded.updated_at""",
            (user_id,
             1 if settings.get("auto_trade_enabled", True) else 0,
             1 if settings.get("report_enabled", True) else 0,
             _now())
        )
    trigger_backup()


def reset_portfolio(user_id: int):
    p = _default_portfolio()
    save_portfolio(user_id, p)
    return p


def delete_user(user_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    trigger_backup()


# --- Settings ---

def get_settings(user_id: int) -> dict:
    """Holt Settings. Telegram kommt bevorzugt aus Env-Vars (sicher gegen Datenverlust)."""
    defaults = {
        "telegram_bot_token": os.environ.get("TELEGRAM_BOT_TOKEN") or config.TELEGRAM_BOT_TOKEN,
        "telegram_chat_id": os.environ.get("TELEGRAM_CHAT_ID") or config.TELEGRAM_CHAT_ID,
        "auto_trade_enabled": True,
        "report_enabled": True,
    }
    if not user_id:
        return defaults
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM settings WHERE user_id = ?", (user_id,)).fetchone()
        if row:
            return {
                "telegram_bot_token": defaults["telegram_bot_token"],
                "telegram_chat_id": defaults["telegram_chat_id"],
                "auto_trade_enabled": bool(row["auto_trade_enabled"]),
                "report_enabled": bool(row["report_enabled"]),
            }
    return defaults


# --- Migration: alter default user ---

def migrate_legacy_portfolio():
    """Migriert das alte portfolio.json in user_id 1, falls vorhanden."""
    legacy = os.path.join(os.path.dirname(__file__), "..", "data", "portfolio.json")
    if not os.path.exists(legacy):
        return
    try:
        with open(legacy, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash, created_at) VALUES (1, 'default', '', CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO portfolios (user_id, data, updated_at) VALUES (1, ?, CURRENT_TIMESTAMP)",
            (json.dumps(data, default=str),)
        )
    trigger_backup()


# Beim Import: Restore aus Backup, dann initialisieren
restore_db_backup()
init_db()
migrate_legacy_portfolio()
