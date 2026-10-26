import httpx

from .base import Provider

LIMIT = 4900  # LINE allows 5000 characters per text message


class LineProvider(Provider):
    """LINE Messaging API push (LINE Notify has been shut down)."""

    name = "line"
    URL = "https://api.line.me/v2/bot/message/push"

    def __init__(self, access_token: str, user_ids: list[str]):
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._user_ids = user_ids

    async def send_text(self, text: str) -> None:
        chunks = [text[i : i + LIMIT] for i in range(0, len(text), LIMIT)]
        async with httpx.AsyncClient(timeout=15, headers=self._headers) as http:
            for user_id in self._user_ids:
                for chunk in chunks:
                    resp = await http.post(
                        self.URL,
                        json={"to": user_id, "messages": [{"type": "text", "text": chunk}]},
                    )
                    resp.raise_for_status()
