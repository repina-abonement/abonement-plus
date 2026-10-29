"""Перевод ученика в другую группу без удаления абонемента.

С даты перевода ребёнок ходит в новую группу, до неё остаётся в журнале старой (посещения и зарплата не сдвигаются).
Абонементы старой группы с месяца перевода переезжают в новую: та же цена одного занятия (со скидкой, наценкой
и перерасчётом, как продали), число занятий по новому расписанию. Если перевод с середины месяца, абонемент делится:
занятия до даты остаются старой группе, после неё переходят в новую. Разница по деньгам сама уходит на депозит
или в долг: платежи ученика общие и раскладываются по абонементам заново (money.reconcile).
"""
from __future__ import annotations

import math
from datetime import date

from . import logic
from .db import now_iso
from .services import ApiError, _clean_days, _days_label, log, sync_primary, vacations_of


def init(con):
    cols = {r[1] for r in con.execute("PRAGMA table_info(enrollments)")}
    if "start_date" not in cols:  # с какой даты ребёнок в группе и до какой (не включая): нужно для журнала при переводе
        con.execute("ALTER TABLE enrollments ADD COLUMN start_date TEXT NOT NULL DEFAULT ''")
        con.execute("ALTER TABLE enrollments ADD COLUMN end_date TEXT NOT NULL DEFAULT ''")


def _dates(con, group_id: int, days: list[int], ym: str, vacations) -> list[date]:
    from . import closures
    ms, me = logic.month_bounds(ym)
    closed = closures.closed_dates(con, group_id, ms, me)
    return [d for d in logic.lesson_dates(days, ym) if d.isoformat() not in closed and not logic.in_vacation(d, vacations)]


def _load(con, eid: int, new_gid: int, start: str, weekdays):
    e = con.execute("""SELECT e.*, g.name AS group_name, g.weekdays AS gw, g.program_id FROM enrollments e
                       JOIN groups g ON g.id=e.group_id WHERE e.id=?""", (eid,)).fetchone()
    if not e or not e["active"]:
        raise ApiError("Группа ученика не найдена", 404)
    g = con.execute("SELECT * FROM groups WHERE id=? AND active=1 AND is_makeup=0", (new_gid,)).fetchone()
    if not g:
        raise ApiError("Выберите группу, куда переводим")
    if g["id"] == e["group_id"]:
        raise ApiError("Ученик уже в этой группе")
    if e["program_id"] or g["program_id"]:
        raise ApiError("Перевод в сборную и из неё пока делается вручную")
    if con.execute("SELECT 1 FROM enrollments WHERE student_id=? AND group_id=? AND active=1", (e["student_id"], g["id"])).fetchone():
        raise ApiError("Ученик уже ходит в эту группу")
    try:
        d0 = date.fromisoformat(start or "")
    except ValueError:
        raise ApiError("Укажите дату, с которой ребёнок ходит в новую группу")
    if d0 < date.today().replace(day=1):
        raise ApiError("Перевести можно с начала текущего месяца или позже")
    marks = con.execute("SELECT COUNT(*) FROM attendance WHERE student_id=? AND group_id=? AND date>=?",
                        (e["student_id"], e["group_id"], d0.isoformat())).fetchone()[0]
    if marks:
        raise ApiError(f"В старой группе уже есть отметки с {d0:%d.%m}. Выберите дату после последней отметки")
    new_days = _clean_days(weekdays, g["weekdays"]) if weekdays else ""
    return dict(e), dict(g), d0, new_days


def plan(con, eid: int, new_gid: int, start: str, weekdays=None) -> dict:
    """Что произойдёт при переводе: по каждому абонементу с месяца перевода."""
    e, g, d0, new_days = _load(con, eid, new_gid, start, weekdays)
    sid = e["student_id"]
    vac = vacations_of(con, sid)
    old_days = logic.parse_weekdays(e["weekdays"]) or logic.parse_weekdays(e["gw"])
    nd = logic.parse_weekdays(new_days) or logic.parse_weekdays(g["weekdays"])
    months = []
    for x in con.execute("""SELECT * FROM subscriptions WHERE enrollment_id=? AND month>=? AND status!='cancelled'
                            ORDER BY month""", (eid, logic.month_of(d0))).fetchall():
        x = dict(x)
        unit = x["price"] / x["lessons"] if x["lessons"] else x["per_lesson"]
        keep = [d for d in _dates(con, e["group_id"], old_days, x["month"], vac) if d < d0]
        new = [d for d in _dates(con, g["id"], nd, x["month"], vac) if d >= d0]
        keep_price = round(unit * len(keep)) if keep else 0
        new_price = math.ceil(unit * len(new) - 1e-9)
        months.append({"sub_id": x["id"], "month": x["month"], "label": logic.month_label(x["month"]), "status": x["status"],
                       "old_lessons": x["lessons"], "old_price": x["price"],
                       "keep_lessons": len(keep), "keep_price": keep_price,
                       "new_lessons": len(new), "new_price": new_price, "new_dates": [d.isoformat() for d in new]})
    diff = sum(m["keep_price"] + m["new_price"] - m["old_price"] for m in months)
    return {"student_id": sid, "from_group": e["group_name"], "to_group": g["name"], "start": d0.isoformat(),
            "days_label": _days_label(new_days) if new_days else "", "months": months, "diff": diff}


def apply(con, eid: int, new_gid: int, start: str, user, weekdays=None) -> dict:
    p = plan(con, eid, new_gid, start, weekdays)
    e, g, d0, new_days = _load(con, eid, new_gid, start, weekdays)
    sid = e["student_id"]
    con.execute("UPDATE enrollments SET active=0, end_date=? WHERE id=?", (d0.isoformat(), eid))
    new_eid = con.execute("""INSERT INTO enrollments(student_id, group_id, weekdays, tariff, created_at, start_date)
                             VALUES (?,?,?,?,?,?)""", (sid, g["id"], new_days, e["tariff"], now_iso(), d0.isoformat())).lastrowid
    cols = [r[1] for r in con.execute("PRAGMA table_info(subscriptions)") if r[1] != "id"]
    notes = []
    for m in p["months"]:
        x = dict(con.execute("SELECT * FROM subscriptions WHERE id=?", (m["sub_id"],)).fetchone())
        part = lambda v, n: round((v or 0) * n / x["lessons"]) if x["lessons"] else 0  # скидку и наценку делим по занятиям
        share = lambda v: part(v, m["new_lessons"])
        if m["keep_lessons"]:  # с середины месяца: начало месяца остаётся старой группе, остальное новым абонементом
            con.execute("UPDATE subscriptions SET lessons=?, price=?, late_fee=?, discount_amount=? WHERE id=?",
                        (m["keep_lessons"], m["keep_price"], part(x.get("late_fee"), m["keep_lessons"]),
                         part(x.get("discount_amount"), m["keep_lessons"]), x["id"]))
            row = x | {"enrollment_id": new_eid, "lessons": m["new_lessons"], "price": m["new_price"], "credit_lessons": 0,
                       "late_fee": share(x.get("late_fee")), "discount_amount": share(x.get("discount_amount")),
                       "status": "invoiced", "paid_at": None, "created_at": now_iso()}
            con.execute(f"INSERT INTO subscriptions({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [row[c] for c in cols])
        else:
            con.execute("UPDATE subscriptions SET enrollment_id=?, lessons=?, price=?, late_fee=?, discount_amount=? WHERE id=?",
                        (new_eid, m["new_lessons"], m["new_price"], share(x.get("late_fee")), share(x.get("discount_amount")), x["id"]))
        notes.append(f"{m['label']}: {m['old_lessons']} зан. {m['old_price']} ₽ → "
                     + (f"{m['keep_lessons']} зан. в старой группе {m['keep_price']} ₽ и " if m["keep_lessons"] else "")
                     + f"{m['new_lessons']} зан. в новой {m['new_price']} ₽")
    sync_primary(con, sid)
    from . import capacity, money
    money.reconcile(con, sid)
    capacity.ensure_roster(con, g["id"])
    log(con, sid, f"Переведён из группы {e['group_name']} в {g['name']} с {d0:%d.%m.%Y}"
        + (f" ({_days_label(new_days)})" if new_days else "") + (". Абонементы: " + "; ".join(notes) if notes else ""), user, "system")
    return p | {"enrollment_id": new_eid}
