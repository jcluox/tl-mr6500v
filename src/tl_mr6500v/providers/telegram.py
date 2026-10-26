import httpx

from .base import Provider

LIMIT = 4000  # Telegram allows 4096 characters per message


class TelegramProvider(Provider):
    name = "telegram"

    def __init__(self, bot_token: str, chat_ids: list[str]):
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_ids = chat_ids

    async def send_text(self, text: str) -> None:
        chunks = [text[i : i + LIMIT] for i in range(0, len(text), LIMIT)]
        async with httpx.AsyncClient(timeout=15) as http:
            for chat_id in self._chat_ids:
                for chunk in chunks:
                    resp = await http.post(self._url, json={"chat_id": chat_id, "text": chunk})
                    if resp.is_error:
                        # Not raise_for_status(): its message contains the URL with the bot token, which would end up in the logs
                        raise RuntimeError(f"Telegram API responded {resp.status_code}: {resp.text[:200]}")
