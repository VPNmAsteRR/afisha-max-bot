"""Плановые задачи: синхронизация таблицы, резервные копии, ежедневная сводка (п. 3.3, 6)."""

from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import dates
from .config import settings
from .storage import storage

log = logging.getLogger(__name__)


def backup_database(keep_days: int = 14) -> Path | None:
    """Копия базы через SQLite backup API + очистка старых файлов."""
    settings.ensure_dirs()
    stamp = datetime.now(tz=settings.tz).strftime("%Y%m%d-%H%M%S")
    target = settings.bak_dir / f"afisha-{stamp}.db"
    try:
        source = sqlite3.connect(settings.db_path)
        destination = sqlite3.connect(target)
        with destination:
            source.backup(destination)
        destination.close()
        source.close()
    except Exception as exc:  # noqa: BLE001
        log.error("Резервная копия не создана: %s", exc)
        return None

    limit = datetime.now(tz=settings.tz) - timedelta(days=keep_days)
    for old in settings.bak_dir.glob("afisha-*.db"):
        try:
            if datetime.fromtimestamp(old.stat().st_mtime, tz=settings.tz) < limit:
                old.unlink()
        except OSError:
            continue
    storage.log(action="backup", details=target.name)
    log.info("Резервная копия: %s", target)
    return target


def backup_sheet_copy() -> None:
    """Сохраняет снимок таблицы в JSON рядом с базой."""
    from .sheets import sheet

    if not settings.sheet_enabled or not sheet.parsed.events:
        return
    settings.ensure_dirs()
    stamp = datetime.now(tz=settings.tz).strftime("%Y%m%d-%H%M%S")
    target = settings.bak_dir / f"afisha-table-{stamp}.json"
    import json

    payload = [event.to_row() for event in sheet.parsed.events]
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Копия таблицы: %s", target)


async def daily_digest(bot, days_ahead: int = 7) -> None:
    """Ежедневная сводка администратору (п. 6)."""
    if not settings.admin_chat_id:
        return
    today = dates.today_local()
    horizon = today + timedelta(days=days_ahead)
    events = [
        event
        for event in storage.events()
        if event.is_active and today <= event.date <= horizon
    ]
    events.sort(key=lambda event: event.key())
    lines = [f"Сводка на {dates.human_full(today)}: событий на неделю — {len(events)}."]
    for event in events[:15]:
        lines.append(f"• {dates.human_dt(event.starts_at)} — {event.title} ({event.settlement})")
    if len(events) > 15:
        lines.append(f"… и ещё {len(events) - 15}")
    if not events:
        lines.append("Афиша на неделю пуста — стоит добавить события в таблицу.")
    await bot.api.send_message(
        chat_id=settings.admin_chat_id,
        text="\n".join(lines),
    )


def build_scheduler(bot, sheet_sync=None) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.tz)

    if sheet_sync is not None:
        scheduler.add_job(
            sheet_sync.reload,
            IntervalTrigger(seconds=settings.sheet_refresh_seconds),
            id="sheet-sync",
            max_instances=1,
            coalesce=True,
        )

    scheduler.add_job(
        backup_database,
        CronTrigger(hour=settings.backup_hour, minute=0, timezone=settings.tz),
        id="db-backup",
        max_instances=1,
    )
    scheduler.add_job(
        backup_sheet_copy,
        CronTrigger(hour=settings.backup_hour, minute=5, timezone=settings.tz),
        id="sheet-backup",
        max_instances=1,
    )
    scheduler.add_job(
        daily_digest,
        CronTrigger(hour=settings.daily_digest_hour, minute=0, timezone=settings.tz),
        args=[bot],
        id="daily-digest",
        max_instances=1,
    )
    return scheduler
