"""Быстрый вход по коду из 4 цифр, как в банковском приложении.

Код привязан к устройству: после входа по паролю браузер получает длинный случайный ключ устройства
(хранится в самом браузере), а сервер хранит только отпечаток ключа и хеш кода. Войти по коду можно только
с этого устройства. 5 неверных попыток подряд — код на устройстве удаляется, нужен вход по паролю.
Смена пароля или архив сотрудника отключают все его коды.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime

from . import db
from .services import ApiError

MAX_FAILS = 5
TRIVIAL = {"0000", "1111", "2222", "3333", "4444", "5555", "6666", "7777", "8888", "9999", "1234", "4321", "0123", "9876"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS device_pins (
    device TEXT PRIMARY KEY,               -- sha256 ключа устройства
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    pin_hash TEXT NOT NULL,
    fails INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_used TEXT
);
"""


def init(con):
    con.executescript(SCHEMA)


def _dev(token: str) -> str:
    t = (token or "").strip()
    if len(t) < 32 or len(t) > 128:
        raise ApiError("Устройство не распознано", 400)
    return hashlib.sha256(t.encode()).hexdigest()


def _check_pin_format(pin: str) -> str:
    pin = (pin or "").strip()
    if len(pin) != 4 or not pin.isdigit():
        raise ApiError("Код - 4 цифры")
    return pin


def set_pin(con, user: dict, device_token: str, pin: str):
    pin = _check_pin_format(pin)
    if pin in TRIVIAL:
        raise ApiError("Слишком простой код. Придумайте другой")
    dev = _dev(device_token)
    con.execute("""INSERT INTO device_pins(device, user_id, pin_hash, created_at) VALUES (?,?,?,?)
                   ON CONFLICT(device) DO UPDATE SET user_id=excluded.user_id, pin_hash=excluded.pin_hash, fails=0,
                   created_at=excluded.created_at""",
                (dev, user["id"], db.hash_password(pin + dev), datetime.now().isoformat(timespec="seconds")))


def remove(con, user: dict, device_token: str):
    con.execute("DELETE FROM device_pins WHERE device=? AND user_id=?", (_dev(device_token), user["id"]))


def remove_all(con, user_id: int):
    con.execute("DELETE FROM device_pins WHERE user_id=?", (user_id,))


def hello(con, device_token: str) -> dict | None:
    """Кому принадлежит устройство: для приветствия «Здравствуйте, Юлия!»."""
    try:
        dev = _dev(device_token)
    except ApiError:
        return None
    r = con.execute("""SELECT u.name FROM device_pins p JOIN users u ON u.id=p.user_id WHERE p.device=? AND u.active=1""",
                    (dev,)).fetchone()
    return {"name": r["name"].split()[0]} if r else None


def has_pin(con, user: dict, device_token: str) -> bool:
    try:
        dev = _dev(device_token)
    except ApiError:
        return False
    return bool(con.execute("SELECT 1 FROM device_pins WHERE device=? AND user_id=?", (dev, user["id"])).fetchone())


def login(con, device_token: str, pin: str) -> tuple[dict | None, dict | None]:
    """Проверить код. Возвращает (пользователь, None) или (None, ошибка). Ошибку не бросаем,
    чтобы счётчик неверных попыток сохранился. После MAX_FAILS ошибок код удаляется."""
    dev = _dev(device_token)
    r = con.execute("""SELECT p.*, u.active, u.name, u.role, u.login FROM device_pins p JOIN users u ON u.id=p.user_id
                       WHERE p.device=?""", (dev,)).fetchone()
    if not r or not r["active"]:
        return None, {"error": "Быстрый вход не настроен. Войдите по паролю", "need_password": True}
    pin = (pin or "").strip()
    if len(pin) == 4 and pin.isdigit() and db.check_password(pin + dev, r["pin_hash"]):
        con.execute("UPDATE device_pins SET fails=0, last_used=? WHERE device=?", (datetime.now().isoformat(timespec="seconds"), dev))
        return {"id": r["user_id"], "name": r["name"], "role": r["role"], "login": r["login"]}, None
    fails = r["fails"] + 1
    if fails >= MAX_FAILS:
        con.execute("DELETE FROM device_pins WHERE device=?", (dev,))
        return None, {"error": "Код введён неверно 5 раз. Войдите по паролю", "need_password": True}
    con.execute("UPDATE device_pins SET fails=? WHERE device=?", (fails, dev))
    return None, {"error": f"Неверный код. Осталось попыток: {MAX_FAILS - fails}", "need_password": False}


def new_device_token() -> str:
    return secrets.token_hex(32)
