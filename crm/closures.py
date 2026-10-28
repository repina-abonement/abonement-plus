"""Календарь без занятий: праздники и другие даты, когда тренировок нет.

Дата закрывается для всей школы, для филиала или для одной группы. Закрытые даты не входят в цену абонемента,
не показываются в журнале и на столе тренера, по ним не считается аренда.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS closures (
    id INTEGER PRIMARY KEY,
    date_from TEXT NOT NULL,
    date_to TEXT NOT NULL,
    branch_id INTEGER REFERENCES branches(id),
    group_id INTEGER REFERENCES groups(id),
    note TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


class ClosureError(ValueError):
    pass


def init(con):
    con.executescript(SCHEMA)
    # Юлия 28.10.2026: новогодние праздники, занятий нет 31.12.2026–03.01.2027, старт 4 января
    if not con.execute("SELECT 1 FROM settings WHERE key='closures_ny2027'").fetchone():
        if not con.execute("SELECT 1 FROM closures WHERE date_from='2026-12-31'").fetchone():
            con.execute("""INSERT INTO closures(date_from, date_to, note, created_by, created_at)
                           VALUES ('2026-12-31','2027-01-03','Новогодние праздники','Система',?)""",
                        (datetime.now().isoformat(timespec="minutes"),))
        con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('closures_ny2027','1')")


def add(con, data: dict, user: dict) -> int:
    try:
        a = date.fromisoformat(data.get("date_from") or "")
        b = date.fromisoformat(data.get("date_to") or data.get("date_from") or "")
    except ValueError:
        raise ClosureError("Укажите даты")
    if b < a:
        raise ClosureError("Дата окончания раньше начала")
    bid = int(data["branch_id"]) if data.get("branch_id") else None
    gid = int(data["group_id"]) if data.get("group_id") else None
    return con.execute("""INSERT INTO closures(date_from, date_to, branch_id, group_id, note, created_by, created_at)
                          VALUES (?,?,?,?,?,?,?)""",
                       (a.isoformat(), b.isoformat(), bid, gid, (data.get("note") or "").strip()[:200], user.get("name", ""),
                        datetime.now().isoformat(timespec="minutes"))).lastrowid


def remove(con, cid: int):
    con.execute("DELETE FROM closures WHERE id=?", (cid,))


def listing(con) -> list[dict]:
    return [dict(r) for r in con.execute(
        """SELECT c.*, b.name AS branch, g.name AS grp FROM closures c LEFT JOIN branches b ON b.id=c.branch_id
           LEFT JOIN groups g ON g.id=c.group_id WHERE c.date_to >= ? ORDER BY c.date_from""",
        ((date.today() - timedelta(days=60)).isoformat(),))]


def closed_dates(con, group_id: int, d_from: date, d_to: date) -> set[str]:
    """Закрытые даты группы в периоде (учитываются закрытия школы, филиала группы и самой группы)."""
    g = con.execute("SELECT branch_id FROM groups WHERE id=?", (group_id,)).fetchone()
    bid = g[0] if g else None
    out = set()
    for r in con.execute("""SELECT date_from, date_to FROM closures WHERE date_to >= ? AND date_from <= ?
                            AND (group_id IS NULL OR group_id=?) AND (branch_id IS NULL OR branch_id=?)""",
                         (d_from.isoformat(), d_to.isoformat(), group_id, bid)):
        a, b = max(date.fromisoformat(r[0]), d_from), min(date.fromisoformat(r[1]), d_to)
        while a <= b:
            out.add(a.isoformat())
            a += timedelta(days=1)
    return out


def is_closed(con, group_id: int, d: date | str) -> bool:
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return bool(closed_dates(con, group_id, d, d))
