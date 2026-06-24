"""Git-Backup der SQLite-Datenbank (Persistenz auf Render)."""
import os
import subprocess
from datetime import datetime, timezone


def commit_db_backup(db_path: str) -> dict:
    """Committet und pusht die SQLite-DB in das Git-Repo, falls verfügbar."""
    try:
        repo = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5
        )
        if repo.returncode != 0:
            return {"ok": False, "error": "Kein Git-Repo"}
        top = repo.stdout.strip()
        # Relativen Pfad zum Repo-Root berechnen
        rel = os.path.relpath(db_path, top)
        subprocess.run(
            ["git", "add", rel],
            capture_output=True, timeout=10, cwd=top
        )
        res = subprocess.run(
            ["git", "commit", "-m", f"Auto-DB-Backup {datetime.now(timezone.utc).isoformat()}"],
            capture_output=True, text=True, timeout=10, cwd=top
        )
        if res.returncode == 0:
            subprocess.run(
                ["git", "push"],
                capture_output=True, timeout=30, cwd=top
            )
            return {"ok": True}
        return {"ok": False, "error": res.stderr[:200] or "No changes to backup"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
