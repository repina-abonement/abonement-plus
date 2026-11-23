"""Системные статусы посетителей по подтверждённым правилам ERP (снимок 21.11.2026).

Автоматически рассчитываются только объективные системные статусы. Региональные и
служебные пользовательские статусы без подтверждённого события здесь не выдумываются.
При наличии подтверждённых абонементов статус определяется по месяцу действия.
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


def confirmed_monthly_months(con, sid: int) -> list[str]:
    """Months with confirmed service entitlement, without inventing a CRM sale/payment."""
    months = {r[0] for r in con.execute(
        """SELECT month FROM subscriptions WHERE student_id=? AND status='paid'
           AND COALESCE(kind,'month')='month'""", (sid,)
    )}
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='erp_history_subscriptions'").fetchone():
        months.update(r[0] for r in con.execute(
            "SELECT DISTINCT month FROM erp_history_subscriptions WHERE student_id=?", (sid,)
        ))
    return sorted(months)


def calculate(con, student, on: date | None = None, *, monthly=None) -> str:
    on = on or date.today()
    cur = logic.month_of(on)
    sid = student["id"]
    months = confirmed_monthly_months(con, sid) if monthly is None else monthly
    if months:
        if cur in months:
            return "Постоянный посетитель"
        if max(months) > cur:
            return "Куплен на будущий период"
        return "Не оплачен текущий месяц"

    if con.execute("SELECT 1 FROM waitlist WHERE student_id=? AND status IN ('queued','offered')", (sid,)).fetchone():
        return "Лист ожидания"
    singles = con.execute("SELECT 1 FROM subscriptions WHERE student_id=? AND status='paid' AND kind='single' LIMIT 1", (sid,)).fetchone()
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
    monthly = confirmed_monthly_months(con, sid)
    if (row["erp_id"] or "").strip() and not monthly:
        return row["erp_status"] or ""
    value = calculate(con, row, on, monthly=monthly)
    has_current = month in monthly
    if has_current and (row["funnel"] != "members" or row["stage"] != "active"):
        con.execute("UPDATE students SET funnel='members',stage='active',priority=0 WHERE id=?", (sid,))
        if log:
            from .db import now_iso
            con.execute("INSERT INTO history(student_id,ts,user_name,kind,text) VALUES (?,?,'CRM','stage','Оплаченный абонемент: карточка переведена в действующие клиенты')",
                        (sid, now_iso()))
    if monthly and max(monthly) >= month:
        obsolete = """student_id=? AND status='open' AND source!='manual' AND
                      (kind IN ('call_lead','no_answer','close_sale','trial_confirm','trial_today','how_was',
                                'why_not_bought','reinvite_pu','no_show') OR title='Новый лид')"""
        if con.execute(f"SELECT 1 FROM tasks WHERE {obsolete} LIMIT 1",(sid,)).fetchone():
            con.execute(f"""UPDATE tasks SET status='cancelled',done_at=datetime('now'),
                            result='Есть подтверждённый абонемент' WHERE {obsolete}""",(sid,))
    if value != (row["erp_status"] or ""):
        con.execute("UPDATE students SET erp_status=? WHERE id=?", (value, sid))
        if log:
            from .db import now_iso
            con.execute("INSERT INTO history(student_id,ts,user_name,kind,text) VALUES (?,?,'CRM','system',?)",
                        (sid, now_iso(), f"Статус по правилам ERP: «{value}»"))
    return value


def sync_all(con, on: date | None = None) -> int:
    """Reconcile every active card; imported cards without newer evidence remain untouched."""
    changed = 0
    for row in con.execute("SELECT id,erp_status FROM students WHERE archived=0 ORDER BY id"):
        before = row["erp_status"] or ""
        changed += sync_one(con, row["id"], on) != before
    return changed


def sync_native(con, on: date | None = None) -> int:
    """Backward-compatible name: the safe reconciliation now covers the whole CRM."""
    return sync_all(con, on)
