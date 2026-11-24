import os
import tempfile
import unittest
from unittest.mock import patch

from crm import chatbulk, db, services as sv


class ChatBulkTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(os.path.join(tempfile.mkdtemp(), "chat-bulk.sqlite3"))
        db.init_db(self.con)
        branch = self.con.execute("INSERT INTO branches(name,active) VALUES ('Небо',1)").lastrowid
        self.group = self.con.execute("""INSERT INTO groups(branch_id,name,weekdays,time,active)
            VALUES (?,'Небо ПУП','0,3','16:45',1)""", (branch,)).lastrowid
        self.user = {"id": 1, "name": "Юлия", "role": "admin"}
        self.ivan = sv.create_student(self.con, {"name": "Иван Иванов", "parent_name": "Анна",
            "phone": "+79990000001", "group_id": self.group}, self.user)
        self.misha = sv.create_student(self.con, {"name": "Михаил Морозов", "parent_name": "Наталья",
            "phone": "+79990000002", "group_id": self.group}, self.user)
        family = self.con.execute("SELECT family_id FROM students WHERE id=?", (self.misha,)).fetchone()[0]
        self.ilya = sv.create_student(self.con, {"name": "Илья Морозов", "family_id": family,
            "group_id": self.group}, self.user)

    def exports(self):
        care = {"name": "Служба заботы", "id": 1, "messages": [
            {"id": 10, "date": "2026-11-02T12:00:00", "text": "Иван Иванов просит ссылку на оплату"},
            {"id": 11, "date": "2026-11-02T12:01:00", "text": "Доброе утро"},
        ]}
        trainers = {"name": "ТРЕНЕРЫ | СОЛНЦЕВО", "id": 2, "messages": [
            {"id": 20, "date": "2026-11-02T20:00:00", "text": "16:45\nИван Иванов\nМорозов"},
        ]}
        return [care, trainers, dict(care)]

    @patch("crm.chatbulk.date")
    def test_plan_deduplicates_exports_and_does_not_guess_ambiguous_surname(self, fake_date):
        from datetime import date
        fake_date.today.return_value = date(2026, 11, 24)
        report = chatbulk.plan(self.con, self.exports())
        self.assertEqual({"care": 2, "trainers": 1}, report["messages"])
        self.assertEqual(1, report["trainer"]["ready"])
        self.assertEqual(1, len(report["trainer"]["unresolved"]))
        self.assertEqual("Морозов", report["trainer"]["unresolved"][0]["name"])
        self.assertEqual(1, report["care"]["significant"])
        self.assertEqual(1, report["care"]["without_events"])

    @patch("crm.chatbulk.date")
    def test_apply_is_preview_guarded_and_idempotent(self, fake_date):
        from datetime import date
        fake_date.today.return_value = date(2026, 11, 24)
        exports = self.exports()
        plan = chatbulk.plan(self.con, exports)
        with self.assertRaisesRegex(chatbulk.BulkError, "Сначала снова"):
            chatbulk.apply(self.con, exports, "wrong", self.user)
        first = chatbulk.apply(self.con, exports, plan["token"], self.user)
        self.assertEqual(1, first["added"])
        self.assertTrue(first["review_task"])
        second_plan = chatbulk.plan(self.con, exports)
        second = chatbulk.apply(self.con, exports, second_plan["token"], self.user)
        self.assertEqual(0, second["added"])
        self.assertEqual(1, self.con.execute(
            "SELECT count(*) FROM erp_history_unassigned_visits WHERE source_id LIKE 'trainers:20:%'"
        ).fetchone()[0])
        self.assertEqual(1, self.con.execute(
            "SELECT count(*) FROM tasks WHERE auto_key='chat-bulk:trainers-unresolved:2026-11'"
        ).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
