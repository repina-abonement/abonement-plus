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


def outside_period(con, sid, gid, day):
    """Учитываем точный срок, если для этой группы он перенесён из ERP."""
    periods = con.execute("""SELECT x.valid_from, x.valid_until
        FROM subscriptions x JOIN enrollments e ON e.id=x.enrollment_id
        WHERE x.student_id=? AND e.group_id=? AND x.kind='month' AND x.status!='cancelled'
          AND (x.month=? OR (x.valid_from<=? AND x.valid_until>=?))""",
        (sid, gid, day[:7], day, day)).fetchall()
    return bool(periods) and all(
        (start and day < start) or (end and day > end)
        for start, end in periods
    )


def correct_period(con, sub_id, data, user):
    from . import services as sv
    row = con.execute("SELECT * FROM subscriptions WHERE id=? AND kind='month'", (sub_id,)).fetchone()
    if not row:
        raise sv.ApiError("Абонемент не найден", 404)
    start, end = data.get("valid_from"), data.get("valid_until")
    try:
        if date.fromisoformat(start).isoformat() != start or (end and date.fromisoformat(end).isoformat() != end):
            raise ValueError()
    except (ValueError, TypeError):
        raise sv.ApiError("Укажите даты действия абонемента")
    if start[:7] != row["month"] or (end and end < start):
        raise sv.ApiError("Дата начала должна быть в месяце абонемента, окончание не раньше начала")
    end = end or None
    previous = row["valid_from"], row["valid_until"]
    if previous == (start, end):
        return
    if previous != (data.get("expected_from"), data.get("expected_until")):
        raise sv.ApiError("Срок уже изменился. Обновите карточку", 409)
    con.execute("UPDATE subscriptions SET valid_from=?,valid_until=? WHERE id=?", (start, end, sub_id))
    sv.log(con, row["student_id"], f"Срок абонемента #{sub_id}: {previous[0] or 'не указан'} / {previous[1] or 'не указан'} -> {start} / {end or 'не указано'}", user, "system")
    sv.audit(con, user, "subscription.period", str(sub_id))


def guard_duplicate(con, sid, gid, ym, chosen=None):
    """Не допускаем повторную продажу через другую запись той же группы."""
    from .services import ApiError
    rows=con.execute("""SELECT x.id,x.month,x.lesson_schedule FROM subscriptions x
        LEFT JOIN enrollments e ON e.id=x.enrollment_id JOIN students s ON s.id=x.student_id
        WHERE x.student_id=? AND x.status!='cancelled' AND COALESCE(x.kind,'month')='month'
        AND COALESCE(e.group_id,s.group_id)=?""",(sid,gid))
    for row in rows:
        saved=json.loads(row['lesson_schedule'] or '[]')
        overlap=bool(chosen and isinstance(saved,list) and any((c['group_id'],day) in chosen for c in saved for day in c.get('dates',[])))
        if row['month']==ym or overlap:
            raise ApiError(f"На этот период уже есть абонемент №{row['id']}. Откройте существующий абонемент — повторно вносить его не нужно")
