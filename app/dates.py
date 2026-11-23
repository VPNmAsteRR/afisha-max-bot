"""Работа с датами: всё в часовом поясе Asia/Krasnoyarsk (UTC+7)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from .config import settings

WEEKDAYS_FULL = [
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
]
WEEKDAYS_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS_GENITIVE = [
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]


def now_local() -> datetime:
    """Текущее время в поясе заказчика независимо от пояса сервера."""
    return datetime.now(tz=settings.tz)


def today_local() -> date:
    return now_local().date()


def to_local(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=settings.tz)
    return value.astimezone(settings.tz)


def week_bounds(day: date | None = None) -> tuple[date, date]:
    """Границы текущей недели: понедельник..воскресенье по местному времени."""
    day = day or today_local()
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def next_week_bounds(day: date | None = None) -> tuple[date, date]:
    monday, sunday = week_bounds(day)
    return sunday + timedelta(days=1), sunday + timedelta(days=7)


def parse_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_time(raw: str) -> time | None:
    raw = (raw or "").strip().replace(".", ":").replace("-", ":")
    for fmt in ("%H:%M", "%H.%M", "%H"):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    return None


def combine(day: date, moment: time) -> datetime:
    return datetime.combine(day, moment, tzinfo=settings.tz)


def iso(day: date) -> str:
    return day.isoformat()


def human_date(day: date) -> str:
    return f"{day.day} {MONTHS_GENITIVE[day.month - 1]}"


def human_weekday(day: date) -> str:
    return WEEKDAYS_FULL[day.weekday()]


def short_weekday(day: date) -> str:
    return WEEKDAYS_SHORT[day.weekday()]


def human_full(day: date) -> str:
    return f"{short_weekday(day)}, {human_date(day)}"


def human_dt(moment: datetime) -> str:
    moment = to_local(moment)
    return f"{human_full(moment.date())}, {moment:%H:%M}"


def day_label(day: date) -> str:
    today = today_local()
    if day == today:
        return "Сегодня"
    if day == today + timedelta(days=1):
        return "Завтра"
    if day == today - timedelta(days=1):
        return "Вчера"
    return f"{short_weekday(day)}, {human_date(day)}"


def week_title(monday: date) -> str:
    sunday = monday + timedelta(days=6)
    if monday.month == sunday.month:
        return f"{monday.day}–{sunday.day} {MONTHS_GENITIVE[sunday.month - 1]}"
    return (
        f"{monday.day} {MONTHS_GENITIVE[monday.month - 1]} — "
        f"{sunday.day} {MONTHS_GENITIVE[sunday.month - 1]}"
    )
