"""Распознавание свободных вопросов (п. 3.1, сценарий В).

Разбираем дни недели (включая сокращения и опечатки), «сегодня/завтра»,
«бесплатно», категории и слова про детей.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from difflib import SequenceMatcher

from . import dates
from .models import CATEGORY_ALIASES, Event

WEEKDAY_WORDS: dict[int, tuple[str, ...]] = {
    0: ("понедельник", "понед", "пон", "пн", "понедельникам"),
    1: ("вторник", "вторн", "вт", "вторникам"),
    2: ("среда", "среду", "сред", "ср", "средам"),
    3: ("четверг", "четв", "чт", "четвергам"),
    4: ("пятница", "пятницу", "пятн", "пт", "пятницам"),
    5: ("суббота", "субботу", "субб", "сб", "субботам"),
    6: ("воскресенье", "воскресение", "воскр", "вс", "воскресеньям"),
}

RELATIVE_TODAY = ("сегодня", "севодня", "исёдня", "седня", "нынче")
RELATIVE_TOMORROW = ("завтра", "завтро", "зафтра")
RELATIVE_WEEKEND = ("выходные", "выходных", "на выходных")
RELATIVE_NEXT_WEEK = ("следующая неделя", "следующей неделе", "на следующей неделе")
RELATIVE_THIS_WEEK = ("эта неделя", "этой неделе", "на этой неделе", "неделя", "неделю")

FREE_WORDS = ("бесплатн", "бесплатно", "даром", "без денег", "свободный вход", "free")
PAID_WORDS = ("платн", "платно", "за деньги", "стоимость", "цена")
KIDS_WORDS = ("ребен", "ребён", "дет", "дит", "семь", "семей", "школьник", "малыш")
FAMILY_WORDS = ("семь", "семей", "с детьми", "ребен", "ребён")
CANCEL_WORDS = ("отмен", "перенес", "перенесён")


@dataclass(slots=True)
class Query:
    """Разобранный запрос пользователя."""

    raw: str = ""
    dates: set[date] = field(default_factory=set)
    categories: set[str] = field(default_factory=set)
    free_only: bool = False
    family_only: bool = False
    kids_only: bool = False
    week_only: bool = False
    empty_slots: bool = False
    settlement: str = ""
    tokens: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (
            self.dates
            or self.categories
            or self.free_only
            or self.family_only
            or self.kids_only
            or self.week_only
            or self.settlement
            or self.tokens
        )


def _normalize(text: str) -> str:
    text = (text or "").lower().replace("ё", "е")
    text = re.sub(r"[^0-9a-zа-я\s:.\-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _similar(token: str, sample: str) -> bool:
    if token == sample:
        return True
    if len(token) < 4 or len(sample) < 4:
        return False
    return SequenceMatcher(a=token, b=sample).ratio() >= 0.8


def _match_weekday(word: str) -> int | None:
    for index, variants in WEEKDAY_WORDS.items():
        for variant in variants:
            if word == variant or _similar(word, variant):
                return index
    return None


def _match_category(word: str) -> str | None:
    """Категория по слову с учётом словоформ (выставка/выставки/выставок)."""
    if word in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[word]
    for alias, category in CATEGORY_ALIASES.items():
        stem = alias[: max(4, len(alias) - 2)]
        if len(stem) >= 4 and word.startswith(stem):
            return category
    return None


def parse_query(text: str) -> Query:
    normalized = _normalize(text)
    query = Query(raw=(text or "").strip())
    words = normalized.split()
    today = dates.today_local()
    monday, sunday = dates.week_bounds(today)

    # Даты в формате ДД.ММ / ДД.ММ.ГГГГ
    for match in re.finditer(r"\b(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?\b", normalized):
        day, month = int(match.group(1)), int(match.group(2))
        year = int(match.group(3)) if match.group(3) else today.year
        if year < 100:
            year += 2000
        try:
            query.dates.add(date(year, month, day))
        except ValueError:
            pass

    for word in words:
        weekday = _match_weekday(word)
        if weekday is not None:
            offset = (weekday - today.weekday()) % 7
            query.dates.add(today + timedelta(days=offset))
            continue
        if word in RELATIVE_TODAY or _similar(word, "сегодня"):
            query.dates.add(today)
            continue
        if word in RELATIVE_TOMORROW or _similar(word, "завтра"):
            query.dates.add(today + timedelta(days=1))
            continue
        if word.startswith("бесплат"):
            query.free_only = True
            continue
        if word in {"платно", "платный"}:
            continue
        if word in KIDS_WORDS or word.startswith("дет") or word.startswith("ребен") or word.startswith("малыш"):
            query.kids_only = True
            continue
        if word in FAMILY_WORDS or word.startswith("семей") or word.startswith("семь"):
            query.family_only = True
            continue
        if word in {"отменено", "отменен", "перенесено"}:
            query.empty_slots = True
            continue
        category = _match_category(word)
        if category:
            query.categories.add(category)

    normalized_no_space = normalized
    if any(phrase in normalized_no_space for phrase in RELATIVE_WEEKEND):
        query.dates.update({sunday - timedelta(days=1), sunday})
    if any(phrase in normalized_no_space for phrase in RELATIVE_NEXT_WEEK):
        start, end = dates.next_week_bounds(today)
        current = start
        while current <= end:
            query.dates.add(current)
            current += timedelta(days=1)
    if any(phrase in normalized_no_space for phrase in RELATIVE_THIS_WEEK):
        query.week_only = True
    if any(word.startswith("бесплат") for word in words) or any(
        phrase in normalized_no_space for phrase in FREE_WORDS
    ):
        query.free_only = True

    # Населённый пункт
    for settlement in ("казачинское", "пировское"):
        if settlement in normalized_no_space:
            query.settlement = settlement

    # Свободный поиск по названию и описанию
    stop = {
        "что", "где", "когда", "будет", "есть", "какие", "какой", "какая", "куда",
        "сходить", "можно", "пойти", "афиша", "афише", "события", "событие",
        "мероприятие", "мероприятия", "на", "в", "во", "с", "и", "или", "по",
        "мне", "нам", "хочу", "хотим", "подскажите", "подскажи", "сегодня",
        "завтра", "неделя", "неделе", "неделю", "этой", "эта", "выходные",
        "выходных", "дети", "детям", "ребенку", "ребенка", "семьей", "семьёй",
        "бесплатное", "бесплатные", "бесплатно", "платно", "цена", "стоимость",
        "показать", "покажи", "список", "интересно", "чем", "заняться",
    }
    query.tokens = [
        word
        for word in words
        if len(word) >= 3 and word not in stop and not word.isdigit()
        and _match_weekday(word) is None
    ][:6]
    return query


def _tokens_match(event: Event, tokens: list[str]) -> bool:
    haystack = " ".join(
        [event.title, event.description, event.place, event.settlement, event.category]
    ).lower().replace("ё", "е")
    found = 0
    for token in tokens:
        stem = token[: max(4, len(token) - 2)]
        if stem in haystack or token in haystack:
            found += 1
    return found > 0


def apply_query(query: Query, events: list[Event]) -> list[Event]:
    """Фильтрует активные события по разобранному запросу."""
    result = [event for event in events if event.is_active]

    if query.dates:
        result = [event for event in result if event.date in query.dates]
    else:
        # Без явной даты показываем текущую и следующую неделю, начиная с сегодня
        today = dates.today_local()
        _, sunday_next = dates.next_week_bounds(today)
        result = [event for event in result if today <= event.date <= sunday_next]

    if query.categories:
        result = [event for event in result if event.category in query.categories]
    if query.free_only:
        result = [event for event in result if event.is_free]
    if query.family_only or query.kids_only:
        result = [event for event in result if event.family and event.age != "18+"]
    if query.settlement:
        result = [event for event in result if query.settlement in event.settlement.lower()]

    if query.tokens:
        matched = [event for event in result if _tokens_match(event, query.tokens)]
        if matched:
            result = matched
        elif query.dates or query.categories or query.free_only or query.family_only or query.kids_only:
            pass  # остальные фильтры уже дали осмысленную выборку
        else:
            result = []

    result.sort(key=lambda event: event.key())
    return result


def describe_hints() -> str:
    return (
        "Например: «Что будет в субботу?», «Есть что-то бесплатное?», "
        "«Какие выставки?», «Куда сходить с ребёнком?»"
    )
