"""Лист ожидания для всех групп.

До 25 числа желающих ставят в очередь в нужную группу. С 26 числа (срок продления прошёл) на рабочем столе админов
появляется воронка: в каждой группе — кто не продлил следующий месяц (их место больше не гарантировано), сколько мест
свободно и кто ждёт. Админ предлагает место по очереди: «Предложить место» → «Записан» (или «Отказался», и место идёт
следующему). Когда ребёнка из очереди записали в группу, он сам уходит из листа.
"""
from __future__ import annotations

from datetime import date, datetime

from . import logic
from .services import ApiError

SCHEMA = """
CREATE TABLE IF NOT EXISTS waitlist (
    id INTEGER PRIMARY KEY,
    group_id INTEGER NOT NULL REFERENCES groups(id),
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    month TEXT NOT NULL,                       -- с какого месяца хочет заниматься
    tariff TEXT NOT NULL DEFAULT '',
    ofp_group_id INTEGER,
    status TEXT NOT NULL DEFAULT 'queued',     -- queued | offered | done | declined
    note TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""
STATUSES = {"queued": "в очереди", "offered": "предложили место", "done": "записан", "declined": "отказался"}


def _now() -> str:
    return datetime.now().isoformat(timespec="minutes")


def init(con):
    con.executescript(SCHEMA)


def add(con, sid: int, group_id: int, month: str | None, data: dict, user: dict) -> int:
    from . import services as sv
    sv.get_student_row(con, sid)
    g = con.execute("SELECT id, name FROM groups WHERE id=? AND active=1", (group_id,)).fetchone()
    if not g:
        raise ApiError("Выберите группу")
    month = month or logic.shift_month(logic.month_of(date.today()), 1)
    if con.execute("SELECT 1 FROM waitlist WHERE student_id=? AND group_id=? AND status IN ('queued','offered')", (sid, group_id)).fetchone():
        raise ApiError("Ребёнок уже в листе ожидания этой группы")
    wid = con.execute("""INSERT INTO waitlist(group_id, student_id, month, tariff, ofp_group_id, note, created_by, created_at, updated_at)
                         VALUES (?,?,?,?,?,?,?,?,?)""",
                      (group_id, sid, month, data.get("tariff") or "", int(data["ofp_group_id"]) if data.get("ofp_group_id") else None,
                       (data.get("note") or "").strip()[:200], user.get("name", ""), _now(), _now())).lastrowid
    sv.log(con, sid, f"В листе ожидания: {g['name']} с {logic.month_label(month)}", user, "system")
    return wid


def set_status(con, wid: int, status: str, user: dict):
    from . import services as sv
    if status not in STATUSES:
        raise ApiError("Неизвестный статус")
    w = con.execute("SELECT w.*, g.name FROM waitlist w JOIN groups g ON g.id=w.group_id WHERE w.id=?", (wid,)).fetchone()
    if not w:
        raise ApiError("Запись не найдена", 404)
    con.execute("UPDATE waitlist SET status=?, updated_at=? WHERE id=?", (status, _now(), wid))
    sv.log(con, w["student_id"], f"Лист ожидания, {w['name']}: {STATUSES[status]}", user, "system")


def _sync_done(con):
    """Кого из очереди уже записали в группу — отмечаем «записан»."""
    con.execute("""UPDATE waitlist SET status='done', updated_at=? WHERE status IN ('queued','offered') AND EXISTS (
                     SELECT 1 FROM enrollments e WHERE e.student_id=waitlist.student_id AND e.group_id=waitlist.group_id AND e.active=1)""",
                (_now(),))


def for_student(con, sid: int) -> list[dict]:
    return [dict(r) | {"status_label": STATUSES[r["status"]]} for r in con.execute(
        """SELECT w.*, g.name AS group_name FROM waitlist w JOIN groups g ON g.id=w.group_id WHERE w.student_id=? ORDER BY w.id DESC""",
        (sid,))]


def queue(con, group_id: int) -> list[dict]:
    return [dict(r) for r in con.execute(
        """SELECT w.*, s.name, s.family_id FROM waitlist w JOIN students s ON s.id=w.student_id
           WHERE w.group_id=? AND w.status IN ('queued','offered') AND NOT EXISTS (
             SELECT 1 FROM enrollments e WHERE e.student_id=w.student_id AND e.group_id=w.group_id AND e.active=1) ORDER BY w.id""", (group_id,))]


def funnel(con, today: date | None = None, force: bool = False) -> dict | None:
    """Воронка на рабочий стол с 26 числа: по группам — не продлили / свободно / очередь."""
    from . import capacity, program
    today = today or date.today()
    deadline = int((con.execute("SELECT value FROM settings WHERE key='renewal_deadline_day'").fetchone() or ["25"])[0])
    if today.day <= deadline and not force:
        n = con.execute("SELECT COUNT(*) FROM waitlist WHERE status IN ('queued','offered')").fetchone()[0]
        return {"active": False, "waiting": n} if n else None
    cur = logic.month_of(today)
    nxt = logic.shift_month(cur, 1)
    out = []
    for g in con.execute("""SELECT g.*, b.name AS branch, b.lesson_limit FROM groups g JOIN branches b ON b.id=g.branch_id
                            WHERE g.active=1 AND g.is_makeup=0 AND (g.lesson_kind IS NULL OR g.lesson_kind!='ofp') ORDER BY b.id, g.name""").fetchall():
        members = con.execute("""SELECT DISTINCT s.id, s.name FROM enrollments e JOIN students s ON s.id=e.student_id
                                 JOIN subscriptions x ON x.enrollment_id=e.id AND x.month=? AND x.status='paid'
                                 WHERE e.group_id=? AND e.active=1 AND s.stage!='left' AND s.archived=0""", (cur, g["id"])).fetchall()
        paid_next = {r[0] for r in con.execute("""SELECT x.student_id FROM subscriptions x JOIN enrollments e ON e.id=x.enrollment_id
                                                  WHERE e.group_id=? AND x.month=? AND x.status='paid'""", (g["id"], nxt))}
        not_renewed = []
        for m in members:
            if m["id"] in paid_next:
                continue
            inv = con.execute("""SELECT x.price FROM subscriptions x JOIN enrollments e ON e.id=x.enrollment_id
                                 WHERE x.student_id=? AND e.group_id=? AND x.month=? AND x.status='invoiced'""", (m["id"], g["id"], nxt)).fetchone()
            amount = inv[0] if inv else None
            if amount is None and g["program_id"]:
                try:
                    e = con.execute("SELECT e.*, g.name AS group_name FROM enrollments e JOIN groups g ON g.id=e.group_id WHERE e.student_id=? AND e.group_id=? AND e.active=1",
                                    (m["id"], g["id"])).fetchone()
                    amount = program.quote(con, m["id"], nxt, dict(e), on=today)["total"]
                except Exception:  # noqa: BLE001
                    amount = None
            not_renewed.append({"id": m["id"], "name": m["name"], "amount": amount, "invoiced": bool(inv)})
        q = queue(con, g["id"])
        cap = min(g["capacity"], g["lesson_limit"]) if g["lesson_limit"] else g["capacity"]
        free_now = max(0, cap - len(paid_next))
        if not q:
            continue
        out.append({"group_id": g["id"], "group": g["name"], "branch": g["branch"], "capacity": cap, "paid_next": len(paid_next),
                    "free": free_now, "not_renewed": not_renewed, "queue": q})
    return {"active": True, "month": nxt, "month_label": logic.month_label(nxt), "groups": out,
            "waiting": sum(len(x["queue"]) for x in out)}
