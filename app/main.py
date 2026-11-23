"""FastAPI-приложение: вебхук MAX, long polling для разработки, health-check."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request

from . import ui
from .bot import Bot, Incoming
from .config import settings
from .maxapi import MaxApiError, client
from .storage import storage

log = logging.getLogger(__name__)

# Счётчики для /health
METRICS: dict[str, Any] = {"updates": 0, "errors": 0, "started_at": "", "last_error": ""}


def extract_incoming(update: dict[str, Any]) -> Incoming | None:
    """Приводит событие MAX к внутреннему виду."""
    update_type = update.get("update_type") or update.get("type") or ""

    if update_type in {"message_created", "message_callback", "bot_started"}:
        message = update.get("message") or {}
        sender = message.get("sender") or update.get("user") or {}
        recipient = message.get("recipient") or {}
        user_id = str(sender.get("user_id") or sender.get("id") or update.get("user_id") or "")
        chat_id = str(recipient.get("chat_id") or message.get("chat_id") or user_id)
        name = sender.get("name") or sender.get("first_name") or ""
        body = message.get("body") or {}
        text = (body.get("text") or "").strip()
        callback = update.get("callback") or body.get("callback") or {}
        is_callback = bool(callback) or update_type == "message_callback"

        if not user_id and not chat_id:
            return None

        return Incoming(
            user_id=user_id or chat_id,
            chat_id=chat_id,
            name=name,
            text=text,
            payload=str(callback.get("payload") or ""),
            callback_id=str(callback.get("callback_id") or ""),
            message_id=str(message.get("body", {}).get("mid", "")),
            is_callback=is_callback,
            is_start=update_type == "bot_started" or text.startswith("/start"),
        )
    return None


class Application:
    """Связывает API, бота и планировщик."""

    def __init__(self) -> None:
        self.bot = Bot()
        self.scheduler = None
        self.sheet = None
        self._poll_task: asyncio.Task | None = None
        self._marker: int | None = None

    async def startup(self) -> None:
        from .jobs import build_scheduler
        from .sheets import sheet

        settings.ensure_dirs()
        await client.start()
        self.sheet = sheet
        self.bot.sheet = sheet

        if settings.sheet_enabled:
            count, errors = await sheet.reload(force=True)
            log.info("Стартовая синхронизация: %s событий, %s ошибок", count, errors)
        else:
            log.warning(
                "GOOGLE_SHEET_ID/GOOGLE_CREDENTIALS не заданы — работаем с локальной базой."
            )

        self.scheduler = build_scheduler(self.bot, sheet if settings.sheet_enabled else None)
        self.scheduler.start()

        if settings.webhook_enabled:
            try:
                await client.subscribe(
                    settings.webhook_url.rstrip("/") + settings.webhook_path,
                    settings.webhook_secret,
                )
                log.info("Вебхук подписан: %s%s", settings.webhook_url, settings.webhook_path)
            except MaxApiError as exc:
                log.error("Не удалось подписать вебхук: %s", exc)
        else:
            log.warning("WEBHOOK_URL не задан — запускаю Long Polling (только для разработки).")
            self._poll_task = asyncio.create_task(self._poll_loop())

        try:
            me = await client.me()
            log.info("Бот подключён: %s", me.get("name") or me.get("username") or "без имени")
        except MaxApiError as exc:
            log.error("Не удалось получить профиль бота: %s", exc)

    async def shutdown(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self.scheduler:
            self.scheduler.shutdown(wait=False)
        await client.close()
        storage.log(action="shutdown", details="остановка сервиса")

    async def _poll_loop(self) -> None:
        """Long Polling: допустим только для разработки и тестирования."""
        backoff = 1.0
        while True:
            try:
                response = await client.updates(
                    marker=self._marker,
                    timeout=settings.poll_timeout,
                    limit=settings.poll_limit,
                )
                updates = response.get("updates") or []
                for update in updates:
                    marker = update.get("marker")
                    if isinstance(marker, int):
                        self._marker = marker
                    await self.dispatch(update)
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except MaxApiError as exc:
                METRICS["errors"] += 1
                METRICS["last_error"] = str(exc)
                log.warning("Ошибка Long Polling: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
            except Exception as exc:  # noqa: BLE001
                METRICS["errors"] += 1
                METRICS["last_error"] = str(exc)
                log.exception("Непредвиденная ошибка цикла опроса")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def dispatch(self, update: dict[str, Any]) -> None:
        incoming = extract_incoming(update)
        if incoming is None:
            return
        METRICS["updates"] += 1
        try:
            await self.bot.handle(incoming)
        except MaxApiError as exc:
            METRICS["errors"] += 1
            METRICS["last_error"] = str(exc)
            log.error("Ошибка API при обработке: %s", exc)
            await self.notify_admins(f"Сбой при обработке запроса: {exc}")
            await self.bot.send(incoming, ui.ERROR_TEXT, ui.main_menu())
        except Exception as exc:  # noqa: BLE001
            METRICS["errors"] += 1
            METRICS["last_error"] = str(exc)
            log.exception("Ошибка обработки события")
            await self.notify_admins(f"Сбой при обработке запроса: {exc}")
            await self.bot.send(incoming, ui.ERROR_TEXT, ui.main_menu())

    async def notify_admins(self, text: str) -> None:
        if not settings.admin_chat_id:
            return
        try:
            await client.send_message(chat_id=settings.admin_chat_id, text=text[:3500])
        except MaxApiError as exc:
            log.warning("Не удалось уведомить администратора: %s", exc)


app_state = Application()


@asynccontextmanager
async def lifespan(app: FastAPI):
    from .dates import now_local
    from .logging_setup import setup_logging

    setup_logging()
    METRICS["started_at"] = now_local().isoformat(timespec="seconds")
    await app_state.startup()
    try:
        yield
    finally:
        await app_state.shutdown()


app = FastAPI(title="Афиша Казачинско-Пировского округа", version="1.0.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, Any]:
    stats = storage.stats()
    return {
        "status": "ok",
        "time": METRICS["started_at"],
        "updates": METRICS["updates"],
        "errors": METRICS["errors"],
        "last_error": METRICS["last_error"],
        "events": stats["events"],
        "published": stats["published"],
        "subscribers": stats["subscribers"],
        "last_sync": stats["last_sync"],
        "webhook": settings.webhook_enabled,
    }


@app.post(settings.webhook_path)
async def webhook(
    request: Request,
    authorization: str | None = Header(default=None),
    x_max_secret: str | None = Header(default=None, alias="X-Max-Secret"),
) -> dict[str, Any]:
    if settings.webhook_secret:
        provided = x_max_secret or (authorization or "").removeprefix("Bearer ").strip()
        if provided != settings.webhook_secret:
            raise HTTPException(status_code=403, detail="forbidden")
    try:
        update = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="bad json")
    # Платформе отвечаем сразу, обработку выносим в фон
    asyncio.create_task(app_state.dispatch(update))
    return {"ok": True}


@app.post("/admin/reload")
async def reload_sheet(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Ручная перезагрузка афиши по секрету (для cron/monitoring)."""
    if settings.webhook_secret and (authorization or "").removeprefix("Bearer ") != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="forbidden")
    if app_state.sheet is None:
        return {"ok": False, "reason": "sheet not configured"}
    count, errors = await app_state.sheet.reload(force=True)
    return {"ok": True, "events": count, "errors": errors}
