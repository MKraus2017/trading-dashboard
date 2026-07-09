"""Persistente Speicherung von Usern, Portfolio und Einstellungen in SQLite."""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional

import config


DB_DIR = os.environ.get("RENDER_DISK_PATH", os.path.join(os.path.dirname(__file__), "..", "data"))
DB_PATH = os.path.join(DB_DIR, "trading.db")

# Zusätzliche lokale Timestamped-Backups auf der Render-Disk (mehrere Generationen).
LOCAL_BACKUP_DIR = os.path.join(DB_DIR, "backups")

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
    return os.path.join(os.path.dirname(__file__), "..", "data", "backup", "trading.db")


REPO_BACKUP_PATH = _backup_db_path()


def _copy_file(src: str, dst: str):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)


def _db_mtime(path: str) -> float:
    """Liefert Modifikationszeit der DB-Datei."""
    try:
        return os.path.getmtime(path)
    except Exception:
        return 0.0


def _newest_existing(*paths: str) -> Optional[str]:
    """Gibt den Pfad der neuesten existierenden und validen DB zurück."""
    valid = [(p, _db_mtime(p)) for p in paths if os.path.exists(p) and _db_size(p) > 0 and _db_has_users(p)]
    if not valid:
        return None
    return max(valid, key=lambda x: x[1])[0]


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
    """Stellt die aktuellste bekannte DB aus Backups wieder her.

    Reihenfolge der Auswahl (neueste validierte DB gewinnt):
    1. Lokale Arbeits-DB auf RENDER_DISK_PATH
    2. Lokale Timestamped-Backups auf der Render-Disk
    3. Git-Repo-Backup

    Wichtig: Diese Funktion darf niemals eine neuere lokale DB
    überschreiben. Sie wird nur einmal beim Import ausgeführt.
    """
    global _RESTORE_DONE
    if _RESTORE_DONE:
        return
    _RESTORE_DONE = True

    backup_path = _backup_db_path()
    ts_backup = _newest_timestamped_backup()
    candidates = [DB_PATH, backup_path]
    if ts_backup:
        candidates.append(ts_backup)

    newest = _newest_existing(*candidates)

    print("=" * 60)
    print("[DB] Startup DB restore check")
    print(f"[DB] Candidates:")
    for c in candidates:
        print(f"[DB]   {c}: exists={os.path.exists(c)}, size={_db_size(c)} bytes, mtime={_db_mtime(c)}")

    # Bei leerer lokaler DB (z. B. neues Render-Deploy ohne persistente Disk) immer
    # das neueste verfügbare Backup verwenden, auch wenn DB_PATH existiert, aber leer ist.
    local_size = _db_size(DB_PATH)
    local_users = _count_users(DB_PATH)
    local_portfolios = _count_portfolios(DB_PATH)

    if newest == DB_PATH and local_users > 0:
        print(f"[DB] Local DB is newest and has data -> KEEP (users={local_users}, portfolios={local_portfolios})")
        print("=" * 60)
        return

    if DB_PATH == newest and local_size == 0:
        print("[DB] Local DB exists but is empty -> searching for backup")
        candidates_without_local = [c for c in candidates if c != DB_PATH]
        newest = _newest_existing(*candidates_without_local)

    if newest:
        print(f"[DB] Restoring newest DB from {newest}")
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _copy_file(newest, DB_PATH)
        print("[DB] Restore complete")
    else:
        print("[DB] No usable backup found, starting fresh")
    print("=" * 60)


def _count_portfolios(path: str) -> int:
    try:
        with sqlite3.connect(path) as conn:
            cur = conn.execute("SELECT COUNT(*) FROM portfolios")
            return cur.fetchone()[0]
    except Exception:
        return 0


def _timestamped_backup_path() -> str:
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return os.path.join(LOCAL_BACKUP_DIR, f"trading_{ts}.db")


def _newest_timestamped_backup() -> Optional[str]:
    try:
        if not os.path.isdir(LOCAL_BACKUP_DIR):
            return None
        files = [
            os.path.join(LOCAL_BACKUP_DIR, f)
            for f in os.listdir(LOCAL_BACKUP_DIR)
            if f.startswith("trading_") and f.endswith(".db")
        ]
        files = [f for f in files if os.path.isfile(f) and _db_has_users(f)]
        if not files:
            return None
        return max(files, key=os.path.getmtime)
    except Exception:
        return None


def _rotate_timestamped_backups(keep: int = 10):
    try:
        if not os.path.isdir(LOCAL_BACKUP_DIR):
            return
        files = [
            os.path.join(LOCAL_BACKUP_DIR, f)
            for f in os.listdir(LOCAL_BACKUP_DIR)
            if f.startswith("trading_") and f.endswith(".db")
        ]
        files = [f for f in files if os.path.isfile(f)]
        files.sort(key=os.path.getmtime, reverse=True)
        for old in files[keep:]:
            try:
                os.remove(old)
            except Exception:
                pass
    except Exception:
        pass


def get_conn():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _ensure_crypto_tables(conn)
    return conn


def _ensure_crypto_tables(conn):
    """Selbstheilende Absicherung: falls eine wiederhergestellte/alte DB die neuen
    Krypto-Tabellen nicht kennt (z.B. nach Restore eines alten Backups auf Render),
    werden sie hier zusaetzlich sichergestellt. Guenstig, da CREATE TABLE IF NOT EXISTS."""
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS crypto_portfolios (
                user_id INTEGER PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS crypto_backtest_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                run_at TEXT NOT NULL,
                params TEXT NOT NULL,
                results TEXT NOT NULL,
                applied INTEGER DEFAULT 0
            );
        """)
    except sqlite3.OperationalError:
        pass


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
        # Migration: Telegram-Credential-Spalten nachträglich hinzufügen
        try:
            conn.execute("ALTER TABLE settings ADD COLUMN telegram_bot_token TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE settings ADD COLUMN telegram_chat_id TEXT")
        except sqlite3.OperationalError:
            pass

        # Separates virtuelles Krypto-Depot (unabhaengig vom Aktien-Portfolio)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS crypto_portfolios (
                user_id INTEGER PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)
        # Backtest-/Selbstoptimierungs-Historie fuer den Krypto-Bot
        conn.execute("""
            CREATE TABLE IF NOT EXISTS crypto_backtest_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                run_at TEXT NOT NULL,
                params TEXT NOT NULL,
                results TEXT NOT NULL,
                applied INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)


def _now() -> str:
    return datetime.utcnow().isoformat()


# --- Backups ---

def _backup_async():
    """Kopiert DB ins Repo-Backup, erstellt lokale Timestamp-Backups und versucht Git-Push."""
    try:
        if not os.path.exists(DB_PATH):
            print(f"[DB Backup] No local DB to backup: {DB_PATH}")
            return

        # 1) Synchrones Dateisystem-Backup ins Repo – das geht schnell, egal ob Git funktioniert
        backup_path = _backup_db_path()
        os.makedirs(os.path.dirname(backup_path), exist_ok=True)
        _copy_file(DB_PATH, backup_path)

        # 2) Lokale Timestamped-Backups auf der Render-Disk
        os.makedirs(LOCAL_BACKUP_DIR, exist_ok=True)
        ts_path = _timestamped_backup_path()
        _copy_file(DB_PATH, ts_path)
        _rotate_timestamped_backups(keep=20)

        root = _repo_root()
        if not root:
            return
        rel = os.path.relpath(backup_path, root)
        # Git-Author sicherstellen
        subprocess.run(["git", "config", "user.email", "bot@trading-dashboard.local"], capture_output=True, cwd=root, timeout=3)
        subprocess.run(["git", "config", "user.name", "Trading Dashboard Bot"], capture_output=True, cwd=root, timeout=3)
        subprocess.run(["git", "add", rel], capture_output=True, cwd=root, timeout=3)
        subprocess.run(
            ["git", "commit", "-m", f"Auto-DB-Backup {_now()}"],
            capture_output=True, cwd=root, timeout=3
        )
        push_res = subprocess.run(["git", "push"], capture_output=True, cwd=root, timeout=5)
        if push_res.returncode != 0:
            err = push_res.stderr.decode('utf-8', errors='ignore')[:200]
            print(f"[DB Backup] Push warning (non-critical): {err}")
    except subprocess.TimeoutExpired as e:
        print(f"[DB Backup] Timeout: {e}")
    except Exception as e:
        print(f"[DB Backup] Warning: {e}")


def backup_db(synchronous=False):
    """Backup. Synchronous=True bedeutet nur, dass wir direkt ausführen (für Startup)."""
    try:
        _backup_async()
    except Exception as e:
        print(f"[DB Backup] Backup skipped at startup: {e}")


def trigger_backup():
    """Startet Backup in separatem Daemon-Thread."""
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
    # Kopie der DB sofort ins Git-Repo/auf Disk, aber asynchron, damit der Request nicht blockiert.
    backup_db()


# --- Krypto-Depot (separat, unabhaengig vom Aktien-Portfolio) ---

def _default_crypto_portfolio():
    return {
        "cash": config.CRYPTO_START_CAPITAL,
        "start_capital": config.CRYPTO_START_CAPITAL,
        "positions": [],       # offene Hebel-Positionen (long/short, virtuell)
        "trades": [],          # geschlossene Trades (Historie)
        "value_history": [{"date": _now(), "value": config.CRYPTO_START_CAPITAL}],
    }


def load_crypto_portfolio(user_id: int) -> Optional[dict]:
    if not user_id:
        return None
    with get_conn() as conn:
        row = conn.execute("SELECT data FROM crypto_portfolios WHERE user_id = ?", (user_id,)).fetchone()
        if row and row["data"]:
            return json.loads(row["data"])
    return None


def save_crypto_portfolio(user_id: int, p: dict):
    if not user_id:
        return
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO crypto_portfolios (user_id, data, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
            (user_id, json.dumps(p, default=str), _now())
        )
    backup_db()


def save_crypto_backtest_run(user_id: int, params: dict, results: dict, applied: bool = False):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO crypto_backtest_runs (user_id, run_at, params, results, applied) VALUES (?, ?, ?, ?, ?)",
            (user_id, _now(), json.dumps(params, default=str), json.dumps(results, default=str), 1 if applied else 0)
        )
    backup_db()


def get_crypto_backtest_history(user_id: int, limit: int = 20) -> List[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, run_at, params, results, applied FROM crypto_backtest_runs "
            "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "run_at": r["run_at"],
            "params": json.loads(r["params"]),
            "results": json.loads(r["results"]),
            "applied": bool(r["applied"]),
        })
    return out


def save_settings(user_id: int, settings: dict):
    """Speichert alle Settings inkl. Telegram-Credentials. Validiert vorab."""
    if not user_id:
        return

    token = settings.get("telegram_bot_token", "").strip()
    chat_id = settings.get("telegram_chat_id", "").strip()

    # Validierung: Token darf nur aus dem Bot-Token-Format bestehen
    # Telegram liefert Token als "ZIFFERN:ALPHA_NUM_UNDERSCORE_HYPHEN" (z. B. 123456789:ABC...)
    if token and not re.match(r"^\d+:[A-Za-z0-9_-]{30,}$", token):
        raise ValueError(
            "Ungültiger Telegram-Bot-Token. Erwarte Format wie '123456789:ABC...'. "
            "Der Username (@...) ist nicht der Token."
        )

    # Validierung: Chat-ID darf numerisch oder mit '-' beginnend sein
    if chat_id and not re.match(r"^-?\d+$", chat_id):
        raise ValueError("Telegram Chat-ID muss eine Zahl sein (z. B. 12345678).")

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
             token,
             chat_id,
             1 if settings.get("auto_trade_enabled", True) else 0,
             1 if settings.get("report_enabled", True) else 0,
             _now())
        )
    # Backup sofort anstoßen, aber niemals synchron -> Webserver bleibt responsiv
    backup_db()


def reset_portfolio(user_id: int):
    p = _default_portfolio()
    save_portfolio(user_id, p)
    return p


def delete_user(user_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    backup_db()


# --- Settings ---

def get_settings(user_id: int) -> dict:
    """Holt Settings. Telegram hat Priorität: DB > Env > config."""
    env_token = os.environ.get("TELEGRAM_BOT_TOKEN") or config.TELEGRAM_BOT_TOKEN
    env_chat = os.environ.get("TELEGRAM_CHAT_ID") or config.TELEGRAM_CHAT_ID
    defaults = {
        "telegram_bot_token": env_token,
        "telegram_chat_id": env_chat,
        "auto_trade_enabled": True,
        "report_enabled": True,
    }
    if not user_id:
        return defaults
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM settings WHERE user_id = ?", (user_id,)).fetchone()
            if row:
                return {
                    "telegram_bot_token": row["telegram_bot_token"] or env_token,
                    "telegram_chat_id": row["telegram_chat_id"] or env_chat,
                    "auto_trade_enabled": bool(row["auto_trade_enabled"]),
                    "report_enabled": bool(row["report_enabled"]),
                }
    except Exception as e:
        print(f"[get_settings] Error: {e}")
        # Falls Settings-Table nicht existiert, Defaults zurückgeben
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
    backup_db()


# Beim Import: Restore aus Backup, dann initialisieren
restore_db_backup()
init_db()
migrate_legacy_portfolio()
# Direkt nach dem Start nochmal ein Backup anstoßen, um sicherzustellen,
# dass die aktuelle lokale DB im Git-Repo gespiegelt ist.
backup_db(synchronous=True)
