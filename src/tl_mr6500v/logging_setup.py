import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .db import Database

LOGGER_NAME = "tl_mr6500v"
MAX_DB_LOGS = 5000
_PRUNE_EVERY = 200


class SqliteLogHandler(logging.Handler):
    """Writes logs to SQLite for the Dashboard; keeps only the latest MAX_DB_LOGS rows."""

    def __init__(self, db: Database):
        super().__init__(logging.INFO)
        self._db = db
        self._inserted = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info:
                message += "\n" + logging.Formatter().formatException(record.exc_info)
            self._db.execute(
                "INSERT INTO logs (created, levelno, level, logger, message) VALUES (?, ?, ?, ?, ?)",
                (record.created, record.levelno, record.levelname, record.name, message),
            )
            self._inserted += 1
            if self._inserted % _PRUNE_EVERY == 0:
                self._db.execute(
                    "DELETE FROM logs WHERE id <= (SELECT MAX(id) FROM logs) - ?", (MAX_DB_LOGS,)
                )
        except Exception:
            self.handleError(record)


def query_logs(db: Database, min_level: int = logging.INFO, limit: int = 200,
               before_id: int | None = None) -> list[dict]:
    rows = db.query(
        "SELECT * FROM logs WHERE levelno >= ? AND (? IS NULL OR id < ?) ORDER BY id DESC LIMIT ?",
        (min_level, before_id, before_id, limit),
    )
    return [
        {"id": r["id"], "time": r["created"], "level": r["level"],
         "logger": r["logger"], "message": r["message"]}
        for r in rows
    ]


def count_recent_errors(db: Database, window_seconds: float = 86400) -> int:
    return db.query(
        "SELECT COUNT(*) AS n FROM logs WHERE levelno >= ? AND created >= ?",
        (logging.ERROR, time.time() - window_seconds),
    )[0]["n"]


def setup_logging(log_dir: Path, db: Database | None = None) -> logging.Logger:
    """All events go to the console and logs/app.log; errors (including push failures) also go to logs/error.log.

    If db is given, logs are also written to SQLite for the Dashboard."""
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    for name, level in (("app.log", logging.INFO), ("error.log", logging.ERROR)):
        file = RotatingFileHandler(
            log_dir / name, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
        )
        file.setLevel(level)
        file.setFormatter(fmt)
        logger.addHandler(file)

    if db is not None:
        logger.addHandler(SqliteLogHandler(db))
    return logger
