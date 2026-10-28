"""Лимит детей на тренировку и «предупредил, что не придёт».

В филиале может быть лимит детей на одну тренировку (в ТЦ «Небо» 10). Считаются все, кто придёт в эту дату:
ученики группы, пробные, отработки. Ученики в отпуске и те, кто заранее предупредил о пропуске, место не занимают,
поэтому на эту дату в группу можно записать другого ребёнка на отработку или пробное.

Предупреждение о пропуске хранится как пропуск с пометкой warned=1: его можно отработать, как обычный пропуск.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import logic
from .services import ApiError


class CapacityError(ApiError):
    pass


def init(con):
    bcols = {r[1] for r in con.execute("PRAGMA table_info(branches)")}
    if "lesson_limit" not in bcols:
        con.execute("ALTER TABLE branches ADD COLUMN lesson_limit INTEGER NOT NULL DEFAULT 0")
        # Юлия 27.10.2026: в ТЦ «Небо» не больше 10 детей на тренировку
        con.execute("UPDATE branches SET lesson_limit=10 WHERE name LIKE '%Небо%'")
        con.execute("""UPDATE groups SET capacity=10 WHERE capacity>10 AND is_makeup=0
                       AND branch_id IN (SELECT id FROM branches WHERE lesson_limit=10)""")
    acols = {r[1] for r in con.execute("PRAGMA table_info(attendance)")}
    if "warned" not in acols:
        con.execute("ALTER TABLE attendance ADD COLUMN warned INTEGER NOT NULL DEFAULT 0")
    if "note" not in acols:
        con.execute("ALTER TABLE attendance ADD COLUMN note TEXT NOT NULL DEFAULT ''")


def limit_for(con, group_id: int) -> int:
    r = con.execute("""SELECT b.lesson_limit, g.is_makeup FROM groups g JOIN branches b ON b.id=g.branch_id WHERE g.id=?""",
                    (group_id,)).fetchone()
    return int(r[0] or 0) if r and not r[1] else 0


def _partners(con, group_id: int) -> list[int]:
    """Группы, которые занимаются вместе на одном поле (например, ОФП 6–8 и сборная в субботу 17:00)."""
    try:
        r = con.execute("SELECT shared_with FROM groups WHERE id=?", (group_id,)).fetchone()
        out = {group_id}
        if r and r[0]:
            out.add(r[0])
        for x in con.execute("SELECT id FROM groups WHERE shared_with IN (%s) AND active=1" % ",".join("?" * len(out)), list(out)):
            out.add(x[0])
        return sorted(out)
    except Exception:  # noqa: BLE001 — до миграции колонки нет
        return [group_id]


def occupancy(con, group_id: int, day: str) -> dict:
    """Сколько детей придёт на тренировку группы в дату и есть ли свободные места."""
    from . import services as sv
    j = sv.journal(con, group_id, day)
    rows = list(j["rows"])
    for pg in _partners(con, group_id):
        if pg != group_id:
            pj = sv.journal(con, pg, day)
            if pj["has_lesson"]:
                rows += pj["rows"]
    coming, warned, vacation = 0, [], 0
    for r in rows:
        if r.get("status") == "vacation":
            vacation += 1
        elif r.get("warned"):
            warned.append(r["name"])
        else:
            coming += 1
    lim = limit_for(con, group_id)
    return {"group_id": group_id, "date": day, "has_lesson": j["has_lesson"], "limit": lim, "count": coming,
            "free": max(0, lim - coming) if lim else None, "warned": warned, "vacation": vacation}


def ensure(con, group_id: int, day: str, what: str = "записать"):
    """Вызывать после записи (в той же транзакции): если вышли за лимит, запись откатится."""
    lim = limit_for(con, group_id)
    if not lim:
        return
    o = occupancy(con, group_id, day)
    if o["count"] > lim:
        g = con.execute("SELECT name FROM groups WHERE id=?", (group_id,)).fetchone()
        raise CapacityError(f"Нельзя {what}: на {date.fromisoformat(day).strftime('%d.%m')} в группе {g[0] if g else ''} "
                            f"уже {lim} из {lim} детей. Место освободится, если кто-то предупредит, что не придёт")


def ensure_roster(con, group_id: int, weeks: int = 6):
    """Новый ученик в группе: проверяем ближайшие тренировки (постоянный состав не должен быть больше лимита)."""
    lim = limit_for(con, group_id)
    if not lim:
        return
    g = con.execute("SELECT weekdays FROM groups WHERE id=?", (group_id,)).fetchone()
    days = logic.parse_weekdays(g[0]) if g else []
    d = date.today()
    for _ in range(weeks * 7):
        if d.weekday() in days:
            ensure(con, group_id, d.isoformat(), "добавить ученика в группу")
        d += timedelta(days=1)


def _student_lessons(con, sid: int, start: date, days: int) -> list[dict]:
    out = []
    for e in con.execute("""SELECT e.group_id, e.weekdays, g.name, g.weekdays AS gw, g.time, g.day_times, g.is_makeup
                            FROM enrollments e JOIN groups g ON g.id=e.group_id WHERE e.student_id=? AND e.active=1 AND g.active=1""",
                         (sid,)).fetchall():
        wd = logic.parse_weekdays(e["weekdays"]) if e["weekdays"] else logic.parse_weekdays(e["gw"])
        from . import closures
        closed = closures.closed_dates(con, e["group_id"], start, start + timedelta(days=days))
        for i in range(days):
            d = start + timedelta(days=i)
            if d.weekday() in wd and d.isoformat() not in closed:
                out.append({"group_id": e["group_id"], "group": e["name"], "date": d.isoformat(),
                            "time": logic.group_time(dict(e) | {"weekdays": e["gw"]}, d.weekday())})
    out.sort(key=lambda x: (x["date"], x["time"] or ""))
    return out


def family_lessons(con, family_id: int, days: int = 15) -> list[dict]:
    """Ближайшие тренировки детей семьи с отметками «предупредил» — для окна переписки и карточки."""
    from . import services as sv
    today = date.today()
    out = []
    for s in con.execute("""SELECT id, name FROM students WHERE family_id=? AND funnel='members' AND stage!='left' AND archived=0
                            ORDER BY name""", (family_id,)).fetchall():
        vac = sv.vacations_of(con, s["id"])
        lessons = []
        now_t = datetime.now().strftime("%H:%M")
        for les in _student_lessons(con, s["id"], today, days):
            if les["date"] == today.isoformat() and les["time"] and les["time"] <= now_t:
                continue  # сегодняшняя уже началась
            a = con.execute("SELECT status, warned, note FROM attendance WHERE student_id=? AND group_id=? AND date=?",
                            (s["id"], les["group_id"], les["date"])).fetchone()
            les["warned"] = bool(a and a["warned"])
            les["note"] = a["note"] if a else ""
            les["vacation"] = logic.in_vacation(date.fromisoformat(les["date"]), vac)
            les["marked"] = a["status"] if a and not a["warned"] else None
            lessons.append(les)
        out.append({"id": s["id"], "name": s["name"], "lessons": lessons[:5]})
    for s in con.execute("""SELECT s.id, s.name, s.trial_at, g.name AS grp FROM students s LEFT JOIN groups g ON g.id=s.group_id
                            WHERE s.family_id=? AND s.funnel='sales' AND s.archived=0 AND s.trial_at>=?
                            AND s.stage IN ('trial_booked','trial_reminder')""", (family_id, datetime.now().strftime("%Y-%m-%dT%H:%M"))):
        out.append({"id": s["id"], "name": s["name"], "lessons": [], "trial": {"at": s["trial_at"], "group": s["grp"] or ""}})
    return out


def warn(con, sid: int, day: str, group_id: int | None, note: str, user: dict) -> dict:
    """Ребёнок заранее предупредил, что не придёт. Место на эту дату освобождается."""
    from . import services as sv
    s = sv.get_student_row(con, sid)
    d = date.fromisoformat(day)
    if d < date.today():
        raise CapacityError("Дата уже прошла: отметьте пропуск в журнале")
    lessons = [x for x in _student_lessons(con, sid, d, 1)]
    if group_id:
        lessons = [x for x in lessons if x["group_id"] == group_id]
    if not lessons:
        raise CapacityError("В этот день у ребёнка нет тренировки")
    les = lessons[0]
    a = con.execute("SELECT id, status, warned FROM attendance WHERE student_id=? AND group_id=? AND date=?",
                    (sid, les["group_id"], day)).fetchone()
    note = (note or "").strip()[:200]
    if a:
        con.execute("UPDATE attendance SET status='absent', warned=1, note=? WHERE id=?", (note, a["id"]))
    else:
        con.execute("INSERT INTO attendance(student_id, group_id, date, status, warned, note) VALUES (?,?,?,'absent',1,?)",
                    (sid, les["group_id"], day, note))
    sv.log(con, sid, f"Предупредил, что не придёт {d.strftime('%d.%m')} ({les['group']})" + (f": {note}" if note else ""), user, "attendance")
    o = occupancy(con, les["group_id"], day)
    return {"group_id": les["group_id"], "group": les["group"], "date": day, "name": s["name"], "occupancy": o}


def unwarn(con, sid: int, day: str, group_id: int, user: dict):
    from . import services as sv
    a = con.execute("SELECT id FROM attendance WHERE student_id=? AND group_id=? AND date=? AND warned=1", (sid, group_id, day)).fetchone()
    if not a:
        return
    if day < date.today().isoformat():
        raise CapacityError("Дата уже прошла: поправьте отметку в журнале")
    # если на освободившееся место уже кого-то записали и мест не останется — не даём отменить
    con.execute("UPDATE makeups SET status='cancelled' WHERE absence_id=? AND status='booked'", (a["id"],))
    con.execute("DELETE FROM attendance WHERE id=?", (a["id"],))
    ensure(con, group_id, day, "отменить предупреждение (место уже заняли)")
    sv.log(con, sid, f"Отменено «не придёт {date.fromisoformat(day).strftime('%d.%m')}»: ребёнок придёт", user, "attendance")


def free_slots(con, days: int = 14, branch_id: int | None = None, age_of: int | None = None) -> list[dict]:
    """Тренировки ближайших дней в филиалах с лимитом, где есть свободные места (для отработок и пробных)."""
    today = date.today()
    now_t = datetime.now().strftime("%H:%M")
    out = []
    for g in con.execute("""SELECT g.*, b.name AS branch, b.lesson_limit FROM groups g JOIN branches b ON b.id=g.branch_id
                            WHERE g.active=1 AND g.is_makeup=0 AND b.lesson_limit>0 AND g.program_id IS NULL""" + (" AND g.branch_id=?" if branch_id else ""),
                         (branch_id,) if branch_id else ()).fetchall():
        wd = logic.parse_weekdays(g["weekdays"])
        for i in range(days):
            d = today + timedelta(days=i)
            if d.weekday() not in wd:
                continue
            t = logic.group_time(dict(g), d.weekday()) or ""
            if i == 0 and t and t <= now_t:
                continue
            o = occupancy(con, g["id"], d.isoformat())
            if o["free"] and o["has_lesson"]:
                out.append({"group_id": g["id"], "group": g["name"], "branch": g["branch"], "age": g["age"], "date": d.isoformat(),
                            "time": t, "free": o["free"], "count": o["count"], "limit": o["limit"], "warned": o["warned"]})
    out.sort(key=lambda x: (x["date"], x["time"]))
    return out
