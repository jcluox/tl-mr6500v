import json
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .db import Database


class AppSettings(BaseModel):
    """Settings you can change live from the Dashboard, stored in SQLite. Environment variables only give the initial defaults."""

    poll_interval_seconds: float = Field(30, ge=5, le=3600)
    kick_existing_login: bool = False  # Whether to kick out a user who is already logged in to the router
    allow_lan_access: bool = False  # Whether devices on the local network (other than this machine) may open the Dashboard
    notify_errors: bool = False  # Whether to push error messages to the providers too


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    poll_interval_seconds: Optional[float] = Field(None, ge=5, le=3600)
    kick_existing_login: Optional[bool] = None
    allow_lan_access: Optional[bool] = None
    notify_errors: Optional[bool] = None


class SettingsStore:
    def __init__(self, db: Database, defaults: AppSettings):
        self._db = db
        stored = {row["key"]: json.loads(row["value"]) for row in db.query("SELECT key, value FROM settings")}
        self._current = AppSettings(**{**defaults.model_dump(), **stored})

    def get(self) -> AppSettings:
        return self._current

    def update(self, patch: SettingsUpdate) -> dict:
        """Apply the changes and save them to the database. Return the fields that actually changed as {key: (old, new)}."""
        changes = {
            key: (getattr(self._current, key), value)
            for key, value in patch.model_dump(exclude_none=True).items()
            if getattr(self._current, key) != value
        }
        for key, (_, value) in changes.items():
            self._db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )
        self._current = self._current.model_copy(update={k: v for k, (_, v) in changes.items()})
        return changes
