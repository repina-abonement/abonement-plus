"""Read actual December invoice coverage and align only Kononenko's open task."""
import json
from datetime import date

from . import erpstatus, logic, money, tasks

KEY = "targeted_kononenko_december_task_20261122"
PROFILE_KEY = "targeted_kononenko_profile_reconcile_20261122_v3"


def apply(con):
    previous = con.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    if previous:
        try:
            saved = json.loads(previous[0])
            if saved.get("state") == "complete":
                return saved
            con.execute("DELETE FROM settings WHERE key=?", (KEY,))
        except (TypeError, ValueError):
            return {"state": "invalid_previous_report"}
    student = con.execute("SELECT id,name,erp_id,erp_status FROM students WHERE id=2").fetchone()
    if not student or " ".join(student["name"].casefold().split()) not in {"иван кононенко", "кононенко иван"} or str(student["erp_id"] or "") != "92421":
        report = {"state": "identity_mismatch", "student_id": 2}
    else:
        erp_status = student["erp_status"]
        covered = money.balance(con, 2)["covered"]
        invoices = []
        for row in con.execute("""SELECT id,price,status,paid_at FROM subscriptions WHERE student_id=2
                AND month='2026-12' AND status!='cancelled' AND COALESCE(kind,'month')='month' ORDER BY id"""):
            paid = min(row["price"], max(0, covered.get(row["id"], 0)))
            invoices.append({"id": row["id"], "price": row["price"], "covered": paid,
                             "remaining": max(0, row["price"] - paid), "status": row["status"], "paid_at": row["paid_at"]})
        tasks.sync_subscription_payment_tasks(con, 2)
        if invoices and not any(x["remaining"] for x in invoices) and tasks.invoice_payment_plan(con, 2) is None:
            tasks.schedule_renewal(con, 2)
        opened = con.execute("""SELECT id,kind,title,due_at,status,note FROM tasks WHERE student_id=2 AND status='open'
            ORDER BY CASE kind WHEN 'payment_check' THEN 0 WHEN 'renew' THEN 1 ELSE 2 END,due_at,id LIMIT 1""").fetchone()
        unchanged = con.execute("SELECT erp_status FROM students WHERE id=2").fetchone()[0] == erp_status
        report = {"state": "complete", "student_id": 2, "erp_id": "92421", "invoices": invoices,
                  "open_task": dict(opened) if opened else None, "erp_status_unchanged": unchanged}
    con.execute("INSERT INTO settings(key,value) VALUES (?,?)", (KEY, json.dumps(report, ensure_ascii=False)))
    return report


def report(con):
    row = con.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    return json.loads(row[0]) if row else {"state": "pending"}


def reconcile_profile(con, on: date | None = None):
    previous = con.execute("SELECT value FROM settings WHERE key=?", (PROFILE_KEY,)).fetchone()
    if previous:
        return json.loads(previous[0])
    on = on or date.today()
    student = con.execute("SELECT id,name,erp_id,erp_status,funnel,stage FROM students WHERE id=2").fetchone()
    identity_ok = bool(student and " ".join(student["name"].casefold().split()) in {"иван кононенко", "кононенко иван"}
                       and str(student["erp_id"] or "") == "92421")
    cur, nxt = logic.month_of(on), logic.shift_month(logic.month_of(on), 1)
    paid_months = [month for month in erpstatus.confirmed_monthly_months(con, 2) if month in (cur, nxt)]
    if not identity_ok:
        result = {"state": "identity_mismatch", "student_id": 2}
    elif paid_months != [cur, nxt]:
        result = {"state": "not_applied", "reason": "current_and_next_month_not_both_paid", "paid_months": paid_months}
    else:
        status = erpstatus.sync_one(con, 2, on)
        current = con.execute("SELECT erp_status,funnel,stage FROM students WHERE id=2").fetchone()
        lead_tasks = con.execute("""SELECT COUNT(*) FROM tasks WHERE student_id=2 AND status='open' AND kind IN
            ('call_lead','no_answer','close_sale','trial_confirm','trial_today','how_was','why_not_bought','reinvite_pu','no_show')""").fetchone()[0]
        result = {"state": "complete", "student_id": 2, "erp_id": "92421", "paid_months": paid_months,
                  "status": status, "funnel": current["funnel"], "stage": current["stage"], "open_lead_tasks": lead_tasks}
    con.execute("INSERT INTO settings(key,value) VALUES (?,?)", (PROFILE_KEY, json.dumps(result, ensure_ascii=False)))
    return result


def profile_report(con):
    row = con.execute("SELECT value FROM settings WHERE key=?", (PROFILE_KEY,)).fetchone()
    return json.loads(row[0]) if row else {"state": "pending"}
