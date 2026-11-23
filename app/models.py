"""Модель события и правила валидации (п. 3.2 и Приложение А ТЗ)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any

from . import dates

CATEGORIES = ("культура", "спорт", "массовое")
CATEGORY_LABELS = {
    "культура": "Концерты и выставки",
    "спорт": "Спорт",
    "массовое": "Массовые события",
}
CATEGORY_ALIASES = {
    "культура": "культура",
    "культ": "культура",
    "концерт": "культура",
    "выставка": "культура",
    "музыка": "культура",
    "театр": "культура",
    "спорт": "спорт",
    "спортивное": "спорт",
    "массовое": "массовое",
    "массовое мероприятие": "массовое",
    "массовка": "массовое",
    "праздник": "массовое",
    "ярмарка": "массовое",
}
AGE_MARKS = ("0+", "6+", "12+", "16+", "18+")
AGE_ORDER = {mark: index for index, mark in enumerate(AGE_MARKS)}
STATUSES = ("опубликовано", "отменено", "перенесено")
STATUS_LABELS = {
    "опубликовано": "Опубликовано",
    "отменено": "Отменено",
    "перенесено": "Перенесено",
}
STATUS_ALIASES = {
    "опубликовано": "опубликовано",
    "опубликован": "опубликовано",
    "активно": "опубликовано",
    "да": "опубликовано",
    "1": "опубликовано",
    "отменено": "отменено",
    "отменён": "отменено",
    "отменен": "отменено",
    "отмена": "отменено",
    "нет": "отменено",
    "0": "отменено",
    "перенесено": "перенесено",
    "перенесён": "перенесено",
    "перенесен": "перенесено",
}
YES = {"да", "yes", "y", "1", "true", "истина", "ага", "+"}
NO = {"нет", "no", "n", "0", "false", "ложь", "-"}

REQUIRED_FIELDS = (
    "id",
    "title",
    "date",
    "time",
    "settlement",
    "place",
    "category",
    "family",
    "age",
    "price",
    "description",
    "status",
)

FIELD_LABELS = {
    "id": "ID",
    "title": "Название",
    "date": "Дата",
    "time": "Время начала",
    "settlement": "Населённый пункт",
    "place": "Место и адрес",
    "category": "Категория",
    "family": "Семейное",
    "age": "Возрастная маркировка",
    "price": "Стоимость",
    "description": "Краткое описание",
    "contact": "Контакт или ссылка",
    "status": "Статус",
    "source": "Источник",
}


def parse_family(raw: Any) -> bool | None:
    text = str(raw or "").strip().lower()
    if text in YES:
        return True
    if text in NO:
        return False
    return None


def parse_age(raw: Any) -> str | None:
    text = str(raw or "").strip().lower().replace(" ", "")
    text = text.replace("лет", "").replace("года", "").replace("+", "") 
    if not text:
        return None
    if text.isdigit():
        value = int(text)
        if value <= 6:
            return "0+" if value == 0 else "6+"
        if value <= 12:
            return "12+"
        if value <= 16:
            return "16+"
        return "18+"
    return None


def parse_category(raw: Any) -> str | None:
    text = str(raw or "").strip().lower()
    return CATEGORY_ALIASES.get(text)


def parse_status(raw: Any) -> str | None:
    text = str(raw or "").strip().lower()
    return STATUS_ALIASES.get(text)


def normalize_price(raw: Any) -> str:
    """«Бесплатно», число рублей или «По записи» (п. 3.2)."""
    text = str(raw or "").strip()
    if not text:
        return ""
    low = text.lower()
    if low in {"бесплатно", "бесплатный", "free", "0", "0р", "0 руб", "0₽", "нет"}:
        return "Бесплатно"
    if "запис" in low or low in {"по записи", "регистрация"}:
        return "По записи"
    cleaned = (
        low.replace("руб.", "")
        .replace("руб", "")
        .replace("₽", "")
        .replace("р.", "")
        .replace(" ", "")
        .replace(",", ".")
    )
    try:
        value = float(cleaned)
    except ValueError:
        return text
    if value <= 0:
        return "Бесплатно"
    if value.is_integer():
        return f"{int(value)} ₽"
    return f"{value:.2f} ₽".replace(".", ",")


@dataclass(slots=True)
class Event:
    """Одна строка листа «Афиша»."""

    id: int
    title: str
    date: date
    time: time
    settlement: str
    place: str
    category: str
    family: bool
    age: str
    price: str
    description: str
    status: str = "опубликовано"
    contact: str = ""
    source: str = ""
    errors: list[str] = field(default_factory=list)

    # --- Производные признаки ---
    @property
    def starts_at(self) -> datetime:
        return dates.combine(self.date, self.time)

    @property
    def is_active(self) -> bool:
        return self.status == "опубликовано"

    @property
    def is_cancelled(self) -> bool:
        return self.status == "отменено"

    @property
    def is_free(self) -> bool:
        return self.price.strip().lower() == "бесплатно"

    @property
    def age_value(self) -> int:
        return AGE_ORDER.get(self.age, 0)

    def matches_age_group(self, group: str) -> bool:
        """Маппинг из п. 3.1, сценарий Б. 18+ не попадает никогда."""
        limits = {"child": "6+", "teen": "12+", "adult": "16+"}
        limit = limits.get(group)
        if limit is None:
            return False
        return self.age_value <= AGE_ORDER[limit]

    def in_week(self, monday: date, sunday: date) -> bool:
        return monday <= self.date <= sunday

    def key(self) -> tuple[datetime, str]:
        return (self.starts_at, self.title.lower())

    def to_row(self) -> dict[str, str]:
        return {
            "id": str(self.id),
            "title": self.title,
            "date": self.date.strftime("%d.%m.%Y"),
            "time": self.time.strftime("%H:%M"),
            "settlement": self.settlement,
            "place": self.place,
            "category": self.category,
            "family": "да" if self.family else "нет",
            "age": self.age,
            "price": self.price,
            "description": self.description,
            "contact": self.contact,
            "status": self.status,
            "source": self.source,
        }
