"""Индивидуальные исключения дат при продаже абонемента."""
import json
from datetime import date


def validate(raw, available):
    from .services import ApiError
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, list) or any(not isinstance(d, str) for d in raw):
        raise ApiError("Передайте список исключённых дат")
    dates = sorted(set(raw))
    try:
        for d in dates:
            if date.fromisoformat(d).isoformat() != d:
                raise ValueError()
    except ValueError:
        raise ApiError("Неверная дата занятия")
    if not set(dates).issubset(set(available)):
        raise ApiError("Исключать можно только занятия выбранного месяца и группы")
    if dates and not set(available) - set(dates):
        raise ApiError("В абонементе должно остаться хотя бы одно занятие")
    return dates


def excluded(con, sid, gid, day):
    rows = con.execute("""SELECT x.excluded_dates,x.lesson_schedule,x.historical_sale FROM subscriptions x JOIN enrollments e ON e.id=x.enrollment_id
        JOIN groups g ON g.id=e.group_id JOIN groups target ON target.id=?
        WHERE x.student_id=? AND x.month=? AND x.status!='cancelled'
          AND (e.group_id=? OR (g.program_id IS NOT NULL AND g.program_id=target.program_id))""",
        (gid, sid, day[:7], gid))
    excluded_flags=[]
    for row in rows:
        if row['historical_sale'] and row['lesson_schedule']:
            components=json.loads(row['lesson_schedule'])
            excluded_flags.append(not any(c.get('group_id')==gid and day in c.get('dates',[]) for c in components))
        else:
            excluded_flags.append(day in json.loads(row['excluded_dates']))
    return bool(excluded_flags) and all(excluded_flags)
