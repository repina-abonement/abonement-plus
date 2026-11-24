import os
import tempfile
import unittest

from crm import db, targetedfreshtelegram


class FreshTelegramFactsTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(os.path.join(tempfile.mkdtemp(), "fresh-telegram.sqlite3"))
        db.init_db(self.con)
        self.con.execute("DELETE FROM settings WHERE key=?", (targetedfreshtelegram.KEY,))
        self.con.execute("INSERT INTO users(name,login,pw_hash,role,active) VALUES ('Юлия Репина','owner-fresh','x','admin',1)")
        for name in sorted({event["student_name"] for event in targetedfreshtelegram.events()}):
            family = self.con.execute("INSERT INTO families(name) VALUES (?)", (name,)).lastrowid
            self.con.execute("""INSERT INTO students(family_id,name,stage,stage_changed_at,created_at)
                VALUES (?,?,'active','2026-11-01','2026-11-01')""", (family, name))

    def test_every_event_has_full_name_and_unique_source(self):
        events = targetedfreshtelegram.events()
        self.assertEqual(len(events), len({event["source_id"] for event in events}))
        self.assertTrue(all(len(event["student_name"].split()) >= 2 for event in events))

    def test_facts_and_tasks_apply_once(self):
        first = targetedfreshtelegram.apply(self.con)
        self.assertEqual("complete", first["state"], first)
        self.assertEqual(len(targetedfreshtelegram.events()), first["counts"]["applied"])
        second = targetedfreshtelegram.apply(self.con)
        self.assertEqual(first, second)
        self.assertEqual(len(targetedfreshtelegram.events()), self.con.execute(
            "SELECT count(*) FROM chat_day_transfer_events WHERE source_id LIKE 'telegram:sz-22%' OR source_id LIKE 'telegram:sz-23%'"
        ).fetchone()[0])
        self.assertEqual(1, self.con.execute(
            "SELECT count(*) FROM tasks WHERE auto_key='chat-day:telegram:sz-23001:chugunov' AND status='open'"
        ).fetchone()[0])

    def test_missing_card_is_recreated_from_full_erp_profile(self):
        row = self.con.execute("SELECT id,family_id FROM students WHERE name='Виктор Данейкин'").fetchone()
        self.con.execute("DELETE FROM students WHERE id=?", (row["id"],))
        self.con.execute("DELETE FROM families WHERE id=?", (row["family_id"],))
        result = targetedfreshtelegram.apply(self.con)
        self.assertEqual("complete", result["state"], result)
        self.assertEqual(1, result["counts"]["created_from_erp"])
        student = self.con.execute("SELECT id,erp_id,birth,erp_status FROM students WHERE erp_id='88688'").fetchone()
        self.assertIsNotNone(student)
        self.assertEqual(("88688", "2020-04-06", "Постоянный посетитель"), tuple(student)[1:])
        parent = self.con.execute("""SELECT name,phone,email FROM parents
            WHERE family_id=(SELECT family_id FROM students WHERE id=?)""", (student["id"],)).fetchone()
        self.assertEqual(("Татьяна", "+79265241538", "tiana2003@bk.ru"), tuple(parent))
        self.assertEqual(1, self.con.execute(
            "SELECT count(*) FROM history WHERE student_id=? AND kind='erp' AND text LIKE 'ERP с 01.11:%'", (student["id"],)
        ).fetchone()[0])

    def test_explicit_trainer_names_become_idempotent_attendance_facts(self):
        first = targetedfreshtelegram.apply(self.con)
        self.assertGreater(first["trainer_attendance"]["added"], 0)
        count = self.con.execute("""SELECT count(*) FROM erp_history_unassigned_visits
            WHERE source_id LIKE 'trainers:27148:%' AND visit_date='2026-11-23' AND status='present'""").fetchone()[0]
        self.assertEqual(first["trainer_attendance"]["added"], count)
        self.con.execute("DELETE FROM settings WHERE key=?", (targetedfreshtelegram.KEY,))
        second = targetedfreshtelegram.apply(self.con)
        self.assertEqual(0, second["trainer_attendance"]["added"])
        self.assertEqual(count, second["trainer_attendance"]["already"])


if __name__ == "__main__":
    unittest.main()
