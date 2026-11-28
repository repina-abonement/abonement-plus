"""События пробного создают задачи с подтверждёнными ERP-сроками."""
import os
import tempfile
import unittest
from datetime import datetime

from crm import db, pu, schedule, services


class ErpTrialEventsTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(os.path.join(tempfile.mkdtemp(), "erp-events.sqlite3"))
        db.init_db(self.con)
        branch = schedule.save_branch(self.con, {"name": "Филиал"})
        self.group = schedule.save_group(self.con, {"name": "Группа", "branch_id": branch, "weekdays": [5], "time": "10:00", "age": "4–5"})
        self.user = {"id": 1, "name": "Тренер", "role": "coach"}

    def lead(self, name, phone):
        sid = services.create_student(self.con, {"name": name, "parent_name": "Мама", "phone": phone}, self.user)
        self.con.execute("UPDATE students SET group_id=?,trial_at='2026-11-21T10:00',stage='trial_booked' WHERE id=?", (self.group, sid))
        return sid

    def test_no_show_creates_exact_delayed_task(self):
        sid = self.lead("Не пришёл", "+7 900 100-00-01")
        pu.on_trial_mark(self.con, sid, False, self.user, datetime(2026, 11, 21, 11, 0))
        row = self.con.execute("SELECT kind,title,due_at,source FROM tasks WHERE student_id=? AND status='open'", (sid,)).fetchone()
        self.assertEqual(tuple(row), ("no_show", "Не пришли на ПУ", "2026-11-22T01:59", "erp_rule"))

    def test_late_absence_replaces_missing_mark_without_duplicate(self):
        sid = self.lead("Поздняя отметка", "+7 900 100-00-03")
        pu.tick(self.con, datetime(2026, 11, 22, 2, 0))
        for minute in (5, 6):
            self.con.execute("UPDATE students SET trial_came=0 WHERE id=?", (sid,))
            pu.on_trial_mark(self.con, sid, False, self.user, datetime(2026, 11, 22, 2, minute))
        rows = self.con.execute("SELECT kind,title,due_at FROM tasks WHERE student_id=? AND status='open'", (sid,)).fetchall()
        self.assertEqual([tuple(r) for r in rows], [("no_show", "Не пришли на ПУ", "2026-11-22T17:04")])
        self.assertEqual(pu.misses_count(self.con, sid), 1)
        self.assertEqual(self.con.execute("SELECT kind FROM trial_misses WHERE student_id=? AND undone=0", (sid,)).fetchone()[0], "no_show")
        pu.tick(self.con, datetime(2026, 11, 22, 3, 0))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM tasks WHERE student_id=? AND status='open' AND kind='rebook'", (sid,)).fetchone()[0], 0)

    def test_no_mark_waits_full_15_hours_and_is_reversible(self):
        sid = self.lead("Без отметки", "+7 900 100-00-02")
        early = pu.tick(self.con, datetime(2026, 11, 22, 1, 59))
        self.assertEqual(early["moved"], 0)
        late = pu.tick(self.con, datetime(2026, 11, 22, 2, 0))
        self.assertGreaterEqual(late["moved"], 1)
        row = self.con.execute("SELECT kind,title,source FROM tasks WHERE student_id=? AND status='open'", (sid,)).fetchone()
        self.assertEqual(tuple(row), ("not_marked", "Нет отметки о ПУ", "erp_rule"))
        pu.on_trial_mark(self.con, sid, True, self.user, datetime(2026, 11, 22, 2, 5))
        self.assertEqual(self.con.execute("SELECT stage FROM students WHERE id=?", (sid,)).fetchone()[0], "how_was")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM tasks WHERE student_id=? AND status='open'", (sid,)).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
