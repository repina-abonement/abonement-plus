"""Narrow production task repair for Damir Minibaev requested on 2026-11-22."""
import json
from datetime import date

from . import erpstatus, logic, money, tasks


KEY = "targeted_minibaev_december_task_20261122"
PROFILE_KEY = "targeted_minibaev_profile_reconcile_20261122_v2"
STUDENT_ID = 4
ERP_ID = "64928"
OLD_TASK_ID = 4642
NAMES = {"дамир минибаев", "минибаев дамир"}


def _save(con, report):
    con.execute("INSERT INTO settings(key,value) VALUES (?,?)", (KEY, json.dumps(report, ensure_ascii=False)))
    return report


def _matches_student(con, student):
    if not student or " ".join(student["name"].casefold().split()) not in NAMES:
        return False
    direct = str(student["erp_id"] or "")
    if direct and direct != ERP_ID:
        return False
    owners = con.execute("SELECT id FROM students WHERE erp_id=?", (ERP_ID,)).fetchall()
    if any(row[0] != STUDENT_ID for row in owners):
        return False
    linked = []
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='erp_full_identities'").fetchone():
        linked = con.execute("""SELECT source_id,student_id FROM erp_full_identities
            WHERE namespace='student' AND (source_id=? OR student_id=?)""", (ERP_ID, STUDENT_ID)).fetchall()
        if any(str(row[0]) != ERP_ID or row[1] != STUDENT_ID for row in linked):
            return False
    if direct == ERP_ID or linked:
        return True
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='erp_history_subscriptions'").fetchone():
        return False
    historical = con.execute("""SELECT source_id FROM erp_history_subscriptions
        WHERE student_id=? AND month='2026-11'""", (STUDENT_ID,)).fetchall()
    return sorted(str(row[0]) for row in historical) == ["825123", "825124"]


def apply(con):
    previous = con.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    if previous:
        try:
            saved = json.loads(previous[0])
            if saved.get("state") == "complete":
                return saved
            con.execute("DELETE FROM settings WHERE key=?", (KEY,))
        except (TypeError, ValueError):
            return {"state": "invalid_previous_report", "student_id": STUDENT_ID}

    student = con.execute("SELECT id,name,erp_id,erp_status FROM students WHERE id=?", (STUDENT_ID,)).fetchone()
    if not _matches_student(con, student):
        return _save(con, {"state": "identity_mismatch", "student_id": STUDENT_ID, "erp_id": ERP_ID})

    invoices = con.execute("""SELECT id,price,status,paid_at FROM subscriptions
        WHERE student_id=? AND month='2026-12' AND status!='cancelled'
          AND COALESCE(kind,'month')='month' ORDER BY id""", (STUDENT_ID,)).fetchall()
    erp_status = student["erp_status"]
    branch = "invoice"
    remaining = None

    if invoices:
        covered = money.balance(con, STUDENT_ID)["covered"]
        remaining = sum(max(0, row["price"] - covered.get(row["id"], 0)) for row in invoices)
        task_id = tasks.sync_subscription_payment_tasks(con, STUDENT_ID)
        if remaining == 0 and tasks.invoice_payment_plan(con, STUDENT_ID) is None:
            task_id = tasks.schedule_renewal(con, STUDENT_ID)
    else:
        branch = "erp_history"
        history_count = 0
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='erp_history_subscriptions'").fetchone():
            history_count = con.execute("""SELECT COUNT(*) FROM erp_history_subscriptions
                WHERE student_id=? AND month='2026-11'""", (STUDENT_ID,)).fetchone()[0]
        old = con.execute("SELECT id,student_id,title,status FROM tasks WHERE id=?", (OLD_TASK_ID,)).fetchone()
        if history_count != 2:
            return _save(con, {"state": "history_mismatch", "student_id": STUDENT_ID,
                               "erp_id": ERP_ID, "november_history_count": history_count})
        if (not old or old["student_id"] != STUDENT_ID or old["status"] != "open"
                or old["title"].strip().casefold() != "новый лид"):
            return _save(con, {"state": "old_task_mismatch", "student_id": STUDENT_ID,
                               "erp_id": ERP_ID, "old_task_id": OLD_TASK_ID})
        task_id = tasks.schedule_renewal(con, STUDENT_ID)

    chosen = con.execute("""SELECT id,kind,title,due_at,status FROM tasks
        WHERE id=? AND student_id=? AND status='open' AND kind IN ('renew','payment_check')""",
        (task_id or 0, STUDENT_ID)).fetchone()
    if chosen:
        for duplicate in con.execute("""SELECT id FROM tasks WHERE student_id=? AND status='open'
            AND kind IN ('renew','payment_check') AND id!=?""", (STUDENT_ID, chosen["id"])).fetchall():
            con.execute("""UPDATE tasks SET status='cancelled',done_at=datetime('now'),
                result='Дубликат задачи продления или оплаты' WHERE id=?""", (duplicate["id"],))

    open_rows = con.execute("""SELECT id,kind,title,due_at,status FROM tasks WHERE student_id=?
        AND status='open' AND kind IN ('renew','payment_check') ORDER BY id""", (STUDENT_ID,)).fetchall()
    old_after = con.execute("SELECT status FROM tasks WHERE id=?", (OLD_TASK_ID,)).fetchone()
    expected_kind = "payment_check" if invoices and remaining else "renew"
    complete = (len(open_rows) == 1 and open_rows[0]["kind"] == expected_kind
                and con.execute("SELECT erp_status FROM students WHERE id=?", (STUDENT_ID,)).fetchone()[0] == erp_status
                and (invoices or (old_after and old_after[0] == "cancelled")))
    report = {
        "state": "complete" if complete else "verification_failed",
        "student_id": STUDENT_ID, "erp_id": ERP_ID, "branch": branch,
        "december_invoice_ids": [row["id"] for row in invoices], "remaining": remaining,
        "open_task": dict(open_rows[0]) if len(open_rows) == 1 else None,
        "open_renewal_or_payment_count": len(open_rows),
        "old_task_id": OLD_TASK_ID, "old_task_status": old_after[0] if old_after else "missing",
        "erp_status_unchanged": con.execute("SELECT erp_status FROM students WHERE id=?", (STUDENT_ID,)).fetchone()[0] == erp_status,
    }
    return _save(con, report)


def report(con):
    row = con.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    return json.loads(row[0]) if row else {"state": "pending", "student_id": STUDENT_ID}


def reconcile_profile(con, on: date | None = None):
    previous = con.execute("SELECT value FROM settings WHERE key=?", (PROFILE_KEY,)).fetchone()
    if previous:
        return json.loads(previous[0])
    on = on or date.today()
    student = con.execute("SELECT id,name,erp_id FROM students WHERE id=?", (STUDENT_ID,)).fetchone()
    identity_ok = bool(student and " ".join(student["name"].casefold().split()) in NAMES
                       and str(student["erp_id"] or "") == ERP_ID)
    cur = logic.month_of(on)
    months = erpstatus.confirmed_monthly_months(con, STUDENT_ID)
    if not identity_ok:
        result = {"state": "identity_mismatch", "student_id": STUDENT_ID, "erp_id": ERP_ID}
    elif cur not in months:
        result = {"state": "not_applied", "reason": "no_confirmed_current_subscription", "confirmed_months": months}
    else:
        status = erpstatus.sync_one(con, STUDENT_ID, on)
        current = con.execute("SELECT erp_status,funnel,stage FROM students WHERE id=?", (STUDENT_ID,)).fetchone()
        open_tasks = [dict(r) for r in con.execute("SELECT id,kind,title,due_at,status FROM tasks WHERE student_id=? AND status='open' ORDER BY id", (STUDENT_ID,))]
        result = {"state": "complete", "student_id": STUDENT_ID, "erp_id": ERP_ID,
                  "confirmed_months": months, "status": status, "funnel": current["funnel"],
                  "stage": current["stage"], "open_tasks": open_tasks}
    con.execute("INSERT INTO settings(key,value) VALUES (?,?)", (PROFILE_KEY, json.dumps(result, ensure_ascii=False)))
    return result


def profile_report(con):
    row = con.execute("SELECT value FROM settings WHERE key=?", (PROFILE_KEY,)).fetchone()
    return json.loads(row[0]) if row else {"state": "pending", "student_id": STUDENT_ID}
