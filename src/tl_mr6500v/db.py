import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT UNIQUE NOT NULL,
    idx INTEGER NOT NULL,
    sender TEXT NOT NULL,
    content TEXT NOT NULL,
    received_at TEXT NOT NULL,
    unread INTEGER NOT NULL,
    delivered TEXT NOT NULL,
    notified_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created REAL NOT NULL,
    levelno INTEGER NOT NULL,
    level TEXT NOT NULL,
    logger TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS logs_levelno ON logs (levelno, id);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    created REAL NOT NULL,
    expires REAL NOT NULL
);
"""


class Database:
    """Minimal SQLite wrapper: one connection plus a lock; every statement commits automatically."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def execute(self, sql: str, params: tuple = ()) -> int:
        """Return the number of affected rows."""
        with self._lock:
            return self._conn.execute(sql, params).rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()
