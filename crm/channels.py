"""Посты в каналы филиалов: Telegram и MAX (Юлия, 02.11).

Руководитель или старший администратор пишет пост (текст и фото), отмечает каналы и публикует сразу или в выбранное
время. Публикует бот школы: в каждом канале он должен быть администратором с правом публиковать.
Каналы CRM находит сама:
  - Telegram: бот видит канал, когда его добавили администратором или в канале вышел пост (обновления my_chat_member
    и channel_post);
  - MAX: список чатов и каналов бота (GET /chats).
Дальше в Настройках → «Каналы филиалов» канал привязывается к филиалу.
Фото отдаётся ботам по секретной ссылке CRM (без входа), иначе Telegram и MAX не смогут его забрать.
Это ручная публикация руководителя: главный выключатель автосообщений её не держит.
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS known_channels (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,                 -- tg | max
    chat_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    seen_at TEXT NOT NULL,
    UNIQUE(kind, chat_id)
);
CREATE TABLE IF NOT EXISTS branch_channels (
    branch_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    PRIMARY KEY(branch_id, kind)
);
CREATE TABLE IF NOT EXISTS channel_posts (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL DEFAULT '',
    photo TEXT NOT NULL DEFAULT '',     -- секретное имя файла фото
    targets TEXT NOT NULL DEFAULT '[]', -- [{kind, chat_id, title}]
    publish_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled',   -- scheduled | sent | partial | failed | cancelled
    results TEXT NOT NULL DEFAULT '[]',
    base_url TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    sent_at TEXT
);
"""
KINDS = {"tg": "Telegram", "max": "MAX"}
TG_CAPTION = 1024


class PostError(Exception):
    pass


def init(con):
    con.executescript(SCHEMA)


def _now() -> str:
    return datetime.now().isoformat(timespec="minutes")


def photo_dir() -> str:
    from .profiles import PHOTO_DIR
    return os.path.join(PHOTO_DIR, "posts")


def photo_path(token: str) -> str | None:
    if not token or not all(c in "0123456789abcdef" for c in token) or len(token) != 32:
        return None
    return os.path.join(photo_dir(), token + ".jpg")


# ---------------- каналы ----------------

def note_tg(con, chat: dict) -> None:
    """Из обновлений Telegram: бот добавлен в канал или в канале вышел пост."""
    if (chat or {}).get("type") != "channel" or not chat.get("id"):
        return
    con.execute("""INSERT INTO known_channels(kind, chat_id, title, seen_at) VALUES ('tg',?,?,?)
                   ON CONFLICT(kind, chat_id) DO UPDATE SET title=excluded.title, seen_at=excluded.seen_at""",
                (str(chat["id"]), chat.get("title") or "", _now()))


def refresh_max(con) -> int:
    """Каналы и чаты, где есть бот MAX."""
    from . import comms
    js = comms.max_call(con, "GET", "chats", query={"count": 100}) or {}
    n = 0
    for c in js.get("chats") or []:
        if c.get("type") not in ("channel", "chat") or not c.get("chat_id"):
            continue
        con.execute("""INSERT INTO known_channels(kind, chat_id, title, seen_at) VALUES ('max',?,?,?)
                       ON CONFLICT(kind, chat_id) DO UPDATE SET title=excluded.title, seen_at=excluded.seen_at""",
                    (str(c["chat_id"]), (c.get("title") or "") + ("" if c.get("type") == "channel" else " (чат)"), _now()))
        n += 1
    return n


def ensure_tg_hook(con, base: str) -> None:
    """Чтобы бот видел каналы, вебхук Telegram должен получать channel_post и my_chat_member."""
    from . import comms
    if comms.get(con, "tg_ok") != "1" or comms.get(con, "tg_upd_v") == "2":
        return
    if not comms.get(con, "tg_relay") and not base:
        return
    try:
        comms.tg_connect(con, base)
        comms.put(con, "tg_upd_v", "2")
    except comms.CommsError:
        pass


def overview(con) -> dict:
    from . import comms
    st = comms.status(con)
    branches = [dict(b) for b in con.execute("SELECT id, name FROM branches ORDER BY name")]
    linked = {(r["branch_id"], r["kind"]): r["chat_id"] for r in con.execute("SELECT * FROM branch_channels")}
    known = [dict(r) for r in con.execute("SELECT kind, chat_id, title FROM known_channels ORDER BY kind, title")]
    for b in branches:
        b["tg"], b["max"] = linked.get((b["id"], "tg"), ""), linked.get((b["id"], "max"), "")
    return {"branches": branches, "known": known, "tg_ok": bool(st.get("telegram")), "max_ok": bool(st.get("max")),
            "tg_bot": comms.get(con, "tg_bot"), "max_bot": comms.get(con, "max_bot")}


def link(con, branch_id: int, kind: str, chat_id: str) -> None:
    if kind not in KINDS:
        raise PostError("Неизвестный мессенджер")
    if not con.execute("SELECT 1 FROM branches WHERE id=?", (branch_id,)).fetchone():
        raise PostError("Филиал не найден")
    if not chat_id:
        con.execute("DELETE FROM branch_channels WHERE branch_id=? AND kind=?", (branch_id, kind))
        return
    if not con.execute("SELECT 1 FROM known_channels WHERE kind=? AND chat_id=?", (kind, str(chat_id))).fetchone():
        raise PostError("Канал не найден: сначала добавьте бота администратором канала")
    con.execute("INSERT OR REPLACE INTO branch_channels(branch_id, kind, chat_id) VALUES (?,?,?)", (branch_id, kind, str(chat_id)))


def targets(con) -> list[dict]:
    """Куда можно опубликовать: каналы, привязанные к филиалам."""
    out = []
    for r in con.execute("""SELECT bc.kind, bc.chat_id, b.name AS branch, k.title FROM branch_channels bc
                            JOIN branches b ON b.id=bc.branch_id
                            LEFT JOIN known_channels k ON k.kind=bc.kind AND k.chat_id=bc.chat_id
                            ORDER BY b.name, bc.kind DESC"""):
        out.append({"key": f"{r['kind']}:{r['chat_id']}", "kind": r["kind"], "chat_id": r["chat_id"],
                    "label": f"{r['branch']}: {KINDS[r['kind']]}", "title": r["title"] or ""})
    return out


# ---------------- посты ----------------

def save_photo(raw: bytes) -> str:
    from .profiles import _kind
    if not raw or len(raw) > 8 * 1024 * 1024 or not _kind(raw):
        raise PostError("Нужна картинка до 8 МБ (JPG или PNG)")
    token = secrets.token_hex(16)
    os.makedirs(photo_dir(), exist_ok=True)
    with open(photo_path(token), "wb") as f:
        f.write(raw)
    return token


def create(con, user: dict, data: dict, base: str) -> dict:
    text = (data.get("text") or "").strip()
    photo = str(data.get("photo") or "")
    if not text and not photo:
        raise PostError("Напишите текст или добавьте фото")
    if len(text) > 4000:
        raise PostError("Текст длиннее 4000 знаков: Telegram такой не примет")
    allowed = {t["key"]: t for t in targets(con)}
    picked = [allowed[k] for k in (data.get("targets") or []) if k in allowed]
    if not picked:
        raise PostError("Отметьте хотя бы один канал")
    when = (data.get("publish_at") or "").strip()
    if when:
        try:
            when = datetime.fromisoformat(when).isoformat(timespec="minutes")
        except ValueError:
            raise PostError("Неверные дата и время")
    pid = con.execute("""INSERT INTO channel_posts(text, photo, targets, publish_at, base_url, created_by, created_at)
                         VALUES (?,?,?,?,?,?,?)""",
                      (text, photo, json.dumps([{k: t[k] for k in ("kind", "chat_id", "label")} for t in picked], ensure_ascii=False),
                       when or _now(), base.rstrip("/"), user.get("name", ""), _now())).lastrowid
    if not when or when <= _now():
        return send(con, pid)
    return post(con, pid)


def post(con, pid: int) -> dict:
    r = con.execute("SELECT * FROM channel_posts WHERE id=?", (pid,)).fetchone()
    if not r:
        raise PostError("Пост не найден")
    d = dict(r)
    d["targets"], d["results"] = json.loads(d["targets"]), json.loads(d["results"])
    return d


def _send_one(con, t: dict, text: str, photo_url: str) -> str:
    from . import comms
    if t["kind"] == "tg":
        if photo_url:
            cap = text if len(text) <= TG_CAPTION else ""
            comms.tg_call(con, "sendPhoto", {"chat_id": t["chat_id"], "photo": photo_url, **({"caption": cap} if cap else {})})
            if text and not cap:
                comms.tg_call(con, "sendMessage", {"chat_id": t["chat_id"], "text": text})
        else:
            comms.tg_call(con, "sendMessage", {"chat_id": t["chat_id"], "text": text})
    else:
        body = {"text": text}
        if photo_url:
            body["attachments"] = [{"type": "image", "payload": {"url": photo_url}}]
        comms.max_call(con, "POST", "messages", query={"chat_id": t["chat_id"]}, data=body)
    return ""


def send(con, pid: int) -> dict:
    from . import comms
    p = post(con, pid)
    if p["status"] != "scheduled":
        return p
    url = f"{p['base_url']}/api/post-photo?t={p['photo']}" if p["photo"] else ""
    res, ok = [], 0
    for t in p["targets"]:
        try:
            _send_one(con, t, p["text"], url)
            res.append({"label": t["label"], "ok": True})
            ok += 1
        except comms.CommsError as e:
            res.append({"label": t["label"], "ok": False, "error": str(e)})
    status = "sent" if ok == len(res) else ("partial" if ok else "failed")
    con.execute("UPDATE channel_posts SET status=?, results=?, sent_at=? WHERE id=?",
                (status, json.dumps(res, ensure_ascii=False), _now(), pid))
    return post(con, pid)


def cancel(con, pid: int) -> None:
    con.execute("UPDATE channel_posts SET status='cancelled' WHERE id=? AND status='scheduled'", (pid,))


def history(con, limit: int = 30) -> list[dict]:
    return [post(con, r[0]) for r in con.execute("SELECT id FROM channel_posts ORDER BY id DESC LIMIT ?", (limit,))]


def tick(con) -> int:
    """Запланированные посты, время которых пришло."""
    n = 0
    for (pid,) in con.execute("SELECT id FROM channel_posts WHERE status='scheduled' AND publish_at <= ?", (_now(),)).fetchall():
        send(con, pid)
        n += 1
    ensure_tg_hook(con, "")
    return n
