"""Новые карточки CRM используют подтверждённую цепочку ERP, не старые лестницы."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta

from crm import db, lifecycle, services


class ErpNativeLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(os.path.join(tempfile.mkdtemp(), "native-erp.sqlite3"))
        db.init_db(self.con)
        self.user = {"name": "Администратор"}

    def child(self, name, phone):
        return services.create_student(self.con, {"name": name, "parent_name": "Мама", "phone": phone}, self.user)

    def test_new_lead_gets_immediate_exact_task(self):
        sid = self.child("Новый", "+7 900 200-00-01")
        created = self.con.execute("SELECT created_at FROM students WHERE id=?", (sid,)).fetchone()[0]
        lifecycle.tick(self.con, datetime.fromisoformat(created[:16]) + timedelta(minutes=1))
        row = self.con.execute("SELECT kind,title,due_at,source FROM tasks WHERE student_id=? AND status='open'", (sid,)).fetchone()
        self.assertEqual(row["kind"], "call_lead")
        self.assertEqual(row["title"], "Новый лид")
        self.assertEqual(row["due_at"], created[:16])
        self.assertEqual(row["source"], "life")

    def test_unverified_old_chain_is_cancelled_and_not_recreated(self):
        sid = self.child("Старая цепочка", "+7 900 200-00-02")
        self.con.execute("UPDATE students SET stage='no_answer',stage_changed_at=? WHERE id=?", (datetime.now().isoformat(timespec='minutes'), sid))
        self.con.execute("""INSERT INTO tasks(kind,title,student_id,due_at,status,source,created_at)
                            VALUES ('no_answer','Старая лестница',?,'2026-11-22T10:00','open','life','2026-11-21T10:00')""", (sid,))
        lifecycle.tick(self.con, datetime(2026, 11, 21, 12, 0))
        row = self.con.execute("SELECT status,result FROM tasks WHERE student_id=?", (sid,)).fetchone()
        self.assertEqual(tuple(row), ("cancelled", "Отключена неподтверждённая цепочка CRM"))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM tasks WHERE student_id=? AND status='open'", (sid,)).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
