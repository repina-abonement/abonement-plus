"""Narrow production recalculation requested for the two Yamroz invoices."""
import json

from . import money, tasks

KEY = "targeted_renewal_payment_20261122_yamroz"
TARGETS = (
    (8, 11, {"артемий ямроз", "ямроз артемий"}),
    (9, 10, {"лукерия ямроз", "ямроз лукерия"}),
)


def apply(con):
    previous = con.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    if previous:
        try:
            saved = json.loads(previous[0])
            if saved.get("state") == "complete":
                return saved
            con.execute("DELETE FROM settings WHERE key=?", (KEY,))
        except (TypeError, ValueError):
            return {"state": "invalid_previous_report", "rows": []}
    rows = []
    for student_id, invoice_id, names in TARGETS:
        student = con.execute("SELECT id,name,erp_status FROM students WHERE id=?", (student_id,)).fetchone()
        invoice = con.execute("SELECT id,student_id,month,price,status FROM subscriptions WHERE id=?", (invoice_id,)).fetchone()
        if not student or " ".join(student["name"].casefold().split()) not in names:
            rows.append({"student_id": student_id, "invoice_id": invoice_id, "state": "student_mismatch"})
            continue
        if not invoice or invoice["student_id"] != student_id or invoice["status"] == "cancelled":
            rows.append({"student_id": student_id, "invoice_id": invoice_id, "state": "invoice_mismatch"})
            continue
        erp_status = student["erp_status"]
        covered = money.balance(con, student_id)["covered"].get(invoice_id, 0)
        remaining = max(0, invoice["price"] - covered)
        tasks.sync_subscription_payment_tasks(con, student_id)
        if remaining == 0 and tasks.invoice_payment_plan(con, student_id) is None:
            tasks.schedule_renewal(con, student_id)
        opened = con.execute("""SELECT id,kind,title,due_at,status FROM tasks
            WHERE student_id=? AND status='open' AND kind IN ('payment_check','renew')
            ORDER BY CASE kind WHEN 'payment_check' THEN 0 ELSE 1 END,due_at,id LIMIT 1""", (student_id,)).fetchone()
        unchanged = con.execute("SELECT erp_status FROM students WHERE id=?", (student_id,)).fetchone()[0] == erp_status
        state = "applied" if opened and unchanged else "verification_failed"
        rows.append({"student_id": student_id, "invoice_id": invoice_id, "invoice_status": invoice["status"],
                     "remaining": remaining, "open_task_id": opened["id"] if opened else None,
                     "open_task_kind": opened["kind"] if opened else None,
                     "open_task_title": opened["title"] if opened else None,
                     "open_task_due": opened["due_at"] if opened else None,
                     "erp_status_unchanged": unchanged, "state": state})
    report = {"state": "complete" if len(rows) == 2 and all(row["state"] == "applied" for row in rows) else "not_applied", "rows": rows}
    con.execute("INSERT INTO settings(key,value) VALUES (?,?)", (KEY, json.dumps(report, ensure_ascii=False)))
    return report


def report(con):
    row = con.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    return json.loads(row[0]) if row else {"state": "pending", "rows": []}
