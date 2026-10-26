import asyncio
import logging
from datetime import datetime

from .history import MessageStore
from .logging_setup import LOGGER_NAME
from .models import Sms
from .notifier import Notifier
from .router import RouterBusy, RouterClient, RouterError
from .runtime_settings import SettingsStore
from .state import SeenStore

log = logging.getLogger(f"{LOGGER_NAME}.poller")


def _now() -> str:
    # Include the time zone: the container runs in UTC, and the browser needs it to show local time correctly
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Poller:
    def __init__(self, router: RouterClient, notifier: Notifier, store: SeenStore,
                 history: MessageStore, settings: SettingsStore, import_limit: int = 0):
        self.router, self.notifier, self.store = router, notifier, store
        self.history, self.settings, self.import_limit = history, settings, import_limit
        self.wake = asyncio.Event()  # Set when settings change, so a new interval takes effect right away
        self._lock = asyncio.Lock()
        self.last_poll_at: str | None = None  # Last successful check
        self.last_error: str | None = None
        # Router state: ok (last check succeeded) / busy (skipped because someone is logged in) / error / unknown (not checked yet)
        self.router_state = "unknown"
        self.router_checked_at: str | None = None
        self.network: dict | None = None
        self.network_at: str | None = None

    async def poll_once(self) -> list[Sms]:
        async with self._lock:
            self.router_checked_at = _now()
            try:
                result = await self.router.poll(
                    lambda page: all(m.uid not in self.store for m in page),
                    kick=self.settings.get().kick_existing_login,
                )
            except RouterBusy as exc:
                # Someone using the web interface is normal. Try again next round; this is not an error
                log.info("%s", exc)
                self.router_state = "busy"
                return []
            except RouterError as exc:
                self.router_state = "error"
                self.last_error = str(exc)
                log.error("Checking messages failed: %s", exc)
                return []
            self.router_state = "ok"
            self.last_error = None
            self.last_poll_at = self.router_checked_at
            if result.network is not None:
                self.network, self.network_at = result.network, self.last_poll_at

            fetched = result.messages
            new = [m for m in fetched if m.uid not in self.store]
            first_run = not self.store.initialized
            if first_run:
                log.info("First start: recording %d existing messages without pushing them", len(fetched))
                for sms in new:
                    self.store.add(sms.uid)
                self._import_history(new)
            else:
                # Push oldest first, so the order matches when they arrived
                for sms in sorted(new, key=lambda m: m.index):
                    outcome = await self.notifier.notify(sms)
                    self.store.add(sms.uid)
                    self.history.add(sms, outcome)
                    log.info("New message %s from %s, delivery result %s", sms.uid, sms.sender, outcome)
            if new or first_run:
                self.store.save()
            return [] if first_run else new

    def _import_history(self, messages: list[Sms]) -> int:
        """Save the latest import_limit messages (given newest first) into the history without pushing them; 0 = all. Return how many were added."""
        chosen = messages[: self.import_limit] if self.import_limit > 0 else messages
        return sum(self.history.add(sms, {}) for sms in chosen)

    async def import_existing(self) -> int:
        """Import the latest messages from the router inbox into the history (nothing is pushed). Return how many were added.

        Every message that is read (including older ones beyond the import limit) is marked as handled, so it is never pushed later as a new message."""
        async with self._lock:
            result = await self.router.poll(
                lambda page: True, kick=self.settings.get().kick_existing_login, max_pages=100
            )
            added = self._import_history(result.messages)
            for sms in result.messages:
                self.store.add(sms.uid)
            self.store.save()
            log.info("Imported existing router messages: %d in the inbox, %d added (limit %s)",
                     len(result.messages), added, self.import_limit or "none")
            return added

    async def run(self) -> None:
        while True:
            self.wake.clear()
            try:
                await self.poll_once()
            except Exception:
                log.exception("Unexpected error while polling")
            try:
                await asyncio.wait_for(self.wake.wait(), self.settings.get().poll_interval_seconds)
            except asyncio.TimeoutError:
                pass
