"""Информационные рассылки (п. 3.4 ТЗ).

Только анонсы событий, отмены и переносы. Массовая реклама запрещена.
Каждое сообщение содержит кнопку «Не присылать анонсы».
Ограничения платформы: 2 сообщения/с в диалог, 30 запросов/с ко всему API —
отправка идёт последовательно с паузами.
"""

from __future__ import annotations

import asyncio
import logging

from . import ui
from .maxapi import MaxApiError, client
from .storage import storage

log = logging.getLogger(__name__)

PAUSE_SECONDS = 0.06  # ~16 сообщений/с, с запасом ниже лимита 30 запросов/с


async def broadcast(text: str, *, dry_run: bool = False) -> dict[str, int]:
    """Рассылает текст всем активным подписчикам.

    Возвращает статистику: sent / failed / total.
    """
    subscribers = storage.subscribers()
    stats = {"total": len(subscribers), "sent": 0, "failed": 0}
    if dry_run:
        return stats

    for index, subscriber in enumerate(subscribers):
        user_id = subscriber.get("user_id")
        if not user_id:
            continue
        try:
            await client.send_message(
                user_id=user_id,
                text=text,
                keyboard=ui.unsubscribe_keyboard(),
            )
            stats["sent"] += 1
        except MaxApiError as exc:
            stats["failed"] += 1
            log.warning("Рассылка: не доставлено %s — %s", user_id, exc)
            # 403/404 означают, что диалог недоступен — отписываем
            if exc.status in {403, 404}:
                storage.set_subscription(str(user_id), False)
        if index and index % 20 == 0:
            await asyncio.sleep(1.0)
        else:
            await asyncio.sleep(PAUSE_SECONDS)

    storage.log(
        action="broadcast",
        details=f"отправлено: {stats['sent']}, ошибок: {stats['failed']}, всего: {stats['total']}",
    )
    return stats


async def announce_event(event_id: int, kind: str = "announce") -> dict[str, int]:
    """Анонс конкретного события или уведомление об отмене/переносе."""
    event = storage.event(event_id)
    if event is None:
        return {"total": 0, "sent": 0, "failed": 0}

    titles = {
        "announce": "Новое событие в афише",
        "cancel": "Событие отменено",
        "move": "Событие перенесено",
    }
    header = titles.get(kind, titles["announce"])
    text = f"{header}\n\n{ui.event_card_plain(event)}"
    if kind == "cancel":
        text += "\n\nПриносим извинения за неудобства."
    return await broadcast(text)
