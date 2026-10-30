"""Send a plain-text message to one Telegram chat through the Bot API.

The bot token is part of the request URL, so neither the URL nor httpx error text is
ever logged or raised; callers get a short error naming only the failure type.
"""

import httpx

_API = "https://api.telegram.org"


class TelegramError(Exception):
    """A message was not delivered; the text never contains the token."""


class TelegramSender:
    def __init__(self, token: str, chat_id: str, client: httpx.AsyncClient) -> None:
        if not token or not chat_id:
            raise ValueError("Telegram needs a bot token and a chat id")
        self._token = token
        self._chat_id = chat_id
        self._client = client

    async def send(self, text: str) -> None:
        try:
            response = await self._client.post(
                f"{_API}/bot{self._token}/sendMessage",
                json={"chat_id": self._chat_id, "text": text[:4000],
                      "disable_web_page_preview": True},
                timeout=10.0,
            )
        except httpx.HTTPError as error:
            raise TelegramError(f"Telegram request failed ({type(error).__name__})") from None
        if response.status_code != 200:
            raise TelegramError(f"Telegram answered HTTP {response.status_code}")
