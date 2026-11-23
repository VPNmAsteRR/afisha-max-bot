"""Проверка сценариев бота с подменённым API (без сети)."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="afisha-flow-"))
os.environ.setdefault("ADMIN_IDS", "42,43,44")

import pytest  # noqa: E402

from app import dates, ui  # noqa: E402
from app.bot import Bot, Incoming  # noqa: E402
from app.models import Event  # noqa: E402
from app.storage import storage  # noqa: E402


class FakeApi:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.answered: list[dict] = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return {"ok": True}

    async def answer_callback(self, callback_id, **kwargs):
        self.answered.append({"id": callback_id, **kwargs})
        return {"ok": True}


@pytest.fixture(autouse=True)
def clean_storage():
    storage._conn.execute("DELETE FROM events")
    storage._conn.execute("DELETE FROM dialogs")
    storage._conn.commit()
    yield


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def seed():
    today = dates.today_local()
    events = [
        Event(1, "Концерт", today, __import__("datetime").time(18, 0), "с. Казанское", "ДК",
              "культура", True, "6+", "Бесплатно", "Музыка", "опубликовано"),
        Event(2, "Матч", today, __import__("datetime").time(11, 0), "с. Пировское", "Стадион",
              "спорт", False, "18+", "300", "Футбол", "опубликовано"),
        Event(3, "Отменённое", today, __import__("datetime").time(15, 0), "с. Пировское", "Клуб",
              "культура", True, "0+", "По записи", "Не придёт", "отменено"),
    ]
    storage.replace_events(events)


def make_bot() -> tuple[Bot, FakeApi]:
    bot = Bot()
    fake = FakeApi()
    bot.api = fake
    return bot, fake


def incoming(text="", payload="", admin=False, callback=False) -> Incoming:
    return Incoming(
        user_id="42" if admin else "777",
        chat_id="42" if admin else "777",
        name="Тест",
        text=text,
        payload=payload,
        callback_id="cb-1" if callback else "",
        is_callback=callback,
        is_start=False,
    )


def test_start_greeting_has_four_buttons():
    bot, fake = make_bot()
    run(bot.handle(incoming("/start")))
    assert fake.sent[0]["text"] == ui.GREETING
    labels = [b["text"] for row in fake.sent[0]["keyboard"] for b in row]
    assert labels == ["Афиша на неделю", "Семейный досуг", "Помощь", "Связаться с нами"]


def test_week_catalog_hides_cancelled_and_paginates():
    seed()
    bot, fake = make_bot()
    run(bot.handle(incoming(payload="catalog|cat=all|page=0", callback=True)))
    assert fake.sent
    text = fake.sent[0]["text"]
    assert "Концерт" in text
    assert "Отменённое" not in text
    assert "Всего событий: 2" in text


def test_family_filter_excludes_adults():
    seed()
    bot, fake = make_bot()
    run(bot.handle(incoming(payload="age|name=child", callback=True)))
    text = fake.sent[0]["text"]
    assert "Концерт" in text
    assert "Матч" not in text


def test_free_text_saturday():
    seed()
    bot, fake = make_bot()
    run(bot.handle(incoming("Что будет в субботу?")))
    assert fake.sent
    assert "не понял" in fake.sent[0]["text"].lower()


def test_unknown_query_returns_menu():
    bot, fake = make_bot()
    run(bot.handle(incoming("абракадабра зюзюка")))
    assert "не понял" in fake.sent[0]["text"].lower()
    labels = [b["text"] for row in fake.sent[0]["keyboard"] for b in row]
    assert "Афиша на неделю" in labels


def test_admin_denied_for_stranger():
    bot, fake = make_bot()
    run(bot.handle(incoming("/list")))
    assert fake.sent[0]["text"] == ui.DENIED_TEXT


def test_admin_add_flow_creates_event():
    bot, fake = make_bot()
    steps = [
        "/add",
        "Новое событие",
        (dates.today_local()).strftime("%d.%m.%Y"),
        "19:00",
        "с. Казачинское",
        "Дом культуры",
        "культура",
        "да",
        "6+",
        "Бесплатно",
        "Интересное событие",
        "8-900-000-00-00",
    ]
    for step in steps:
        run(bot.handle(incoming(step, admin=True)))
    assert storage.count() == 1
    event = storage.events()[0]
    assert event.title == "Новое событие"
    assert event.status == "опубликовано"


def test_admin_cancel_add():
    bot, fake = make_bot()
    run(bot.handle(incoming("/add", admin=True)))
    run(bot.handle(incoming(payload="admin:cancel", admin=True, callback=True)))
    assert storage.count() == 0


def test_admin_delete_marks_cancelled():
    seed()
    bot, fake = make_bot()
    run(bot.handle(incoming(payload="admin:delete|id=1", admin=True, callback=True)))
    event = storage.event(1)
    assert event is not None
    assert event.status == "отменено"
