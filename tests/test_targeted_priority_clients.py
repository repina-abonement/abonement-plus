import os
import tempfile
import unittest

from crm import db, money, program, schedule, services, smart, targetedpriorityclients


class PriorityClientFactsTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(os.path.join(tempfile.mkdtemp(), "priority.sqlite3"))
        db.init_db(self.con)
        self.con.execute("DELETE FROM settings WHERE key=?", (targetedpriorityclients.KEY,))
        self.con.execute("INSERT INTO users(name,login,pw_hash,role,active) VALUES ('Юлия Репина','owner-test','x','admin',1)")
        for number, name in enumerate(targetedpriorityclients.CLIENTS.values(), 1):
            family = self.con.execute("INSERT INTO families(name) VALUES (?)", (name,)).lastrowid
            self.con.execute("INSERT INTO students(family_id,name,stage,stage_changed_at,created_at) VALUES (?,?, 'active','2026-11-01','2026-11-01')", (family, name))

    def test_all_facts_attach_once_and_no_cards_are_created(self):
        before = self.con.execute("SELECT count(*) FROM students").fetchone()[0]
        first = targetedpriorityclients.apply(self.con)
        self.assertEqual(first["state"], "complete")
        self.assertEqual(before, self.con.execute("SELECT count(*) FROM students").fetchone()[0])
        self.assertEqual(len(targetedpriorityclients._events()), self.con.execute(
            "SELECT count(*) FROM chat_day_transfer_events WHERE source_id LIKE 'telegram:%'").fetchone()[0])
        second = targetedpriorityclients.apply(self.con)
        self.assertEqual(second, first)
        self.assertEqual(len(targetedpriorityclients._events()), self.con.execute(
            "SELECT count(*) FROM chat_day_transfer_events WHERE source_id LIKE 'telegram:%'").fetchone()[0])
        self.assertEqual(1, self.con.execute("SELECT count(*) FROM tasks WHERE auto_key='priority-clients:sharipov-december-sale-2026-11-23'").fetchone()[0])
        self.assertEqual(38, self.con.execute("SELECT count(*) FROM erp_history_unassigned_visits WHERE source_id LIKE 'priority-trainers:%'").fetchone()[0])
        self.assertEqual(9, self.con.execute("SELECT count(*) FROM tasks WHERE auto_key LIKE 'priority-clients:attendance-audit:%'").fetchone()[0])
        targetedpriorityclients.ensure_attendance_control(self.con)
        self.assertEqual(38, self.con.execute("SELECT count(*) FROM erp_history_unassigned_visits WHERE source_id LIKE 'priority-trainers:%'").fetchone()[0])
        self.assertEqual(9, self.con.execute("SELECT count(*) FROM tasks WHERE auto_key LIKE 'priority-clients:attendance-audit:%'").fetchone()[0])
        report = targetedpriorityclients.attendance_report(self.con)
        self.assertEqual(9, report["summary"]["cards"])
        self.assertEqual(38, report["summary"]["marks"])
        self.assertEqual(9, report["summary"]["open_tasks"])
        self.assertEqual(0, report["summary"]["missing_tasks"])
        self.assertEqual(1, report["miller"]["card_matches"])
        self.assertIsNotNone(report["miller"]["pennant_task"])
        self.assertGreaterEqual(report["miller"]["telegram_events"], 1)

    def test_sharipov_confirmed_december_sale_is_created_once(self):
        sid = self.con.execute("SELECT id FROM students WHERE name='Даниил Шарипов'").fetchone()[0]
        self.con.execute("UPDATE students SET erp_id='85160',birth='2019-11-27' WHERE id=?", (sid,))
        branch = schedule.save_branch(self.con, {"name": "ТЦ Небо"})
        pid = program.ensure_sbornaya(self.con)
        arena = schedule.save_group(self.con, {"name": "Арктика Вт/Вс", "branch_id": branch, "weekdays": [1, 6]})
        ofp = schedule.save_group(self.con, {"name": "ОФП Сб", "branch_id": branch, "weekdays": [5]})
        self.con.execute("UPDATE groups SET program_id=?,lesson_kind='arena' WHERE id=?", (pid, arena))
        self.con.execute("UPDATE groups SET program_id=?,lesson_kind='ofp' WHERE id=?", (pid, ofp))
        actor = dict(self.con.execute("SELECT id,name,role FROM users WHERE login='owner-test'").fetchone())
        old_require = smart.REQUIRE
        smart.REQUIRE = False
        try:
            services.add_enrollment(self.con, sid, arena, None, "fixed", actor)
            for source_id, group_id, start, end, lessons, revenue in (
                    ("828113", arena, "2026-11-01", "2026-11-29", 9, 13500),
                    ("828114", ofp, "2026-11-07", "2026-11-28", 4, 4800)):
                self.con.execute("""INSERT INTO erp_history_subscriptions
                    (source_id,student_id,group_id,month,valid_from,valid_until,lessons,revenue_amount,source_payload,imported_at,imported_by)
                    VALUES (?,?,?,'2026-11',?,?,?,?,?,'2026-11-23','test')""",
                    (source_id, sid, group_id, start, end, lessons, revenue, "{}"))
            first = targetedpriorityclients.ensure_sharipov_december_sale(self.con, actor)
            second = targetedpriorityclients.ensure_sharipov_december_sale(self.con, actor)
        finally:
            smart.REQUIRE = old_require
        self.assertEqual("created", first["state"])
        self.assertEqual("already_complete", second["state"])
        rows = self.con.execute("SELECT id,price,lessons,status FROM subscriptions WHERE student_id=? AND month='2026-12'", (sid,)).fetchall()
        self.assertEqual(1, len(rows))
        self.assertEqual((20400, 13, "paid"), tuple(rows[0])[1:])
        payments = self.con.execute("SELECT amount,method,cancelled FROM payments WHERE subscription_id=?", (rows[0]["id"],)).fetchall()
        self.assertEqual([(20400, "paykeeper_manual", 0)], [tuple(row) for row in payments])
        self.assertEqual(20400, money.balance(self.con, sid)["covered"][rows[0]["id"]])
        report = targetedpriorityclients.sharipov_sale_report(self.con)
        self.assertTrue(report["complete"])
        self.assertEqual(1, report["subscriptions"])
        self.assertEqual(1, len(report["payments"]))

    def test_sharipov_reuses_existing_unallocated_paykeeper_payment(self):
        sid = self.con.execute("SELECT id FROM students WHERE name='Даниил Шарипов'").fetchone()[0]
        self.con.execute("UPDATE students SET erp_id='85160',birth='2019-11-27' WHERE id=?", (sid,))
        branch = schedule.save_branch(self.con, {"name": "ТЦ Небо"})
        pid = program.ensure_sbornaya(self.con)
        arena = schedule.save_group(self.con, {"name": "Арктика Вт/Вс", "branch_id": branch, "weekdays": [1, 6]})
        ofp = schedule.save_group(self.con, {"name": "ОФП Сб", "branch_id": branch, "weekdays": [5]})
        self.con.execute("UPDATE groups SET program_id=?,lesson_kind='arena' WHERE id=?", (pid, arena))
        self.con.execute("UPDATE groups SET program_id=?,lesson_kind='ofp' WHERE id=?", (pid, ofp))
        actor = dict(self.con.execute("SELECT id,name,role FROM users WHERE login='owner-test'").fetchone())
        old_require = smart.REQUIRE
        smart.REQUIRE = False
        try:
            services.add_enrollment(self.con, sid, arena, None, "fixed", actor)
            for source_id, group_id, start, end, lessons, revenue in (
                    ("828113", arena, "2026-11-01", "2026-11-29", 9, 13500),
                    ("828114", ofp, "2026-11-07", "2026-11-28", 4, 4800)):
                self.con.execute("""INSERT INTO erp_history_subscriptions
                    (source_id,student_id,group_id,month,valid_from,valid_until,lessons,revenue_amount,source_payload,imported_at,imported_by)
                    VALUES (?,?,?,'2026-11',?,?,?,?,?,'2026-11-23','test')""",
                    (source_id, sid, group_id, start, end, lessons, revenue, "{}"))
            existing = money.add_payment(self.con, sid, 20400, "paykeeper_manual", actor,
                                         paid_at="2026-11-23T12:44:19", comment="Подтверждено СЗ")
            result = targetedpriorityclients.ensure_sharipov_december_sale(self.con, actor)
        finally:
            smart.REQUIRE = old_require
        self.assertEqual("created", result["state"], result)
        self.assertEqual(existing, result["payment_id"])
        self.assertEqual(1, self.con.execute("SELECT count(*) FROM payments WHERE student_id=? AND cancelled=0", (sid,)).fetchone()[0])
        self.assertTrue(targetedpriorityclients.sharipov_sale_report(self.con)["complete"])


if __name__ == "__main__":
    unittest.main()
