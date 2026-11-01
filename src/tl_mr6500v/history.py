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

    def get(self, message_id: int) -> Sms | None:
        rows = self._db.query("SELECT * FROM messages WHERE id = ?", (message_id,))
        if not rows:
            return None
        r = rows[0]
        return Sms(r["idx"], r["sender"], r["content"], r["received_at"], bool(r["unread"]))

    def pending_retries(self, delays: tuple[float, ...], now: float) -> list[tuple[int, Sms, dict[str, bool], int]]:
        """Messages with a failed provider whose next automatic retry is due: (id, sms, delivered, retries).

        delays[n] is how long to wait after the last attempt before retry number n + 1; once they are used up, a message is left alone."""
        due = []
        rows = self._db.query(
            "SELECT * FROM messages WHERE retries < ? AND delivered LIKE '%false%' ORDER BY received_at, id", (len(delays),)
        )
        for r in rows:
            delivered = json.loads(r["delivered"])
            if False in delivered.values() and now >= (r["last_attempt"] or r["notified_at"]) + delays[r["retries"]]:
                due.append((r["id"], Sms(r["idx"], r["sender"], r["content"], r["received_at"], bool(r["unread"])),
                            delivered, r["retries"]))
        return due

    def get_delivery(self, message_id: int) -> tuple[Sms, dict[str, bool], int] | None:
        """The message, its current delivery result and how many retries it had, or None if it is unknown."""
        rows = self._db.query("SELECT * FROM messages WHERE id = ?", (message_id,))
        if not rows:
            return None
        r = rows[0]
        return (Sms(r["idx"], r["sender"], r["content"], r["received_at"], bool(r["unread"])),
                json.loads(r["delivered"]), r["retries"])

    def record_attempt(self, message_id: int, outcome: dict[str, bool]) -> tuple[dict[str, bool], int] | None:
        """Merge a retry's result into the delivery result and count the attempt. Return (delivered, retries), or None if the message is gone."""
        found = self.get_delivery(message_id)
        if found is None:
            return None
        delivered = {**found[1], **outcome}
        retries = found[2] + 1
        self._db.execute(
            "UPDATE messages SET delivered = ?, retries = ?, last_attempt = ? WHERE id = ?",
            (json.dumps(delivered), retries, time.time(), message_id),
        )
        return delivered, retries

    def mark_read(self, message_id: int) -> None:
        self._db.execute("UPDATE messages SET unread = 0 WHERE id = ?", (message_id,))

    def delete(self, message_id: int) -> None:
        self._db.execute("DELETE FROM messages WHERE id = ?", (message_id,))

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
                "retries": r["retries"],
            }
            for r in rows
        ]
        return {"total": total, "items": items}
