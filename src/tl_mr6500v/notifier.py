import asyncio
import logging

from .logging_setup import LOGGER_NAME
from .models import Sms
from .providers.base import Provider

log = logging.getLogger(f"{LOGGER_NAME}.notifier")


class Notifier:
    def __init__(self, providers: list[Provider]):
        self.providers = providers

    async def notify(self, sms: Sms, only: set[str] | None = None, retry: bool = False) -> dict[str, bool]:
        """Send to all providers at once (or only those named in `only`). One provider failing is only logged and does not affect the others.

        A failed retry is logged as a warning, so it does not raise another error alert."""
        providers = [p for p in self.providers if only is None or p.name in only]
        return await self._fan_out(providers, lambda p: p.send(sms), f"sms={sms.uid}", alert=not retry,
                                   level=logging.WARNING if retry else logging.ERROR)

    async def notify_text(self, text: str) -> dict[str, bool]:
        """Send a system message (such as an error alert). A failure does not raise another alert, to avoid an endless loop."""
        return await self._fan_out(self.providers, lambda p: p.send_text(text), "system message", alert=False)

    async def _fan_out(self, providers, send, what: str, alert: bool, level: int = logging.ERROR) -> dict[str, bool]:
        results = await asyncio.gather(*(send(p) for p in providers), return_exceptions=True)
        outcome = {}
        for provider, result in zip(providers, results):
            outcome[provider.name] = not isinstance(result, BaseException)
            if isinstance(result, BaseException):
                log.log(
                    level,
                    "Push failed, provider=%s %s: %r", provider.name, what, result,
                    exc_info=(type(result), result, result.__traceback__),
                    extra={} if alert else {"no_alert": True},
                )
        return outcome
