import os
import tempfile
import unittest

from crm import db, erphistory, targetedmillertask


class TargetedMillerTaskTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(os.path.join(tempfile.mkdtemp(), "miller.sqlite3"))
        db.init_db(self.con)
        self.con.execute("DELETE FROM settings WHERE key=?", (targetedmillertask.KEY,))
        self.con.execute("INSERT INTO families(id,name) VALUES (3,'Миллеры')")
        self.con.execute("""INSERT INTO students(id,family_id,name,erp_id,stage,stage_changed_at,created_at)
            VALUES (3,3,'Миллер Матвей','79204','lead','2026-11-01','2026-11-01')""")
        self.con.execute("""INSERT INTO tasks(id,kind,title,student_id,due_at,status,source,created_at)
            VALUES (4641,'call_lead','Новый лид',3,'2026-11-22T23:59','open','auto','2026-11-01')""")

    def test_exact_history_period_is_authorized(self):
        self.assertIn(erphistory._pilot_key("79204", "2026-11"), erphistory.PILOT_KEYS)
        self.assertNotIn(erphistory._pilot_key("79204", "2026-10"), erphistory.PILOT_KEYS)
        self.assertNotIn(erphistory._pilot_key("79205", "2026-11"), erphistory.PILOT_KEYS)

    def test_unpaid_december_invoice_replaces_new_lead(self):
        invoice = self.con.execute("""INSERT INTO subscriptions
            (student_id,month,kind,tariff,lessons,per_lesson,price,status,created_at)
            VALUES (3,'2026-12','month','fixed',8,1500,12000,'invoiced','2026-11-22')""").lastrowid
        report = targetedmillertask.apply(self.con)
        self.assertEqual(report["state"], "complete")
        self.assertEqual(report["remaining"], 12000)
        self.assertEqual(report["december_invoice_ids"], [invoice])
        self.assertEqual(report["open_task"]["kind"], "payment_check")
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE id=4641").fetchone()[0], "cancelled")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM payments").fetchone()[0], 0)
        self.assertEqual(targetedmillertask.apply(self.con), report)

    def test_without_december_invoice_changes_no_task(self):
        report = targetedmillertask.apply(self.con)
        self.assertEqual(report["result"], "no_december_invoice")
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE id=4641").fetchone()[0], "open")


if __name__ == "__main__":
    unittest.main()
