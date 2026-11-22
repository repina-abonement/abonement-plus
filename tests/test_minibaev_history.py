import json
import tempfile
import unittest
from pathlib import Path

from crm import db, erphistory, services as sv


class MinibaevHistoryTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(str(Path(tempfile.mkdtemp()) / "minibaev.sqlite3"))
        db.init_db(self.con)
        self.addCleanup(self.con.close)
        self.con.execute("INSERT INTO branches(id,name) VALUES (1,'Арктика'),(2,'ТЦ Небо')")
        self.con.execute("INSERT INTO groups(id,branch_id,name,weekdays,time,age,source_name) VALUES (31,1,'Сборная','1,6','18:30','6-8','Арктика сборная')")
        self.con.execute("UPDATE groups SET day_times='1=18:30,6=19:30' WHERE id=31")
        self.con.execute("INSERT INTO groups(id,branch_id,name,weekdays,time,age,source_name) VALUES (32,2,'Сб 17:00','5','17:00','6-8','Возраст не указан ОФП')")
        self.con.execute("INSERT INTO groups(id,branch_id,name,weekdays,time,age,source_name) VALUES (33,2,'Чт 20:30','3','20:30','6-8','Возраст не указан ОФП')")
        user = {"id": 1, "role": "admin", "name": "Тест"}
        for number in range(1, 5):
            sid = sv.create_student(self.con, {"name": "Минибаев Дамир" if number == 4 else f"Ребёнок {number}", "parent_name": "Родитель", "phone": f"+7900000000{number}"}, user)
        self.assertEqual(sid, 4)
        self.con.execute("UPDATE students SET erp_id='64928',birth='2020-05-05' WHERE id=4")
        for gid in (31, 32):
            self.con.execute("INSERT INTO enrollments(student_id,group_id,created_at) VALUES (4,?,'2026-11-01')", (gid,))
        fixture = Path(__file__).parent / "fixtures" / "Minibaev_2026-11.json"
        self.payload = json.loads(fixture.read_text())

    def test_exact_import_keeps_manager_absence_without_fake_erp_id(self):
        preview = erphistory.preview(self.con, self.payload, "2026-11")
        self.assertTrue(preview["can_apply"], preview)
        self.assertEqual((preview["added"], preview["review"]), (20, 0))
        result = erphistory.apply(self.con, self.payload, "2026-11", preview["token"], {"name": "Тест"})
        self.assertEqual((result["subscriptions_added"], result["planned_lessons_added"], result["visits_added"]), (2, 13, 5))
        absence = self.con.execute("SELECT * FROM erp_history_visits WHERE visit_date='2026-11-14'").fetchone()
        self.assertEqual(absence["status"], "absent")
        self.assertTrue(absence["source_id"].startswith("manager-confirmation:"))
        source = json.loads(absence["source_payload"])
        self.assertNotIn("erp_attendance_id", source)
        self.assertEqual(source["note"], "Пропуск по указанию руководителя. Турнир в Небе")
        again = erphistory.preview(self.con, self.payload, "2026-11")
        self.assertEqual((again["added"], again["existing"], again["review"]), (0, 20, 0))


if __name__ == "__main__":
    unittest.main()
