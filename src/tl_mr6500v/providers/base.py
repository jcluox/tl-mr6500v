from abc import ABC, abstractmethod

from ..models import Sms


class Provider(ABC):
    name: str

    @abstractmethod
    async def send_text(self, text: str) -> None:
        """Send a piece of text. Raise an exception on failure; the Notifier logs it."""

    async def send(self, sms: Sms) -> None:
        """Send the full message."""
        await self.send_text(sms.render())
