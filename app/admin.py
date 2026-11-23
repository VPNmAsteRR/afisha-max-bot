"""Административный контур (п. 3.3 ТЗ).

Команды: /add, /edit <id>, /del <id>, /list, /check, /reload, /admin.
Доступ только для ID из белого списка (не более 3 человек).
"""

from __future__ import annotations

import logging
from datetime import date, time
from typing import Any

from . import dates, ui
from .config import settings
from .models import (
    AGE_MARKS,
    CATEGORIES,
    FIELD_LABELS,
    Event,
    normalize_price,
    parse_age,
    parse_category,
    parse_family,
    parse_status,
)
from .storage import storage

log = logging.getLogger(__name__)

CANCEL_PAYLOAD = "admin:cancel"

ADD_STEPS = (
    "title", "date", "time", "settlement", "place", "category",
    "family", "age", "price", "description", "contact",
)

STEP_PROMPTS = {
    "title": "Название события (до 80 символов):",
    "date": "Дата в формате ДД.ММ.ГГГГ, например 08.11.2026:",
    "time": "Время начала в формате ЧЧ:ММ, например 18:30 (местное время, UTC+7):",
    "settlement": "Населённый пункт, например: с. Казачинское",
    "place": "Место и адрес: учреждение, улица, дом:",
    "category": "Категория: культура, спорт или массовое",
    "family": "Семейное событие? Напишите «да» или «нет»",
    "age": "Возрастная маркировка: 0+, 6+, 12+, 16+ или 18+",
    "price": "Стоимость: «Бесплатно», число рублей или «По записи»",
    "description": "Краткое описание, 1–2 предложения (до 200 символов):",
    "contact": "Контакт или ссылка для записи (можно пропустить):",
}

EDITABLE = (
    "title", "date", "time", "settlement", "place", "category",
    "family", "age", "price", "description", "contact", "status",
)


def event_summary(event: Event) -> str:
    return (
        f"id {event.id} · {dates.human_dt(event.starts_at)}\n"
        f"{event.title}\n"
        f"{event.settlement}, {event.place}\n"
        f"{event.category} · {'семейное' if event.family else 'не семейное'} · {event.age} · {event.price}\n"
        f"Статус: {event.status}"
    )


class AdminFlow:
    def __init__(self, bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------ команды
    async def run(self, incoming, command: str, args: str) -> None:
        if command == "add":
            await self.start_add(incoming)
        elif command == "edit":
            await self.start_edit(incoming, args)
        elif command == "del":
            await self.start_delete(incoming, args)
        elif command == "list":
            await self.show_list(incoming)
        elif command == "check":
            await self.run_check(incoming)
        elif command == "reload":
            await self.run_reload(incoming)
        else:
            await self.bot.send(incoming, "Неизвестная команда.", ui.admin_menu())

    async def show_list(self, incoming) -> None:
        today = dates.today_local()
        events = [event for event in storage.events() if event.date >= today]
        events.sort(key=lambda event: event.key())
        if not events:
            await self.bot.send(incoming, "Ближайших событий нет.", ui.admin_menu())
            return
        lines = ["Ближайшие 20 событий:"]
        for event in events[:20]:
            mark = "" if event.status == "опубликовано" else f" [{event.status}]"
            lines.append(
                f"{event.id}. {dates.human_full(event.date)}, {event.time:%H:%M} — "
                f"{event.title}{mark}"
            )
        await self.bot.send(incoming, "\n".join(lines), ui.admin_menu())

    async def run_check(self, incoming) -> None:
        from .sheet import ParsedSheet, RowIssue

        sheet = getattr(self.bot, "sheet", None)
        if sheet is not None:
            parsed: ParsedSheet = sheet.parsed
            text = parsed.report()
        else:
            text = "Google Таблица не подключена. Проверьте GOOGLE_SHEET_ID и GOOGLE_CREDENTIALS."

        events = storage.events()
        missing = [
            event.id
            for event in events
            if not event.title or not event.settlement or not event.place or not event.description
        ]
        if missing:
            text += "\n\nВ кэше неполные записи: " + ", ".join(str(i) for i in missing)
        text += f"\n\nСобытий в кэше: {len(events)}, последняя синхронизация: {storage.meta_get('last_sync', '—')}"
        await self.bot.send(incoming, text, ui.admin_menu())

    async def run_reload(self, incoming) -> None:
        sheet = getattr(self.bot, "sheet", None)
        if sheet is None:
            await self.bot.send(incoming, "Google Таблица не подключена.", ui.admin_menu())
            return
        count, errors = await sheet.reload()
        storage.log(action="reload", actor_id=incoming.user_id, details=f"событий: {count}")
        text = f"Афиша перечитана. Загружено событий: {count}."
        if errors:
            text += f"\nСтрок с ошибками: {errors}. Проверьте командой /check."
        await self.bot.send(incoming, text, ui.admin_menu())

    # ------------------------------------------------------------ добавление
    async def start_add(self, incoming) -> None:
        storage.set_dialog(incoming.user_id, "admin:add:title", {"data": {}})
        await self.bot.send(
            incoming,
            f"Добавление события.\n\n{STEP_PROMPTS['title']}",
            ui.cancel_keyboard(CANCEL_PAYLOAD),
        )

    # ------------------------------------------------------------ редактирование
    async def start_edit(self, incoming, args: str) -> None:
        event_id = self._parse_id(args)
        if event_id is None:
            await self.bot.send(incoming, "Укажите номер: /edit 101", ui.admin_menu())
            return
        event = storage.event(event_id)
        if event is None:
            await self.bot.send(incoming, f"Событие с id {event_id} не найдено.", ui.admin_menu())
            return
        rows = [
            [ui.btn(FIELD_LABELS[name].split(" ")[0], f"admin:editfield|id={event_id}|field={name}")]
            for name in EDITABLE
        ]
        keyboard = [rows[i : i + 3] for i in range(0, len(rows), 3)]
        keyboard.append([ui.btn("Отмена", CANCEL_PAYLOAD)])
        await self.bot.send(
            incoming,
            f"Что изменить?\n\n{event_summary(event)}",
            keyboard,
        )

    async def start_delete(self, incoming, args: str) -> None:
        event_id = self._parse_id(args)
        if event_id is None:
            await self.bot.send(incoming, "Укажите номер: /del 101", ui.admin_menu())
            return
        event = storage.event(event_id)
        if event is None:
            await self.bot.send(incoming, f"Событие с id {event_id} не найдено.", ui.admin_menu())
            return
        await self.bot.send(
            incoming,
            f"Снять событие с публикации?\n\n{event_summary(event)}",
            ui.confirm_keyboard(
                f"admin:delete|id={event_id}", f"admin:cancel"
            ),
        )

    # ------------------------------------------------------------ callback-и
    async def callback(self, incoming, params: dict[str, str]) -> None:
        action = params.get("action") or params.get("_action", "")
        # params приходят из payload вида admin:editfield|id=..|field=..
        raw = incoming.payload
        head = raw.split("|", 1)[0]
        action = head.split(":", 1)[1] if ":" in head else head

        if action == "cancel":
            storage.clear_dialog(incoming.user_id)
            await self.bot.send(incoming, "Действие отменено.", ui.admin_menu())
        elif action == "delete":
            event_id = self._parse_id(params.get("id", ""))
            event = storage.event(event_id) if event_id is not None else None
            if event is None:
                await self.bot.send(incoming, "Событие не найдено.", ui.admin_menu())
                return
            event.status = "отменено"
            storage.upsert_event(event, source="admin")
            sheet = getattr(self.bot, "sheet", None)
            if sheet is not None:
                await sheet.mark_cancelled(event.id)
            storage.log(
                action="del",
                actor_id=incoming.user_id,
                target=str(event.id),
                details="снято с публикации",
            )
            await self.bot.send(
                incoming,
                f"Событие {event.id} снято с публикации. В таблице строка сохранена.",
                ui.admin_menu(),
            )
            await self._notify_cancel(event.id)
        elif action == "editfield":
            event_id = self._parse_id(params.get("id", ""))
            field = params.get("field", "")
            if event_id is None or field not in EDITABLE:
                await self.bot.send(incoming, "Не удалось выбрать поле.", ui.admin_menu())
                return
            storage.set_dialog(
                incoming.user_id,
                f"admin:edit:{field}",
                {"id": event_id, "field": field},
            )
            prompt = STEP_PROMPTS.get(field, "Новое значение:")
            await self.bot.send(
                incoming, f"{FIELD_LABELS.get(field, field)}.\n\n{prompt}", ui.cancel_keyboard(CANCEL_PAYLOAD)
            )
        elif action == "add":
            await self.start_add(incoming)
        elif action == "list":
            await self.show_list(incoming)
        elif action == "check":
            await self.run_check(incoming)
        elif action == "reload":
            await self.run_reload(incoming)
        elif action == "stats":
            await self.show_stats(incoming)
        elif action == "log":
            await self.show_log(incoming)
        else:
            await self.bot.send(incoming, "Не понял команду.", ui.admin_menu())

    async def show_stats(self, incoming) -> None:
        stats = storage.stats()
        text = (
            "Статистика\n\n"
            f"Событий в кэше: {stats['events']}\n"
            f"Опубликовано: {stats['published']}\n"
            f"Отменено: {stats['cancelled']}\n"
            f"Подписчиков: {stats['subscribers']} (с анонсами: {stats['active_subscribers']})\n"
            f"Последняя синхронизация: {stats['last_sync']}"
        )
        await self.bot.send(incoming, text, ui.admin_menu())

    async def show_log(self, incoming) -> None:
        rows = storage.recent_log(15)
        if not rows:
            await self.bot.send(incoming, "Журнал пуст.", ui.admin_menu())
            return
        lines = ["Последние изменения:"]
        for row in rows:
            lines.append(
                f"{row['created_at'][:16].replace('T', ' ')} · {row['action']} "
                f"· {row['target'] or '—'} · {row['details']}"
            )
        await self.bot.send(incoming, "\n".join(lines), ui.admin_menu())

    # ------------------------------------------------------------ пошаговый ввод
    async def step(self, incoming, state: str, payload: dict[str, Any], text: str) -> bool:
        """Обрабатывает ответ на шаг /add или /edit. Возвращает True, если шаг обработан."""
        text = (text or "").strip()
        if not text:
            return False

        if state.startswith("admin:add:"):
            field = state.split(":", 2)[2]
            return await self._add_step(incoming, field, payload, text)
        if state.startswith("admin:edit:"):
            field = state.split(":", 2)[2]
            return await self._edit_step(incoming, field, payload, text)
        return False

    async def _validate_field(self, field: str, text: str) -> tuple[Any, str | None]:
        if field == "title":
            if len(text) > 80:
                return None, "Название длиннее 80 символов. Сократите, пожалуйста."
            return text, None
        if field == "date":
            value = dates.parse_date(text)
            return (value, None) if value else (None, "Не понял дату. Нужен формат ДД.ММ.ГГГГ, например 08.11.2026.")
        if field == "time":
            value = dates.parse_time(text)
            return (value, None) if value else (None, "Не понял время. Нужен формат ЧЧ:ММ, например 18:30.")
        if field == "settlement":
            return (text, None) if len(text) >= 2 else (None, "Укажите населённый пункт.")
        if field == "place":
            return (text, None) if len(text) >= 3 else (None, "Укажите место и адрес подробнее.")
        if field == "category":
            value = parse_category(text)
            return (value, None) if value else (None, "Допустимо: культура, спорт или массовое.")
        if field == "family":
            value = parse_family(text)
            return (value, None) if value is not None else (None, "Напишите «да» или «нет».")
        if field == "age":
            value = parse_age(text)
            return (value, None) if value else (None, "Допустимо: 0+, 6+, 12+, 16+ или 18+.")
        if field == "price":
            value = normalize_price(text)
            return (value, None) if value else (None, "Укажите «Бесплатно», число рублей или «По записи».")
        if field == "description":
            if len(text) > 200:
                return None, "Описание длиннее 200 символов. Сократите, пожалуйста."
            return text, None
        if field == "contact":
            return text, None
        if field == "status":
            value = parse_status(text)
            return (value, None) if value else (None, "Допустимо: опубликовано, отменено или перенесено.")
        return text, None

    async def _add_step(self, incoming, field: str, payload: dict[str, Any], text: str) -> bool:
        data: dict[str, Any] = dict(payload.get("data") or {})

        if field == "contact" and text.lower() in {"пропустить", "нет", "-", "скип"}:
            value: Any = ""
            error = None
        else:
            value, error = await self._validate_field(field, text)
        if error:
            await self.bot.send(
                incoming, f"{error}\n\n{STEP_PROMPTS[field]}", ui.cancel_keyboard(CANCEL_PAYLOAD)
            )
            return True

        if isinstance(value, date):
            data[field] = value.isoformat()
        elif isinstance(value, time):
            data[field] = value.strftime("%H:%M")
        else:
            data[field] = value

        index = ADD_STEPS.index(field)
        if index + 1 < len(ADD_STEPS):
            next_field = ADD_STEPS[index + 1]
            storage.set_dialog(incoming.user_id, f"admin:add:{next_field}", {"data": data})
            await self.bot.send(
                incoming, STEP_PROMPTS[next_field], ui.cancel_keyboard(CANCEL_PAYLOAD)
            )
            return True

        # все поля собраны — сохраняем
        event_id = self._next_id()
        event = Event(
            id=event_id,
            title=data["title"],
            date=dates.parse_date(data["date"]),
            time=dates.parse_time(data["time"]),
            settlement=data["settlement"],
            place=data["place"],
            category=data["category"],
            family=bool(data["family"]),
            age=data["age"],
            price=data["price"],
            description=data["description"],
            contact=data.get("contact", ""),
            status="опубликовано",
            source=f"admin:{incoming.user_id}",
        )
        storage.upsert_event(event, source="admin")
        storage.clear_dialog(incoming.user_id)
        storage.log(
            action="add",
            actor_id=incoming.user_id,
            target=str(event.id),
            details=event.title,
        )

        sheet = getattr(self.bot, "sheet", None)
        sheet_note = ""
        if sheet is not None:
            ok = await sheet.append_event(event)
            sheet_note = (
                "\nСтрока добавлена в Google Таблицу."
                if ok
                else "\nТаблица недоступна: событие сохранено в базе, но не в таблице. Сообщите исполнителю."
            )
        await self.bot.send(
            incoming,
            f"Событие добавлено.\n\n{event_summary(event)}{sheet_note}",
            ui.admin_menu(),
        )
        return True

    async def _edit_step(self, incoming, field: str, payload: dict[str, Any], text: str) -> bool:
        event_id = self._parse_id(str(payload.get("id", "")))
        if event_id is None:
            storage.clear_dialog(incoming.user_id)
            return False
        event = storage.event(event_id)
        if event is None:
            storage.clear_dialog(incoming.user_id)
            await self.bot.send(incoming, "Событие не найдено.", ui.admin_menu())
            return True

        value, error = await self._validate_field(field, text)
        if error:
            await self.bot.send(
                incoming, f"{error}\n\n{STEP_PROMPTS.get(field, 'Новое значение:')}",
                ui.cancel_keyboard(CANCEL_PAYLOAD),
            )
            return True

        setattr(event, field, value)
        storage.upsert_event(event, source="admin")
        storage.clear_dialog(incoming.user_id)
        storage.log(
            action="edit",
            actor_id=incoming.user_id,
            target=str(event.id),
            details=f"{FIELD_LABELS.get(field, field)} → {value}",
        )
        sheet = getattr(self.bot, "sheet", None)
        if sheet is not None:
            await sheet.update_event(event)
        await self.bot.send(
            incoming,
            f"Изменения сохранены.\n\n{event_summary(event)}",
            ui.admin_menu(),
        )
        return True

    async def _notify_cancel(self, event_id: int) -> None:
        """Информационное уведомление подписчикам об отмене (п. 3.4)."""
        if not settings.bot_token:
            return
        from .notifications import announce_event

        try:
            await announce_event(event_id, kind="cancel")
        except Exception as exc:  # noqa: BLE001
            log.warning("Не удалось разослать уведомление об отмене: %s", exc)

    # ------------------------------------------------------------ утилиты
    @staticmethod
    def _parse_id(raw: str) -> int | None:
        match = __import__("re").search(r"\d+", raw or "")
        return int(match.group()) if match else None

    @staticmethod
    def _next_id() -> int:
        events = storage.events()
        return max((event.id for event in events), default=100) + 1
