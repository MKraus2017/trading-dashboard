"""Persistente Speicherung von Usern, Portfolio und Empfehlungen in SQLite."""
import json
import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

import config


DB_DIR = os.environ.get("RENDER_DISK_PATH", os.path.join(os.path.dirname(__file__), "..", "data"))
DB_PATH = os.path.join(DB_DIR, "trading.db")


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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                user_id INTEGER PRIMARY KEY,
                telegram_bot_token TEXT,
                telegram_chat_id TEXT,
                auto_trade_enabled INTEGER DEFAULT 1,
                report_enabled INTEGER DEFAULT 1,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)


def _now() -> str:
    return datetime.utcnow().isoformat()


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
    # Git-Backup der SQLite-DB für Persistenz auf Render
    try:
        from analyzer import db_backup
        db_backup.commit_db_backup(DB_PATH)
    except Exception:
        pass


def reset_portfolio(user_id: int):
    p = _default_portfolio()
    save_portfolio(user_id, p)
    return p


def delete_user(user_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


# --- Settings ---

def get_settings(user_id: int) -> dict:
    if not user_id:
        return {}
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM settings WHERE user_id = ?", (user_id,)).fetchone()
        if row:
            return {
                "telegram_bot_token": row["telegram_bot_token"] or "",
                "telegram_chat_id": row["telegram_chat_id"] or "",
                "auto_trade_enabled": bool(row["auto_trade_enabled"]),
                "report_enabled": bool(row["report_enabled"]),
            }
    return {
        "telegram_bot_token": "",
        "telegram_chat_id": "",
        "auto_trade_enabled": True,
        "report_enabled": True,
    }


def save_settings(user_id: int, settings: dict):
    if not user_id:
        return
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO settings (user_id, telegram_bot_token, telegram_chat_id, auto_trade_enabled, report_enabled, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 telegram_bot_token=excluded.telegram_bot_token,
                 telegram_chat_id=excluded.telegram_chat_id,
                 auto_trade_enabled=excluded.auto_trade_enabled,
                 report_enabled=excluded.report_enabled,
                 updated_at=excluded.updated_at""",
            (user_id,
             settings.get("telegram_bot_token", ""),
             settings.get("telegram_chat_id", ""),
             1 if settings.get("auto_trade_enabled", True) else 0,
             1 if settings.get("report_enabled", True) else 0,
             _now())
        )


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


init_db()
migrate_legacy_portfolio()
