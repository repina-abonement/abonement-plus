import os
import tempfile
import unittest
from datetime import date

from crm import db, services as sv, trialdesk


class TrialDeskTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(os.path.join(tempfile.mkdtemp(), "trialdesk.sqlite3"))
        db.init_db(self.con)
        self.user = dict(self.con.execute("SELECT * FROM users WHERE role='admin'").fetchone())
        branch = self.con.execute("INSERT INTO branches(name,active) VALUES ('Тестовый филиал',1)").lastrowid
        self.group = self.con.execute("""INSERT INTO groups(branch_id,name,weekdays,time,age,active)
            VALUES (?,'Пн/Вс 18:00','0,6','18:00','5–7',1)""", (branch,)).lastrowid
        self.sid = sv.create_student(self.con, {"name": "Пробный Ребёнок", "birth": "2020-01-01",
            "parent_name": "Родитель", "phone": "+79990000001", "funnel": "members",
            "group_id": self.group}, self.user)
        self.con.execute("""UPDATE students SET funnel='sales',stage='trial_booked',trial_at='2026-11-24T18:00',
            trial_group_id=?,trial_came=0,converted_at=NULL WHERE id=?""", (self.group, self.sid))

    def tearDown(self):
        self.con.close()

    def test_today_trials_show_all_tariffs_and_two_month_bundle_without_writes(self):
        before = dict(self.con.execute("SELECT funnel,stage,trial_came,converted_at FROM students WHERE id=?",
                                       (self.sid,)).fetchone())
        result = trialdesk.today_offers(self.con, self.user, date(2026, 11, 24))
        self.assertEqual(1, len(result))
        child = result[0]
        self.assertEqual(("Пробный Ребёнок", "18:00", "Тестовый филиал"),
                         (child["name"], child["time"], child["branch"]))
        self.assertEqual(["fixed", "flex"], [offer["tariff"] for offer in child["offers"]])
        fixed = child["offers"][0]
        self.assertEqual((2160, 8640, 10800),
                         (fixed["current"]["total"], fixed["next"]["total"], fixed["bundle_total"]))
        self.assertEqual((20, 20),
                         (fixed["current"]["discount_percent"], fixed["next"]["discount_percent"]))
        after = dict(self.con.execute("SELECT funnel,stage,trial_came,converted_at FROM students WHERE id=?",
                                      (self.sid,)).fetchone())
        self.assertEqual(before, after)
        self.assertEqual(0, self.con.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0])
        self.assertEqual(0, self.con.execute("SELECT COUNT(*) FROM payments").fetchone()[0])

    def test_other_days_are_not_shown(self):
        self.assertEqual([], trialdesk.today_offers(self.con, self.user, date(2026, 11, 25)))


if __name__ == "__main__":
    unittest.main()
