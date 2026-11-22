import tempfile
import unittest
from pathlib import Path

from crm import db, erphistory, services as sv


class KononenkoHistoryTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(str(Path(tempfile.mkdtemp()) / "kononenko.sqlite3"))
        db.init_db(self.con)
        self.addCleanup(self.con.close)
        self.con.execute("INSERT INTO branches(id,name) VALUES (31,'Арктика'),(34,'Небо')")
        self.con.execute("INSERT INTO groups(id,branch_id,name,weekdays,time,age) VALUES (31,31,'Сборная', '1,6','18:30','6-8')")
        self.con.execute("INSERT INTO groups(id,branch_id,name,weekdays,time,age) VALUES (34,34,'ОФП Сб', '5','17:00','6-8')")
        user = {"id": 1, "role": "admin", "name": "Тест"}
        sv.create_student(self.con, {"name": "Другой Ребёнок", "parent_name": "Родитель", "phone": "+79000000001"}, user)
        self.sid = sv.create_student(self.con, {"name": "Кононенко Иван", "parent_name": "Родитель", "phone": "+79000000002"}, user)
        self.assertEqual(self.sid, 2)
        self.con.execute("UPDATE students SET erp_id='92421' WHERE id=2")
        for gid in (31, 34):
            self.con.execute("INSERT INTO enrollments(student_id,group_id,created_at) VALUES (2,?,'2026-11-01')", (gid,))
        arena = ["2026-11-01", "2026-11-03", "2026-11-08", "2026-11-10", "2026-11-15", "2026-11-17", "2026-11-22"]
        ofp = ["2026-11-07", "2026-11-14", "2026-11-21"]
        self.payload = {"child": "Кононенко Иван", "erp_student_id": "92421", "month": "2026-11",
            "partial_attendance": True, "attendance": [], "subscriptions": [
                {"erp_id": "825986", "valid_from": arena[0], "valid_until": arena[-1], "lesson_count": len(arena),
                 "price": 7000, "schedule": [{"date": day, "time": "18:30", "group": "Арктика Сборная"} for day in arena]},
                {"erp_id": "825983", "valid_from": ofp[0], "valid_until": ofp[-1], "lesson_count": len(ofp),
                 "price": 3000, "schedule": [{"date": day, "time": "17:00", "group": "Небо ОФП Сб"} for day in ofp]},
            ]}

    def test_exact_empty_attendance_import_is_twelve_rows_and_idempotent(self):
        before = (self.con.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0],
                  self.con.execute("SELECT COUNT(*) FROM payments").fetchone()[0],
                  self.con.execute("SELECT COUNT(*) FROM attendance").fetchone()[0])
        preview = erphistory.preview(self.con, self.payload, "2026-11")
        self.assertTrue(preview["can_apply"], preview)
        self.assertEqual((preview["added"], preview["review"]), (12, 0))
        result = erphistory.apply(self.con, self.payload, "2026-11", preview["token"], {"name": "Тест"})
        self.assertEqual((result["subscriptions_added"], result["planned_lessons_added"], result["visits_added"]), (2, 10, 0))
        again = erphistory.preview(self.con, self.payload, "2026-11")
        self.assertEqual((again["added"], again["existing"], again["review"]), (0, 12, 0))
        after = (self.con.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0],
                 self.con.execute("SELECT COUNT(*) FROM payments").fetchone()[0],
                 self.con.execute("SELECT COUNT(*) FROM attendance").fetchone()[0])
        self.assertEqual(after, before)

    def test_scope_rejects_marks_wrong_card_group_or_count(self):
        variants = [
            {**self.payload, "attendance": [{"erp_attendance_id": "x"}]},
            {**self.payload, "subscriptions": [{**self.payload["subscriptions"][0], "schedule": self.payload["subscriptions"][0]["schedule"][:-1]}, self.payload["subscriptions"][1]]},
        ]
        for payload in variants:
            self.assertFalse(erphistory.preview(self.con, payload, "2026-11")["can_apply"])
        self.con.execute("UPDATE students SET erp_id='' WHERE id=2")
        self.con.execute("UPDATE students SET erp_id='92421' WHERE id=1")
        self.assertFalse(erphistory.preview(self.con, self.payload, "2026-11")["can_apply"])


if __name__ == "__main__":
    unittest.main()
