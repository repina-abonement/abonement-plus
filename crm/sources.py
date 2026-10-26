"""Рекламные каналы и источники лидов + отчёт: сколько лидов, пробных и покупок дал каждый."""
from __future__ import annotations

from datetime import datetime

DEFAULT_CHANNELS = ["Вконтакте", "Инстаграм", "Промоутер", "Промо-акция", "Сайт", "Сарафан", "Филиал", "Другое"]

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
    for n in DEFAULT_CHANNELS:
        con.execute("INSERT OR IGNORE INTO ad_channels(name) VALUES (?)", (n,))
    cols = {r[1] for r in con.execute("PRAGMA table_info(students)")}
    if "source_id" not in cols:
        con.execute("ALTER TABLE students ADD COLUMN source_id INTEGER REFERENCES ad_sources(id)")
    if not con.execute("SELECT 1 FROM ad_sources").fetchone():
        site = con.execute("SELECT id FROM ad_channels WHERE name='Сайт'").fetchone()[0]
        con.execute("INSERT INTO ad_sources(channel_id, name, created_at) VALUES (?,?,?)", (site, "Заявка с сайта", _now()))


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat(timespec="minutes")


def listing(con, active_only: bool = True) -> dict:
    ch = [dict(r) for r in con.execute("SELECT * FROM ad_channels" + (" WHERE active=1" if active_only else "") + " ORDER BY id")]
    src = [dict(r) for r in con.execute(
        "SELECT s.*, c.name AS channel FROM ad_sources s JOIN ad_channels c ON c.id=s.channel_id"
        + (" WHERE s.active=1" if active_only else "") + " ORDER BY c.id, s.name")]
    return {"channels": ch, "sources": src}


def ensure_source(con, channel: str, name: str) -> int:
    channel, name = channel.strip() or "Другое", name.strip()
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
