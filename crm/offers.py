"""Оферты: все редакции по программам (обычные группы, сборная/ПРО и т.п.), с датой редакции и датой вступления в силу.

Ничего не удаляется: новая редакция встаёт поверх, старые остаются в истории. Для каждой программы есть
постоянная ссылка на действующую редакцию (/offers/<программа>.pdf) — её можно давать родителям и ставить на сайт,
а каждая конкретная редакция доступна по своей ссылке (/offers/v<номер>.pdf).
"""
from __future__ import annotations

import base64
import hashlib
import re
from datetime import date, datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
    id INTEGER PRIMARY KEY,
    program TEXT NOT NULL,                 -- «Футболика ПРО (сборная)», «Основные группы»
    slug TEXT NOT NULL,                    -- для постоянной ссылки
    group_ids TEXT NOT NULL DEFAULT '',    -- к каким группам относится, пусто = ко всем
    edition_date TEXT NOT NULL,            -- редакция от
    effective_from TEXT NOT NULL,          -- вступает в силу
    file_name TEXT NOT NULL,
    file_data BLOB NOT NULL,
    sha256 TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    uploaded_by TEXT NOT NULL DEFAULT '',
    uploaded_at TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0
);
"""
_TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r",
                                                   "s", "t", "u", "f", "h", "c", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))
MAX_SIZE = 15 * 1024 * 1024


class OfferError(ValueError):
    pass


def init(con):
    con.executescript(SCHEMA)


def slugify(s: str) -> str:
    out = "".join(_TR.get(c, c) for c in s.lower())
    return re.sub(r"[^a-z0-9]+", "-", out).strip("-")[:60] or "oferta"


def upload(con, data: dict, user: dict) -> int:
    program = (data.get("program") or "").strip()
    if not program:
        raise OfferError("Укажите, к какой программе относится оферта")
    try:
        ed = date.fromisoformat(data.get("edition_date") or "").isoformat()
        eff = date.fromisoformat(data.get("effective_from") or data.get("edition_date") or "").isoformat()
    except ValueError:
        raise OfferError("Укажите дату редакции и дату вступления в силу")
    raw = data.get("file_base64") or ""
    try:
        blob = base64.b64decode(raw.split(",", 1)[-1], validate=False)
    except Exception:
        raise OfferError("Не удалось прочитать файл")
    if not blob.startswith(b"%PDF"):
        raise OfferError("Нужен файл PDF")
    if len(blob) > MAX_SIZE:
        raise OfferError("Файл больше 15 МБ")
    existing = con.execute("SELECT slug FROM offers WHERE program=? LIMIT 1", (program,)).fetchone()
    slug = existing[0] if existing else slugify(program)
    if not existing and con.execute("SELECT 1 FROM offers WHERE slug=?", (slug,)).fetchone():
        slug = f"{slug}-{int(datetime.now().timestamp()) % 10000}"
    gids = ",".join(str(int(x)) for x in data.get("group_ids") or [])
    return con.execute("""INSERT INTO offers(program, slug, group_ids, edition_date, effective_from, file_name, file_data, sha256,
                          note, uploaded_by, uploaded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                       (program, slug, gids, ed, eff, (data.get("file_name") or "oferta.pdf")[:120], blob,
                        hashlib.sha256(blob).hexdigest(), (data.get("note") or "").strip()[:500], user.get("name", ""),
                        datetime.now().isoformat(timespec="minutes"))).lastrowid


def archive(con, oid: int, value: bool = True):
    con.execute("UPDATE offers SET archived=? WHERE id=?", (1 if value else 0, oid))


def current_id(con, slug: str, on: str | None = None) -> int | None:
    on = on or date.today().isoformat()
    r = con.execute("""SELECT id FROM offers WHERE slug=? AND archived=0 AND effective_from<=?
                       ORDER BY effective_from DESC, id DESC LIMIT 1""", (slug, on)).fetchone()
    if not r:  # ещё ни одна не вступила в силу — отдаём ближайшую
        r = con.execute("SELECT id FROM offers WHERE slug=? AND archived=0 ORDER BY effective_from, id LIMIT 1", (slug,)).fetchone()
    return r[0] if r else None


def for_group(con, group_id: int, on: str | None = None) -> dict | None:
    """Какая оферта действует для группы на дату (для карточки ученика и будущих договоров)."""
    on = on or date.today().isoformat()
    rows = con.execute("""SELECT id, program, slug, group_ids, edition_date, effective_from FROM offers
                          WHERE archived=0 AND effective_from<=? ORDER BY effective_from DESC, id DESC""", (on,)).fetchall()
    for r in rows:
        if str(group_id) in r["group_ids"].split(","):
            return dict(r)
    for r in rows:
        if not r["group_ids"]:
            return dict(r)
    return None


def file(con, oid: int):
    r = con.execute("SELECT file_name, file_data FROM offers WHERE id=?", (oid,)).fetchone()
    return (r["file_name"], bytes(r["file_data"])) if r else None


def listing(con, base_url: str = "") -> list[dict]:
    today = date.today().isoformat()
    progs = {}
    groups = {r["id"]: r["name"] for r in con.execute("SELECT id, name FROM groups")}
    for r in con.execute("""SELECT id, program, slug, group_ids, edition_date, effective_from, file_name, sha256, note,
                            uploaded_by, uploaded_at, archived, length(file_data) AS size FROM offers ORDER BY effective_from DESC, id DESC"""):
        p = progs.setdefault(r["slug"], {"program": r["program"], "slug": r["slug"], "versions": [],
                                         "link": f"{base_url}/offers/{r['slug']}.pdf"})
        p["versions"].append(dict(r) | {"groups": [groups.get(int(x), "?") for x in r["group_ids"].split(",") if x],
                                        "link": f"{base_url}/offers/v{r['id']}.pdf", "sha256": r["sha256"][:12]})
    for p in progs.values():
        cur = current_id(con, p["slug"], today)
        for v in p["versions"]:
            v["state"] = ("archived" if v["archived"] else "upcoming" if v["effective_from"] > today
                          else "current" if v["id"] == cur else "past")
    return sorted(progs.values(), key=lambda x: x["program"])
