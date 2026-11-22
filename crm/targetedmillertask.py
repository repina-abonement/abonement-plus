"""Reconcile only Matvey Miller's December task from the actual invoice balance."""
import json

from . import money, tasks


KEY = "targeted_miller_december_task_20261122"
STUDENT_ID = 3
ERP_ID = "79204"
NAMES = {"матвей миллер", "миллер матвей"}


def _save(con, report):
    con.execute("INSERT INTO settings(key,value) VALUES (?,?)", (KEY, json.dumps(report, ensure_ascii=False)))
    return report


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
    if (not student or " ".join(student["name"].casefold().split()) not in NAMES
            or str(student["erp_id"] or "") != ERP_ID):
        return _save(con, {"state": "identity_mismatch", "student_id": STUDENT_ID, "erp_id": ERP_ID})

    invoices = con.execute("""SELECT id,price,status,paid_at FROM subscriptions
        WHERE student_id=? AND month='2026-12' AND status!='cancelled'
          AND COALESCE(kind,'month')='month' ORDER BY id""", (STUDENT_ID,)).fetchall()
    if not invoices:
        return _save(con, {"state": "complete", "result": "no_december_invoice",
                           "student_id": STUDENT_ID, "erp_id": ERP_ID,
                           "december_invoice_ids": [], "open_task": None})

    erp_status = student["erp_status"]
    covered = money.balance(con, STUDENT_ID)["covered"]
    remaining = sum(max(0, row["price"] - covered.get(row["id"], 0)) for row in invoices)
    task_id = tasks.sync_subscription_payment_tasks(con, STUDENT_ID)
    if remaining == 0 and tasks.invoice_payment_plan(con, STUDENT_ID) is None:
        task_id = tasks.schedule_renewal(con, STUDENT_ID)
    if remaining:
        con.execute("""UPDATE tasks SET status='cancelled',done_at=datetime('now'),
            result='Счёт выставлен: ожидаем оплату' WHERE student_id=? AND status='open'
            AND kind='call_lead' AND trim(title)='Новый лид'""", (STUDENT_ID,))

    expected_kind = "payment_check" if remaining else "renew"
    opened = con.execute("""SELECT id,kind,title,due_at,status,note FROM tasks
        WHERE student_id=? AND status='open' AND kind=? ORDER BY id""", (STUDENT_ID, expected_kind)).fetchall()
    unchanged = con.execute("SELECT erp_status FROM students WHERE id=?", (STUDENT_ID,)).fetchone()[0] == erp_status
    report = {
        "state": "complete" if len(opened) == 1 and unchanged else "verification_failed",
        "result": "payment_control" if remaining else "renewal",
        "student_id": STUDENT_ID, "erp_id": ERP_ID,
        "december_invoice_ids": [row["id"] for row in invoices], "remaining": remaining,
        "open_task": dict(opened[0]) if len(opened) == 1 else None,
        "open_expected_task_count": len(opened), "erp_status_unchanged": unchanged,
        "selected_task_id": task_id,
    }
    return _save(con, report)


def report(con):
    row = con.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    return json.loads(row[0]) if row else {"state": "pending", "student_id": STUDENT_ID}
