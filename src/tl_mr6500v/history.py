import json
import time

from .db import Database
from .models import Sms


class MessageStore:
    """History of pushed messages, with each provider's delivery result."""

    def __init__(self, db: Database):
        self._db = db

    def add(self, sms: Sms, delivered: dict[str, bool]) -> bool:
        """Return True if the message was added (False if it already existed). An empty delivered means imported only, not pushed."""
        return bool(self._db.execute(
            "INSERT OR IGNORE INTO messages "
            "(uid, idx, sender, content, received_at, unread, delivered, notified_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (sms.uid, sms.index, sms.sender, sms.content, sms.received_at,
             int(sms.unread), json.dumps(delivered), time.time()),
        ))

    def clear(self) -> int:
        """Delete the message records in the database and return how many were deleted. Only this app's SQLite is touched, never the messages on the router."""
        return self._db.execute("DELETE FROM messages")

    def list(self, limit: int = 50, offset: int = 0) -> dict:
        rows = self._db.query(
            "SELECT * FROM messages ORDER BY received_at DESC, id DESC LIMIT ? OFFSET ?", (limit, offset)
        )
        total = self._db.query("SELECT COUNT(*) AS n FROM messages")[0]["n"]
        items = [
            {
                "id": r["id"],
                "index": r["idx"],
                "sender": r["sender"],
                "content": r["content"],
                "received_at": r["received_at"],
                "unread": bool(r["unread"]),
                "delivered": json.loads(r["delivered"]),
                "notified_at": r["notified_at"],
            }
            for r in rows
        ]
        return {"total": total, "items": items}
