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
        self.varya = self.con.execute("""INSERT INTO users(name,login,pw_hash,role,active,is_senior)
            VALUES ('Варвара Батарева','varya-fresh','x','operator',1,1)""").lastrowid
        branch = self.con.execute("INSERT INTO branches(name) VALUES ('Небо')").lastrowid
        self.group_1945 = self.con.execute("""INSERT INTO groups(branch_id,name,weekdays,time,active)
            VALUES (?,'Небо ПУП пн/чт 19:45','0,3','19:45',1)""", (branch,)).lastrowid
        self.group_1200 = self.con.execute("""INSERT INTO groups(branch_id,name,weekdays,time,active)
            VALUES (?,'Небо НУП сб/вс 12:00 возраст 5-6','5,6','12:00',1)""", (branch,)).lastrowid
        for name in sorted({event["student_name"] for event in targetedfreshtelegram.events()}):
            family = self.con.execute("INSERT INTO families(name) VALUES (?)", (name,)).lastrowid
            self.con.execute("""INSERT INTO students(family_id,name,stage,stage_changed_at,created_at)
                VALUES (?,?,'active','2026-11-01','2026-11-01')""", (family, name))
        daneykin = self.con.execute("SELECT id,family_id FROM students WHERE name='Виктор Данейкин'").fetchone()
        self.con.execute("UPDATE students SET erp_id='88688',birth='2020-04-06' WHERE id=?", (daneykin["id"],))
        self.con.execute("""INSERT INTO parents(family_id,name,phone,phone_norm,email,is_primary)
            VALUES (?,'Татьяна','+79265241538','79265241538','tiana2003@bk.ru',1)""", (daneykin["family_id"],))
        pogosyan = self.con.execute("SELECT id,family_id FROM students WHERE name='Леон Погосян'").fetchone()
        self.con.execute("UPDATE students SET erp_id='105641',birth='2020-11-19' WHERE id=?", (pogosyan["id"],))
        self.con.execute("""INSERT INTO parents(family_id,name,phone,phone_norm,is_primary)
            VALUES (?,'Лена','+79613348693','79613348693',1)""", (pogosyan["family_id"],))

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
        sale = self.con.execute("""SELECT price,status,excluded_dates FROM subscriptions
            WHERE student_id=(SELECT id FROM students WHERE erp_id='88688') AND month='2026-12'""").fetchone()
        self.assertEqual((11500, "paid", '["2026-12-28"]'), tuple(sale))
        payment = self.con.execute("""SELECT amount,method,cancelled FROM payments
            WHERE student_id=(SELECT id FROM students WHERE erp_id='88688')""").fetchone()
        self.assertEqual((11500, "card_branch", 0), tuple(payment))
        self.assertEqual(0, self.con.execute(
            "SELECT count(*) FROM tasks WHERE auto_key=? AND status='open'",
            (targetedfreshtelegram.DANEYKIN_DECEMBER_TASK,)).fetchone()[0])
        pogosyan = self.con.execute("SELECT id FROM students WHERE name='Леон Погосян'").fetchone()[0]
        december = self.con.execute("""SELECT price,lessons,status FROM subscriptions
            WHERE student_id=? AND month='2026-12'""", (pogosyan,)).fetchone()
        self.assertEqual((9720, 8, "paid"), tuple(december))
        pogosyan_payment = self.con.execute("""SELECT amount,method,cancelled FROM payments
            WHERE student_id=?""", (pogosyan,)).fetchone()
        self.assertEqual((9720, "tochka_qr_link", 0), tuple(pogosyan_payment))
        self.assertEqual(0, self.con.execute("""SELECT count(*) FROM tasks
            WHERE student_id=? AND auto_key='chat-day:telegram:sz-23043:pogosyan' AND status='open'""",
            (pogosyan,)).fetchone()[0])

    def test_missing_card_is_recreated_from_full_erp_profile(self):
        row = self.con.execute("SELECT id,family_id FROM students WHERE name='Виктор Данейкин'").fetchone()
        self.con.execute("DELETE FROM students WHERE id=?", (row["id"],))
        self.con.execute("DELETE FROM families WHERE id=?", (row["family_id"],))
        result = targetedfreshtelegram.apply(self.con)
        self.assertEqual("complete", result["state"], result)
        self.assertEqual(1, result["counts"]["created_from_erp"])
        student = self.con.execute("SELECT id,erp_id,birth,erp_status FROM students WHERE erp_id='88688'").fetchone()
        self.assertIsNotNone(student)
        self.assertEqual(("88688", "2020-04-06", "Куплен на будущий период"), tuple(student)[1:])
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
        task = self.con.execute("""SELECT title,assignee_id,due_at FROM tasks
            WHERE auto_key='trainers:27148:identify-missing-clients'""").fetchone()
        self.assertEqual(("Уточнить восьмого ребёнка группы 18:45", self.varya, "2026-11-24T23:59"), tuple(task))


if __name__ == "__main__":
    unittest.main()
