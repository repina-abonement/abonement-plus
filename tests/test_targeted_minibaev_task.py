import os
import tempfile
import unittest

from crm import db, targetedminibaevtask


class TargetedMinibaevTaskTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(os.path.join(tempfile.mkdtemp(), "minibaev.sqlite3"))
        db.init_db(self.con)
        self.con.execute("DELETE FROM settings WHERE key=?", (targetedminibaevtask.KEY,))
        self.con.execute("INSERT INTO families(id,name) VALUES (4,'Минибаевы')")
        self.con.execute("""INSERT INTO students(id,family_id,name,erp_id,stage,stage_changed_at,created_at)
            VALUES (4,4,'Минибаев Дамир','64928','lead','2026-11-01','2026-11-01')""")
        branch = self.con.execute("INSERT INTO branches(name) VALUES ('Synthetic')").lastrowid
        for number in (1, 2):
            group = self.con.execute("INSERT INTO groups(branch_id,name,weekdays) VALUES (?,?,?)",
                                     (branch, f"Group {number}", "5")).lastrowid
            self.con.execute("""INSERT INTO erp_history_subscriptions
                (source_id,student_id,group_id,month,valid_from,valid_until,lessons,source_payload,imported_at,imported_by)
                VALUES (?,4,?,'2026-11','2026-11-01','2026-11-30',4,'{}','2026-11-22','test')""",
                (f"hist-{number}", group))
        self.con.execute("""INSERT INTO tasks(id,kind,title,student_id,due_at,status,source,created_at)
            VALUES (4642,'call_lead','Новый лид',4,'2026-11-20T23:59','open','auto','2026-11-01')""")

    def test_no_december_invoice_uses_two_history_subscriptions(self):
        report = targetedminibaevtask.apply(self.con)
        self.assertEqual(report["state"], "complete")
        self.assertEqual(report["old_task_status"], "cancelled")
        self.assertEqual(report["open_task"]["kind"], "renew")
        self.assertEqual(report["open_task"]["due_at"], "2026-11-20T23:59")
        self.assertEqual(report["open_renewal_or_payment_count"], 1)
        self.assertEqual(targetedminibaevtask.apply(self.con), report)

    def test_unpaid_december_invoice_uses_payment_control(self):
        self.con.execute("""INSERT INTO subscriptions(student_id,month,kind,tariff,lessons,per_lesson,price,status,created_at)
            VALUES (4,'2026-12','month','fixed',8,1500,12000,'invoiced','2026-11-22')""")
        report = targetedminibaevtask.apply(self.con)
        self.assertEqual(report["state"], "complete")
        self.assertEqual(report["remaining"], 12000)
        self.assertEqual(report["open_task"]["kind"], "payment_check")
        self.assertEqual(report["open_renewal_or_payment_count"], 1)


if __name__ == "__main__":
    unittest.main()
