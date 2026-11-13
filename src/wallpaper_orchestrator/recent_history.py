"""C5 durable, cross-process wallpaper history.

The separate SQLite file avoids changing the existing catalogue schema and
gives the GUI/background agent transactional, concurrent access.
"""

from collections.abc import Mapping
import os
from pathlib import Path
import sqlite3
import time


def history_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".config"
    return root / "WallpaperOrchestrator" / "selection-history.sqlite3"


def path_key(path: Path | str) -> str:
    return str(Path(path).resolve()).casefold()


class RecentHistory:
    """Last 100 successful choices per stable monitor ID, newest first."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else history_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.execute("PRAGMA busy_timeout = 10000")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            monitor_id TEXT NOT NULL,
            path_key TEXT NOT NULL,
            applied_utc REAL NOT NULL
        )""")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_history_monitor ON history(monitor_id, id DESC)")
        self.db.commit()

    def __enter__(self) -> "RecentHistory":
        return self

    def __exit__(self, *_args):
        self.db.close()

    def recent(self, monitor_id: str, limit: int) -> tuple[str, ...]:
        if limit <= 0:
            return ()
        rows = self.db.execute(
            "SELECT path_key FROM history WHERE monitor_id = ? ORDER BY id DESC LIMIT ?",
            (monitor_id, limit),
        )
        return tuple(row[0] for row in rows)

    def recent_global_events(self, limit: int) -> tuple[str, ...]:
        """Paths used in the last N successful shuffle *events* on any monitor.

        Each multi-display application shares one applied_utc value, so it
        counts as one shuffle, rather than consuming N history slots.
        """
        if limit <= 0:
            return ()
        rows = self.db.execute("""
            SELECT path_key FROM history
            WHERE applied_utc IN (
                SELECT DISTINCT applied_utc FROM history
                ORDER BY applied_utc DESC LIMIT ?
            ) ORDER BY applied_utc DESC, id DESC
        """, (limit,))
        return tuple(dict.fromkeys(row[0] for row in rows))

    def record(self, assignments: Mapping[str, Path | str]) -> None:
        """Only call after a complete, successful multi-monitor application."""
        if not assignments:
            return
        moment = time.time()
        with self.db:
            self.db.executemany(
                "INSERT INTO history(monitor_id, path_key, applied_utc) VALUES (?, ?, ?)",
                [(monitor_id, path_key(path), moment)
                 for monitor_id, path in assignments.items()],
            )
            # A bounded history keeps lookups fast for long-lived installations.
            for monitor_id in assignments:
                self.db.execute("""DELETE FROM history WHERE monitor_id = ?
                    AND id NOT IN (SELECT id FROM history WHERE monitor_id = ?
                                   ORDER BY id DESC LIMIT 100)""",
                                (monitor_id, monitor_id))
