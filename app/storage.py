"""SQLite-хранилище: кэш афиши, подписчики, состояния диалогов, журнал (п. 3.2–3.4)."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from .config import settings
from .models import Event

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    title       TEXT NOT NULL,
    date        TEXT NOT NULL,      -- ISO YYYY-MM-DD
    time        TEXT NOT NULL,      -- HH:MM
    settlement  TEXT NOT NULL,
    place       TEXT NOT NULL,
    category    TEXT NOT NULL,
    family      INTEGER NOT NULL DEFAULT 0,
    age         TEXT NOT NULL,
    price       TEXT NOT NULL,
    description TEXT NOT NULL,
    contact     TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'опубликовано',
    source      TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_events_date ON events(date);
CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);

CREATE TABLE IF NOT EXISTS subscribers (
    user_id     TEXT PRIMARY KEY,
    chat_id     TEXT NOT NULL DEFAULT '',
    name        TEXT NOT NULL DEFAULT '',
    subscribed  INTEGER NOT NULL DEFAULT 1,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dialogs (
    user_id     TEXT PRIMARY KEY,
    state       TEXT NOT NULL DEFAULT '',
    payload     TEXT NOT NULL DEFAULT '{}',
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id    TEXT NOT NULL DEFAULT '',
    action      TEXT NOT NULL,
    target      TEXT NOT NULL DEFAULT '',
    details     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Storage:
    """Тонкая обёртка над SQLite с блокировкой на запись."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or settings.db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # --- Служебное ---
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def meta_get(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def meta_set(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._conn.commit()

    # --- События ---
    def replace_events(self, events: Iterable[Event], source: str = "sheet") -> None:
        """Полная синхронизация кэша с источником."""
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock:
            self._conn.execute("BEGIN")
            self._conn.execute("DELETE FROM events")
            self._conn.executemany(
                "INSERT INTO events(id,title,date,time,settlement,place,category,family,age,price,"
                "description,contact,status,source,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        e.id, e.title, e.date.isoformat(), e.time.strftime("%H:%M"),
                        e.settlement, e.place, e.category, int(e.family), e.age, e.price,
                        e.description, e.contact, e.status, e.source or source, stamp,
                    )
                    for e in events
                ],
            )
            self._conn.commit()
        self.meta_set("last_sync", stamp)

    def upsert_event(self, event: Event, source: str = "admin") -> None:
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock:
            self._conn.execute(
                "INSERT INTO events(id,title,date,time,settlement,place,category,family,age,price,"
                "description,contact,status,source,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET title=excluded.title, date=excluded.date, "
                "time=excluded.time, settlement=excluded.settlement, place=excluded.place, "
                "category=excluded.category, family=excluded.family, age=excluded.age, "
                "price=excluded.price, description=excluded.description, contact=excluded.contact, "
                "status=excluded.status, source=excluded.source, updated_at=excluded.updated_at",
                (
                    event.id, event.title, event.date.isoformat(), event.time.strftime("%H:%M"),
                    event.settlement, event.place, event.category, int(event.family), event.age,
                    event.price, event.description, event.contact, event.status,
                    event.source or source, stamp,
                ),
            )
            self._conn.commit()

    def delete_event(self, event_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM events WHERE id=?", (event_id,))
            self._conn.commit()
        return cur.rowcount > 0

    def events(self) -> list[Event]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM events").fetchall()
        return [self._row_to_event(row) for row in rows]

    def event(self, event_id: int) -> Event | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return self._row_to_event(row) if row else None

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()
        return int(row["c"]) if row else 0

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> Event:
        from datetime import date, time

        y, m, d = (int(part) for part in row["date"].split("-"))
        hh, mm = (int(part) for part in row["time"].split(":"))
        return Event(
            id=int(row["id"]),
            title=row["title"],
            date=date(y, m, d),
            time=time(hh, mm),
            settlement=row["settlement"],
            place=row["place"],
            category=row["category"],
            family=bool(row["family"]),
            age=row["age"],
            price=row["price"],
            description=row["description"],
            contact=row["contact"],
            status=row["status"],
            source=row["source"],
        )

    # --- Подписчики (п. 3.4) ---
    def touch_subscriber(self, user_id: str, chat_id: str = "", name: str = "") -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock:
            self._conn.execute(
                "INSERT INTO subscribers(user_id, chat_id, name, subscribed, first_seen, last_seen) "
                "VALUES(?, ?, ?, 1, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET chat_id=excluded.chat_id, "
                "name=CASE WHEN excluded.name<>'' THEN excluded.name ELSE subscribers.name END, "
                "last_seen=excluded.last_seen",
                (str(user_id), str(chat_id), name, now, now),
            )
            self._conn.commit()

    def set_subscription(self, user_id: str, subscribed: bool) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE subscribers SET subscribed=? WHERE user_id=?", (int(subscribed), str(user_id))
            )
            self._conn.commit()

    def subscribers(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM subscribers WHERE subscribed=1"
            ).fetchall()
        return [dict(row) for row in rows]

    def subscriber_stats(self) -> tuple[int, int]:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) AS c FROM subscribers").fetchone()["c"]
            active = self._conn.execute(
                "SELECT COUNT(*) AS c FROM subscribers WHERE subscribed=1"
            ).fetchone()["c"]
        return int(total), int(active)

    # --- Состояния диалогов ---
    def get_dialog(self, user_id: str) -> tuple[str, dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT state, payload FROM dialogs WHERE user_id=?", (str(user_id),)
            ).fetchone()
        if not row:
            return "", {}
        try:
            payload = json.loads(row["payload"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        return row["state"], payload

    def set_dialog(self, user_id: str, state: str, payload: dict[str, Any] | None = None) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock:
            self._conn.execute(
                "INSERT INTO dialogs(user_id, state, payload, updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET state=excluded.state, "
                "payload=excluded.payload, updated_at=excluded.updated_at",
                (str(user_id), state, json.dumps(payload or {}, ensure_ascii=False), now),
            )
            self._conn.commit()

    def clear_dialog(self, user_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM dialogs WHERE user_id=?", (str(user_id),))
            self._conn.commit()

    # --- Журнал (п. 3.3) ---
    def log(self, action: str, actor_id: str = "", target: str = "", details: str = "") -> None:
        now = datetime.now(tz=settings.tz).isoformat(timespec="seconds")
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit(actor_id, action, target, details, created_at) "
                "VALUES(?,?,?,?,?)",
                (str(actor_id), action, str(target), details, now),
            )
            self._conn.commit()

    def recent_log(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        events = self.events()
        total, active = self.subscriber_stats()
        return {
            "events": len(events),
            "published": sum(1 for e in events if e.is_active),
            "cancelled": sum(1 for e in events if e.is_cancelled),
            "subscribers": total,
            "active_subscribers": active,
            "last_sync": self.meta_get("last_sync", "—"),
        }


storage = Storage()
