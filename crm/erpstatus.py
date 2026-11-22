"""Системные статусы посетителей по подтверждённым правилам ERP (снимок 21.11.2026).

Автоматически рассчитываются только объективные системные статусы. Региональные и
служебные пользовательские статусы без подтверждённого события здесь не выдумываются.
Карточки, пришедшие из ERP, сохраняют исходный ``erp_status``, пока в CRM не появится
более новое подтверждённое событие — оплаченный абонемент текущего/будущего месяца.
"""
from __future__ import annotations

from datetime import date

from . import logic


SYSTEM_STATUSES = (
    "Новый лид", "Записан на ПУ", "Был на пробном, не купил абонемент",
    "Не пришел на пробный урок", "Новый оплативший посетитель",
    "Постоянный посетитель", "Вернувшийся", "Не оплачен текущий месяц",
    "Нет абонемента", "Ходит разово", "Куплен на будущий период",
)

STAGE_STATUSES = {
    "no_answer": "Не удалось связаться",
    "later": "Думают",
    "refused": "Клиент отказался",
    "reinvite_pu": "Думают",
}

USER_STATUSES = (
    "Думают", "Не удалось связаться", "Лист ожидания", "Клиент отказался",
    "Ушедший", "ОПЕРАТОРУ", "Взрослый ребенок", "Новопеределкино",
    "Пыхтино", "Вечный недозвон", "Разовик",
)


def calculate(con, student, on: date | None = None) -> str:
    on = on or date.today()
    cur, prev = logic.month_of(on), logic.shift_month(logic.month_of(on), -1)
    sid = student["id"]
    if con.execute("SELECT 1 FROM waitlist WHERE student_id=? AND status IN ('queued','offered')", (sid,)).fetchone():
        return "Лист ожидания"
    paid = [tuple(r) for r in con.execute(
        """SELECT month,COALESCE(kind,'month') FROM subscriptions
           WHERE student_id=? AND status='paid' ORDER BY month""", (sid,)
    )]
    monthly = [month for month, kind in paid if kind == "month"]
    singles = [month for month, kind in paid if kind == "single"]

    if monthly:
        has_cur = cur in monthly
        has_future = any(month > cur for month in monthly)
        had_prev = prev in monthly
        had_earlier = any(month < cur for month in monthly)
        if not has_cur and has_future:
            return "Куплен на будущий период"
        if has_cur:
            if had_prev or has_future:
                return "Постоянный посетитель"
            if had_earlier:
                return "Вернувшийся"
            return "Новый оплативший посетитель"
        if had_prev:
            return "Не оплачен текущий месяц"
        return "Нет абонемента"
    if singles:
        return "Ходит разово"

    stage = student["stage"]
    if student["funnel"] == "members":
        return "Ушедший" if stage == "left" else "Нет абонемента"
    if stage in STAGE_STATUSES:
        return STAGE_STATUSES[stage]
    if stage in ("trial_booked", "on_trial", "trial_done") and student["trial_at"]:
        return "Записан на ПУ"
    if stage == "missed_trial" or student["trial_came"] == 0:
        return "Не пришел на пробный урок"
    if stage == "how_was" or student["trial_came"] == 1:
        return "Был на пробном, не купил абонемент"
    return "Новый лид"


def sync_one(con, sid: int, on: date | None = None, *, log: bool = True) -> str:
    row = con.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
    if not row:
        return ""
    month = logic.month_of(on or date.today())
    objective_paid = con.execute(
        """SELECT 1 FROM subscriptions WHERE student_id=? AND status='paid'
           AND COALESCE(kind,'month')='month' AND month>=? LIMIT 1""", (sid, month)
    ).fetchone()
    if (row["erp_id"] or "").strip() and not objective_paid:
        return row["erp_status"] or ""
    value = calculate(con, row, on)
    has_current = con.execute(
        """SELECT 1 FROM subscriptions WHERE student_id=? AND status='paid'
           AND COALESCE(kind,'month')='month' AND month=? LIMIT 1""", (sid, month)
    ).fetchone()
    if has_current and (row["funnel"] != "members" or row["stage"] != "active"):
        con.execute("UPDATE students SET funnel='members',stage='active',priority=0 WHERE id=?", (sid,))
        con.execute("""UPDATE tasks SET status='cancelled',done_at=datetime('now'),result='Отменена: есть оплаченный абонемент'
                       WHERE student_id=? AND status='open' AND kind IN
                       ('call_lead','no_answer','close_sale','trial_confirm','trial_today','how_was','why_not_bought','reinvite_pu','no_show')""", (sid,))
        if log:
            from .db import now_iso
            con.execute("INSERT INTO history(student_id,ts,user_name,kind,text) VALUES (?,?,'CRM','stage','Оплаченный абонемент: карточка переведена в действующие клиенты')",
                        (sid, now_iso()))
    if value != (row["erp_status"] or ""):
        con.execute("UPDATE students SET erp_status=? WHERE id=?", (value, sid))
        if log:
            from .db import now_iso
            con.execute("INSERT INTO history(student_id,ts,user_name,kind,text) VALUES (?,?,'CRM','system',?)",
                        (sid, now_iso(), f"Статус по правилам ERP: «{value}»"))
    return value


def sync_native(con, on: date | None = None) -> int:
    changed = 0
    for row in con.execute("SELECT id,erp_status FROM students WHERE archived=0 AND COALESCE(erp_id,'')='' ORDER BY id"):
        before = row["erp_status"] or ""
        changed += sync_one(con, row["id"], on) != before
    return changed
