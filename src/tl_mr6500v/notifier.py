import asyncio
import logging

from .logging_setup import LOGGER_NAME
from .models import Sms
from .providers.base import Provider

log = logging.getLogger(f"{LOGGER_NAME}.notifier")


class Notifier:
    def __init__(self, providers: list[Provider]):
        self.providers = providers

    async def notify(self, sms: Sms) -> dict[str, bool]:
        """Send to all providers at once. One provider failing is only logged and does not affect the others."""
        return await self._fan_out(lambda p: p.send(sms), f"sms={sms.uid}", alert=True)

    async def notify_text(self, text: str) -> dict[str, bool]:
        """Send a system message (such as an error alert). A failure does not raise another alert, to avoid an endless loop."""
        return await self._fan_out(lambda p: p.send_text(text), "system message", alert=False)

    async def _fan_out(self, send, what: str, alert: bool) -> dict[str, bool]:
        results = await asyncio.gather(*(send(p) for p in self.providers), return_exceptions=True)
        outcome = {}
        for provider, result in zip(self.providers, results):
            outcome[provider.name] = not isinstance(result, BaseException)
            if isinstance(result, BaseException):
                log.error(
                    "Push failed, provider=%s %s: %r", provider.name, what, result,
                    exc_info=(type(result), result, result.__traceback__),
                    extra={} if alert else {"no_alert": True},
                )
        return outcome
