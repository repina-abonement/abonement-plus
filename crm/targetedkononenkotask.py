"""Read actual December invoice coverage and align only Kononenko's open task."""
import json

from . import money, tasks

KEY = "targeted_kononenko_december_task_20261122"


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
