"""Филиалы, группы, смены админов, расписание на день и неделю, выгрузка списка группы, архив.

Ничего не удаляется: филиалы, группы, ученики и сотрудники уходят в архив и возвращаются оттуда.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import logic

SCHEMA = """
CREATE TABLE IF NOT EXISTS shifts (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    branch_id INTEGER NOT NULL REFERENCES branches(id),
    weekdays TEXT NOT NULL,                -- "0,2,4"
    time_from TEXT NOT NULL DEFAULT '',
    time_to TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS substitutions (
    id INTEGER PRIMARY KEY,
    group_id INTEGER NOT NULL REFERENCES groups(id),
    date TEXT NOT NULL,
    coach_id INTEGER NOT NULL REFERENCES users(id),   -- кто выходит вместо штатного тренера
    note TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(group_id, date)
);
"""


class SchedError(ValueError):
    pass


def init(con):
    con.executescript(SCHEMA)
    bcols = {r[1] for r in con.execute("PRAGMA table_info(branches)")}
    for col in ("address TEXT NOT NULL DEFAULT ''", "active INTEGER NOT NULL DEFAULT 1"):
        if col.split()[0] not in bcols:
            con.execute(f"ALTER TABLE branches ADD COLUMN {col}")
    scols = {r[1] for r in con.execute("PRAGMA table_info(students)")}
    if "archived" not in scols:
        con.execute("ALTER TABLE students ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")


def _days(v) -> str:
    if isinstance(v, str):
        v = [x for x in v.split(",") if x.strip()]
    days = sorted({int(x) for x in (v or []) if 0 <= int(x) <= 6})
    if not days:
        raise SchedError("Выберите хотя бы один день недели")
    return ",".join(map(str, days))


def _time(v: str) -> str:
    v = (v or "").strip()
    if v and not (len(v) == 5 and v[2] == ":" and v[:2].isdigit() and v[3:].isdigit() and int(v[:2]) < 24 and int(v[3:]) < 60):
        raise SchedError("Время в формате ЧЧ:ММ, например 17:45")
    return v


# ---------------- филиалы ----------------

def save_branch(con, data: dict, bid: int | None = None) -> int:
    name = (data.get("name") or "").strip()
    if not name:
        raise SchedError("Укажите название филиала")
    addr = (data.get("address") or "").strip()
    if bid:
        con.execute("UPDATE branches SET name=?, address=? WHERE id=?", (name, addr, bid))
        return bid
    return con.execute("INSERT INTO branches(name, address) VALUES (?,?)", (name, addr)).lastrowid


def set_branch_active(con, bid: int, active: bool):
    if not active and con.execute("SELECT 1 FROM groups WHERE branch_id=? AND active=1", (bid,)).fetchone():
        raise SchedError("Сначала перенесите в архив группы этого филиала")
    con.execute("UPDATE branches SET active=? WHERE id=?", (1 if active else 0, bid))


# ---------------- группы ----------------

def save_group(con, data: dict, gid: int | None = None) -> int:
    name = (data.get("name") or "").strip()
    if not name:
        raise SchedError("Укажите название группы")
    bid = int(data.get("branch_id") or 0)
    if not con.execute("SELECT 1 FROM branches WHERE id=? AND active=1", (bid,)).fetchone():
        raise SchedError("Выберите филиал")
    coach = int(data["coach_id"]) if data.get("coach_id") else None
    if coach and not con.execute("SELECT 1 FROM users WHERE id=? AND role='coach' AND active=1", (coach,)).fetchone():
        raise SchedError("Тренер не найден")
    cap = int(data.get("capacity") or 12)
    if not 0 <= cap <= 200:
        raise SchedError("Неверное число мест")
    vals = (bid, name, _days(data.get("weekdays")), _time(data.get("time")), (data.get("age") or "").strip(),
            1 if data.get("is_makeup") else 0, coach, cap)
    if gid:
        old = con.execute("SELECT weekdays FROM groups WHERE id=?", (gid,)).fetchone()
        if not old:
            raise SchedError("Группа не найдена")
        con.execute("""UPDATE groups SET branch_id=?, name=?, weekdays=?, time=?, age=?, is_makeup=?, coach_id=?, capacity=?
                       WHERE id=?""", (*vals, gid))
        # ученики, которые ходили не во все дни, — оставляем только дни, которые у группы остались
        allowed = set(vals[2].split(","))
        for eid, wd in con.execute("SELECT id, weekdays FROM enrollments WHERE group_id=? AND weekdays != ''", (gid,)).fetchall():
            keep = [x for x in wd.split(",") if x in allowed]
            con.execute("UPDATE enrollments SET weekdays=? WHERE id=?", (",".join(keep) if keep and len(keep) < len(allowed) else "", eid))
        return gid
    return con.execute("""INSERT INTO groups(branch_id, name, weekdays, time, age, is_makeup, coach_id, capacity)
                          VALUES (?,?,?,?,?,?,?,?)""", vals).lastrowid


def set_group_active(con, gid: int, active: bool):
    if not active:
        n = con.execute("""SELECT COUNT(*) FROM enrollments e JOIN students s ON s.id=e.student_id
                           WHERE e.group_id=? AND e.active=1 AND s.stage != 'left' AND s.archived=0""", (gid,)).fetchone()[0]
        if n:
            raise SchedError(f"В группе ещё {n} учеников. Сначала переведите их в другие группы")
    con.execute("UPDATE groups SET active=? WHERE id=?", (1 if active else 0, gid))


def structure(con) -> dict:
    """Филиалы с группами и сменами — для конструктора в настройках."""
    branches = [dict(r) for r in con.execute("SELECT * FROM branches ORDER BY active DESC, id")]
    for b in branches:
        b["groups"] = []
        for g in con.execute("""SELECT g.*, u.name AS coach_name,
                                (SELECT COUNT(DISTINCT e.student_id) FROM enrollments e JOIN students s ON s.id=e.student_id
                                 WHERE e.group_id=g.id AND e.active=1 AND s.funnel='members' AND s.stage != 'left' AND s.archived=0) AS kids
                                FROM groups g LEFT JOIN users u ON u.id=g.coach_id WHERE g.branch_id=?
                                ORDER BY g.active DESC, g.is_makeup, g.time, g.name""", (b["id"],)):
            b["groups"].append(dict(g))
        b["shifts"] = [dict(r) for r in con.execute(
            """SELECT sh.*, u.name AS user_name FROM shifts sh JOIN users u ON u.id=sh.user_id
               WHERE sh.branch_id=? AND sh.active=1 ORDER BY sh.time_from""", (b["id"],))]
    return {"branches": branches}


# ---------------- смены ----------------

def save_shift(con, data: dict, shid: int | None = None) -> int:
    uid, bid = int(data.get("user_id") or 0), int(data.get("branch_id") or 0)
    if not con.execute("SELECT 1 FROM users WHERE id=? AND active=1 AND role IN ('admin','operator','coach')", (uid,)).fetchone():
        raise SchedError("Выберите сотрудника")
    if not con.execute("SELECT 1 FROM branches WHERE id=?", (bid,)).fetchone():
        raise SchedError("Выберите филиал")
    vals = (uid, bid, _days(data.get("weekdays")), _time(data.get("time_from")), _time(data.get("time_to")))
    if shid:
        con.execute("UPDATE shifts SET user_id=?, branch_id=?, weekdays=?, time_from=?, time_to=? WHERE id=?", (*vals, shid))
        return shid
    return con.execute("INSERT INTO shifts(user_id, branch_id, weekdays, time_from, time_to) VALUES (?,?,?,?,?)", vals).lastrowid


def remove_shift(con, shid: int):
    con.execute("UPDATE shifts SET active=0 WHERE id=?", (shid,))


# ---------------- расписание ----------------

def day(con, d: date) -> dict:
    """Что происходит в этот день: по филиалам — тренировки, тренеры, кто на смене."""
    from . import services as sv
    out = []
    for b in con.execute("SELECT * FROM branches WHERE active=1 ORDER BY id"):
        lessons = []
        for g in con.execute("""SELECT g.*, u.name AS coach_name FROM groups g LEFT JOIN users u ON u.id=g.coach_id
                                WHERE g.branch_id=? AND g.active=1 ORDER BY g.time, g.name""", (b["id"],)):
            if d.weekday() not in logic.parse_weekdays(g["weekdays"]):
                continue
            j = sv.journal(con, g["id"], d.isoformat())
            kids = [r for r in j["rows"] if r.get("status") != "trial"]
            sub = sub_on(con, g["id"], d.isoformat())
            lessons.append({"id": g["id"], "name": g["name"], "time": g["time"], "age": g["age"],
                            "coach": sub["coach_name"] if sub else (g["coach_name"] or ""), "coach_id": g["coach_id"],
                            "sub": {"id": sub["id"], "coach_id": sub["coach_id"], "instead": g["coach_name"] or "", "note": sub["note"]} if sub else None,
                            "is_makeup": g["is_makeup"], "kids": len(kids), "capacity": g["capacity"],
                            "trials": sum(1 for r in j["rows"] if r.get("status") == "trial")})
        staff = [dict(r) for r in con.execute(
            """SELECT u.name, sh.time_from, sh.time_to, u.role FROM shifts sh JOIN users u ON u.id=sh.user_id
               WHERE sh.branch_id=? AND sh.active=1 AND u.active=1 AND (',' || sh.weekdays || ',') LIKE ?
               ORDER BY sh.time_from""", (b["id"], f"%,{d.weekday()},%"))]
        if lessons or staff:
            out.append({"id": b["id"], "name": b["name"], "address": b["address"], "lessons": lessons, "staff": staff})
    coaches = [dict(r) for r in con.execute("SELECT id, name FROM users WHERE role='coach' AND active=1 ORDER BY name")]
    return {"date": d.isoformat(), "weekday": d.weekday(), "branches": out, "coaches": coaches}


def week(con) -> dict:
    rows = [dict(r) for r in con.execute(
        """SELECT g.id, g.name, g.weekdays, g.time, g.age, g.is_makeup, g.branch_id, b.name AS branch, u.name AS coach
           FROM groups g JOIN branches b ON b.id=g.branch_id LEFT JOIN users u ON u.id=g.coach_id
           WHERE g.active=1 AND b.active=1 ORDER BY g.time, b.id""")]
    days = []
    for wd in range(7):
        days.append([r for r in rows if wd in logic.parse_weekdays(r["weekdays"])])
    return {"days": days}


# ---------------- замены тренеров ----------------

def save_sub(con, data: dict, user: dict) -> int:
    gid, coach = int(data.get("group_id") or 0), int(data.get("coach_id") or 0)
    try:
        d = date.fromisoformat(data.get("date") or "")
    except ValueError:
        raise SchedError("Укажите дату замены")
    g = con.execute("SELECT * FROM groups WHERE id=?", (gid,)).fetchone()
    if not g:
        raise SchedError("Группа не найдена")
    if d.weekday() not in logic.parse_weekdays(g["weekdays"]):
        raise SchedError("В этот день у группы нет тренировки")
    if not con.execute("SELECT 1 FROM users WHERE id=? AND role='coach' AND active=1", (coach,)).fetchone():
        raise SchedError("Выберите тренера")
    if coach == g["coach_id"]:
        con.execute("DELETE FROM substitutions WHERE group_id=? AND date=?", (gid, d.isoformat()))
        return 0
    con.execute("""INSERT INTO substitutions(group_id, date, coach_id, note, created_by, created_at) VALUES (?,?,?,?,?,?)
                   ON CONFLICT(group_id, date) DO UPDATE SET coach_id=excluded.coach_id, note=excluded.note,
                   created_by=excluded.created_by, created_at=excluded.created_at""",
                (gid, d.isoformat(), coach, (data.get("note") or "").strip()[:200], user.get("name", ""),
                 datetime.now().isoformat(timespec="minutes")))
    return con.execute("SELECT id FROM substitutions WHERE group_id=? AND date=?", (gid, d.isoformat())).fetchone()[0]


def remove_sub(con, sub_id: int):
    con.execute("DELETE FROM substitutions WHERE id=?", (sub_id,))


def sub_on(con, gid: int, d: str):
    return con.execute("""SELECT s.*, u.name AS coach_name FROM substitutions s JOIN users u ON u.id=s.coach_id
                          WHERE s.group_id=? AND s.date=?""", (gid, d)).fetchone()


def coach_subs(con, coach_id: int, d_from: str, d_to: str) -> dict:
    """Замены тренера на период: где он выходит вместо другого и где заменяют его."""
    q = """SELECT s.id, s.date, s.note, g.id AS group_id, g.name AS group_name, g.time, b.name AS branch,
                  u.name AS sub_name, o.name AS own_name
           FROM substitutions s JOIN groups g ON g.id=s.group_id JOIN branches b ON b.id=g.branch_id
           JOIN users u ON u.id=s.coach_id LEFT JOIN users o ON o.id=g.coach_id
           WHERE s.date BETWEEN ? AND ? AND {} ORDER BY s.date, g.time"""
    return {"covering": [dict(r) for r in con.execute(q.format("s.coach_id=?"), (d_from, d_to, coach_id))],
            "covered": [dict(r) for r in con.execute(q.format("g.coach_id=?"), (d_from, d_to, coach_id))]}


def coach_may(con, coach_id: int, gid: int) -> bool:
    """Тренер может открыть журнал своей группы и той, где у него замена (за неделю до и после)."""
    g = con.execute("SELECT coach_id FROM groups WHERE id=?", (gid,)).fetchone()
    if g and g["coach_id"] == coach_id:
        return True
    t = date.today()
    return bool(con.execute("SELECT 1 FROM substitutions WHERE group_id=? AND coach_id=? AND date BETWEEN ? AND ?",
                            (gid, coach_id, (t - timedelta(days=7)).isoformat(), (t + timedelta(days=7)).isoformat())).fetchone())


# ---------------- выгрузка ----------------

def group_export(con, gid: int) -> tuple[str, list[list]]:
    from . import money
    g = con.execute("SELECT g.*, b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id WHERE g.id=?", (gid,)).fetchone()
    if not g:
        raise SchedError("Группа не найдена")
    cur = logic.month_of(date.today())
    rows = [["Ученик", "Дата рождения", "Возраст", "Родитель", "Телефон", "Другие телефоны", "Дни", "Тариф",
             f"Абонемент {logic.month_label(cur)}", "Долг, ₽", "Источник"]]
    for s in con.execute("""SELECT s.*, e.weekdays AS e_days, e.tariff AS e_tariff, e.id AS eid FROM enrollments e
                            JOIN students s ON s.id=e.student_id WHERE e.group_id=? AND e.active=1
                            AND s.funnel='members' AND s.stage != 'left' AND s.archived=0 ORDER BY s.name""", (gid,)).fetchall():
        parents = con.execute("SELECT name, phone FROM parents WHERE family_id=? ORDER BY id", (s["family_id"],)).fetchall()
        sub = con.execute("SELECT status FROM subscriptions WHERE enrollment_id=? AND month=? AND status != 'cancelled'",
                          (s["eid"], cur)).fetchone()
        age = ""
        if s["birth"]:
            try:
                b = date.fromisoformat(s["birth"])
                t = date.today()
                age = t.year - b.year - ((t.month, t.day) < (b.month, b.day))
            except ValueError:
                pass
        days = s["e_days"] or g["weekdays"]
        rows.append([s["name"], s["birth"], age, parents[0]["name"] if parents else "", parents[0]["phone"] if parents else "",
                     ", ".join(f"{p['name']} {p['phone']}" for p in parents[1:]),
                     ", ".join(logic.WEEKDAY_NAMES[int(x)] for x in days.split(",") if x),
                     "гибкий" if s["e_tariff"] == "flex" else "негибкий",
                     {"paid": "оплачен", "invoiced": "счёт выставлен"}.get(sub["status"], "") if sub else "нет",
                     money.balance(con, s["id"])["debt"] or "", s["source"]])
    return f"{g['name']}.csv", rows


# ---------------- архив учеников ----------------

def set_student_archived(con, sid: int, archived: bool, user: dict):
    con.execute("UPDATE students SET archived=? WHERE id=?", (1 if archived else 0, sid))
    con.execute("INSERT INTO history(student_id, ts, user_name, kind, text) VALUES (?,?,?,?,?)",
                (sid, datetime.now().isoformat(timespec="minutes"), user.get("name", ""), "system",
                 "Карточка перенесена в архив" if archived else "Карточка возвращена из архива"))


def archive_list(con) -> dict:
    return {
        "students": [dict(r) for r in con.execute(
            "SELECT s.id, s.name, f.name AS family FROM students s JOIN families f ON f.id=s.family_id WHERE s.archived=1 ORDER BY s.name")],
        "groups": [dict(r) for r in con.execute(
            "SELECT g.id, g.name, b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id WHERE g.active=0 ORDER BY g.name")],
        "branches": [dict(r) for r in con.execute("SELECT id, name FROM branches WHERE active=0 ORDER BY name")],
        "users": [dict(r) for r in con.execute("SELECT id, name, login, role FROM users WHERE active=0 ORDER BY name")],
    }
