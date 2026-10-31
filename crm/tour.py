"""Обучение при первом входе: экскурсия по разделам своей должности и 3 вопроса.

Тексты и вопросы лежат в static/tour.js, здесь только отметка «прошёл». Владелец обучение не проходит.
Если обучение сильно поменяется, поднимаем VERSION: все пройдут его заново.
"""
from __future__ import annotations

from datetime import datetime

VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS staff_tour (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    version INTEGER NOT NULL,
    done_at TEXT NOT NULL
);
"""


def init(con):
    con.executescript(SCHEMA)


def done_at(con, uid: int):
    r = con.execute("SELECT done_at FROM staff_tour WHERE user_id=? AND version>=?", (uid, VERSION)).fetchone()
    return r[0] if r else None


def pending(con, user: dict) -> bool:
    from . import staffdocs
    if user["id"] == staffdocs.owner_id(con):
        return False
    return done_at(con, user["id"]) is None


def finish(con, user: dict) -> str:
    now = datetime.now().isoformat(timespec="seconds")
    con.execute("INSERT INTO staff_tour(user_id, version, done_at) VALUES (?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET version=excluded.version, done_at=excluded.done_at", (user["id"], VERSION, now))
    return now
