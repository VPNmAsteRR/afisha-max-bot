"""Синхронизация с Google Таблицей (п. 3.2, 5.3 ТЗ).

Таблица — форма ввода, SQLite — быстрый кэш. Правки подхватываются
не реже раза в минуту без перезапуска бота.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any

from . import dates
from .config import settings
from .models import Event
from .sheet import COLUMNS, ParsedSheet, parse_rows
from .storage import storage

log = logging.getLogger(__name__)

try:  # gspread нужен только в рабочем режиме
    import gspread  # type: ignore
except Exception:  # pragma: no cover
    gspread = None  # type: ignore


class SheetSync:
    """Читает лист «Афиша» и обновляет кэш."""

    def __init__(self) -> None:
        self.parsed = ParsedSheet()
        self._hash = ""
        self._client = None
        self._worksheet = None
        self._lock = asyncio.Lock()
        self.last_error = ""

    # ------------------------------------------------------------- подключение
    def _connect(self) -> bool:
        if self._worksheet is not None:
            return True
        if gspread is None:
            self.last_error = (
                "Библиотека gspread не установлена. Выполните: pip install -r requirements.txt"
            )
            return False
        if not settings.sheet_enabled:
            self.last_error = "Не заданы GOOGLE_SHEET_ID и GOOGLE_CREDENTIALS."
            return False
        try:
            self._client = gspread.service_account(filename=settings.google_credentials)
            spreadsheet = self._client.open_by_key(settings.google_sheet_id)
            try:
                self._worksheet = spreadsheet.worksheet(settings.google_sheet_name)
            except gspread.WorksheetNotFound:
                self._worksheet = spreadsheet.sheet1
            return True
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"Не удалось открыть таблицу: {exc}"
            log.error(self.last_error)
            return False

    # ------------------------------------------------------------- чтение
    def _read_rows_sync(self) -> list[list[Any]]:
        if not self._connect():
            return []
        assert self._worksheet is not None
        return self._worksheet.get_all_values()

    async def reload(self, force: bool = False) -> tuple[int, int]:
        """Перечитывает таблицу. Возвращает (количество событий, количество ошибок)."""
        async with self._lock:
            try:
                rows = await asyncio.to_thread(self._read_rows_sync)
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"Ошибка чтения таблицы: {exc}"
                log.error(self.last_error)
                return storage.count(), len(self.parsed.issues)

            if not rows:
                self.last_error = "Таблица пуста или недоступна."
                return storage.count(), len(self.parsed.issues)

            digest = hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()
            if digest == self._hash and not force:
                return len(self.parsed.events), len(self.parsed.issues)

            parsed = parse_rows(rows)
            self.parsed = parsed
            self._hash = digest
            self.last_error = ""

            active = [event for event in parsed.events if event.is_active]
            storage.replace_events(active, source="sheet")
            cancelled = [event for event in parsed.events if event.is_cancelled]
            if cancelled:
                # отменённые не показываем, но сохраняем, чтобы /check и админы их видели
                for event in cancelled:
                    storage.upsert_event(event, source="sheet")

            storage.log(
                action="sheet_sync",
                details=(
                    f"событий: {len(active)}, отменённых: {len(cancelled)}, "
                    f"ошибок: {len(parsed.issues)}"
                ),
            )
            log.info(
                "Афиша синхронизирована: %s событий, %s ошибок",
                len(active),
                len(parsed.issues),
            )
            return len(active), len(parsed.issues)

    # ------------------------------------------------------------- запись
    async def append_event(self, event: Event) -> bool:
        """Добавляет строку в таблицу (используется командой /add)."""
        data = event.to_row()
        row = [data.get(column, "") for column in COLUMNS]
        try:
            await asyncio.to_thread(self._append_sync, row)
            self._hash = ""
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("Не удалось добавить строку в таблицу: %s", exc)
            return False

    def _append_sync(self, row: list[str]) -> None:
        if not self._connect():
            raise RuntimeError(self.last_error or "Таблица недоступна")
        assert self._worksheet is not None
        self._worksheet.append_row(row, value_input_option="USER_ENTERED")

    async def update_event(self, event: Event) -> bool:
        """Обновляет строку события по id."""
        try:
            return await asyncio.to_thread(self._update_sync, event)
        except Exception as exc:  # noqa: BLE001
            log.error("Не удалось обновить строку в таблице: %s", exc)
            return False

    def _update_sync(self, event: Event) -> bool:
        if not self._connect():
            return False
        assert self._worksheet is not None
        values = self._worksheet.get_all_values()
        data = event.to_row()
        row = [data.get(column, "") for column in COLUMNS]
        for index, row_values in enumerate(values[1:], start=2):
            if row_values and str(row_values[0]).strip() == str(event.id):
                self._worksheet.update(f"A{index}:M{index}", [row], value_input_option="USER_ENTERED")
                self._hash = ""
                return True
        self._worksheet.append_row(row, value_input_option="USER_ENTERED")
        self._hash = ""
        return True

    async def mark_cancelled(self, event_id: int) -> bool:
        event = storage.event(event_id)
        if event is None:
            return False
        event.status = "отменено"
        return await self.update_event(event)


sheet = SheetSync()
