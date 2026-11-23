"""Разбор строк листа «Афиша» и отчёт об ошибках (п. 3.2, Приложение А)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from typing import Any, Iterable, Sequence

from . import dates
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

# Порядок колонок A..M строго по Приложению А.
COLUMNS = (
    "id", "title", "date", "time", "settlement", "place",
    "category", "family", "age", "price", "description", "contact", "status",
)
HEADER_ROW = (
    "id", "Название", "Дата", "Время начала", "Населённый пункт",
    "Место и адрес", "Категория", "Семейное", "Возрастная маркировка",
    "Стоимость", "Описание", "Контакт или ссылка", "Статус",
)
MAX_TITLE = 80
MAX_DESCRIPTION = 200


@dataclass(slots=True)
class RowIssue:
    """Ошибка или пропуск в конкретной строке таблицы."""

    row_number: int
    event_id: str
    problems: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        ident = f"id={self.event_id}" if self.event_id else "без id"
        return f"Строка {self.row_number} ({ident}): " + "; ".join(self.problems)


@dataclass(slots=True)
class ParsedSheet:
    events: list[Event] = field(default_factory=list)
    issues: list[RowIssue] = field(default_factory=list)
    skipped_cancelled: int = 0

    @property
    def has_issues(self) -> bool:
        return bool(self.issues)

    def report(self, limit: int = 20) -> str:
        if not self.issues:
            return "Ошибок нет: все строки заполнены корректно."
        lines = [f"Найдено проблем: {len(self.issues)}"]
        for issue in self.issues[:limit]:
            lines.append("• " + issue.as_text())
        if len(self.issues) > limit:
            lines.append(f"… и ещё {len(self.issues) - limit}")
        return "\n".join(lines)


def _cell(row: Sequence[Any], index: int) -> str:
    if index >= len(row):
        return ""
    value = row[index]
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def row_to_mapping(row: Sequence[Any]) -> dict[str, str]:
    return {name: _cell(row, index) for index, name in enumerate(COLUMNS)}


def looks_like_header(row: Sequence[Any]) -> bool:
    first = _cell(row, 0).lower()
    return first in {"id", "ид", "№", "номер"}


def build_event(row: Sequence[Any], row_number: int) -> tuple[Event | None, RowIssue | None]:
    """Возвращает событие или описание проблем строки."""
    data = row_to_mapping(row)
    problems: list[str] = []

    raw_id = data.get("id", "")
    event_id: int | None = None
    if not raw_id:
        problems.append("не заполнено поле «ID»")
    else:
        try:
            event_id = int(float(raw_id.replace(",", ".")))
        except ValueError:
            problems.append("поле «ID» должно быть числом")

    title = data.get("title", "").strip()
    if not title:
        problems.append("не заполнено поле «Название»")
    elif len(title) > MAX_TITLE:
        problems.append(f"«Название» длиннее {MAX_TITLE} символов")

    raw_date = data.get("date", "")
    parsed_date: date | None = dates.parse_date(raw_date)
    if not raw_date:
        problems.append("не заполнено поле «Дата»")
    elif parsed_date is None:
        problems.append("«Дата» должна быть в формате ДД.ММ.ГГГГ")

    raw_time = data.get("time", "")
    parsed_time: time | None = dates.parse_time(raw_time)
    if not raw_time:
        problems.append("не заполнено поле «Время начала»")
    elif parsed_time is None:
        problems.append("«Время начала» должно быть в формате ЧЧ:ММ")

    settlement = data.get("settlement", "")
    if not settlement:
        problems.append("не заполнено поле «Населённый пункт»")

    place = data.get("place", "")
    if not place:
        problems.append("не заполнено поле «Место и адрес»")

    raw_category = data.get("category", "")
    category = parse_category(raw_category)
    if not raw_category:
        problems.append("не заполнено поле «Категория»")
    elif category is None:
        problems.append("«Категория»: допустимо культура, спорт или массовое")

    raw_family = data.get("family", "")
    family = parse_family(raw_family)
    if not raw_family:
        problems.append("не заполнено поле «Семейное»")
    elif family is None:
        problems.append("«Семейное»: укажите «да» или «нет»")

    raw_age = data.get("age", "")
    age = parse_age(raw_age)
    if not raw_age:
        problems.append("не заполнено поле «Возрастная маркировка»")
    elif age is None:
        problems.append("«Возрастная маркировка»: допустимо " + ", ".join(AGE_MARKS))

    raw_price = data.get("price", "")
    price = normalize_price(raw_price)
    if not raw_price:
        problems.append("не заполнено поле «Стоимость»")

    description = data.get("description", "")
    if not description:
        problems.append("не заполнено поле «Описание»")
    elif len(description) > MAX_DESCRIPTION:
        problems.append(f"«Описание» длиннее {MAX_DESCRIPTION} символов")

    raw_status = data.get("status", "")
    status = parse_status(raw_status)
    if not raw_status:
        problems.append("не заполнено поле «Статус»")
    elif status is None:
        problems.append("«Статус»: допустимо опубликовано, отменено или перенесено")

    issue = RowIssue(row_number=row_number, event_id=raw_id, problems=problems) if problems else None
    if problems or event_id is None or parsed_date is None or parsed_time is None:
        return None, issue or RowIssue(row_number, raw_id, ["строка пропущена"])

    event = Event(
        id=event_id,
        title=title,
        date=parsed_date,
        time=parsed_time,
        settlement=settlement,
        place=place,
        category=category,
        family=bool(family),
        age=age,
        price=price,
        description=description,
        contact=data.get("contact", ""),
        status=status,
    )
    return event, None


def parse_rows(rows: Iterable[Sequence[Any]]) -> ParsedSheet:
    """Парсит все строки листа, начиная с данных после заголовка."""
    parsed = ParsedSheet()
    seen_ids: dict[int, int] = {}
    for index, row in enumerate(rows):
        row_number = index + 2  # первая строка листа — заголовки
        if looks_like_header(row) and index == 0:
            continue
        if not any(str(cell or "").strip() for cell in row):
            continue
        event, issue = build_event(row, row_number)
        if issue is not None:
            parsed.issues.append(issue)
            continue
        assert event is not None
        if event.id in seen_ids:
            parsed.issues.append(
                RowIssue(
                    row_number=row_number,
                    event_id=str(event.id),
                    problems=[f"повторяющийся ID (уже был в строке {seen_ids[event.id]})"],
                )
            )
            continue
        seen_ids[event.id] = row_number
        if event.is_cancelled:
            parsed.skipped_cancelled += 1
        parsed.events.append(event)
    return parsed


def required_fields_help() -> str:
    return ", ".join(FIELD_LABELS[name] for name in COLUMNS if FIELD_LABELS.get(name))
