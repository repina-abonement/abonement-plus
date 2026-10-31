"""Этап «Не дошёл до ПУ» и контроль отметок в журнале (правила Юлии от 28.10.2026).

На этап ребёнок попадает только так:
- отменил запись заранее (админ отмечает отмену с причиной);
- тренер отметил «не пришёл»;
- тренер не поставил отметку в течение 2 часов после конца тренировки (пометка «нет отметки тренера»).
Если тренер потом отметил «пришёл» — ребёнок возвращается в «Как прошло пробное», задача закрывается.

На этапе у семьи всегда одна открытая задача «Перезаписать на ПУ» на ответственного за филиал (видят все админы
и операторы). Закрыть её можно только результатами: перезаписан / не дозвонились / перезвонить позже (до 14 дней) /
отказ (мягкий: пригласить снова через 3 месяца или в окно 1–15 августа / 5–10 января; жёсткий: «не звонить» → архив).
Недозвоны: 1-й → через 2 рабочих часа (позвонить и написать ещё раз), 2-й → завтра, 3-й → через 7 дней, 4-й → через 14,
5-й → через месяц, дальше — ближайшее окно (август или январь). В «Отказ» сам ребёнок не уходит никогда.

Контроль отметок: через 30 минут после конца любой тренировки без отметок — красный флаг на столе у админов и
тренера; через 2 часа — уведомление руководителю, пробные без отметки уходят на этап.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from . import logic
from .services import ApiError

SCHEMA = """
CREATE TABLE IF NOT EXISTS trial_misses (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    trial_at TEXT NOT NULL,
    group_id INTEGER,
    kind TEXT NOT NULL,              -- cancel | no_show | no_mark
    reason TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    undone INTEGER NOT NULL DEFAULT 0  -- тренер потом отметил «пришёл»
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    key TEXT UNIQUE,
    text TEXT NOT NULL,
    student_id INTEGER,
    group_id INTEGER,
    created_at TEXT NOT NULL,
    closed_at TEXT,
    closed_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS lesson_marks (
    group_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    PRIMARY KEY (group_id, date)
);
"""
KINDS = {"cancel": "отменил заранее", "no_show": "не пришёл", "no_mark": "нет отметки тренера"}
REFUSALS = ["Дорого", "Далеко", "Время не подходит", "Выбрали другую секцию", "Ребёнок не готов", "Попросили не звонить", "Другое"]
HARD = {"Попросили не звонить"}
# рабочее время админов: будни 10–21 (Ольга), суббота 10–20 (Ольга), воскресенье 10–21 (Вика)
DEFAULT_HOURS = {0: (10, 21), 1: (10, 21), 2: (10, 21), 3: (10, 21), 4: (10, 21), 5: (10, 20), 6: (10, 21)}


def _now() -> datetime:
    return datetime.now().replace(second=0, microsecond=0)


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="minutes")


def init(con):
    con.executescript(SCHEMA)
    ucols = {r[1] for r in con.execute("PRAGMA table_info(users)")}
    if "tg_username" not in ucols:
        con.execute("ALTER TABLE users ADD COLUMN tg_username TEXT NOT NULL DEFAULT ''")
    scols = {r[1] for r in con.execute("PRAGMA table_info(students)")}
    for col in ("dnd INTEGER NOT NULL DEFAULT 0", "trial_marked_at TEXT"):
        if col.split()[0] not in scols:
            con.execute(f"ALTER TABLE students ADD COLUMN {col}")
    acols = {r[1] for r in con.execute("PRAGMA table_info(attendance)")}
    if "marked_at" not in acols:
        con.execute("ALTER TABLE attendance ADD COLUMN marked_at TEXT")
    tcols = {r[1] for r in con.execute("PRAGMA table_info(tasks)")}
    for col in ("family_id INTEGER", "attempt INTEGER NOT NULL DEFAULT 0"):
        if col.split()[0] not in tcols:
            con.execute(f"ALTER TABLE tasks ADD COLUMN {col}")


# ---------------- рабочее время ----------------

def hours(con) -> dict:
    r = con.execute("SELECT value FROM settings WHERE key='work_hours'").fetchone()
    if r and r[0]:
        return {int(k): tuple(v) for k, v in json.loads(r[0]).items()}
    return DEFAULT_HOURS


def add_work(con, start: datetime, minutes: int) -> datetime:
    """start + minutes рабочего времени (вне рабочих часов время не идёт)."""
    h = hours(con)
    t = start
    left = minutes
    for _ in range(30):
        a, b = h[t.weekday()]
        day_start, day_end = t.replace(hour=a, minute=0), t.replace(hour=b, minute=0)
        if t < day_start:
            t = day_start
        if t >= day_end:
            t = (t + timedelta(days=1)).replace(hour=0, minute=0)
            continue
        avail = int((day_end - t).total_seconds() // 60)
        if left <= avail:
            return t + timedelta(minutes=left)
        left -= avail
        t = (t + timedelta(days=1)).replace(hour=0, minute=0)
    return t


def end_of_workday(con, t: datetime) -> datetime:
    h = hours(con)
    a, b = h[t.weekday()]
    e = t.replace(hour=b, minute=0)
    return e if t < e else add_work(con, t, 60)


def next_day_start(con, t: datetime, days: int = 1) -> datetime:
    d = (t + timedelta(days=days)).replace(minute=0)
    a, _b = hours(con)[d.weekday()]
    return d.replace(hour=a + 1)  # к 11 утра


def window(t: datetime) -> datetime:
    """Ближайшее окно набора: 1–15 августа или 5–10 января."""
    y = t.year
    cands = [datetime(y, 8, 1, 11), datetime(y + 1, 1, 5, 11), datetime(y, 1, 5, 11), datetime(y + 1, 8, 1, 11)]
    return min(c for c in cands if c > t)


# ---------------- ответственные ----------------

def responsible(con, group_id: int | None) -> int | None:
    from . import tasks
    if group_id:
        b = con.execute("SELECT branch_id FROM groups WHERE id=?", (group_id,)).fetchone()
        if b:
            r = con.execute("SELECT value FROM settings WHERE key=?", (f"resp_branch_{b[0]}",)).fetchone()
            if r and str(r[0]).isdigit() and con.execute("SELECT 1 FROM users WHERE id=? AND active=1", (int(r[0]),)).fetchone():
                return int(r[0])
    return tasks.owner(con)


def responsibles(con) -> list[dict]:
    out = []
    for b in con.execute("SELECT id, name FROM branches WHERE active=1 ORDER BY id"):
        r = con.execute("SELECT value FROM settings WHERE key=?", (f"resp_branch_{b['id']}",)).fetchone()
        out.append({"branch_id": b["id"], "branch": b["name"], "user_id": int(r[0]) if r and str(r[0]).isdigit() else None})
    return out


def set_responsible(con, branch_id: int, user_id: int | None):
    con.execute("INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (f"resp_branch_{branch_id}", str(user_id or "")))


# ---------------- попадание на этап ----------------

def _to_stage(con, s, kind: str, reason: str, user: dict | None, now: datetime):
    from . import services as sv
    con.execute("""INSERT INTO trial_misses(student_id, trial_at, group_id, kind, reason, created_by, created_at)
                   VALUES (?,?,?,?,?,?,?)""", (s["id"], s["trial_at"] or _iso(now), s["group_id"], kind, reason.strip()[:300],
                                              (user or {}).get("name", "CRM"), _iso(now)))
    con.execute("UPDATE students SET stage='missed_trial', stage_changed_at=?, priority=0 WHERE id=?", (_iso(now), s["id"]))
    n = misses_count(con, s["id"])
    sv.log(con, s["id"], f"Не дошёл до ПУ ({KINDS[kind]}{', ' + reason.strip() if reason.strip() else ''}). Всего не доходил: {n}",
           user, "stage")
    due = end_of_workday(con, now) if kind == "cancel" else add_work(con, now, 120)
    ensure_task(con, s["id"], due, now)


def cancel_trial(con, sid: int, reason: str, user: dict, now: datetime | None = None):
    from . import services as sv
    now = now or _now()
    s = sv.get_student_row(con, sid)
    if s["funnel"] != "sales" or not s["trial_at"] or s["stage"] in ("bought", "refused", "missed_trial"):
        raise ApiError("У ребёнка нет записи на пробное")
    if not (reason or "").strip():
        raise ApiError("Укажите причину отмены")
    _to_stage(con, s, "cancel", reason, user, now)
    con.execute("UPDATE students SET trial_at=NULL, trial_came=NULL WHERE id=?", (sid,))
    _close_trial_tasks(con, sid)


def on_trial_mark(con, sid: int, came: bool, user: dict, now: datetime | None = None):
    """Вызывается после отметки тренера на пробном."""
    from . import services as sv
    now = now or _now()
    s = sv.get_student_row(con, sid)
    con.execute("UPDATE students SET trial_marked_at=? WHERE id=?", (_iso(now), sid))
    if not came and s["stage"] != "missed_trial":
        _to_stage(con, s, "no_show", "", user, now)
        _close_trial_tasks(con, sid)
    elif came and s["stage"] == "missed_trial":
        last = con.execute("SELECT id, kind FROM trial_misses WHERE student_id=? AND undone=0 ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
        if last and last["kind"] in ("no_mark", "no_show"):
            con.execute("UPDATE trial_misses SET undone=1 WHERE id=?", (last["id"],))
            con.execute("UPDATE students SET stage='how_was', stage_changed_at=? WHERE id=?", (_iso(now), sid))
            sv.log(con, sid, "Тренер отметил «пришёл»: возвращён в «Как прошло пробное»", user, "stage")
            _close_family_task_if_empty(con, s["family_id"], "Ребёнок был на пробном")
    record_lesson_mark(con, s["group_id"], (s["trial_at"] or "")[:10], now)


def _close_trial_tasks(con, sid):
    con.execute("""UPDATE tasks SET status='done', done_at=?, done_by='CRM', result='Ребёнок на этапе «Не дошёл до ПУ»'
                   WHERE student_id=? AND status='open' AND kind IN ('no_show','not_marked','trial_confirm','trial_today')""",
                (_iso(_now()), sid))


def misses_count(con, sid: int) -> int:
    return con.execute("SELECT COUNT(*) FROM trial_misses WHERE student_id=? AND undone=0", (sid,)).fetchone()[0]


# ---------------- задача на семью ----------------

def _family_kids(con, fam: int) -> list[dict]:
    return [dict(r) for r in con.execute("""SELECT id, name, group_id FROM students WHERE family_id=? AND stage IN ('missed_trial','later')
                                            AND archived=0 AND funnel='sales' ORDER BY id""", (fam,))]


def open_task(con, fam: int):
    return con.execute("SELECT * FROM tasks WHERE kind='rebook' AND family_id=? AND status='open' ORDER BY id DESC LIMIT 1",
                       (fam,)).fetchone()


def ensure_task(con, sid: int, due: datetime, now: datetime | None = None, title: str = "", attempt: int = 0) -> int | None:
    from . import tasks
    s = con.execute("SELECT id, family_id, group_id, dnd FROM students WHERE id=?", (sid,)).fetchone()
    if not s or s["dnd"]:
        return None
    t = open_task(con, s["family_id"])
    if t:
        if _iso(due) < t["due_at"]:
            con.execute("UPDATE tasks SET due_at=? WHERE id=?", (_iso(due), t["id"]))
        return t["id"]
    tid = tasks.add_task(con, "rebook", sid, due, title=title or "Перезаписать на ПУ", source="auto",
                         assignee=responsible(con, s["group_id"]))
    if tid:
        con.execute("UPDATE tasks SET family_id=?, attempt=? WHERE id=?", (s["family_id"], attempt, tid))
    return tid


def _close_family_task_if_empty(con, fam: int, result: str):
    if not _family_kids(con, fam):
        con.execute("UPDATE tasks SET status='done', done_at=?, done_by='CRM', result=? WHERE kind='rebook' AND family_id=? AND status='open'",
                    (_iso(_now()), result, fam))


def repeat_request(con, sid: int):
    """Родитель сам оставил заявку повторно: закрываем задачу, ставим срочную."""
    s = con.execute("SELECT family_id FROM students WHERE id=?", (sid,)).fetchone()
    con.execute("UPDATE tasks SET status='done', done_at=?, done_by='CRM', result='Оставил заявку повторно' WHERE kind='rebook' AND family_id=? AND status='open'",
                (_iso(_now()), s["family_id"]))
    ensure_task(con, sid, _now(), title="Оставил заявку повторно, записать на ПУ")


def complete(con, tid: int, result: str, data: dict, user: dict, now: datetime | None = None) -> dict:
    """Закрыть задачу «Перезаписать на ПУ» одним из разрешённых результатов."""
    from . import services as sv, waitlist  # noqa: F401
    now = now or _now()
    t = con.execute("SELECT * FROM tasks WHERE id=? AND kind='rebook'", (tid,)).fetchone()
    if not t or t["status"] != "open":
        raise ApiError("Задача не найдена или уже закрыта")
    kids = _family_kids(con, t["family_id"]) or [dict(con.execute("SELECT id, name, group_id FROM students WHERE id=?", (t["student_id"],)).fetchone())]
    who = user.get("name", "")

    def close(text):
        con.execute("UPDATE tasks SET status='done', done_at=?, done_by=?, result=? WHERE id=?", (_iso(now), who, text, tid))

    if result == "rebooked":
        plan = {int(k["student_id"]): k for k in data.get("kids") or []}
        if not plan:
            raise ApiError("Выберите новую дату и группу пробного")
        for sid, k in plan.items():
            if not k.get("group_id") or not k.get("trial_at"):
                raise ApiError("Для каждого ребёнка нужны новая дата и группа")
            if k["trial_at"][:16] <= _iso(now):
                raise ApiError("Новая дата пробного должна быть в будущем")
            con.execute("UPDATE students SET stage='lead' WHERE id=?", (sid,))  # чтобы update_student перевёл в «Записан»
            sv.update_student(con, sid, {"group_id": int(k["group_id"]), "trial_at": k["trial_at"][:16]}, user)
            con.execute("UPDATE students SET stage='trial_booked', stage_changed_at=? WHERE id=?", (_iso(now), sid))
            sv.log(con, sid, f"Перезаписан на ПУ: {k['trial_at'][8:10]}.{k['trial_at'][5:7]} {k['trial_at'][11:16]}", user, "stage")
        close("Перезаписан на ПУ")
        rest = _family_kids(con, t["family_id"])
        if rest:
            ensure_task(con, rest[0]["id"], now)
        return {"ok": True}
    if result == "no_answer":
        n = (t["attempt"] or 0) + 1
        if n == 1:
            due, label = add_work(con, now, 120), "через 2 часа: позвонить и написать ещё раз"
        elif n == 2:
            due, label = next_day_start(con, now), "завтра"
        elif n == 3:
            due, label = next_day_start(con, now, 7), "через 7 дней"
        elif n == 4:
            due, label = next_day_start(con, now, 14), "через 14 дней"
        elif n == 5:
            due, label = next_day_start(con, now, 30), "через месяц"
        else:
            due, label = window(now), "в ближайшее окно набора"
        if n >= 5:
            for k in kids:
                con.execute("UPDATE students SET stage='later', stage_changed_at=? WHERE id=?", (_iso(now), k["id"]))
        close(f"Не дозвонились ({n}-й раз подряд)")
        for k in kids:
            sv.log(con, k["id"], f"Не дозвонились ({n}-й раз подряд), следующая попытка {label}", user, "note")
        nt = ensure_task(con, kids[0]["id"], due, now, attempt=n)
        return {"ok": True, "next": _iso(due), "label": label, "task_id": nt}
    if result == "later":
        try:
            d = datetime.fromisoformat((data.get("date") or "")[:16]) if len(data.get("date") or "") > 10 else \
                datetime.combine(date.fromisoformat(data.get("date") or ""), datetime.min.time()).replace(hour=11)
        except ValueError:
            raise ApiError("Укажите дату, когда перезвонить")
        if d.date() > (now + timedelta(days=14)).date():
            raise ApiError("Перезвонить можно не позже чем через 14 дней. Если родитель просит позже, это «Отказ: пока не может»")
        if d < now:
            raise ApiError("Дата уже прошла")
        close(f"Перезвонить {d.strftime('%d.%m %H:%M')}" + (f": {data.get('note')}" if data.get("note") else ""))
        ensure_task(con, kids[0]["id"], d, now)
        return {"ok": True}
    if result == "refused":
        reason = (data.get("reason") or "").strip()
        if reason not in REFUSALS:
            raise ApiError("Выберите причину отказа")
        comment = (data.get("comment") or "").strip()
        if reason == "Другое" and not comment:
            raise ApiError("Напишите причину отказа")
        hard = reason in HARD or bool(data.get("hard"))
        text = reason + (f": {comment}" if comment else "")
        again = min(next_day_start(con, now, 91), window(now))
        for k in kids:
            if hard:
                con.execute("UPDATE students SET stage='refused', refusal_reason=?, stage_changed_at=?, dnd=1, archived=1 WHERE id=?",
                            (text, _iso(now), k["id"]))
                sv.log(con, k["id"], f"Отказ: {text}. Больше не звоним и не пишем, карточка в архиве", user, "stage")
            else:
                con.execute("UPDATE students SET stage='later', refusal_reason=?, stage_changed_at=? WHERE id=?", (text, _iso(now), k["id"]))
                sv.log(con, k["id"], f"Пока не может: {text}. Пригласим снова {again.strftime('%d.%m.%Y')}", user, "stage")
        close(f"Отказ: {text}")
        if not hard:
            ensure_task(con, kids[0]["id"], again, now, title=f"Пригласить снова на ПУ (раньше: {text})")
            return {"ok": True, "again": _iso(again)}
        return {"ok": True}
    raise ApiError("Такого результата нет. Можно: перезаписан, не дозвонились, перезвонить позже или отказ")


def guard_due(con, tid: int, new_due: str):
    """Перенос задачи «Перезаписать на ПУ» не дальше 14 дней."""
    t = con.execute("SELECT kind FROM tasks WHERE id=?", (tid,)).fetchone()
    if t and t["kind"] == "rebook" and new_due[:10] > (date.today() + timedelta(days=14)).isoformat():
        raise ApiError("Задачу «Перезаписать на ПУ» нельзя отодвинуть больше чем на 14 дней")


# ---------------- контроль отметок в журнале ----------------

def _lesson_end(con, g, d: date) -> datetime | None:
    from . import schedule
    t = logic.group_time(schedule.on(con, g, d), d.weekday())
    if not t:
        return None
    mins = int((con.execute("SELECT value FROM settings WHERE key='trial_minutes'").fetchone() or ["60"])[0])
    return datetime.combine(d, datetime.strptime(t, "%H:%M").time()) + timedelta(minutes=mins)


def _unmarked(rows) -> int:
    n = 0
    for r in rows:
        st = r.get("status")
        if st == "vacation" or r.get("warned"):
            continue
        if st == "trial":
            n += r.get("trial_came") is None
        elif st == "makeup":
            n += r.get("mk_status") == "booked"
        elif "makeup_id" in r:
            n += st == "booked"
        else:
            n += st is None
    return n


def record_lesson_mark(con, group_id, day: str, now: datetime | None = None):
    """Когда отмечены все дети тренировки — запоминаем время (для отчёта «вовремя/с опозданием»)."""
    from . import services as sv
    if not group_id or not day:
        return
    try:
        j = sv.journal(con, group_id, day)
    except Exception:  # noqa: BLE001
        return
    if j["has_lesson"] and j["rows"] and not _unmarked(j["rows"]):
        con.execute("INSERT OR IGNORE INTO lesson_marks(group_id, date, completed_at) VALUES (?,?,?)", (group_id, day, _iso(now or _now())))


def missing_marks(con, now: datetime | None = None, min_after: int = 30) -> list[dict]:
    """Тренировки сегодня и вчера, которые закончились min_after минут назад, а отметок нет."""
    from . import closures, services as sv, staff
    now = now or _now()
    out = []
    for d in (now.date() - timedelta(days=1), now.date()):
        for p in staff.planned(con, d):
            g = con.execute("SELECT g.*, b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id WHERE g.id=?", (p["group_id"],)).fetchone()
            end = _lesson_end(con, g, d)
            if not end or now < end + timedelta(minutes=min_after) or closures.is_closed(con, g["id"], d):
                continue
            j = sv.journal(con, g["id"], d.isoformat())
            n = _unmarked(j["rows"])
            if not n:
                continue
            coach = con.execute("SELECT name FROM users WHERE id=?", (p["main"],)).fetchone() if p["main"] else None
            out.append({"group_id": g["id"], "group": g["name"], "branch": g["branch"], "date": d.isoformat(),
                        "time": logic.group_time(g, d.weekday()), "coach_id": p["main"], "coach": coach[0] if coach else "",
                        "missing": n, "minutes": int((now - end).total_seconds() // 60),
                        "trials": [r["id"] for r in j["rows"] if r.get("status") == "trial" and r.get("trial_came") is None]})
    return out


def _mention(con, uid) -> str:
    if not uid:
        return ""
    r = con.execute("SELECT name, tg_username FROM users WHERE id=?", (uid,)).fetchone()
    if not r:
        return ""
    return "@" + r["tg_username"].lstrip("@") if r["tg_username"] else r["name"]


def _controller(con):
    for k in ("checkin_reviewer", "planner_controller"):
        r = con.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
        if r and str(r[0]).isdigit():
            return int(r[0])
    return None


def _owner_admin(con):
    r = con.execute("SELECT id FROM users WHERE role='admin' AND active=1 ORDER BY id LIMIT 1").fetchone()
    return r[0] if r else None


def notify_chat(con, which: str, text: str) -> bool:
    """Сообщение в рабочий чат Telegram (ТРЕНЕРЫ или Служба заботы), если чат подключён к боту."""
    from . import comms
    chat = comms.get(con, "tg_chat_" + which)
    if not chat or not comms.get(con, "tg_token"):
        return False
    try:
        comms.tg_call(con, "sendMessage", {"chat_id": chat, "text": text})
        return True
    except Exception:  # noqa: BLE001 — уведомление не должно ломать проход
        return False


def tick(con, now: datetime | None = None) -> dict:
    """Каждые 5 минут: пробные без отметки 2 часа → на этап; уведомления; у каждого на этапе есть задача."""
    from . import services as sv
    now = now or _now()
    moved = alerts = created = 0
    for m in missing_marks(con, now, 30):  # 30 минут: срочно отметить — в чат ТРЕНЕРЫ, тренер и контролёр
        k = f"nomark30:{m['group_id']}:{m['date']}"
        if add_alert(con, "nomark30", k, f"Нет отметок 30 минут: {m['group']} {m['time']}", group_id=m["group_id"]):
            con.execute("UPDATE alerts SET closed_at=? WHERE key=?", (_iso(now), k))  # на столе видно и так (блок «Нет отметок»)
            who = " ".join(x for x in (_mention(con, m["coach_id"]), _mention(con, _controller(con))) if x)
            notify_chat(con, "coaches", f"⚠️ {who} Срочно отметьте детей в журнале CRM: {m['group']}, {m['branch']}, "
                                        f"{m['date'][8:]}.{m['date'][5:7]} {m['time']}. Без отметки: {m['missing']}")
    for m in missing_marks(con, now, 120):
        k = f"nomark:{m['group_id']}:{m['date']}"
        if add_alert(con, "no_marks", k, f"Нет отметок 2 часа: {m['group']} ({m['branch']}) {m['date'][8:]}.{m['date'][5:7]} {m['time']}, "
                                           f"тренер {m['coach'] or '—'}, без отметки {m['missing']}", group_id=m["group_id"]):
            alerts += 1
            who = " ".join(x for x in (_mention(con, _controller(con)), _mention(con, _owner_admin(con))) if x)
            notify_chat(con, "care", f"🔴 {who} Прошло 2 часа, тренер {m['coach'] or '—'} не отметил детей: {m['group']}, {m['branch']}, "
                                     f"{m['date'][8:]}.{m['date'][5:7]} {m['time']}. Без отметки: {m['missing']}. "
                                     "Пробные без отметки ушли на этап «Не дошёл до ПУ», нужно позвонить родителям")
        for sid in m["trials"]:
            s = sv.get_student_row(con, sid)
            if s["stage"] in ("trial_booked", "trial_reminder", "on_trial", "trial_done") and s["funnel"] == "sales":
                _to_stage(con, s, "no_mark", "", None, now)
                _close_trial_tasks(con, sid)
                moved += 1
    # защита от потерь: у каждого на этапе есть открытая задача
    for s in con.execute("""SELECT id, name, family_id FROM students WHERE stage IN ('missed_trial','later') AND archived=0
                            AND funnel='sales' AND dnd=0""").fetchall():
        if not open_task(con, s["family_id"]):
            ensure_task(con, s["id"], now, now)
            add_alert(con, "lost", f"lost:{s['id']}:{_iso(now)[:13]}", f"У {s['name']} на этапе «Не дошёл до ПУ» не было задачи: CRM создала её сама",
                      student_id=s["id"])
            created += 1
    return {"moved": moved, "alerts": alerts, "created": created}


def add_alert(con, kind: str, key: str, text: str, student_id=None, group_id=None) -> bool:
    cur = con.execute("INSERT OR IGNORE INTO alerts(kind, key, text, student_id, group_id, created_at) VALUES (?,?,?,?,?,?)",
                      (kind, key, text, student_id, group_id, _iso(_now())))
    return bool(cur.rowcount)


def open_alerts(con) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT * FROM alerts WHERE closed_at IS NULL ORDER BY id DESC LIMIT 30")]


def close_alert(con, aid: int, user: dict):
    con.execute("UPDATE alerts SET closed_at=?, closed_by=? WHERE id=?", (_iso(_now()), user.get("name", ""), aid))


# ---------------- для карточек, стола и отчёта ----------------

def card_info(con, sid: int) -> dict:
    rows = [dict(r) | {"kind_label": KINDS[r["kind"]]} for r in con.execute(
        """SELECT m.*, g.name AS group_name FROM trial_misses m LEFT JOIN groups g ON g.id=m.group_id
           WHERE m.student_id=? AND m.undone=0 ORDER BY m.id DESC""", (sid,))]
    s = con.execute("SELECT family_id, stage, dnd FROM students WHERE id=?", (sid,)).fetchone()
    t = open_task(con, s["family_id"]) if s else None
    return {"misses": rows, "count": len(rows), "dnd": bool(s and s["dnd"]),
            "task": dict(t) if t else None, "refusals": REFUSALS}


def overdue_color(due_at: str | None, now: datetime | None = None) -> str | None:
    if not due_at:
        return None
    now = now or _now()
    d = datetime.fromisoformat(due_at)
    if now <= d:
        return None
    return "red" if now - d > timedelta(days=1) else "yellow"


def report(con, d_from: str, d_to: str, branch_id: int | None = None) -> dict:
    """Отчёт «Не дошли до ПУ» за период по дате пробного."""
    bq = " AND g.branch_id=?" if branch_id else ""
    ba = (branch_id,) if branch_id else ()
    # записано на ПУ: все пробные в периоде (текущие + те, что ушли на этап) — по trial_at учеников и записям пропусков
    booked = con.execute(f"""SELECT COUNT(*) FROM students s JOIN groups g ON g.id=s.group_id
                             WHERE s.trial_at BETWEEN ? AND ?{bq}""", (d_from, d_to + "T23:59", *ba)).fetchone()[0]
    came = con.execute(f"""SELECT COUNT(*) FROM students s JOIN groups g ON g.id=s.group_id
                           WHERE s.trial_at BETWEEN ? AND ? AND s.trial_came=1{bq}""", (d_from, d_to + "T23:59", *ba)).fetchone()[0]
    misses = [dict(r) for r in con.execute(
        f"""SELECT m.*, s.name, s.stage, s.refusal_reason, s.converted_at, s.trial_came, s.trial_at AS cur_trial, g.name AS group_name,
                   g.branch_id FROM trial_misses m JOIN students s ON s.id=m.student_id LEFT JOIN groups g ON g.id=m.group_id
            WHERE m.undone=0 AND m.trial_at BETWEEN ? AND ?{bq}""", (d_from, d_to + "T23:59", *ba))]
    total_booked = booked + len([m for m in misses if m["kind"] == "cancel"])  # отменившие уже без даты
    by_kind = {k: sum(1 for m in misses if m["kind"] == k) for k in KINDS}
    rebooked = [m for m in misses if m["stage"] not in ("missed_trial",) and (m["cur_trial"] or m["converted_at"])]
    came_after = [m for m in rebooked if m["trial_came"] == 1 or m["converted_at"]]
    bought = [m for m in misses if m["converted_at"]]
    refused = {}
    for m in misses:
        if m["stage"] == "refused":
            refused[m["refusal_reason"] or "без причины"] = refused.get(m["refusal_reason"] or "без причины", 0) + 1
    by_group, by_resp = {}, {}
    for m in misses:
        by_group[m["group_name"] or "—"] = by_group.get(m["group_name"] or "—", 0) + 1
    now = _now()
    stuck = []
    for s in con.execute("""SELECT s.id, s.name, s.family_id FROM students s WHERE s.stage='missed_trial' AND s.archived=0""").fetchall():
        t = open_task(con, s["family_id"])
        if not t:
            stuck.append({"id": s["id"], "name": s["name"], "problem": "нет задачи"})
        elif overdue_color(t["due_at"], now):
            u = con.execute("SELECT name FROM users WHERE id=?", (t["assignee_id"],)).fetchone()
            stuck.append({"id": s["id"], "name": s["name"], "problem": f"задача просрочена с {t['due_at'][8:10]}.{t['due_at'][5:7]} {t['due_at'][11:16]}",
                          "assignee": u[0] if u else ""})
    for t in con.execute("""SELECT t.assignee_id, u.name, COUNT(*) AS n, SUM(t.status='open' AND t.due_at < ?) AS overdue
                            FROM tasks t LEFT JOIN users u ON u.id=t.assignee_id WHERE t.kind='rebook' AND t.created_at BETWEEN ? AND ?
                            GROUP BY t.assignee_id""", (_iso(now), d_from, d_to + "T23:59")):
        by_resp[t["name"] or "—"] = {"tasks": t["n"], "overdue": t["overdue"] or 0}
    nm = len(misses)
    return {"from": d_from, "to": d_to, "booked": total_booked, "came": came, "missed": nm, "by_kind": by_kind,
            "attendance": round(came * 100 / total_booked) if total_booked else None,
            "rebooked": len(rebooked), "came_after": len(came_after), "bought": len(bought), "refused": refused,
            "stuck": stuck, "by_group": by_group, "by_resp": by_resp}


def marks_report(con, d_from: date, d_to: date) -> dict:
    """Отметки в журнале по тренерам и филиалам: вовремя (до 30 минут), с опозданием, не отмечено."""
    from . import closures, services as sv, staff
    now = _now()
    rows = {}
    d = d_from
    while d <= d_to:
        for p in staff.planned(con, d):
            g = con.execute("SELECT g.*, b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id WHERE g.id=?", (p["group_id"],)).fetchone()
            end = _lesson_end(con, g, d)
            if not end or end + timedelta(minutes=30) > now or closures.is_closed(con, g["id"], d):
                continue
            j = sv.journal(con, g["id"], d.isoformat())
            if not j["rows"]:
                continue
            coach = (con.execute("SELECT name FROM users WHERE id=?", (p["main"],)).fetchone() or ["—"])[0] if p["main"] else "—"
            key = (coach, g["branch"])
            r = rows.setdefault(key, {"coach": coach, "branch": g["branch"], "on_time": 0, "late": 0, "missing": 0})
            mk = con.execute("SELECT completed_at FROM lesson_marks WHERE group_id=? AND date=?", (g["id"], d.isoformat())).fetchone()
            if _unmarked(j["rows"]):
                r["missing"] += 1
            elif mk and datetime.fromisoformat(mk[0]) <= end + timedelta(minutes=30):
                r["on_time"] += 1
            else:
                r["late"] += 1
        d += timedelta(days=1)
    return {"rows": sorted(rows.values(), key=lambda x: (-x["missing"], -x["late"], x["coach"]))}


def reassign(con, tid: int, user_id: int, by: dict):
    u = con.execute("SELECT id, name, role FROM users WHERE id=? AND active=1", (user_id,)).fetchone()
    if not u or u["role"] not in ("admin", "operator"):
        raise ApiError("Передать можно только админу или оператору")
    t = con.execute("SELECT * FROM tasks WHERE id=? AND status='open'", (tid,)).fetchone()
    if not t:
        raise ApiError("Задача не найдена")
    con.execute("UPDATE tasks SET assignee_id=? WHERE id=?", (user_id, tid))
    if t["student_id"]:
        from . import services as sv
        sv.log(con, t["student_id"], f"Задача «{t['title']}» передана: {u['name']}", by, "note")
