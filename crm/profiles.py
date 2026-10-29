"""Фото и анкета сотрудника: аватарка в CRM, карточка тренера (образование, стаж, достижения, слова о школе).

Фото хранится файлом рядом с базой (data/photos/<id>.jpg), в базе — только отметка, что фото есть, и время,
чтобы браузер обновлял картинку после замены. Загрузить можно файлом из CRM или по ссылке с сайта Футболики.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from .db import DB_PATH
from .services import ApiError

PHOTO_DIR = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "photos")
MAX_PHOTO = 4 * 1024 * 1024
ALLOWED_HOSTS = ("https://msk.footbolika.ru/", "https://footbolika.ru/")
BIO_FIELDS = {"nickname": "Прозвище (для аватарки)", "sticker": "Наклейка на аватарке (один эмодзи)", "education": "Образование", "experience": "Стаж", "achievements": "Спортивные заслуги",
              "quote_title": "Заголовок слов о школе", "quote": "Слова о школе", "branches": "Филиалы", "site_url": "Страница на сайте"}


def init(con):
    cols = {r[1] for r in con.execute("PRAGMA table_info(users)")}
    if "phone" not in cols:
        con.execute("ALTER TABLE users ADD COLUMN phone TEXT NOT NULL DEFAULT ''")
    if "photo_at" not in cols:
        con.execute("ALTER TABLE users ADD COLUMN photo_at TEXT NOT NULL DEFAULT ''")
    if "bio" not in cols:
        con.execute("ALTER TABLE users ADD COLUMN bio TEXT NOT NULL DEFAULT '{}'")
    if "photo_sq" not in cols:  # 1 - фото уже обрезано по лицу при загрузке (квадрат), 0 - фото во весь рост с сайта
        con.execute("ALTER TABLE users ADD COLUMN photo_sq INTEGER NOT NULL DEFAULT 0")


def _path(uid: int) -> str:
    return os.path.join(PHOTO_DIR, f"{int(uid)}.jpg")


def _kind(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def save_photo(con, uid: int, data: bytes, square: bool = False):
    if not data or len(data) > MAX_PHOTO:
        raise ApiError("Фото до 4 МБ")
    if not _kind(data):
        raise ApiError("Нужна картинка JPG, PNG или WebP")
    os.makedirs(PHOTO_DIR, exist_ok=True)
    with open(_path(uid), "wb") as f:
        f.write(data)
    con.execute("UPDATE users SET photo_at=?, photo_sq=? WHERE id=?", (datetime.now().isoformat(timespec="seconds"), 1 if square else 0, uid))


def photo_from_url(con, uid: int, url: str):
    """Скачать фото с сайта Футболики (только оттуда: чтобы сервер не ходил по чужим ссылкам)."""
    from . import comms
    if not any(url.startswith(h) for h in ALLOWED_HOSTS):
        raise ApiError("Фото можно взять только с сайта Футболики")
    code, data, _ = comms._http("GET", url, raw=True, timeout=20)
    if code != 200:
        raise ApiError(f"Сайт не отдал фото ({code})")
    save_photo(con, uid, data)


def remove_photo(con, uid: int):
    try:
        os.remove(_path(uid))
    except FileNotFoundError:
        pass
    con.execute("UPDATE users SET photo_at='' WHERE id=?", (uid,))


def read_photo(uid: int) -> tuple[bytes, str] | None:
    try:
        with open(_path(uid), "rb") as f:
            data = f.read()
    except FileNotFoundError:
        return None
    return data, _kind(data) or "image/jpeg"


def save_bio(con, uid: int, bio: dict):
    cur = get_bio(con, uid)
    for k, v in (bio or {}).items():
        if k in BIO_FIELDS:
            cur[k] = " ".join(str(v or "").split())[:2000] if k != "quote" else str(v or "").strip()[:3000]
    con.execute("UPDATE users SET bio=? WHERE id=?", (json.dumps(cur, ensure_ascii=False), uid))


def get_bio(con, uid: int) -> dict:
    r = con.execute("SELECT bio FROM users WHERE id=?", (uid,)).fetchone()
    try:
        return json.loads(r[0]) if r and r[0] else {}
    except ValueError:
        return {}


def profile(con, uid: int) -> dict:
    r = con.execute("SELECT id, name, role, phone, tg_username, photo_at FROM users WHERE id=?", (uid,)).fetchone()
    if not r:
        raise ApiError("Сотрудник не найден", 404)
    groups = [dict(g) for g in con.execute(
        """SELECT g.id, g.name, b.name AS branch, CASE WHEN g.coach_id=? THEN 'главный' ELSE 'помощник' END AS role
           FROM groups g JOIN branches b ON b.id=g.branch_id WHERE g.active=1 AND (g.coach_id=? OR g.assistant_id=?)
           ORDER BY b.id, g.name""", (uid, uid, uid))]
    return dict(r) | {"bio": get_bio(con, uid), "fields": BIO_FIELDS, "groups": groups}
