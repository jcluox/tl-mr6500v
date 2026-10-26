import asyncio
import logging
import time

from .notifier import Notifier
from .runtime_settings import SettingsStore

COOLDOWN_SECONDS = 1800


class ErrorAlertHandler(logging.Handler):
    """Pushes ERROR-level logs to the providers too (turn on notify_errors in the settings).

    The same text is pushed only once during the cooldown, so a router that is offline does not flood you every poll."""

    def __init__(self, notifier: Notifier, settings: SettingsStore, loop: asyncio.AbstractEventLoop):
        super().__init__(logging.ERROR)
        self._notifier, self._settings, self._loop = notifier, settings, loop
        self._last_sent: dict[str, float] = {}
        self._tasks: set[asyncio.Task] = set()

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "no_alert", False) or not self._settings.get().notify_errors:
            return
        text = f"⚠️ TL-MR6500v error\n{record.getMessage()}"
        now = time.monotonic()
        if now - self._last_sent.get(text, -COOLDOWN_SECONDS) < COOLDOWN_SECONDS:
            return
        self._last_sent = {t: s for t, s in self._last_sent.items() if now - s < COOLDOWN_SECONDS}
        self._last_sent[text] = now
        self._loop.call_soon_threadsafe(self._spawn, text)

    def _spawn(self, text: str) -> None:
        task = self._loop.create_task(self._notifier.notify_text(text))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
