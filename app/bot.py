"""Сценарии бота: меню, афиша, семейный досуг, свободный вопрос, админ-команды.

Реализует п. 3.1, 3.3, 4.1–4.3 ТЗ.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, time
from typing import Any

from . import dates, search, ui
from .config import settings
from .maxapi import MaxApiError, client
from .models import CATEGORY_LABELS, Event
from .storage import storage

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- helpers

def parse_payload(payload: str) -> tuple[str, dict[str, str]]:
    """«catalog|page=1|cat=спорт» → ("catalog", {"page": "1", "cat": "спорт"}).

    Поддерживает и короткую форму «menu:week» (второй часть — имя действия).
    """
    parts = (payload or "").split("|")
    head = parts[0].strip()
    params: dict[str, str] = {}
    if ":" in head:
        action, name = head.split(":", 1)
        action = action.strip()
        if name.strip():
            params["name"] = name.strip()
    else:
        action = head
    for part in parts[1:]:
        if "=" in part:
            key, value = part.split("=", 1)
            params[key.strip()] = value.strip()
    return action, params


def build_payload(action: str, **params: Any) -> str:
    chunks = [action]
    for key, value in params.items():
        if value is None or value == "":
            continue
        chunks.append(f"{key}={value}")
    return "|".join(chunks)


def is_admin(user_id: str | int) -> bool:
    return str(user_id) in {str(item) for item in settings.admin_ids}


def visible_events() -> list[Event]:
    """Активные события, отсортированные по времени начала."""
    events = [event for event in storage.events() if event.is_active]
    events.sort(key=lambda event: event.key())
    return events


def paginate(items: list[Event], page: int, per_page: int = ui.PAGE_SIZE) -> tuple[list[Event], int, int]:
    pages = max(1, (len(items) + per_page - 1) // per_page)
    page = max(0, min(page, pages - 1))
    chunk = items[page * per_page : (page + 1) * per_page]
    return chunk, page, pages


def week_events(category: str | None = None, group: str | None = None) -> list[Event]:
    monday, sunday = dates.week_bounds()
    result = [
        event
        for event in visible_events()
        if event.in_week(monday, sunday)
        and (category in (None, "", "all") or event.category == category)
    ]
    if group:
        result = [event for event in result if event.family and event.matches_age_group(group)]
    return result


def events_by_day(events: list[Event]) -> list[tuple[date, list[Event]]]:
    grouped: dict[date, list[Event]] = {}
    for event in events:
        grouped.setdefault(event.date, []).append(event)
    return [(day, grouped[day]) for day in sorted(grouped)]


def render_events(events: list[Event], start_index: int = 1) -> str:
    """Карточки сгруппированы по дням (п. 3.1)."""
    if not events:
        return ""
    blocks: list[str] = []
    current_day: date | None = None
    counter = start_index
    for event in events:
        if event.date != current_day:
            current_day = event.date
            blocks.append(f"*{dates.day_label(event.date)}* — {dates.human_date(event.date)}")
        blocks.append(ui.event_card(event, counter))
        counter += 1
    return "\n\n".join(blocks)


# ---------------------------------------------------------------- bot

@dataclass
class Incoming:
    user_id: str
    chat_id: str
    name: str
    text: str = ""
    payload: str = ""
    callback_id: str = ""
    message_id: str = ""
    is_callback: bool = False
    is_start: bool = False


class Bot:
    def __init__(self) -> None:
        self.api = client
        self.sheet = None  # выставляется в main, чтобы не тянуть gspread в тестах

    # ------------------------------ отправка
    async def send(
        self,
        incoming: Incoming,
        text: str,
        keyboard: list[list[dict[str, Any]]] | None = None,
    ) -> None:
        try:
            await self.api.send_message(
                user_id=incoming.user_id,
                chat_id=None,
                text=text,
                keyboard=keyboard,
                fmt="markdown",
            )
        except MaxApiError as exc:
            log.warning("Не удалось отправить сообщение %s: %s", incoming.user_id, exc)
            if incoming.callback_id:
                try:
                    await self.api.answer_callback(incoming.callback_id, notification=ui.ERROR_TEXT)
                except MaxApiError:
                    pass

    async def send_long(
        self,
        incoming: Incoming,
        parts: list[str],
        keyboard: list[list[dict[str, Any]]] | None = None,
    ) -> None:
        messages = ui.chunk_message(parts)
        if not messages:
            messages = [ui.EMPTY_WEEK_TEXT]
        for index, message in enumerate(messages):
            last = index == len(messages) - 1
            await self.send(incoming, message, keyboard if last else None)

    # ------------------------------ точка входа
    async def handle(self, incoming: Incoming) -> None:
        text = (incoming.text or "").strip()
        if incoming.user_id:
            storage.touch_subscriber(incoming.user_id, incoming.chat_id, incoming.name)

        if incoming.is_callback:
            await self.handle_callback(incoming)
            return

        state, payload = storage.get_dialog(incoming.user_id)
        if (state.startswith("admin:add:") or state.startswith("admin:edit:")) and not text.startswith("/"):
            if text.lower() in {"отмена", "cancel", "стоп"}:
                storage.clear_dialog(incoming.user_id)
                await self.send(incoming, "Действие отменено.", ui.admin_menu())
                return
            handled = await self.handle_admin_step(incoming, state, payload, text)
            if handled:
                return

        if incoming.is_start:
            storage.clear_dialog(incoming.user_id)
            await self.send(incoming, ui.GREETING, ui.main_menu())
            return

        if text.startswith("/"):
            await self.handle_command(incoming, text)
            return

        if not text:
            await self.send(incoming, ui.GREETING, ui.main_menu())
            return

        await self.handle_free_text(incoming, text)

    # ------------------------------ команды
    async def handle_command(self, incoming: Incoming, text: str) -> None:
        command = text.split()[0].split("@")[0].lower().lstrip("/")
        args = text[len(text.split()[0]) :].strip()

        mapping = {
            "start": self.cmd_start,
            "afisha": self.cmd_afisha,
            "афиша": self.cmd_afisha,
            "family": self.cmd_family,
            "семья": self.cmd_family,
            "help": self.cmd_help,
            "помощь": self.cmd_help,
            "contacts": self.cmd_contacts,
            "контакты": self.cmd_contacts,
        }
        if command in ("add", "edit", "del", "list", "check", "reload"):
            if not await self.require_admin(incoming):
                return
            await self.handle_admin_command(incoming, command, args)
            return
        if command == "admin":
            if not await self.require_admin(incoming):
                return
            await self.send(incoming, "Администрирование афиши:", ui.admin_menu())
            return
        if command in mapping:
            await mapping[command](incoming)
            return
        await self.send(incoming, ui.UNKNOWN_TEXT, ui.main_menu())

    async def cmd_start(self, incoming: Incoming) -> None:
        await self.send(incoming, ui.GREETING, ui.main_menu())

    async def cmd_help(self, incoming: Incoming) -> None:
        await self.send(incoming, ui.HELP_TEXT, ui.main_menu())

    async def cmd_contacts(self, incoming: Incoming) -> None:
        await self.send(incoming, ui.CONTACTS_TEXT, ui.contacts_menu())

    async def cmd_afisha(self, incoming: Incoming) -> None:
        await self.send(
            incoming,
            "За какой период показать афишу?",
            [
                [ui.btn("Текущая неделя", build_payload("catalog", cat="all", page=0))],
                [ui.btn("Следующая неделя", build_payload("catalog", cat="all", page=0, week="next"))],
                [ui.btn("Ближайшие события", build_payload("soon", page=0))],
                [ui.btn("Главное меню", "menu:main")],
            ],
        )

    async def cmd_family(self, incoming: Incoming) -> None:
        await self.send(
            incoming,
            "Семейный досуг\n\nУточните возраст ребёнка — подберу подходящие события.",
            ui.age_menu(),
        )

    # ------------------------------ callbacks
    async def handle_callback(self, incoming: Incoming) -> None:
        action, params = parse_payload(incoming.payload)
        page = int(params.get("page", "0") or 0)

        try:
            if action == "menu":
                await self.menu_action(incoming, params.get("name", "main"))
            elif action == "catalog":
                await self.show_catalog(incoming, page, params)
            elif action == "cat":
                await self.show_category(incoming, params.get("name", "all"))
            elif action == "soon":
                await self.show_soon(incoming, page)
            elif action == "age":
                await self.show_family(incoming, params.get("name", "child"), page)
            elif action == "sub":
                await self.subscription_action(incoming, params.get("name", "on"))
            elif action == "contact":
                await self.send(incoming, ui.CONTACTS_TEXT, ui.main_menu())
            elif action == "admin":
                await self.admin_callback(incoming, params)
            elif action == "filters":
                await self.send(incoming, "Выберите раздел афиши:", ui.category_menu())
            else:
                await self.send(incoming, ui.UNKNOWN_TEXT, ui.main_menu())
        except MaxApiError as exc:
            log.exception("Ошибка API при обработке callback: %s", exc)
            await self.send(incoming, ui.ERROR_TEXT, ui.main_menu())
        finally:
            if incoming.callback_id:
                try:
                    await self.api.answer_callback(incoming.callback_id)
                except MaxApiError:
                    pass

    async def menu_action(self, incoming: Incoming, name: str) -> None:
        if name == "week":
            await self.show_catalog(incoming, 0, {"cat": "all"})
        elif name == "family":
            await self.send(
                incoming,
                "Семейный досуг\n\nУточните возраст ребёнка — подберу подходящие события.",
                ui.age_menu(),
            )
        elif name == "help":
            await self.send(incoming, ui.HELP_TEXT, ui.main_menu())
        elif name == "filters":
            await self.send(incoming, "Выберите раздел афиши:", ui.category_menu())
        elif name == "contacts":
            await self.send(incoming, ui.CONTACTS_TEXT, ui.main_menu())
        else:
            await self.send(incoming, ui.GREETING, ui.main_menu())

    async def show_catalog(
        self, incoming: Incoming, page: int, params: dict[str, str], filtered: list[Event] | None = None
    ) -> None:
        """Сценарий А: афиша на неделю, фильтр и пагинация по 5 событий."""
        category = params.get("cat", "all")
        if filtered is None:
            if params.get("week") == "next":
                start, end = dates.next_week_bounds()
                events = [
                    event
                    for event in visible_events()
                    if event.in_week(start, end)
                    and (category in ("", "all") or event.category == category)
                ]
                header = f"Афиша на неделю {dates.week_title(start)}"
            else:
                events = week_events(None if category in ("", "all") else category)
                monday, _ = dates.week_bounds()
                header = ui.week_header(monday, monday, None if category in ("", "all") else category)
            filtered = events
        else:
            header = ui.week_header(*dates.week_bounds(), None)

        if not filtered:
            await self.send(
                incoming,
                ui.empty_filtered(None if category in ("", "all") else category),
                [
                    [ui.btn("Ближайшие события", build_payload("soon", page=0))],
                    [ui.btn("Главное меню", "menu:main")],
                ],
            )
            return

        page = max(0, page)
        chunk, page, pages = paginate(filtered, page)
        start_index = page * ui.PAGE_SIZE + 1
        body = render_events(chunk, start_index)
        total_line = f"Всего событий: {len(filtered)}"
        if pages > 1:
            total_line += f" · страница {page + 1} из {pages}"

        nav: list[list[dict[str, Any]]] = []
        row: list[dict[str, Any]] = []
        if page > 0:
            row.append(ui.btn("Назад", build_payload("catalog", page=page - 1, cat=category, week=params.get("week", ""))))
        if page + 1 < pages:
            row.append(ui.btn("Дальше", build_payload("catalog", page=page + 1, cat=category, week=params.get("week", ""))))
        if row:
            nav.append(row)
        nav.append([ui.btn("Сменить раздел", "menu:filters")])
        nav.append([ui.btn("Главное меню", "menu:main")])

        await self.send(incoming, f"{header}\n{total_line}\n\n{body}", nav)

    async def show_category(self, incoming: Incoming, name: str) -> None:
        if name == "kids":
            await self.send(
                incoming,
                "Для детей\n\nУточните возраст ребёнка:",
                ui.age_menu(),
            )
            return
        if name == "all":
            await self.show_catalog(incoming, 0, {"cat": "all"})
            return
        await self.show_catalog(incoming, 0, {"cat": name})

    async def show_soon(self, incoming: Incoming, page: int) -> None:
        today = dates.today_local()
        events = [event for event in visible_events() if event.date >= today]
        if not events:
            await self.send(
                incoming,
                "Ближайших событий пока нет. Афиша обновляется — загляните позже.",
                ui.main_menu(),
            )
            return
        chunk, page, pages = paginate(events, page)
        body = render_events(chunk, page * ui.PAGE_SIZE + 1)
        nav: list[list[dict[str, Any]]] = []
        row: list[dict[str, Any]] = []
        if page > 0:
            row.append(ui.btn("Назад", build_payload("soon", page=page - 1)))
        if page + 1 < pages:
            row.append(ui.btn("Дальше", build_payload("soon", page=page + 1)))
        if row:
            nav.append(row)
        nav.append([ui.btn("Главное меню", "menu:main")])
        await self.send(incoming, f"Ближайшие события\n\n{body}", nav)

    async def show_family(self, incoming: Incoming, group: str, page: int) -> None:
        """Сценарий Б: фильтр по возрасту, 18+ не попадает никогда."""
        labels = {"child": "Младше 6 лет", "teen": "6–12 лет", "adult": "Подростки 12+"}
        if group not in labels:
            await self.send(incoming, "Уточните возраст ребёнка:", ui.age_menu())
            return
        events = week_events(group=group)
        if not events:
            await self.send(
                incoming,
                f"На эту неделю событий для группы «{labels[group]}» пока нет.\n"
                "Посмотрите всю афишу — возможно, найдётся что-то подходящее.",
                [
                    [ui.btn("Афиша на неделю", build_payload("catalog", cat="all", page=0))],
                    [ui.btn("Выбрать другой возраст", "menu:family")],
                    [ui.btn("Главное меню", "menu:main")],
                ],
            )
            return
        chunk, page, pages = paginate(events, page)
        body = render_events(chunk, page * ui.PAGE_SIZE + 1)
        nav: list[list[dict[str, Any]]] = []
        row: list[dict[str, Any]] = []
        if page > 0:
            row.append(ui.btn("Назад", build_payload("age", name=group, page=page - 1)))
        if page + 1 < pages:
            row.append(ui.btn("Дальше", build_payload("age", name=group, page=page + 1)))
        if row:
            nav.append(row)
        nav.append([ui.btn("Другой возраст", "menu:family")])
        nav.append([ui.btn("Главное меню", "menu:main")])
        await self.send(incoming, f"{ui.family_header(labels[group])}\n\n{body}", nav)

    async def subscription_action(self, incoming: Incoming, name: str) -> None:
        subscribed = name != "off"
        storage.set_subscription(incoming.user_id, subscribed)
        storage.log(
            action="subscription",
            actor_id=incoming.user_id,
            details="включил анонсы" if subscribed else "отключил анонсы",
        )
        if subscribed:
            await self.send(incoming, "Хорошо, буду присылать анонсы событий.", ui.main_menu())
        else:
            await self.send(
                incoming,
                "Понятно, анонсы присылать не буду. Афиша всегда доступна в меню.",
                ui.main_menu(),
            )

    async def ask_organizer(self, incoming: Incoming) -> None:
        await self.send(
            incoming,
            "Связаться с организатором можно по телефону или ссылке, "
            "указанным в карточке события.",
            ui.main_menu(),
        )

    # ------------------------------ свободный текст (сценарий В)
    async def handle_free_text(self, incoming: Incoming, text: str) -> None:
        if len(text) > 1000:
            await self.send(incoming, ui.TOO_LONG, ui.main_menu())
            return

        query = search.parse_query(text)
        if query.empty:
            await self.send(incoming, f"{ui.UNKNOWN_TEXT}\n\n{search.describe_hints()}", ui.main_menu())
            return

        events = search.apply_query(query, storage.events())
        if not events:
            await self.send(
                incoming,
                f"{ui.UNKNOWN_TEXT}\n\n{search.describe_hints()}",
                ui.main_menu(),
            )
            return

        messages = ui.chunk_message(
            [ui.search_results_header(text, len(events))]
            + [ui.event_card(event, index) for index, event in enumerate(events[:10], start=1)]
        )
        for index, message in enumerate(messages):
            last = index == len(messages) - 1
            await self.send(incoming, message, ui.all_events_menu() if last else None)

    # ------------------------------ админ: проверка прав
    async def require_admin(self, incoming: Incoming) -> bool:
        if is_admin(incoming.user_id):
            return True
        await self.send(incoming, ui.DENIED_TEXT)
        storage.log(
            action="denied",
            actor_id=incoming.user_id,
            target=incoming.text or incoming.payload,
            details="попытка админ-команды",
        )
        return False

    # ------------------------------ админ: команды
    async def handle_admin_command(self, incoming: Incoming, command: str, args: str) -> None:
        from .admin import AdminFlow

        flow = AdminFlow(self)
        await flow.run(incoming, command, args)

    # ------------------------------ админ: пошаговые состояния
    async def handle_admin_step(
        self, incoming: Incoming, state: str, payload: dict[str, Any], text: str
    ) -> bool:
        from .admin import AdminFlow

        flow = AdminFlow(self)
        return await flow.step(incoming, state, payload, text)

    async def admin_callback(self, incoming: Incoming, params: dict[str, str]) -> None:
        from .admin import AdminFlow

        flow = AdminFlow(self)
        await flow.callback(incoming, params)
