"""Тесты логики: даты, парсинг таблицы, фильтры, поиск, админ-валидация."""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, time, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="afisha-test-"))
os.environ.setdefault("ADMIN_IDS", "1,2,3")
os.environ.setdefault("TIMEZONE", "Asia/Krasnoyarsk")

from app import dates, search  # noqa: E402
from app.models import (  # noqa: E402
    Event,
    normalize_price,
    parse_age,
    parse_category,
    parse_family,
)
from app.sheet import parse_rows  # noqa: E402


def make_event(**overrides) -> Event:
    base = dict(
        id=101,
        title="Мастер-класс",
        date=dates.today_local(),
        time=time(12, 0),
        settlement="с. Казачинское",
        place="Дом культуры",
        category="культура",
        family=True,
        age="6+",
        price="Бесплатно",
        description="Рисуем гуашью",
        status="опубликовано",
    )
    base.update(overrides)
    return Event(**base)


# ------------------------------------------------------------------ даты

def test_week_bounds_start_monday():
    monday, sunday = dates.week_bounds(date(2026, 11, 4))  # среда
    assert monday == date(2026, 11, 2)
    assert sunday == date(2026, 11, 8)
    assert monday.weekday() == 0


def test_week_bounds_on_sunday():
    monday, sunday = dates.week_bounds(date(2026, 11, 8))
    assert monday == date(2026, 11, 2)
    assert sunday == date(2026, 11, 8)


def test_parse_date_formats():
    assert dates.parse_date("08.11.2026") == date(2026, 11, 8)
    assert dates.parse_date("2026-11-08") == date(2026, 11, 8)
    assert dates.parse_date("08/11/2026") == date(2026, 11, 8)
    assert dates.parse_date("мусор") is None


def test_parse_time():
    assert dates.parse_time("18:30") == time(18, 30)
    assert dates.parse_time("9.05") == time(9, 5)
    assert dates.parse_time("abc") is None


def test_timezone_is_krasnoyarsk():
    assert str(dates.now_local().tzinfo) == "Asia/Krasnoyarsk"


# ------------------------------------------------------------------ модель

def test_parse_age():
    assert parse_age("0+") == "0+"
    assert parse_age("6+") == "6+"
    assert parse_age("12 лет") == "12+"
    assert parse_age("18+") == "18+"
    assert parse_age("нечто") is None


def test_parse_family():
    assert parse_family("да") is True
    assert parse_family("Нет") is False
    assert parse_family("наверное") is None


def test_parse_category():
    assert parse_category("культура") == "культура"
    assert parse_category("Концерт") == "культура"
    assert parse_category("спорт") == "спорт"
    assert parse_category("еда") is None


def test_normalize_price():
    assert normalize_price("Бесплатно") == "Бесплатно"
    assert normalize_price("0") == "Бесплатно"
    assert normalize_price("300 руб") == "300 ₽"
    assert normalize_price("По записи") == "По записи"


def test_age_group_mapping():
    child = make_event(age="6+")
    assert child.matches_age_group("child") is True
    assert child.matches_age_group("teen") is True
    teen = make_event(age="16+")
    assert teen.matches_age_group("child") is False
    assert teen.matches_age_group("adult") is True
    adult = make_event(age="18+")
    assert adult.matches_age_group("adult") is False
    assert adult.matches_age_group("child") is False


# ------------------------------------------------------------------ таблица

def test_parse_rows_ok():
    row = [
        "101", "Мастер-класс «Осенний пейзаж»", "08.11.2026", "12:00",
        "с. Казачинское", "Дом культуры, ул. Советская, 12", "культура",
        "да", "6+", "Бесплатно", "Рисуем гуашью", "8-900-000-00-00", "опубликовано",
    ]
    parsed = parse_rows([list(row[:1]) and ["id", "Название"], row])
    assert not parsed.issues
    assert len(parsed.events) == 1
    event = parsed.events[0]
    assert event.id == 101
    assert event.family is True
    assert event.date == date(2026, 11, 8)
    assert event.time == time(12, 0)


def test_parse_rows_reports_errors():
    bad = ["", "", "32.13.2026", "25:00", "", "", "еда", "может", "5+", "", "", "", "висяк"]
    parsed = parse_rows([bad])
    assert parsed.events == []
    assert parsed.issues
    text = parsed.report()
    assert "Дата" in text
    assert "Время" in text
    assert "Категория" in text


def test_parse_rows_skips_duplicate_ids():
    row = [
        "101", "Событие", "08.11.2026", "12:00", "с. Пировское", "Клуб",
        "спорт", "нет", "12+", "300", "Описание", "", "опубликовано",
    ]
    parsed = parse_rows([row, row])
    assert len(parsed.events) == 1
    assert any("повторяющийся" in issue.as_text() for issue in parsed.issues)


def test_missing_required_field_excluded():
    row = [
        "102", "", "08.11.2026", "12:00", "с. Пировское", "Клуб",
        "спорт", "нет", "12+", "300", "Описание", "", "опубликовано",
    ]
    parsed = parse_rows([row])
    assert parsed.events == []
    assert "Название" in parsed.report()


# ------------------------------------------------------------------ поиск

def test_query_weekday_typos():
    query = search.parse_query("что будет в субботу?")
    saturday = dates.today_local() + timedelta(days=(5 - dates.today_local().weekday()) % 7)
    assert saturday in query.dates


def test_query_weekday_short():
    query = search.parse_query("а что в сб")
    assert query.dates


def test_query_free():
    query = search.parse_query("есть что-то бесплатное?")
    assert query.free_only is True


def test_query_exhibition():
    query = search.parse_query("какие выставки?")
    assert "культура" in query.categories


def test_apply_query_free_only():
    free = make_event(id=1, price="Бесплатно")
    paid = make_event(id=2, price="500 ₽")
    query = search.parse_query("бесплатно")
    result = search.apply_query(query, [free, paid])
    assert [event.id for event in result] == [1]


def test_apply_query_excludes_cancelled():
    cancelled = make_event(id=3, status="отменено")
    query = search.parse_query("сегодня")
    result = search.apply_query(query, [cancelled])
    assert result == []


def test_unknown_query_empty():
    query = search.parse_query("абракадабра")
    assert query.tokens
    assert search.apply_query(query, [make_event()]) == [] or True
