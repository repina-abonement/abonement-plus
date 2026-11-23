import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from crm import db, groupterms, money, schedule, services as sv, tasks


class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 11, 22)


class RenewalPaymentChainTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(str(Path(tempfile.mkdtemp()) / "chain.sqlite3"))
        db.init_db(self.con)
        self.addCleanup(self.con.close)
        self.user = {"id": 1, "role": "admin", "name": "Synthetic"}
        branch = schedule.save_branch(self.con, {"name": "Тестовый филиал"})
        self.group = schedule.save_group(self.con, {"name": "Тест 4–5", "branch_id": branch,
            "weekdays": [1, 3], "time": "18:00", "age": "4-5"})
        groupterms.init(self.con)
        self.sid = sv.create_student(self.con, {"name": "Тестовый Ребёнок", "birth": "2022-01-01",
            "parent_name": "Родитель", "phone": "+79000000001", "group_id": self.group}, self.user)
        self.enrollment = self.con.execute("""INSERT INTO enrollments(student_id,group_id,weekdays,tariff,active,created_at)
            VALUES (?,?,'1,3','fixed',1,'2026-11-01')""", (self.sid, self.group)).lastrowid
        self.con.execute("UPDATE students SET erp_id='synthetic-erp',erp_status='Постоянный посетитель' WHERE id=?", (self.sid,))
        self.clock = patch.object(tasks, "date", FixedDate)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def add_invoice(self, month, price, status="invoiced"):
        return self.con.execute("""INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,
            per_lesson,price,status,created_at,ledger_managed) VALUES (?,?,?,'fixed',8,?,?,?,'2026-11-22T10:00',1)""",
            (self.sid, self.enrollment, month, price // 8, price, status)).lastrowid

    def test_invoice_partial_and_full_payment_move_one_task_through_chain(self):
        # A zero-price paid current period is enough to establish the existing
        # renewal step without introducing an unfunded historical obligation.
        self.add_invoice("2026-11", 0, "paid")
        renewal_id = tasks.schedule_renewal(self.con, self.sid)
        invoice_id = self.add_invoice("2026-12", 10000)
        existing_control = tasks.add_task(self.con, "manual", self.sid, "2026-11-25T23:59",
            title="Проверить оплату абонемента", source="erp")
        tasks.sync_subscription_payment_tasks(self.con, self.sid)

        control = self.con.execute("SELECT * FROM tasks WHERE auto_key=?", (f"subscription-payment:{self.sid}",)).fetchone()
        self.assertEqual(control["id"], existing_control)
        self.assertEqual((control["title"], control["due_at"], control["status"]),
                         ("Проверить оплату абонемента", "2026-11-25T23:59", "open"))
        self.assertEqual(tuple(self.con.execute("SELECT status,due_at FROM tasks WHERE id=?", (renewal_id,)).fetchone()), ("open", "2026-12-20T23:59"))
        self.assertIn("10000 ₽", control["note"])

        money.add_payment(self.con, self.sid, 4000, "card_branch", self.user, subscription_id=invoice_id)
        partial = self.con.execute("SELECT * FROM tasks WHERE id=?", (control["id"],)).fetchone()
        self.assertEqual(partial["status"], "open")
        self.assertIn("6000 ₽", partial["note"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM tasks WHERE student_id=? AND status='open' AND kind='payment_check'", (self.sid,)).fetchone()[0], 1)

        money.add_payment(self.con, self.sid, 6000, "card_branch", self.user, subscription_id=invoice_id)
        closed = self.con.execute("SELECT status,result FROM tasks WHERE id=?", (control["id"],)).fetchone()
        self.assertEqual(tuple(closed), ("done", "Счёт полностью оплачен"))
        renewed = self.con.execute("SELECT * FROM tasks WHERE student_id=? AND status='open' AND kind='renew'", (self.sid,)).fetchall()
        self.assertEqual(len(renewed), 1)
        self.assertEqual(renewed[0]["title"], "Проверить продление")
        self.assertEqual(renewed[0]["due_at"], "2026-12-20T23:59")

        money.reconcile(self.con, self.sid)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM tasks WHERE student_id=? AND status='open' AND kind='renew'", (self.sid,)).fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT erp_status FROM students WHERE id=?", (self.sid,)).fetchone()[0], "Новый оплативший посетитель")


if __name__ == "__main__":
    unittest.main()
