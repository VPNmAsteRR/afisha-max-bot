"""Тексты сообщений и inline-клавиатуры (п. 4.1, 4.2, 4.3 ТЗ)."""

from __future__ import annotations

from typing import Any, Iterable

from . import dates
from .models import AGE_MARKS, CATEGORY_LABELS, Event

MAX_MESSAGE = 3500
PAGE_SIZE = 5

GREETING = (
    "Здравствуйте! Я помогу найти, куда сходить на этой неделе.\n"
    "Выберите, что вам интересно."
)
HELP_TEXT = (
    "Что я умею:\n\n"
    "• «Афиша на неделю» — все события с понедельника по воскресенье.\n"
    "• «Семейный досуг» — подберу события по возрасту ребёнка и покажу, "
    "бесплатный вход или платный.\n"
    "• Можно просто написать вопрос: «Что будет в субботу?», "
    "«Есть что-то бесплатное?», «Какие выставки?».\n\n"
    "Команды: /afisha, /family, /help, /contacts."
)
CONTACTS_TEXT = (
    "Связаться с нами\n\n"
    "Если нужно уточнить детали мероприятия, позвоните организатору — "
    "телефон указан в карточке события.\n"
    "По работе бота пишите администратору афиши."
)
UNKNOWN_TEXT = "Я пока не понял вопрос. Вот что я умею:"
EMPTY_WEEK_TEXT = "На эту неделю событий пока нет. Скоро появятся — загляните позже."
ERROR_TEXT = "Что-то пошло не так, попробуйте ещё раз через минуту."
DENIED_TEXT = "Команда недоступна."
TOO_LONG = "Сообщение слишком длинное. Сократите, пожалуйста, и отправьте ещё раз."


def btn(text: str, payload: str) -> dict[str, Any]:
    """Кнопка с callback-событием."""
    return {"type": "callback", "text": text, "payload": payload}


def link_btn(text: str, url: str) -> dict[str, Any]:
    """Кнопка-ссылка (не более 3 в ряду по требованиям платформы)."""
    return {"type": "link", "text": text, "url": url}


def main_menu() -> list[list[dict[str, Any]]]:
    return [
        [btn("Афиша на неделю", "menu:week"), btn("Семейный досуг", "menu:family")],
        [btn("Помощь", "menu:help"), btn("Связаться с нами", "menu:contacts")],
    ]


def category_menu() -> list[list[dict[str, Any]]]:
    return [
        [btn("Всё подряд", "cat:all")],
        [btn(CATEGORY_LABELS["культура"], "cat:культура"), btn("Спорт", "cat:спорт")],
        [btn("Для детей", "cat:kids"), btn("Массовые события", "cat:массовое")],
        [btn("Главное меню", "menu:main")],
    ]


def age_menu() -> list[list[dict[str, Any]]]:
    return [
        [btn("Младше 6 лет", "age:child")],
        [btn("6–12 лет", "age:teen")],
        [btn("Подростки 12+", "age:adult")],
        [btn("Главное меню", "menu:main")],
    ]


def pager(
    base: str,
    page: int,
    pages: int,
    *,
    extra: dict[str, str] | None = None,
) -> list[list[dict[str, Any]]]:
    """Кнопки навигации: «Дальше», «Назад», «Главное меню» (п. 4.1)."""
    query = dict(extra or {})

    def payload(delta: int) -> str:
        parts = [f"{base}|page={page + delta}"]
        parts += [f"{key}={value}" for key, value in query.items()]
        return "|".join(parts)

    nav: list[dict[str, Any]] = []
    if page > 0:
        nav.append(btn("Назад", payload(-1)))
    if page + 1 < pages:
        nav.append(btn("Дальше", payload(1)))
    rows: list[list[dict[str, Any]]] = []
    if nav:
        rows.append(nav)
    rows.append([btn("Главное меню", "menu:main")])
    return rows


def all_events_menu() -> list[list[dict[str, Any]]]:
    return [
        [btn("Вся афиша", "menu:week"), btn("Связаться с организатором", "menu:contacts")],
        [btn("Главное меню", "menu:main")],
    ]


def contacts_menu(contact: str | None = None) -> list[list[dict[str, Any]]]:
    rows: list[list[dict[str, Any]]] = []
    if contact:
        if contact.startswith("http://") or contact.startswith("https://"):
            rows.append([link_btn("Открыть ссылку", contact)])
        else:
            rows.append([btn("Показать телефон", "contact:show")])
    rows.append([btn("Главное меню", "menu:main")])
    return rows


def unsubscribe_keyboard() -> list[list[dict[str, Any]]]:
    return [[btn("Не присылать анонсы", "sub:off"), btn("Главное меню", "menu:main")]]


def event_card(event: Event, index: int | None = None) -> str:
    """Карточка события (п. 3.1): дата-время, название, место, описание, возраст, цена, контакт."""
    head = f"{dates.day_label(event.date)}, {event.time:%H:%M}"
    title = f"{head} — {event.title}"
    if index is not None:
        title = f"{index}. {title}"
    lines = [f"*{title}*", ""]
    lines.append(f"Населённый пункт: {event.settlement}")
    lines.append(f"Место: {event.place}")
    lines.append(f"Возраст: {event.age}")
    lines.append(f"Стоимость: {event.price}")
    if event.description:
        lines.append("")
        description = event.description.strip()
        if len(description) > 280:
            description = description[:277].rstrip() + "…"
        lines.append(description)
    if event.contact:
        lines.append("")
        lines.append(f"Запись: {event.contact}")
    if event.is_cancelled:
        lines.append("")
        lines.append("Отменено")
    return "\n".join(lines)


def event_card_plain(event: Event, index: int | None = None) -> str:
    """Вариант без markdown — используется в рассылке и отчётах."""
    prefix = f"{index}. " if index is not None else ""
    return (
        f"{prefix}{dates.human_dt(event.starts_at)} — {event.title}\n"
        f"{event.settlement}, {event.place}\n"
        f"Возраст: {event.age} · {event.price}"
    )


def chunk_message(parts: Iterable[str], limit: int = MAX_MESSAGE) -> list[str]:
    """Собирает текстовые блоки в сообщения не длиннее лимита."""
    messages: list[str] = []
    buffer = ""
    for part in parts:
        candidate = f"{buffer}\n\n{part}" if buffer else part
        if len(candidate) <= limit:
            buffer = candidate
            continue
        if buffer:
            messages.append(buffer)
        while len(part) > limit:
            messages.append(part[:limit])
            part = part[limit:]
        buffer = part
    if buffer:
        messages.append(buffer)
    return messages


def week_header(monday, sunday, category: str | None) -> str:
    title = f"Афиша на неделю {dates.week_title(monday)}"
    if category:
        title += f"\n{CATEGORY_LABELS.get(category, category)}"
    return title


def family_header(group_label: str) -> str:
    return f"Семейный досуг: {group_label}\nПоказываю только события, подходящие по возрасту."


def empty_filtered(category: str | None) -> str:
    if category:
        return (
            f"На этой неделе в разделе «{CATEGORY_LABELS.get(category, category)}» "
            "событий пока нет. Посмотрите всю афишу — там точно что-то есть."
        )
    return EMPTY_WEEK_TEXT


def search_results_header(query_text: str, found: int) -> str:
    if found == 0:
        return f"По запросу «{query_text}» ничего не нашлось."
    return f"По запросу «{query_text}» нашлось событий: {found}."


def cancel_keyboard(payload: str) -> list[list[dict[str, Any]]]:
    return [[btn("Отмена", payload)]]


def confirm_keyboard(yes_payload: str, no_payload: str) -> list[list[dict[str, Any]]]:
    return [[btn("Да, снять с публикации", yes_payload)], [btn("Нет", no_payload)]]


def admin_menu() -> list[list[dict[str, Any]]]:
    return [
        [btn("Добавить событие", "admin:add"), btn("Ближайшие 20", "admin:list")],
        [btn("Проверить таблицу", "admin:check"), btn("Перечитать афишу", "admin:reload")],
        [btn("Статистика", "admin:stats"), btn("Журнал", "admin:log")],
        [btn("Главное меню", "menu:main")],
    ]
