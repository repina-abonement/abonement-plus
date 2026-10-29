"""Планер: личные задачи, поручения сотрудникам, повторяющиеся дела, дни рождения сотрудников, контроль.

Все дела живут в той же таблице tasks, что и задачи по пробным, поэтому у каждого сотрудника на рабочем столе
одно общее «на сегодня». Повторяющиеся дела описаны в recurring и раскладываются в задачи на сегодня и завтра.
Контролёр (например, Варвара) видит задачи всей команды и может вернуть задачу на доработку.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import comms

SCHEMA = """
CREATE TABLE IF NOT EXISTS recurring (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    assignee_id INTEGER NOT NULL REFERENCES users(id),
    rule TEXT NOT NULL,                  -- daily | weekly | monthly | yearly
    weekdays TEXT NOT NULL DEFAULT '',   -- для weekly: "0,3" (пн, чт)
    monthday INTEGER NOT NULL DEFAULT 0, -- для monthly: число месяца
    yearday TEXT NOT NULL DEFAULT '',    -- для yearly: "MM-DD"
    time TEXT NOT NULL DEFAULT '',
    priority INTEGER NOT NULL DEFAULT 0,
    start_date TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_by_id INTEGER,
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""
RULES = {"daily": "каждый день", "weekly": "каждую неделю", "monthly": "каждый месяц", "yearly": "каждый год"}
CONTROLLER_KEY = "planner_controller"
BDAY_LEAD_KEY = "bday_lead_days"


class PlanError(ValueError):
    pass


def init(con):
    con.executescript(SCHEMA)
    tcols = {r[1] for r in con.execute("PRAGMA table_info(tasks)")}
    for col in ("created_by_id INTEGER", "created_by TEXT NOT NULL DEFAULT ''", "priority INTEGER NOT NULL DEFAULT 0",
                "recur_id INTEGER", "returned_note TEXT NOT NULL DEFAULT ''"):
        if col.split()[0] not in tcols:
            con.execute(f"ALTER TABLE tasks ADD COLUMN {col}")
    ucols = {r[1] for r in con.execute("PRAGMA table_info(users)")}
    if "birth" not in ucols:
        con.execute("ALTER TABLE users ADD COLUMN birth TEXT NOT NULL DEFAULT ''")


def _now() -> datetime:
    return datetime.now().replace(second=0, microsecond=0)


def controller(con) -> int | None:
    v = comms.get(con, CONTROLLER_KEY)
    return int(v) if v.isdigit() else None


def can_manage(con, user: dict) -> bool:
    """Ставить задачи другим и видеть всю команду: руководитель и старший администратор."""
    return user["role"] == "admin" or bool(user.get("is_senior")) or user["id"] == controller(con)


def _due(d: str, t: str) -> str:
    try:
        date.fromisoformat(d)
    except ValueError:
        raise PlanError("Укажите дату")
    t = (t or "").strip()
    if t and not (len(t) == 5 and t[2] == ":"):
        raise PlanError("Время в формате ЧЧ:ММ")
    return f"{d}T{t or '23:59'}"


def add(con, user: dict, data: dict) -> int:
    title = (data.get("title") or "").strip()
    if not title:
        raise PlanError("Напишите, что нужно сделать")
    who = int(data.get("assignee_id") or user["id"])
    if who != user["id"] and not can_manage(con, user):
        raise PlanError("Ставить задачи другим может администратор или контролёр")
    if not con.execute("SELECT 1 FROM users WHERE id=? AND active=1", (who,)).fetchone():
        raise PlanError("Сотрудник не найден")
    d = data.get("date") or date.today().isoformat()
    due = _due(d, data.get("time") or "")
    prio = 1 if data.get("priority") else 0
    note = (data.get("note") or "").strip()[:1000]
    rep = data.get("repeat") or {}
    rule = rep.get("rule") or ""
    rid = None
    if rule:
        if rule not in RULES:
            raise PlanError("Неизвестный повтор")
        wds = ",".join(str(int(x)) for x in rep.get("weekdays") or [] if 0 <= int(x) <= 6)
        if rule == "weekly" and not wds:
            wds = str(date.fromisoformat(d).weekday())
        mday = int(rep.get("monthday") or date.fromisoformat(d).day) if rule == "monthly" else 0
        yday = d[5:] if rule == "yearly" else ""
        rid = con.execute("""INSERT INTO recurring(title, note, assignee_id, rule, weekdays, monthday, yearday, time, priority,
                             start_date, created_by_id, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (title, note, who, rule, wds, mday, yday, (data.get("time") or "").strip(), prio, d,
                           user["id"], user.get("name", ""), _now().isoformat(timespec="minutes"))).lastrowid
        generate(con, date.today())
        return rid
    return con.execute("""INSERT INTO tasks(kind, title, assignee_id, due_at, source, note, created_at, created_by_id,
                          created_by, priority) VALUES ('plan',?,?,?,'manual',?,?,?,?,?)""",
                       (title, who, due, note, _now().isoformat(timespec="minutes"), user["id"], user.get("name", ""),
                        prio)).lastrowid


def _occurs(r, d: date) -> bool:
    if d.isoformat() < r["start_date"]:
        return False
    if r["rule"] == "daily":
        return True
    if r["rule"] == "weekly":
        return str(d.weekday()) in r["weekdays"].split(",")
    if r["rule"] == "monthly":
        last = (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        return d.day == min(r["monthday"], last.day)
    if r["rule"] == "yearly":
        return d.strftime("%m-%d") == r["yearday"]
    return False


def _next_bday(birth: str, today: date) -> date | None:
    try:
        b = date.fromisoformat(birth)
    except ValueError:
        return None
    for y in (today.year, today.year + 1):
        try:
            nb = b.replace(year=y)
        except ValueError:  # 29 февраля
            nb = date(y, 3, 1)
        if nb >= today:
            return nb
    return None


def generate(con, today: date | None = None) -> int:
    """Раскладывает повторяющиеся дела и дни рождения сотрудников в задачи на сегодня и завтра."""
    today = today or date.today()
    made = 0
    now = _now().isoformat(timespec="minutes")
    for r in con.execute("SELECT * FROM recurring WHERE active=1").fetchall():
        for d in (today, today + timedelta(days=1)):
            if not _occurs(r, d):
                continue
            cur = con.execute("""INSERT OR IGNORE INTO tasks(kind, title, assignee_id, due_at, source, auto_key, note, created_at,
                                 created_by_id, created_by, priority, recur_id) VALUES ('plan',?,?,?,'recurring',?,?,?,?,?,?,?)""",
                              (r["title"], r["assignee_id"], f"{d.isoformat()}T{r['time'] or '23:59'}", f"recur:{r['id']}:{d}",
                               r["note"], now, r["created_by_id"], r["created_by"], r["priority"], r["id"]))
            made += cur.rowcount
    boss = controller(con) or (con.execute("SELECT id FROM users WHERE role='admin' AND active=1 ORDER BY id").fetchone() or [None])[0]
    if boss:
        lead = int(comms.get(con, BDAY_LEAD_KEY) or 7)
        for u in con.execute("SELECT id, name, birth FROM users WHERE active=1 AND birth!=''").fetchall():
            nb = _next_bday(u["birth"], today)
            if not nb:
                continue
            left = (nb - today).days
            if left <= lead and u["id"] != boss:
                due = max(today, nb - timedelta(days=lead))
                cur = con.execute("""INSERT OR IGNORE INTO tasks(kind, title, assignee_id, due_at, source, auto_key, note, created_at,
                                     created_by) VALUES ('bday',?,?,?,'auto',?,?,?,'Система')""",
                                  (f"Создать чат в Telegram для сбора денег: день рождения {u['name']} {nb.strftime('%d.%m')}",
                                   boss, f"{due.isoformat()}T23:59", f"bday-chat:{u['id']}:{nb.year}",
                                   "Позвать всех, кроме именинника", now))
                made += cur.rowcount
            if left == 0:
                cur = con.execute("""INSERT OR IGNORE INTO tasks(kind, title, assignee_id, due_at, source, auto_key, created_at,
                                     created_by) VALUES ('bday',?,?,?,'auto',?,?,'Система')""",
                                  (f"Поздравить {u['name']} с днём рождения 🎉", boss, f"{today.isoformat()}T23:59",
                                   f"bday-day:{u['id']}:{nb.year}", now))
                made += cur.rowcount
    return made


def week(con, user: dict, start: str, who: int | None = None) -> dict:
    """Неделя колонками (как в Weeek): задачи по дням, повторы разложены на всю неделю."""
    d0 = date.fromisoformat(start)
    d0 = d0 - timedelta(days=d0.weekday())  # с понедельника
    uid = who if who and can_manage(con, user) else user["id"]
    today = date.today()
    for i in range(7):  # повторы на эту неделю (не дальше двух месяцев вперёд)
        d = d0 + timedelta(days=i)
        if today <= d <= today + timedelta(days=62):
            _materialize(con, d)
    days = []
    for i in range(7):
        d = (d0 + timedelta(days=i)).isoformat()
        rows = [_out(con, r) for r in con.execute(
            """SELECT * FROM tasks WHERE assignee_id=? AND substr(due_at,1,10)=? AND status IN ('open','done')
               ORDER BY status='done', priority DESC, due_at""", (uid, d))]
        days.append({"date": d, "tasks": rows})
    overdue = [_out(con, r) for r in con.execute(
        "SELECT * FROM tasks WHERE assignee_id=? AND status='open' AND substr(due_at,1,10)<? ORDER BY due_at",
        (uid, min(today.isoformat(), d0.isoformat())))]
    name = (con.execute("SELECT name FROM users WHERE id=?", (uid,)).fetchone() or [""])[0]
    return {"start": d0.isoformat(), "days": days, "overdue": overdue, "user_id": uid, "user_name": name,
            "can_manage": can_manage(con, user),
            "staff": [dict(r) for r in con.execute("SELECT id, name FROM users WHERE active=1 ORDER BY role, name")] if can_manage(con, user) else []}


def _materialize(con, d: date):
    now = _now().isoformat(timespec="minutes")
    for r in con.execute("SELECT * FROM recurring WHERE active=1").fetchall():
        if _occurs(r, d):
            con.execute("""INSERT OR IGNORE INTO tasks(kind, title, assignee_id, due_at, source, auto_key, note, created_at,
                           created_by_id, created_by, priority, recur_id) VALUES ('plan',?,?,?,'recurring',?,?,?,?,?,?,?)""",
                        (r["title"], r["assignee_id"], f"{d.isoformat()}T{r['time'] or '23:59'}", f"recur:{r['id']}:{d}",
                         r["note"], now, r["created_by_id"], r["created_by"], r["priority"], r["id"]))


def move(con, tid: int, user: dict, new_date: str):
    """Перетащили задачу на другой день: время остаётся прежним."""
    t = con.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    if not t:
        raise PlanError("Задача не найдена")
    if not (can_manage(con, user) or user["id"] in (t["assignee_id"], t["created_by_id"])):
        raise PlanError("Переносить можно свои задачи")
    try:
        date.fromisoformat(new_date)
    except ValueError:
        raise PlanError("Неверная дата")
    if t["kind"] == "rebook" and new_date > (date.today() + timedelta(days=14)).isoformat():
        raise PlanError("Задачу «Перезаписать на ПУ» нельзя отодвинуть больше чем на 14 дней")
    con.execute("UPDATE tasks SET due_at=? WHERE id=?", (f"{new_date}T{t['due_at'][11:16]}", tid))


def reopen(con, tid: int, user: dict):
    t = con.execute("SELECT assignee_id FROM tasks WHERE id=?", (tid,)).fetchone()
    if not t or not (can_manage(con, user) or t["assignee_id"] == user["id"]):
        raise PlanError("Нет доступа")
    con.execute("UPDATE tasks SET status='open', done_at=NULL WHERE id=?", (tid,))


def cancel(con, tid: int, user: dict):
    t = con.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    if not t:
        raise PlanError("Задача не найдена")
    if not (can_manage(con, user) or t["created_by_id"] == user["id"]):
        raise PlanError("Убрать задачу может тот, кто её поставил")
    con.execute("UPDATE tasks SET status='cancelled', done_at=?, done_by=? WHERE id=?",
                (_now().isoformat(timespec="minutes"), user.get("name", ""), tid))


def return_task(con, tid: int, user: dict, note: str):
    """Контролёр возвращает задачу: снова открыта, на сегодня, с комментарием."""
    if not can_manage(con, user):
        raise PlanError("Возвращать задачи может контролёр или администратор")
    t = con.execute("SELECT due_at FROM tasks WHERE id=?", (tid,)).fetchone()
    if not t:
        raise PlanError("Задача не найдена")
    con.execute("UPDATE tasks SET status='open', done_at=NULL, returned_note=?, due_at=? WHERE id=?",
                ((note or "Вернули на доработку").strip()[:300], f"{date.today().isoformat()}T{t['due_at'][11:16]}", tid))


def stop_recurring(con, rid: int, user: dict):
    r = con.execute("SELECT created_by_id, assignee_id FROM recurring WHERE id=?", (rid,)).fetchone()
    if not r:
        raise PlanError("Повтор не найден")
    if not (can_manage(con, user) or user["id"] in (r["created_by_id"], r["assignee_id"])):
        raise PlanError("Нет доступа")
    con.execute("UPDATE recurring SET active=0 WHERE id=?", (rid,))
    con.execute("UPDATE tasks SET status='cancelled' WHERE recur_id=? AND status='open' AND due_at>?",
                (rid, date.today().isoformat()))


def _out(con, r) -> dict:
    from .tasks import _task_out
    return _task_out(con, r)


def view(con, user: dict) -> dict:
    """Страница «Планер»: мои дела по датам, что я поручила, повторы, команда (для контролёра)."""
    generate(con)
    today = date.today().isoformat()
    mine = [_out(con, r) for r in con.execute(
        "SELECT * FROM tasks WHERE status='open' AND assignee_id=? ORDER BY due_at", (user["id"],))]
    given = [_out(con, r) for r in con.execute(
        """SELECT * FROM tasks WHERE created_by_id=? AND assignee_id!=? AND (status='open' OR done_at>=?)
           ORDER BY status='open' DESC, due_at""", (user["id"], user["id"], (date.today() - timedelta(days=7)).isoformat()))]
    rec_where = "" if can_manage(con, user) else "AND (r.assignee_id=? OR r.created_by_id=?)"
    rec_args = [] if can_manage(con, user) else [user["id"], user["id"]]
    recurring = [dict(r) | {"rule_label": RULES[r["rule"]]} for r in con.execute(
        f"""SELECT r.*, u.name AS assignee FROM recurring r JOIN users u ON u.id=r.assignee_id
            WHERE r.active=1 {rec_where} ORDER BY u.name, r.title""", rec_args)]
    out = {"mine": mine, "given": given, "recurring": recurring, "today": today, "can_manage": can_manage(con, user),
           "controller": controller(con), "rules": RULES,
           "staff": [dict(r) for r in con.execute("SELECT id, name, role, birth FROM users WHERE active=1 ORDER BY role, name")]}
    if out["can_manage"]:
        out["team"] = team(con)
    return out


def team(con) -> list[dict]:
    """Для контролёра: у кого что открыто, просрочено и сделано сегодня."""
    today = date.today().isoformat()
    out = []
    for u in con.execute("SELECT id, name, role FROM users WHERE active=1 ORDER BY role, name").fetchall():
        rows = [_out(con, r) for r in con.execute(
            """SELECT * FROM tasks WHERE assignee_id=? AND status='open' AND due_at<=? ORDER BY due_at""",
            (u["id"], f"{today}T23:59"))]
        done = con.execute("SELECT COUNT(*) FROM tasks WHERE assignee_id=? AND status='done' AND done_at>=?",
                           (u["id"], today)).fetchone()[0]
        if not rows and not done:
            continue
        out.append({"id": u["id"], "name": u["name"], "role": u["role"], "done_today": done,
                    "overdue": [t for t in rows if t["due_at"][:10] < today], "today": [t for t in rows if t["due_at"][:10] == today]})
    return out


def save_settings(con, data: dict):
    if CONTROLLER_KEY in data:
        comms.put(con, CONTROLLER_KEY, str(data[CONTROLLER_KEY] or ""))
    if BDAY_LEAD_KEY in data:
        comms.put(con, BDAY_LEAD_KEY, str(int(data[BDAY_LEAD_KEY] or 7)))


def set_birth(con, uid: int, birth: str):
    if birth:
        try:
            date.fromisoformat(birth)
        except ValueError:
            raise PlanError("Дата рождения в формате ГГГГ-ММ-ДД")
    con.execute("UPDATE users SET birth=? WHERE id=?", (birth or "", uid))
