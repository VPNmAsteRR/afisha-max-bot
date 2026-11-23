"""Демо-режим: наполнение базы тестовыми событиями (этап 1 — прототип).

Запуск: python -m app.seed --days 7
"""

from __future__ import annotations

import argparse
from datetime import time, timedelta

from . import dates
from .models import Event
from .storage import storage

DEMO = [
    ("Мастер-класс «Осенний пейзаж»", time(12, 0), "с. Казачинское", "Дом культуры, ул. Советская, 12",
     "культура", True, "6+", "Бесплатно", "Рисуем гуашью, материалы выдаём на месте.", "8-900-000-00-00"),
    ("Выставка «Родные просторы»", time(10, 0), "с. Казачинское", "Краеведческий музей, ул. Ленина, 5",
     "культура", True, "0+", "150 ₽", "Фотографии природы округа, работает до конца месяца.", ""),
    ("Турнир по мини-футболу", time(11, 0), "с. Пировское", "Стадион, ул. Спортивная, 1",
     "спорт", False, "12+", "Бесплатно", "Соревнования среди школьных команд.", ""),
    ("Концерт ко Дню народного единства", time(18, 0), "с. Казачинское", "Клуб «Юность», ул. Мира, 3",
     "культура", True, "6+", "По записи", "Выступление местных коллективов и ансамблей.", "8-900-111-22-33"),
    ("Семейная игротека", time(15, 0), "с. Пировское", "Библиотека, ул. Школьная, 8",
     "массовое", True, "6+", "Бесплатно", "Настольные игры для детей и родителей.", ""),
    ("Лекция «История села»", time(17, 30), "с. Казачинское", "Библиотека, ул. Советская, 20",
     "культура", False, "16+", "Бесплатно", "Краеведческий рассказ об основании села.", ""),
    ("Ярмарка выходного дня", time(9, 0), "с. Казачинское", "Площадь у администрации",
     "массовое", True, "0+", "Бесплатно", "Фермерская продукция, ремёсла, угощения.", ""),
    ("Вечер отдыха 18+", time(21, 0), "с. Пировское", "Кафе «Огонёк», ул. Центральная, 2",
     "массовое", False, "18+", "500 ₽", "Музыка, танцы, живой звук.", ""),
]


def seed(days: int = 7) -> int:
    today = dates.today_local()
    events: list[Event] = []
    event_id = 101
    for offset in range(days):
        day = today + timedelta(days=offset)
        for title, moment, settlement, place, category, family, age, price, description, contact in DEMO:
            if offset % 3 == 2 and category != "культура":
                continue
            events.append(
                Event(
                    id=event_id,
                    title=title,
                    date=day,
                    time=moment,
                    settlement=settlement,
                    place=place,
                    category=category,
                    family=family,
                    age=age,
                    price=price,
                    description=description,
                    contact=contact,
                    status="опубликовано",
                    source="demo",
                )
            )
            event_id += 1
    storage.replace_events(events, source="demo")
    storage.log(action="seed", details=f"добавлено событий: {len(events)}")
    return len(events)


def main() -> None:
    parser = argparse.ArgumentParser(description="Демо-данные для прототипа")
    parser.add_argument("--days", type=int, default=7, help="на сколько дней вперёд заполнить")
    args = parser.parse_args()
    count = seed(args.days)
    print(f"Добавлено событий: {count}")


if __name__ == "__main__":
    main()
