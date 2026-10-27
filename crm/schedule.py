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
    role TEXT NOT NULL DEFAULT 'main',               -- main: главный тренер, assistant: помощник
    coach_id INTEGER REFERENCES users(id),            -- кто выходит вместо штатного (пусто, если коллега работает один)
    solo INTEGER NOT NULL DEFAULT 0,                  -- 1: замены нет, второй тренер ведёт один
    note TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(group_id, date, role)
);
CREATE TABLE IF NOT EXISTS absences (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    date_from TEXT NOT NULL,
    date_to TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


class SchedError(ValueError):
    pass


def init(con):
    old = {r[1] for r in con.execute("PRAGMA table_info(substitutions)")}
    if old and "role" not in old:  # первая версия замен была без помощников
        con.execute("ALTER TABLE substitutions RENAME TO substitutions_old")
        con.executescript(SCHEMA)
        con.execute("""INSERT INTO substitutions(id, group_id, date, role, coach_id, note, created_by, created_at)
                       SELECT id, group_id, date, 'main', coach_id, note, created_by, created_at FROM substitutions_old""")
        con.execute("DROP TABLE substitutions_old")
    old = {r[1] for r in con.execute("PRAGMA table_info(substitutions)")}
    if old and "solo" not in old:  # добавили вариант «коллега выйдет один»
        con.execute("ALTER TABLE substitutions RENAME TO substitutions_old")
        con.executescript(SCHEMA)
        con.execute("""INSERT INTO substitutions(id, group_id, date, role, coach_id, note, created_by, created_at)
                       SELECT id, group_id, date, role, coach_id, note, created_by, created_at FROM substitutions_old""")
        con.execute("DROP TABLE substitutions_old")
    con.executescript(SCHEMA)
    gcols = {r[1] for r in con.execute("PRAGMA table_info(groups)")}
    for col in ("assistant_id INTEGER REFERENCES users(id)", "day_times TEXT NOT NULL DEFAULT ''", "note TEXT NOT NULL DEFAULT ''"):
        if col.split()[0] not in gcols:
            con.execute(f"ALTER TABLE groups ADD COLUMN {col}")
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
    lim = data.get("lesson_limit")
    lim = int(lim) if str(lim or "").strip().isdigit() else None
    if bid:
        con.execute("UPDATE branches SET name=?, address=? WHERE id=?", (name, addr, bid))
        if lim is not None:
            con.execute("UPDATE branches SET lesson_limit=? WHERE id=?", (lim, bid))
        return bid
    if lim is None:
        lim = 10 if "небо" in name.lower() else 0  # в ТЦ «Небо» не больше 10 детей на тренировку
    return con.execute("INSERT INTO branches(name, address, lesson_limit) VALUES (?,?,?)", (name, addr, lim)).lastrowid


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
    asst = int(data["assistant_id"]) if data.get("assistant_id") else None
    if asst and not con.execute("SELECT 1 FROM users WHERE id=? AND role='coach' AND active=1", (asst,)).fetchone():
        raise SchedError("Помощник не найден")
    cap = int(data.get("capacity") or 12)
    if not 0 <= cap <= 200:
        raise SchedError("Неверное число мест")
    days = _days(data.get("weekdays"))
    dts = data.get("day_times") or {}
    if isinstance(dts, str):
        dts = logic.parse_day_times(dts)
    dts = {int(k): _time(v) for k, v in dts.items() if v and str(k) in days.split(",")}
    vals = (bid, name, days, _time(data.get("time")), (data.get("age") or "").strip(),
            1 if data.get("is_makeup") else 0, coach, cap, asst, ",".join(f"{k}={v}" for k, v in sorted(dts.items())),
            (data.get("note") or "").strip()[:200])
    if gid:
        old = con.execute("SELECT weekdays FROM groups WHERE id=?", (gid,)).fetchone()
        if not old:
            raise SchedError("Группа не найдена")
        con.execute("""UPDATE groups SET branch_id=?, name=?, weekdays=?, time=?, age=?, is_makeup=?, coach_id=?, capacity=?,
                       assistant_id=?, day_times=?, note=? WHERE id=?""", (*vals, gid))
        # ученики, которые ходили не во все дни, — оставляем только дни, которые у группы остались
        allowed = set(vals[2].split(","))
        for eid, wd in con.execute("SELECT id, weekdays FROM enrollments WHERE group_id=? AND weekdays != ''", (gid,)).fetchall():
            keep = [x for x in wd.split(",") if x in allowed]
            con.execute("UPDATE enrollments SET weekdays=? WHERE id=?", (",".join(keep) if keep and len(keep) < len(allowed) else "", eid))
        return gid
    return con.execute("""INSERT INTO groups(branch_id, name, weekdays, time, age, is_makeup, coach_id, capacity,
                          assistant_id, day_times, note) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", vals).lastrowid


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
        for g in con.execute("""SELECT g.*, u.name AS coach_name, (SELECT name FROM users WHERE id=g.assistant_id) AS assistant_name,
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
    ds = d.isoformat()
    for b in con.execute("SELECT * FROM branches WHERE active=1 ORDER BY id"):
        lessons = []
        for g in con.execute("""SELECT g.*, u.name AS coach_name, a.name AS assistant_name FROM groups g
                                LEFT JOIN users u ON u.id=g.coach_id LEFT JOIN users a ON a.id=g.assistant_id
                                WHERE g.branch_id=? AND g.active=1 ORDER BY g.time, g.name""", (b["id"],)):
            if d.weekday() not in logic.parse_weekdays(g["weekdays"]):
                continue
            j = sv.journal(con, g["id"], ds)
            kids = [r for r in j["rows"] if r.get("status") != "trial"]
            subs = {}
            for role in ("main", "assistant"):
                sub = sub_on(con, g["id"], ds, role)
                own_name = g["coach_name"] if role == "main" else g["assistant_name"]
                own_id = _own(g, role)
                if sub:
                    subs[role] = {"id": sub["id"], "coach_id": sub["coach_id"], "name": sub["coach_name"], "instead": own_name or "",
                                  "note": sub["note"], "solo": sub["solo"]}
                elif own_id and absent_on(con, own_id, ds):
                    subs[role] = {"missing": True, "instead": own_name or ""}
            m, a2 = subs.get("main"), subs.get("assistant")
            lessons.append({"id": g["id"], "name": g["name"], "time": logic.group_time(g, d.weekday()), "age": g["age"],
                            "coach": (m["name"] if m and m.get("name") else ("" if m else g["coach_name"] or "")),
                            "assistant": (a2["name"] if a2 and a2.get("name") else ("" if a2 else g["assistant_name"] or "")),
                            "coach_id": g["coach_id"], "assistant_id": g["assistant_id"], "subs": subs,
                            "sub": {**m, "coach_id": m.get("coach_id")} if m and m.get("id") else None,
                            "is_makeup": g["is_makeup"], "kids": len(kids), "capacity": g["capacity"], "note": g["note"],
                            "trials": sum(1 for r in j["rows"] if r.get("status") == "trial")})
        lessons.sort(key=lambda x: (x["time"], x["name"]))
        staff = [dict(r) for r in con.execute(
            """SELECT u.name, sh.time_from, sh.time_to, u.role FROM shifts sh JOIN users u ON u.id=sh.user_id
               WHERE sh.branch_id=? AND sh.active=1 AND u.active=1 AND (',' || sh.weekdays || ',') LIKE ?
               ORDER BY sh.time_from""", (b["id"], f"%,{d.weekday()},%"))]
        if lessons or staff:
            out.append({"id": b["id"], "name": b["name"], "address": b["address"], "lessons": lessons, "staff": staff})
    coaches = [dict(r) for r in con.execute("SELECT id, name FROM users WHERE role='coach' AND active=1 ORDER BY name")]
    for c in coaches:
        c["absent"] = bool(absent_on(con, c["id"], ds))
    return {"date": ds, "weekday": d.weekday(), "branches": out, "coaches": coaches}


def week(con) -> dict:
    rows = [dict(r) for r in con.execute(
        """SELECT g.id, g.name, g.weekdays, g.time, g.day_times, g.age, g.is_makeup, g.branch_id, b.name AS branch,
                  u.name AS coach, a.name AS assistant
           FROM groups g JOIN branches b ON b.id=g.branch_id LEFT JOIN users u ON u.id=g.coach_id
           LEFT JOIN users a ON a.id=g.assistant_id
           WHERE g.active=1 AND b.active=1 ORDER BY g.time, b.id""")]
    days = []
    for wd in range(7):
        lst = [r | {"time": logic.group_time(r, wd)} for r in rows if wd in logic.parse_weekdays(r["weekdays"])]
        days.append(sorted(lst, key=lambda x: (x["time"], x["branch_id"])))
    return {"days": days}


# ---------------- замены и отсутствия тренеров ----------------

ROLES = {"main": "тренер", "assistant": "помощник"}


def _own(g, role):
    return g["coach_id"] if role == "main" else g["assistant_id"]


def save_sub(con, data: dict, user: dict) -> int:
    gid, coach = int(data.get("group_id") or 0), int(data.get("coach_id") or 0)
    role = data.get("role") or "main"
    if role not in ROLES:
        raise SchedError("Неизвестная роль")
    try:
        d = date.fromisoformat(data.get("date") or "")
    except ValueError:
        raise SchedError("Укажите дату замены")
    g = con.execute("SELECT * FROM groups WHERE id=?", (gid,)).fetchone()
    if not g:
        raise SchedError("Группа не найдена")
    if d.weekday() not in logic.parse_weekdays(g["weekdays"]):
        raise SchedError("В этот день у группы нет тренировки")
    solo = bool(data.get("solo"))
    ds = d.isoformat()
    note = (data.get("note") or "").strip()[:200]
    if solo:
        # замены нет: второй тренер группы ведёт один. Если отсутствует главный, ведёт помощник
        other = g["assistant_id"] if role == "main" else g["coach_id"]
        if not other:
            raise SchedError("В группе нет второго тренера, нужна замена")
        if role == "main":
            _put_sub(con, gid, ds, "main", other, 0, note, user)
            _put_sub(con, gid, ds, "assistant", None, 1, note, user)
        else:
            _put_sub(con, gid, ds, "assistant", None, 1, note, user)
        return con.execute("SELECT id FROM substitutions WHERE group_id=? AND date=? AND role=?", (gid, ds, role)).fetchone()[0]
    if not con.execute("SELECT 1 FROM users WHERE id=? AND role='coach' AND active=1", (coach,)).fetchone():
        raise SchedError("Выберите тренера")
    if coach == _own(g, role):
        con.execute("DELETE FROM substitutions WHERE group_id=? AND date=? AND role=?", (gid, d.isoformat(), role))
        return 0
    _put_sub(con, gid, ds, role, coach, 0, note, user)
    return con.execute("SELECT id FROM substitutions WHERE group_id=? AND date=? AND role=?", (gid, ds, role)).fetchone()[0]


def _put_sub(con, gid, ds, role, coach, solo, note, user):
    con.execute("""INSERT INTO substitutions(group_id, date, role, coach_id, solo, note, created_by, created_at) VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(group_id, date, role) DO UPDATE SET coach_id=excluded.coach_id, solo=excluded.solo, note=excluded.note,
                   created_by=excluded.created_by, created_at=excluded.created_at""",
                (gid, ds, role, coach, solo, note, user.get("name", ""), datetime.now().isoformat(timespec="minutes")))


def remove_sub(con, sub_id: int):
    con.execute("DELETE FROM substitutions WHERE id=?", (sub_id,))


def sub_on(con, gid: int, d: str, role: str = "main"):
    return con.execute("""SELECT s.*, COALESCE(u.name, '') AS coach_name FROM substitutions s LEFT JOIN users u ON u.id=s.coach_id
                          WHERE s.group_id=? AND s.date=? AND s.role=?""", (gid, d, role)).fetchone()


def coach_subs(con, coach_id: int, d_from: str, d_to: str) -> dict:
    """Замены тренера на период: где он выходит вместо другого и где заменяют его."""
    q = """SELECT s.id, s.date, s.note, s.role, g.id AS group_id, g.name AS group_name, g.time, g.day_times, b.name AS branch,
                  COALESCE(u.name, 'никто, коллега один') AS sub_name, s.solo, o.name AS own_name
           FROM substitutions s JOIN groups g ON g.id=s.group_id JOIN branches b ON b.id=g.branch_id
           LEFT JOIN users u ON u.id=s.coach_id LEFT JOIN users o ON o.id=(CASE s.role WHEN 'main' THEN g.coach_id ELSE g.assistant_id END)
           WHERE s.date BETWEEN ? AND ? AND {} ORDER BY s.date, g.time"""
    fix = lambda r: dict(r) | {"time": logic.group_time(r, date.fromisoformat(r["date"]).weekday())}
    return {"covering": [fix(r) for r in con.execute(q.format("s.coach_id=?"), (d_from, d_to, coach_id))],
            "covered": [fix(r) for r in con.execute(
                q.format("((s.role='main' AND g.coach_id=?) OR (s.role='assistant' AND g.assistant_id=?))"),
                (d_from, d_to, coach_id, coach_id))]}


def coach_may(con, coach_id: int, gid: int) -> bool:
    """Тренер может открыть журнал своей группы (главным или помощником) и той, где у него замена (неделя до и после)."""
    g = con.execute("SELECT coach_id, assistant_id FROM groups WHERE id=?", (gid,)).fetchone()
    if g and coach_id in (g["coach_id"], g["assistant_id"]):
        return True
    t = date.today()
    return bool(con.execute("SELECT 1 FROM substitutions WHERE group_id=? AND coach_id=? AND date BETWEEN ? AND ?",
                            (gid, coach_id, (t - timedelta(days=7)).isoformat(), (t + timedelta(days=7)).isoformat())).fetchone())


def save_absence(con, data: dict, user: dict) -> int:
    uid = int(data.get("user_id") or 0)
    if not con.execute("SELECT 1 FROM users WHERE id=? AND active=1", (uid,)).fetchone():
        raise SchedError("Выберите сотрудника")
    try:
        d1, d2 = date.fromisoformat(data.get("date_from") or ""), date.fromisoformat(data.get("date_to") or "")
    except ValueError:
        raise SchedError("Укажите даты: с какого и по какое число")
    if d2 < d1:
        raise SchedError("Дата окончания раньше начала")
    return con.execute("""INSERT INTO absences(user_id, date_from, date_to, note, created_by, created_at) VALUES (?,?,?,?,?,?)""",
                       (uid, d1.isoformat(), d2.isoformat(), (data.get("note") or "").strip()[:200], user.get("name", ""),
                        datetime.now().isoformat(timespec="minutes"))).lastrowid


def remove_absence(con, aid: int):
    con.execute("DELETE FROM absences WHERE id=?", (aid,))


def absences(con, d_from: str | None = None) -> list[dict]:
    d_from = d_from or date.today().isoformat()
    return [dict(r) for r in con.execute(
        """SELECT a.*, u.name AS user_name FROM absences a JOIN users u ON u.id=a.user_id
           WHERE a.date_to >= ? ORDER BY a.date_from""", (d_from,))]


def absent_on(con, uid: int, d: str):
    return con.execute("SELECT * FROM absences WHERE user_id=? AND date_from<=? AND date_to>=?", (uid, d, d)).fetchone()


def needs_subs(con, today: date | None = None, horizon: int = 21) -> list[dict]:
    """Тренировки, где тренер или помощник отсутствует, а замену ещё не поставили. Для рабочего стола админа."""
    today = today or date.today()
    end = today + timedelta(days=horizon)
    out = []
    for a in absences(con, today.isoformat()):
        d1 = max(date.fromisoformat(a["date_from"]), today)
        d2 = min(date.fromisoformat(a["date_to"]), end)
        groups = con.execute("""SELECT g.*, b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id
                                WHERE g.active=1 AND (g.coach_id=? OR g.assistant_id=?)""", (a["user_id"], a["user_id"])).fetchall()
        d = d1
        while d <= d2:
            for g in groups:
                if d.weekday() not in logic.parse_weekdays(g["weekdays"]):
                    continue
                for role in ("main", "assistant"):
                    if _own(g, role) != a["user_id"] or sub_on(con, g["id"], d.isoformat(), role):
                        continue
                    out.append({"date": d.isoformat(), "time": logic.group_time(g, d.weekday()), "group_id": g["id"], "age": g["age"],
                                "is_makeup": g["is_makeup"],
                                "group": g["name"], "branch": g["branch"], "role": role, "role_label": ROLES[role],
                                "partner_id": _own(g, "assistant" if role == "main" else "main"),
                                "absent": a["user_name"], "absent_id": a["user_id"], "note": a["note"]})
            d += timedelta(days=1)
    out.sort(key=lambda x: (x["date"], x["time"]))
    return out


# ---------------- перенос расписания ----------------

_TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r",
                                                   "s", "t", "u", "f", "h", "c", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def _login(name: str) -> str:
    return "".join(_TR.get(c, c) for c in name.lower() if c.isalnum() or c in _TR)[:20] or "coach"


def _find_coach(con, name: str, created: list) -> int | None:
    name = (name or "").strip()
    if not name:
        return None
    r = con.execute("SELECT id FROM users WHERE role='coach' AND lower(name)=lower(?)", (name,)).fetchone()
    if r:
        con.execute("UPDATE users SET active=1 WHERE id=?", (r[0],))
        return r[0]
    from .db import hash_password
    import secrets
    login, n = _login(name), 1
    while con.execute("SELECT 1 FROM users WHERE login=?", (login if n == 1 else f"{login}{n}",)).fetchone():
        n += 1
    login = login if n == 1 else f"{login}{n}"
    uid = con.execute("INSERT INTO users(login, name, role, pw_hash) VALUES (?,?,?,?)",
                      (login, name, "coach", hash_password(secrets.token_urlsafe(12)))).lastrowid
    created.append({"name": name, "login": login})
    return uid


def import_schedule(con, data: dict) -> dict:
    """Переносит расписание: филиалы, группы, тренеров и помощников. Повторный запуск обновляет группы по названию.

    archive_others — остальные группы этих филиалов (например, тестовые) уходят в архив вместе с привязками."""
    created, groups_new, groups_upd, archived = [], 0, 0, 0
    keep = set()
    for br in data.get("branches") or []:
        name = (br.get("name") or "").strip()
        r = None
        for alias in [name] + list(br.get("aliases") or []):
            r = con.execute("SELECT id FROM branches WHERE lower(name)=lower(?)", (alias,)).fetchone()
            if r:
                break
        if r:
            bid = r[0]
            con.execute("UPDATE branches SET name=?, active=1, address=COALESCE(NULLIF(?, ''), address) WHERE id=?",
                        (name, br.get("address") or "", bid))
        else:
            bid = save_branch(con, {"name": name, "address": br.get("address") or ""})
        for g in br.get("groups") or []:
            payload = dict(g, branch_id=bid, coach_id=_find_coach(con, g.get("coach"), created),
                           assistant_id=_find_coach(con, g.get("assistant"), created))
            old = con.execute("SELECT id FROM groups WHERE branch_id=? AND lower(name)=lower(?)", (bid, g["name"])).fetchone()
            if old:
                save_group(con, payload, old[0])
                con.execute("UPDATE groups SET active=1 WHERE id=?", (old[0],))
                keep.add(old[0])
                groups_upd += 1
            else:
                keep.add(save_group(con, payload))
                groups_new += 1
        if data.get("archive_others"):
            for (gid,) in con.execute("SELECT id FROM groups WHERE branch_id=? AND active=1", (bid,)).fetchall():
                if gid not in keep:
                    con.execute("UPDATE groups SET active=0 WHERE id=?", (gid,))
                    archived += 1
    return {"groups_new": groups_new, "groups_updated": groups_upd, "archived": archived, "coaches_created": created}


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
