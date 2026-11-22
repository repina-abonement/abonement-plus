"""Рекламные каналы и источники лидов + отчёт: сколько лидов, пробных и покупок дал каждый."""
from __future__ import annotations

from datetime import datetime
import json
import re

DEFAULT_CHANNELS = ["Вконтакте", "Инстаграм", "Промоутер", "Промо-акция", "Сайт", "Сарафан", "Филиал", "Другое"]
NIKITA_CHANNEL = "Промоутер"
NIKITA_SOURCE = "Промоутер Никита Евсюнин"
NIKITA_MERGE_SETTING = "source_alias_nikita_evsyunin_v1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS ad_channels (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS ad_sources (
    id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL REFERENCES ad_channels(id),
    name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE(channel_id, name)
);
"""


def init(con):
    con.executescript(SCHEMA)
    if not con.execute("SELECT 1 FROM settings WHERE key='ad_channels_seeded'").fetchone():
        if not con.execute('SELECT 1 FROM ad_channels').fetchone():
            for n in DEFAULT_CHANNELS:
                con.execute("INSERT OR IGNORE INTO ad_channels(name) VALUES (?)", (n,))
        con.execute("INSERT INTO settings(key,value) VALUES ('ad_channels_seeded','1')")
    cols = {r[1] for r in con.execute("PRAGMA table_info(students)")}
    if "source_id" not in cols:
        con.execute("ALTER TABLE students ADD COLUMN source_id INTEGER REFERENCES ad_sources(id)")
    if not con.execute("SELECT 1 FROM ad_sources").fetchone():
        site = (con.execute("SELECT id FROM ad_channels WHERE name='Сайт'").fetchone() or con.execute("SELECT id FROM ad_channels ORDER BY id LIMIT 1").fetchone())[0]
        con.execute("INSERT INTO ad_sources(channel_id, name, created_at) VALUES (?,?,?)", (site, "Заявка с сайта", _now()))
    if not con.execute("SELECT 1 FROM settings WHERE key=?", (NIKITA_MERGE_SETTING,)).fetchone():
        merge_nikita_sources(con)
        con.execute("INSERT INTO settings(key,value) VALUES (?,?)", (NIKITA_MERGE_SETTING, _now()))


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat(timespec="minutes")


def _source_norm(value: str) -> str:
    return " ".join(re.sub(r"[^a-zа-я0-9]+", " ", str(value or "").casefold().replace("ё", "е")).split())


def is_nikita_evsyunin(value: str) -> bool:
    """Only explicit Nikita/Evsyunin aliases; never every generic promoter."""
    normalized = _source_norm(value)
    if not re.search(r"\bникит[аы]?\b", normalized):
        return False
    if re.search(r"\bевс[юуо]нин[а-я]*\b", normalized):
        return True
    return bool(re.fullmatch(r"(?:пром|промо|промоутер) никит[аы]?(?: \d{1,4})*", normalized))


def canonical_source(channel: str, name: str) -> tuple[str, str]:
    if is_nikita_evsyunin(name) or is_nikita_evsyunin(f"{channel} {name}"):
        return NIKITA_CHANNEL, NIKITA_SOURCE
    return (str(channel or "").strip() or "Другое", str(name or "").strip())


def merge_nikita_sources(con) -> dict:
    """One exact, idempotent source for existing cards; archive only proven aliases."""
    canonical_id = ensure_source(con, NIKITA_CHANNEL, NIKITA_SOURCE)
    alias_ids = {row["id"] for row in con.execute("SELECT id,name FROM ad_sources")
                 if row["id"] != canonical_id and is_nikita_evsyunin(row["name"])}
    changed = []
    for row in con.execute("""SELECT s.id,s.source,s.source_id,a.name AS linked_name
        FROM students s LEFT JOIN ad_sources a ON a.id=s.source_id"""):
        if not (is_nikita_evsyunin(row["source"]) or is_nikita_evsyunin(row["linked_name"])):
            continue
        if row["source_id"] == canonical_id and row["source"] == NIKITA_SOURCE:
            continue
        before = row["linked_name"] or row["source"] or "Без источника"
        con.execute("UPDATE students SET source_id=?,source=? WHERE id=?", (canonical_id, NIKITA_SOURCE, row["id"]))
        con.execute("INSERT INTO history(student_id,ts,user_name,kind,text) VALUES (?,?,?,?,?)",
                    (row["id"], _now(), "CRM", "system", f"Рекламный источник объединён: «{before}» → «{NIKITA_SOURCE}»"))
        changed.append({"student_id": row["id"], "before": before})
    if alias_ids:
        marks = ",".join("?" for _ in alias_ids)
        con.execute(f"UPDATE ad_sources SET active=0 WHERE id IN ({marks})", tuple(sorted(alias_ids)))
    if changed or alias_ids:
        con.execute("INSERT INTO audit(ts,user_id,action,detail) VALUES (?,NULL,?,?)",
                    (_now(), "sources.merge_nikita_evsyunin", json.dumps({"changed": len(changed), "archived_source_ids": sorted(alias_ids)}, ensure_ascii=False)))
    return {"changed": len(changed), "canonical_source_id": canonical_id, "archived_source_ids": sorted(alias_ids)}


def listing(con, active_only: bool = True) -> dict:
    ch = [dict(r) for r in con.execute("SELECT * FROM ad_channels" + (" WHERE active=1" if active_only else "") + " ORDER BY id")]
    src = [dict(r) for r in con.execute(
        "SELECT s.*, c.name AS channel FROM ad_sources s JOIN ad_channels c ON c.id=s.channel_id"
        + (" WHERE s.active=1 AND c.active=1" if active_only else "") + " ORDER BY c.id, s.name")]
    return {"channels": ch, "sources": src}


def ensure_source(con, channel: str, name: str) -> int:
    channel, name = canonical_source(channel, name)
    con.execute("INSERT OR IGNORE INTO ad_channels(name) VALUES (?)", (channel,))
    cid = con.execute("SELECT id FROM ad_channels WHERE name=?", (channel,)).fetchone()[0]
    con.execute("INSERT OR IGNORE INTO ad_sources(channel_id, name, created_at) VALUES (?,?,?)", (cid, name, _now()))
    return con.execute("SELECT id FROM ad_sources WHERE channel_id=? AND name=?", (cid, name)).fetchone()[0]


def import_text(con, text: str) -> int:
    """Строки «Канал;Источник» (так выгружено из ERP). Дубли пропускаются."""
    n = 0
    for line in (text or "").splitlines():
        if ";" not in line:
            continue
        ch, name = line.split(";", 1)
        if name.strip():
            before = con.total_changes
            ensure_source(con, ch, name)
            n += con.total_changes > before
    return n


def set_active(con, sid: int, active: bool):
    con.execute("UPDATE ad_sources SET active=? WHERE id=?", (1 if active else 0, sid))


def save_channel(con,data,cid=None):
    from .services import ApiError
    old=con.execute('SELECT * FROM ad_channels WHERE id=?',(cid,)).fetchone() if cid else None
    if cid and not old:raise ApiError('Канал не найден',404)
    if old and data.get('expected') and data['expected']!={k:old[k] for k in ('name','active')}:raise ApiError('Канал изменился. Обновите список',409)
    name=str(data.get('name',old['name'] if old else '') or '').strip()[:200]
    if not name:raise ApiError('Укажите название канала')
    if any(r['id']!=cid and r['name'].casefold()==name.casefold() for r in con.execute('SELECT id,name FROM ad_channels')):raise ApiError('Канал с таким названием уже существует')
    active=int(bool(data.get('active',old['active'] if old else True)))
    if old:con.execute('UPDATE ad_channels SET name=?,active=? WHERE id=?',(name,active,cid));return cid
    return con.execute('INSERT INTO ad_channels(name,active) VALUES (?,?)',(name,active)).lastrowid


def save_source(con,data,sid=None):
    from .services import ApiError
    old=con.execute('SELECT * FROM ad_sources WHERE id=?',(sid,)).fetchone() if sid else None
    if sid and not old:raise ApiError('Источник не найден',404)
    if old and data.get('expected') and data['expected']!={k:old[k] for k in ('name','channel_id','active')}:raise ApiError('Источник изменился. Обновите список',409)
    cid=int(data.get('channel_id') or (old['channel_id'] if old else 0))
    channel=con.execute('SELECT * FROM ad_channels WHERE id=?',(cid,)).fetchone()
    active=int(bool(data.get('active',old['active'] if old else True)))
    if not channel or (not channel['active'] and (not old or old['channel_id']!=cid)):raise ApiError('Выберите действующий канал')
    name=str(data.get('name',old['name'] if old else '') or '').strip()[:200]
    canonical_channel, name = canonical_source(channel['name'], name)
    if canonical_channel != channel['name']:
        con.execute("INSERT OR IGNORE INTO ad_channels(name) VALUES (?)", (canonical_channel,))
        cid = con.execute("SELECT id FROM ad_channels WHERE name=?", (canonical_channel,)).fetchone()[0]
    if not name:raise ApiError('Укажите название источника')
    if any(r['id']!=sid and r['name'].casefold()==name.casefold() for r in con.execute('SELECT id,name FROM ad_sources WHERE channel_id=?',(cid,))):raise ApiError('Источник с таким названием уже есть в этом канале')
    if old:con.execute('UPDATE ad_sources SET name=?,channel_id=?,active=? WHERE id=?',(name,cid,active,sid));return sid
    return con.execute('INSERT INTO ad_sources(name,channel_id,active,created_at) VALUES (?,?,?,?)',(name,cid,active,_now())).lastrowid


def label(con, source_id) -> str:
    if not source_id:
        return ""
    r = con.execute("SELECT s.name, c.name FROM ad_sources s JOIN ad_channels c ON c.id=s.channel_id WHERE s.id=?",
                    (source_id,)).fetchone()
    return f"{r[1]}: {r[0]}" if r else ""


def report(con, start: str, end: str) -> dict:
    """Лиды за период по источникам: сколько записали на пробное, пришли, купили."""
    rows = {}
    for r in con.execute(
            """SELECT s.source_id, s.source, s.trial_booked_at, s.trial_came, s.converted_at, src.name AS sname, ch.name AS cname
               FROM students s LEFT JOIN ad_sources src ON src.id=s.source_id LEFT JOIN ad_channels ch ON ch.id=src.channel_id
               WHERE s.created_at BETWEEN ? AND ? AND (s.funnel='sales' OR s.converted_at IS NOT NULL)""",
            (start, end + "T23:59")):
        key = (r["cname"] or "Без канала", r["sname"] or (r["source"] or "Без источника"))
        x = rows.setdefault(key, {"channel": key[0], "source": key[1], "leads": 0, "trial": 0, "came": 0, "bought": 0})
        x["leads"] += 1
        x["trial"] += bool(r["trial_booked_at"])
        x["came"] += r["trial_came"] == 1
        x["bought"] += bool(r["converted_at"])
    out = sorted(rows.values(), key=lambda x: (-x["bought"], -x["leads"]))
    channels = {}
    for x in out:
        c = channels.setdefault(x["channel"], {"channel": x["channel"], "leads": 0, "trial": 0, "came": 0, "bought": 0})
        for k in ("leads", "trial", "came", "bought"):
            c[k] += x[k]
    return {"sources": out, "channels": sorted(channels.values(), key=lambda x: -x["leads"])}
