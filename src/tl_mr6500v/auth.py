import hashlib
import hmac
import secrets
import time

from .db import Database

COOKIE_NAME = "tl6500v_session"
MAX_FAILURES = 5
LOCKOUT_SECONDS = 300


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class SessionStore:
    """Dashboard login sessions: the cookie holds only a random token, and the database holds only its hash.

    Each use renews the session when less than half of its time is left, so a device you use often is rarely signed out."""

    def __init__(self, db: Database, ttl_seconds: float):
        self._db, self.ttl = db, ttl_seconds

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        self._db.execute("DELETE FROM sessions WHERE expires < ?", (now,))
        self._db.execute(
            "INSERT INTO sessions (token_hash, created, expires) VALUES (?, ?, ?)",
            (_hash(token), now, now + self.ttl),
        )
        return token

    def validate(self, token: str | None) -> bool:
        if not token:
            return False
        now = time.time()
        rows = self._db.query("SELECT expires FROM sessions WHERE token_hash = ?", (_hash(token),))
        if not rows or rows[0]["expires"] < now:
            return False
        if rows[0]["expires"] - now < self.ttl / 2:
            self._db.execute(
                "UPDATE sessions SET expires = ? WHERE token_hash = ?", (now + self.ttl, _hash(token))
            )
        return True

    def revoke(self, token: str | None) -> None:
        if token:
            self._db.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash(token),))


class LoginThrottle:
    """Temporarily locks a source after too many failed sign-ins in a row."""

    def __init__(self):
        self._failures: dict[str, tuple[int, float]] = {}

    def retry_after(self, source: str) -> int:
        count, locked_until = self._failures.get(source, (0, 0))
        remaining = locked_until - time.monotonic()
        return int(remaining) + 1 if count >= MAX_FAILURES and remaining > 0 else 0

    def failed(self, source: str) -> None:
        count, _ = self._failures.get(source, (0, 0))
        self._failures[source] = (count + 1, time.monotonic() + LOCKOUT_SECONDS)

    def succeeded(self, source: str) -> None:
        self._failures.pop(source, None)


def credentials_match(expected_user: str, expected_password: str, user: str, password: str) -> bool:
    # Compare both before returning, so response time does not reveal which one was wrong
    user_ok = hmac.compare_digest(expected_user.encode(), user.encode())
    password_ok = hmac.compare_digest(expected_password.encode(), password.encode())
    return user_ok and password_ok
