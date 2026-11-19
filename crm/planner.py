"""Планер: личные задачи, поручения сотрудникам, повторяющиеся дела, дни рождения сотрудников, контроль.

Все дела живут в той же таблице tasks, что и задачи по пробным, поэтому у каждого сотрудника на рабочем столе
одно общее «на сегодня». Повторяющиеся дела описаны в recurring и раскладываются в задачи на сегодня и завтра.
Контролёр (например, Варвара) видит задачи всей команды и может вернуть задачу на доработку.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from . import comms,offlineplanner

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
    offlineplanner.init(con)
    rcols = {r[1] for r in con.execute("PRAGMA table_info(recurring)")}
    if "end_date" not in rcols:
        con.execute("ALTER TABLE recurring ADD COLUMN end_date TEXT NOT NULL DEFAULT ''")
    tcols = {r[1] for r in con.execute("PRAGMA table_info(tasks)")}
    for col in ("created_by_id INTEGER", "created_by TEXT NOT NULL DEFAULT ''", "priority INTEGER NOT NULL DEFAULT 0",
                "recur_id INTEGER", "returned_note TEXT NOT NULL DEFAULT ''"):
        if col.split()[0] not in tcols:
            con.execute(f"ALTER TABLE tasks ADD COLUMN {col}")
    for table,column in (('tasks','shift'),('recurring','shift_mode')):
        if column not in {r[1] for r in con.execute(f'PRAGMA table_info({table})')}:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT NOT NULL DEFAULT 'common'")
    if 'checklist_template' not in {r[1] for r in con.execute('PRAGMA table_info(recurring)')}:
        con.execute("ALTER TABLE recurring ADD COLUMN checklist_template TEXT NOT NULL DEFAULT '[]'")
    con.execute("""CREATE TABLE IF NOT EXISTS planner_subtasks(id INTEGER PRIMARY KEY,
        task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,title TEXT NOT NULL,
        position INTEGER NOT NULL,done INTEGER NOT NULL DEFAULT 0,done_by TEXT NOT NULL DEFAULT '',done_at TEXT)""")
    con.execute('CREATE INDEX IF NOT EXISTS planner_subtasks_task ON planner_subtasks(task_id,position,id)')
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
    if user["role"] == "trainee":
        return False
    return user["role"] == "admin" or bool(user.get("is_senior")) or user["id"] == controller(con)


def can_view_plan(con, user: dict, uid: int) -> bool:
    if uid==offlineplanner.pool_id(con):return offlineplanner.can_view(con,user)
    if user["role"] == "trainee":
        return uid == user["id"]
    if uid == user["id"] or user["role"] == "admin":
        return True
    return bool(user.get("is_senior") and con.execute(
        "SELECT 1 FROM users WHERE id=? AND role!='admin'", (uid,)).fetchone())


def can_change_plan(con,user,uid):
    if uid==offlineplanner.pool_id(con):return offlineplanner.can_edit(con,user)
    return can_view_plan(con,user,uid)


def can_add_plan(con,user,uid):
    return can_change_plan(con,user,uid) or (uid==offlineplanner.pool_id(con) and offlineplanner.can_assign(con,user))


def visible_staff(con, user: dict, *, people=False) -> list[dict]:
    rows=[dict(row) for row in con.execute("SELECT id,name,role,birth FROM users WHERE active=1 ORDER BY role,name") if can_view_plan(con,user,row['id'])]
    if not people:rows=[r for r in rows if offlineplanner.board_id(con,r["id"])==r["id"]]
    if not people and offlineplanner.can_view(con,user):rows.append({'id':offlineplanner.pool_id(con),'name':'Офлайн-администраторы — общий планер','role':'offline_pool','birth':''})
    return rows


def _due(d: str, t: str) -> str:
    try:
        date.fromisoformat(d)
    except ValueError:
        raise PlanError("Укажите дату")
    t = (t or "").strip()
    if t and not (len(t) == 5 and t[2] == ":"):
        raise PlanError("Время в формате ЧЧ:ММ")
    return f"{d}T{t or '23:59'}"


def checklist_titles(value):
    if not isinstance(value,list) or len(value)>50:raise PlanError('В списке может быть до 50 подзадач')
    if any(not isinstance(t,str) or not t.strip() or len(t.strip())>200 for t in value):
        raise PlanError('Название подзадачи: от 1 до 200 символов')
    return [t.strip() for t in value]


def sync_checklist(con,tid,titles):
    existing=list(con.execute('SELECT * FROM planner_subtasks WHERE task_id=? ORDER BY position,id',(tid,)))
    for position,title in enumerate(titles):
        same=next((r for r in existing if r['title']==title),None)
        if same:
            con.execute('UPDATE planner_subtasks SET position=? WHERE id=?',(position,same['id']));existing.remove(same)
        else:con.execute('INSERT INTO planner_subtasks(task_id,title,position) VALUES(?,?,?)',(tid,title,position))
    for row in existing:con.execute('DELETE FROM planner_subtasks WHERE id=?',(row['id'],))


def change_subtask(con,tid,user,data,item_id=None):
    task=con.execute("SELECT * FROM tasks WHERE id=? AND kind='plan'",(tid,)).fetchone()
    if not task or not can_change_plan(con,user,task['assignee_id']) or task['status']!='open':
        raise PlanError('Подзадачи можно менять у доступной открытой задачи')
    if item_id is None:
        title=checklist_titles([data.get('title')])[0]
        count=con.execute('SELECT count(*) FROM planner_subtasks WHERE task_id=?',(tid,)).fetchone()[0]
        if count>=50:raise PlanError('В списке может быть до 50 подзадач')
        con.execute('INSERT INTO planner_subtasks(task_id,title,position) VALUES(?,?,?)',(tid,title,count))
    else:
        row=con.execute('SELECT * FROM planner_subtasks WHERE task_id=? AND id=?',(tid,item_id)).fetchone()
        if not row:raise PlanError('Подзадача не найдена')
        if data.get('remove') is True:con.execute('DELETE FROM planner_subtasks WHERE id=?',(item_id,))
        else:
            done=data.get('done')
            if not isinstance(done,bool):raise PlanError('Укажите отметку выполнения')
            if bool(row['done'])!=done:
                con.execute('UPDATE planner_subtasks SET done=?,done_by=?,done_at=? WHERE id=?',
                    (int(done),user.get('name','') if done else '',_now().isoformat(timespec='minutes') if done else None,item_id))
    return task_detail(con,tid,user)


SHIFT_LABELS = {'common':'Общие задачи дня','morning':'☀️ Утренняя смена','evening':'🔵 Основная смена','both':'Обе смены'}


def shift_mode(con,who,value):
    mode=str(value or 'common')
    if mode not in SHIFT_LABELS:raise PlanError('Выберите смену')
    if mode!='common' and who!=offlineplanner.pool_id(con):raise PlanError('Смены доступны в общем планере офлайн-администраторов')
    return mode


def shifts_for(mode,day):
    if day.weekday() not in (0,2):return ['common']
    return ['morning','evening'] if mode=='both' else [mode]


def _emit(con,r,d,now):
    made=0
    for shift in shifts_for(dict(r).get('shift_mode','common'),d):
        key=f"recur:{r['id']}:{d}"+((':'+shift) if dict(r).get('shift_mode','common')!='common' else '')
        cur=con.execute("""INSERT OR IGNORE INTO tasks(kind,title,assignee_id,due_at,source,auto_key,note,created_at,
            created_by_id,created_by,priority,recur_id,shift) VALUES('plan',?,?,?,'recurring',?,?,?,?,?,?,?,?)""",
            (r['title'],r['assignee_id'],f"{d}T{r['time'] or '23:59'}",key,r['note'],now,r['created_by_id'],r['created_by'],r['priority'],r['id'],shift))
        made+=cur.rowcount
        if cur.rowcount:sync_checklist(con,cur.lastrowid,json.loads(dict(r).get('checklist_template') or '[]'))
    return made


def add(con, user: dict, data: dict) -> int:
    title = (data.get("title") or "").strip()
    if not title:
        raise PlanError("Напишите, что нужно сделать")
    who = offlineplanner.board_id(con,int(data.get("assignee_id") or user["id"]))
    if offlineplanner.member(con,user) and who==user['id']:who=offlineplanner.pool_id(con)
    if not can_add_plan(con,user,who):
        raise PlanError("Ставить задачи другим может администратор или контролёр")
    if who!=offlineplanner.pool_id(con) and not con.execute("SELECT 1 FROM users WHERE id=? AND active=1", (who,)).fetchone():
        raise PlanError("Сотрудник не найден")
    d = data.get("date") or date.today().isoformat()
    due = _due(d, data.get("time") or "")
    prio = 1 if data.get("priority") else 0
    note = (data.get("note") or "").strip()[:1000]
    rep = data.get("repeat") or {}
    rule = rep.get("rule") or ""
    titles=checklist_titles(data.get('subtasks',[]))
    mode=shift_mode(con,who,data.get('shift_mode'))
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
        con.execute("UPDATE recurring SET shift_mode=?,checklist_template=? WHERE id=?",(mode,json.dumps(titles,ensure_ascii=False),rid))
        generate(con, date.today())
        return rid
    first=None
    for shift in shifts_for(mode,date.fromisoformat(d)):
        tid=con.execute("""INSERT INTO tasks(kind,title,assignee_id,due_at,source,note,created_at,created_by_id,created_by,priority,shift)
            VALUES('plan',?,?,?,'manual',?,?,?,?,?,?)""",(title,who,due,note,_now().isoformat(timespec='minutes'),user['id'],user.get('name',''),prio,shift)).lastrowid
        sync_checklist(con,tid,titles)
        first=first or tid
    return first



def _occurs(r, d: date) -> bool:
    if d.isoformat() < r["start_date"] or (dict(r).get("end_date") and d.isoformat() > r["end_date"]):
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
            made += _emit(con,r,d,now)
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


def week(con, user: dict, start: str, who: int | None = None, *, rolling: bool = False, days: int = 7) -> dict:
    """Неделя колонками (как в Weeek): задачи по дням, повторы разложены на всю неделю."""
    d0 = date.fromisoformat(start)
    if not rolling:
        d0 -= timedelta(days=d0.weekday())
    who=offlineplanner.board_id(con,who) if who else who
    if who and not can_view_plan(con,user,who):
        if who==offlineplanner.pool_id(con):raise PlanError('Нет доступа к этому Планеру')
        who=None
    uid = who or (offlineplanner.pool_id(con) if offlineplanner.member(con,user) else user['id'])
    today = date.today()
    day_count=1 if days==1 else 7
    for i in range(day_count):  # повторы на эту неделю (не дальше двух месяцев вперёд)
        d = d0 + timedelta(days=i)
        if today <= d <= today + timedelta(days=62):
            _materialize(con, d)
    days = []
    for i in range(day_count):
        d = (d0 + timedelta(days=i)).isoformat()
        rows = [_out(con, r) for r in con.execute(
            """SELECT * FROM tasks WHERE kind IN ('plan','bday') AND assignee_id=? AND substr(due_at,1,10)=? AND status IN ('open','done')
               ORDER BY status='done', priority DESC, due_at""", (uid, d))]
        days.append({"date": d, "tasks": rows})
    overdue = [_out(con, r) for r in con.execute(
        """SELECT * FROM tasks WHERE kind IN ('plan','bday') AND assignee_id=? AND status='open'
           AND substr(due_at,1,10)<? ORDER BY due_at""",
        (uid, min(today.isoformat(), d0.isoformat())))]
    name = (con.execute("SELECT name FROM users WHERE id=?", (uid,)).fetchone() or [""])[0]
    return {"start": d0.isoformat(), "days": days, "overdue": overdue, "user_id": uid, "user_name": name,"can_edit":can_change_plan(con,user,uid),"can_add":can_add_plan(con,user,uid),
            "can_manage": len(visible_staff(con,user))>1,"shifts_enabled":uid==offlineplanner.pool_id(con),
            "staff": visible_staff(con, user), "people":visible_staff(con,user,people=True),"shift_board_id":offlineplanner.pool_id(con)}


def _materialize(con, d: date):
    now = _now().isoformat(timespec="minutes")
    for r in con.execute("SELECT * FROM recurring WHERE active=1").fetchall():
        if _occurs(r, d):
            _emit(con,r,d,now)


def move(con, tid: int, user: dict, new_date: str):
    """Перетащили задачу на другой день: время остаётся прежним."""
    t = con.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    if not t:
        raise PlanError("Задача не найдена")
    if not can_change_plan(con, user, t["assignee_id"]):
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
    if not t or not can_change_plan(con, user, t["assignee_id"]):
        raise PlanError("Нет доступа")
    con.execute("UPDATE tasks SET status='open', done_at=NULL WHERE id=?", (tid,))


def cancel(con, tid: int, user: dict):
    t = con.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    if not t:
        raise PlanError("Задача не найдена")
    if not can_change_plan(con, user, t["assignee_id"]) or not (
        can_manage(con, user) or t["created_by_id"] == user["id"] or (t['assignee_id']==offlineplanner.pool_id(con) and offlineplanner.can_edit(con,user))
    ):
        raise PlanError("Убрать задачу может тот, кто её поставил")
    con.execute("UPDATE tasks SET status='cancelled', done_at=?, done_by=? WHERE id=?",
                (_now().isoformat(timespec="minutes"), user.get("name", ""), tid))


def return_task(con, tid: int, user: dict, note: str):
    """Контролёр возвращает задачу: снова открыта, на сегодня, с комментарием."""
    if not can_manage(con, user):
        raise PlanError("Возвращать задачи может контролёр или администратор")
    t = con.execute("SELECT due_at, assignee_id FROM tasks WHERE id=?", (tid,)).fetchone()
    if not t or not can_change_plan(con, user, t["assignee_id"]):
        raise PlanError("Нет доступа")
    con.execute("UPDATE tasks SET status='open', done_at=NULL, returned_note=?, due_at=? WHERE id=?",
                ((note or "Вернули на доработку").strip()[:300], f"{date.today().isoformat()}T{t['due_at'][11:16]}", tid))


def stop_recurring(con, rid: int, user: dict):
    r = con.execute("SELECT created_by_id, assignee_id FROM recurring WHERE id=?", (rid,)).fetchone()
    if not r:
        raise PlanError("Повтор не найден")
    if not can_change_plan(con, user, r["assignee_id"]):
        raise PlanError("Нет доступа")
    con.execute("UPDATE recurring SET active=0 WHERE id=?", (rid,))
    con.execute("""UPDATE tasks SET status='cancelled', done_at=?, done_by=?
                   WHERE recur_id=? AND kind='plan' AND status='open' AND substr(due_at,1,10)>=?""",
                (_now().isoformat(timespec='minutes'),user.get('name',''),rid,date.today().isoformat()))


def _out(con, r) -> dict:
    from .tasks import _task_out
    out=_task_out(con,r)
    out['subtasks']=[dict(x) for x in con.execute('SELECT * FROM planner_subtasks WHERE task_id=? ORDER BY position,id',(r['id'],))]
    return out


def view(con, user: dict) -> dict:
    """Страница «Планер»: мои дела по датам, что я поручила, повторы, команда (для контролёра)."""
    generate(con)
    today = date.today().isoformat()
    mine_id=offlineplanner.pool_id(con) if offlineplanner.member(con,user) else user['id']
    mine = [_out(con, r) for r in con.execute(
        "SELECT * FROM tasks WHERE kind IN ('plan','bday') AND status='open' AND assignee_id=? ORDER BY due_at", (mine_id,))]
    given = [_out(con, r) for r in con.execute(
        """SELECT * FROM tasks WHERE kind IN ('plan','bday') AND created_by_id=? AND assignee_id!=? AND (status='open' OR done_at>=?)
           ORDER BY status='open' DESC, due_at""", (user["id"], user["id"], (date.today() - timedelta(days=7)).isoformat()))
        if can_view_plan(con, user, r["assignee_id"])]
    recurring = [dict(r) | {"rule_label": RULES[r["rule"]],"can_edit":can_change_plan(con,user,r['assignee_id'])} for r in con.execute(
        """SELECT r.*, u.name AS assignee FROM recurring r JOIN users u ON u.id=r.assignee_id
            WHERE r.active=1 ORDER BY u.name, r.title""") if can_view_plan(con, user, r["assignee_id"])]
    out = {"mine": mine, "given": given, "recurring": recurring, "today": today,
           "can_manage": user["role"] != "trainee" and (can_manage(con,user) or offlineplanner.can_assign(con,user)),
           "controller": controller(con), "rules": RULES,"offline_viewer":comms.get(con,offlineplanner.VIEWER_KEY) if user['role']=='admin' else None,
           "staff": visible_staff(con, user), "people":visible_staff(con,user,people=True),"shift_board_id":offlineplanner.pool_id(con),"my_board_id":mine_id}
    if out["can_manage"]:
        out["team"] = team(con, user)
    return out


def team(con, user: dict) -> list[dict]:
    """Для контролёра: у кого что открыто, просрочено и сделано сегодня."""
    today = date.today().isoformat()
    out = []
    for u in visible_staff(con, user):
        rows = [_out(con, r) for r in con.execute(
            """SELECT * FROM tasks WHERE kind IN ('plan','bday') AND assignee_id=? AND status='open' AND due_at<=? ORDER BY due_at""",
            (u["id"], f"{today}T23:59"))]
        done = con.execute("SELECT COUNT(*) FROM tasks WHERE kind IN ('plan','bday') AND assignee_id=? AND status='done' AND done_at>=?",
                           (u["id"], today)).fetchone()[0]
        if not rows and not done:
            continue
        out.append({"id": u["id"], "name": u["name"], "role": u["role"], "done_today": done,
                    "overdue": [t for t in rows if t["due_at"][:10] < today], "today": [t for t in rows if t["due_at"][:10] == today]})
    return out


def save_settings(con, data: dict):
    if offlineplanner.VIEWER_KEY in data:comms.put(con,offlineplanner.VIEWER_KEY,offlineplanner.validate_viewer(con,data[offlineplanner.VIEWER_KEY]))
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


def task_detail(con, tid: int, user: dict) -> dict:
    row = con.execute("SELECT * FROM tasks WHERE id=? AND kind IN ('plan','bday')", (tid,)).fetchone()
    if not row or not can_view_plan(con, user, row["assignee_id"]):
        raise PlanError("Задача недоступна")
    task = _out(con, row)
    recurring = con.execute("SELECT * FROM recurring WHERE id=?", (row["recur_id"],)).fetchone() if row["recur_id"] else None
    repeat = dict(recurring) if recurring else {}
    if repeat:
        repeat["weekdays"] = [int(x) for x in repeat["weekdays"].split(",") if x]
    return {"task": task, "repeat": repeat, "staff": visible_staff(con, user),
            "subtask_template":json.loads(repeat.get("checklist_template") or "[]"),"can_check_subtasks":row["kind"]=="plan" and row["status"]=="open" and can_change_plan(con,user,row["assignee_id"]),
            "can_assign": can_manage(con, user), "can_edit": row["kind"] == "plan" and row["status"] == "open" and can_change_plan(con,user,row['assignee_id']),
            "can_stop_repeat": bool(recurring and recurring['active'] and can_change_plan(con,user,recurring['assignee_id']))}


def edit_task(con, tid: int, user: dict, data: dict) -> dict:
    detail = task_detail(con, tid, user)
    t = detail["task"]
    if not detail["can_edit"]:
        raise PlanError("Настройки можно менять у открытой задачи Планера")
    titles=checklist_titles(data["subtasks"]) if "subtasks" in data else [x["title"] for x in t["subtasks"]]
    title = str(data.get("title") or "").strip()
    if not title or len(title) > 200:
        raise PlanError("Название: от 1 до 200 символов")
    note = str(data.get("note") or "").strip()
    if len(note) > 1000:
        raise PlanError("Описание: не больше 1000 символов")
    day = str(data.get("date") or t["due_at"][:10])
    tm = str(data.get("time") or "").strip()
    try:
        date.fromisoformat(day)
        if tm:
            datetime.strptime(tm, "%H:%M")
            if len(tm) != 5:
                raise ValueError()
        who = offlineplanner.board_id(con,int(data.get("assignee_id") or t["assignee_id"]))
    except (ValueError, TypeError):
        raise PlanError("Проверьте дату, время и исполнителя")
    if not can_change_plan(con, user, who) or (who!=offlineplanner.pool_id(con) and not con.execute("SELECT 1 FROM users WHERE id=? AND active=1", (who,)).fetchone()):
        raise PlanError("Нельзя назначить задачу этому сотруднику")
    mode=shift_mode(con,who,data.get("shift_mode",t.get("shift","common")))
    scope = data.get("scope") or "one"
    if scope not in ("one", "future"):
        raise PlanError("Выберите одну задачу или будущие повторы")
    rep = data.get("repeat") or {}
    if not isinstance(rep, dict):
        raise PlanError("Проверьте настройки повтора")
    rule = rep.get("rule") or ""
    if rule and rule not in RULES:
        raise PlanError("Неизвестный повтор")
    try:
        weekdays = sorted(set(int(x) for x in rep.get("weekdays") or []))
    except (ValueError, TypeError):
        raise PlanError("Проверьте дни недели")
    if any(x < 0 or x > 6 for x in weekdays):
        raise PlanError("Проверьте дни недели")
    if rule == "weekly" and not weekdays:
        raise PlanError("Выберите хотя бы один день недели")
    rid = t["recur_id"]
    if rid and scope == "one":
        original = detail["repeat"]
        if rule != original.get("rule", "") or (rule == "weekly" and weekdays != original.get("weekdays", [])):
            raise PlanError("Чтобы изменить повтор, выберите «Эта и будущие задачи»")
    if rid and scope=="one" and mode=="both":raise PlanError("Для обеих смен выберите эту и будущие задачи")
    prio = 1 if data.get("priority") else 0
    due = _due(day, tm)
    if not rid or scope == "future":
        if rid:
            cutoff = t["due_at"][:10]
            end = (date.fromisoformat(cutoff) - timedelta(days=1)).isoformat()
            previous_end = detail["repeat"].get("end_date") or "9999-12-31"
            con.execute("UPDATE recurring SET end_date=? WHERE id=?", (min(end, previous_end), rid))
            con.execute("UPDATE tasks SET status='cancelled' WHERE recur_id=? AND status='open' AND substr(due_at,1,10)>=? AND id!=?", (rid, cutoff, tid))
        new_rid = None
        if rule:
            new_rid = con.execute("""INSERT INTO recurring(title,note,assignee_id,rule,weekdays,monthday,yearday,time,priority,start_date,created_by_id,created_by,created_at)
                                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                  (title,note,who,rule,",".join(map(str,weekdays)),date.fromisoformat(day).day if rule == "monthly" else 0,
                                   day[5:] if rule == "yearly" else "",tm,prio,day,user["id"],user.get("name", ""),_now().isoformat(timespec="minutes"))).lastrowid
        if new_rid:con.execute('UPDATE recurring SET shift_mode=?,checklist_template=? WHERE id=?',(mode,json.dumps(titles,ensure_ascii=False),new_rid))
        current_shift=shifts_for(mode,date.fromisoformat(day))[0]
        key=(f"recur:{new_rid}:{day}"+((':'+current_shift) if mode!='common' else '')) if new_rid else None
        con.execute("UPDATE tasks SET recur_id=?,auto_key=?,source=? WHERE id=?", (new_rid,key,"recurring" if new_rid else "manual",tid))
        if new_rid:_materialize(con,date.fromisoformat(day))
    current_shift=shifts_for(mode,date.fromisoformat(day))[0]
    con.execute("UPDATE tasks SET title=?,note=?,assignee_id=?,due_at=?,priority=?,shift=? WHERE id=?", (title,note,who,due,prio,current_shift,tid))
    if not rid and not rule and mode=='both' and current_shift=='morning':
        partner=con.execute("""INSERT INTO tasks(kind,title,assignee_id,due_at,source,note,created_at,created_by_id,created_by,priority,shift)
            VALUES('plan',?,?,?,'manual',?,?,?,?,?,'evening')""",(title,who,due,note,_now().isoformat(timespec='minutes'),user['id'],user.get('name',''),prio))
        sync_checklist(con,partner.lastrowid,titles)
    sync_checklist(con,tid,titles)
    generate(con)
    return {"ok": True, "id": tid}
