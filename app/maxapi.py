"""Клиент Bot API мессенджера MAX (platform-api2.max.ru).

Токен передаётся только в заголовке Authorization (п. 5.2 ТЗ).
Реализованы Long Polling (разработка) и Webhook (рабочий режим).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .config import settings

log = logging.getLogger(__name__)

# Лимиты платформы (п. 3.4 и 5.2): 30 запросов/с всего, 2 сообщения/с в диалог.
GLOBAL_RATE = 25
PER_CHAT_RATE = 2


class MaxApiError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"MAX API {status}: {body[:300]}")
        self.status = status
        self.body = body


class RateLimiter:
    """Простой ограничитель: не более N событий за окно."""

    def __init__(self, rate: int, per_seconds: float = 1.0) -> None:
        self.rate = max(1, rate)
        self.per = per_seconds
        self._events: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            while True:
                now = loop.time()
                self._events = [t for t in self._events if now - t < self.per]
                if len(self._events) < self.rate:
                    self._events.append(now)
                    return
                wait = self.per - (now - self._events[0])
                await asyncio.sleep(max(wait, 0.01))


class MaxClient:
    def __init__(self, token: str | None = None, base: str | None = None) -> None:
        self.token = token or settings.bot_token
        self.base = (base or settings.api_base).rstrip("/")
        self._client: httpx.AsyncClient | None = None
        self._global = RateLimiter(GLOBAL_RATE)
        self._chats: dict[str, RateLimiter] = {}

    async def start(self) -> None:
        if self._client is None:
            verify: Any = True
            try:  # на серверах без полного набора корневых сертификатов
                import ssl

                import certifi

                verify = ssl.create_default_context(cafile=certifi.where())
            except Exception:  # noqa: BLE001
                verify = True
            self._client = httpx.AsyncClient(
                base_url=self.base,
                timeout=httpx.Timeout(60.0, connect=10.0),
                headers={"Authorization": self.token, "Content-Type": "application/json"},
                verify=verify,
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        await self.start()
        assert self._client is not None
        await self._global.acquire()
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:  # сеть недоступна
            raise MaxApiError(0, str(exc)) from exc
        if response.status_code >= 400:
            raise MaxApiError(response.status_code, response.text)
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {}

    @staticmethod
    def _chat_limiter(chat_key: str) -> RateLimiter:
        return RateLimiter(PER_CHAT_RATE)

    # --- Служебные методы ---
    async def me(self) -> dict[str, Any]:
        return await self._request("GET", "/me")

    async def updates(
        self, marker: int | None = None, timeout: int = 30, limit: int = 100
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"timeout": timeout, "limit": limit, "types": "message_created,message_callback,bot_started"}
        if marker is not None:
            params["marker"] = marker
        return await self._request("GET", "/updates", params=params)

    async def subscribe(self, url: str, secret: str = "") -> dict[str, Any]:
        body: dict[str, Any] = {"url": url}
        if secret:
            body["secret"] = secret
        return await self._request("POST", "/subscriptions", json=body)

    async def unsubscribe(self, url: str) -> dict[str, Any]:
        return await self._request("DELETE", "/subscriptions", params={"url": url})

    # --- Отправка ---
    async def send_message(
        self,
        *,
        user_id: str | int | None = None,
        chat_id: str | int | None = None,
        text: str,
        keyboard: list[list[dict[str, Any]]] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        notify: bool = True,
        fmt: str | None = None,
    ) -> dict[str, Any]:
        if not user_id and not chat_id:
            raise ValueError("нужен user_id или chat_id")
        params = {"user_id": user_id} if user_id else {"chat_id": chat_id}
        body: dict[str, Any] = {"text": text[:4000], "notify": notify}
        if fmt:
            body["format"] = fmt
        sent_attachments = list(attachments or [])
        if keyboard:
            sent_attachments.append({"type": "inline_keyboard", "payload": {"buttons": keyboard}})
        if sent_attachments:
            body["attachments"] = sent_attachments

        key = str(chat_id or user_id)
        limiter = self._chats.setdefault(key, RateLimiter(PER_CHAT_RATE))
        await limiter.acquire()
        return await self._request("POST", "/messages", params=params, json=body)

    async def answer_callback(
        self,
        callback_id: str,
        *,
        text: str | None = None,
        keyboard: list[list[dict[str, Any]]] | None = None,
        notification: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        message: dict[str, Any] = {}
        if text is not None:
            message["text"] = text[:4000]
        if keyboard:
            message["attachments"] = [{"type": "inline_keyboard", "payload": {"buttons": keyboard}}]
        if message:
            body["message"] = message
        if notification:
            body["notification"] = notification
        return await self._request("POST", "/answers", params={"callback_id": callback_id}, json=body)

    async def edit_message(
        self,
        message_id: str,
        *,
        text: str,
        keyboard: list[list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"text": text[:4000]}
        if keyboard:
            body["attachments"] = [{"type": "inline_keyboard", "payload": {"buttons": keyboard}}]
        return await self._request("PUT", "/messages", params={"message_id": message_id}, json=body)


client = MaxClient()
