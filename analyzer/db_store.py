"""Persistente Speicherung von Portfolio und Empfehlungen in SQLite."""
import json
import os
import sqlite3
from datetime import datetime
from typing import Dict, Optional

import config


DB_DIR = os.environ.get("RENDER_DISK_PATH", os.path.join(os.path.dirname(__file__), "..", "data"))
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "trading.db")


def _connect():
    return sqlite3.connect(DB_PATH)


def init_db():
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS portfolio (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                data TEXT NOT NULL,
                updated_at TEXT
            )
        """)
        conn.commit()


def save_portfolio(data: Dict):
    """Speichert Portfolio in SQLite."""
    init_db()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO portfolio (id, data, updated_at) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
            (json.dumps(data, default=str), datetime.utcnow().isoformat())
        )
        conn.commit()


def load_portfolio() -> Optional[Dict]:
    """Lädt Portfolio aus SQLite."""
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT data FROM portfolio WHERE id = 1").fetchone()
        if row:
            return json.loads(row[0])
    return None


def reset_portfolio():
    """Löscht das Portfolio (z. B. für Reset-Button)."""
    init_db()
    with _connect() as conn:
        conn.execute("DELETE FROM portfolio")
        conn.commit()
