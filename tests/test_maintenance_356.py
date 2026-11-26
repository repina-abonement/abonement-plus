import unittest
from unittest.mock import patch

from crm import maintenance_356, money
from tests import test_invoice_balance_flows


class DuplicateSaleCorrectionTest(unittest.TestCase):
    setUp = test_invoice_balance_flows.InvoiceBalanceFlowsTest.setUp

    def test_preserves_real_payment_and_is_idempotent(self):
        con = self.con
        branch = con.execute("SELECT branch_id FROM groups WHERE id=?", (self.gid,)).fetchone()[0]
        for group, sale, price, lessons, status in (
            (8, 88, 8100, 6, "paid"), (11, 86, 10800, 7, "cancelled"),
        ):
            con.execute("INSERT INTO groups(id,branch_id,name,weekdays,time) VALUES (?,?,'Test','0,3','17:45')",
                        (group, branch))
            enrollment = con.execute("INSERT INTO enrollments(student_id,group_id,created_at) VALUES (?,?,'2026-11-26')",
                                     (self.sid, group)).lastrowid
            con.execute("""INSERT INTO subscriptions(id,student_id,enrollment_id,month,tariff,lessons,
                per_lesson,price,status,created_at) VALUES (?,?,?,'2026-12','fixed',?,1350,?,?,'2026-11-26')""",
                        (sale, self.sid, enrollment, lessons, price, status))
        con.execute("DELETE FROM payments")
        for payment, sale, amount, method, confirmed in (
            (82, 86, 10800, "tochka_qr_link", 1), (84, 88, 8100, "cash", 0),
        ):
            con.execute("""INSERT INTO payments(id,student_id,subscription_id,amount,method,confirmed,paid_at,created_at)
                VALUES (?,?,?,?,?,?,'2026-11-21','2026-11-26')""",
                        (payment, self.sid, sale, amount, method, confirmed))
        with patch.object(maintenance_356, "STUDENT", self.sid):
            self.assertEqual(maintenance_356.apply(con), "complete")
            self.assertEqual(maintenance_356.apply(con), "complete")
        self.assertEqual(money.balance(con, self.sid)["deposit"], 10800)
        self.assertEqual([tuple(row) for row in con.execute("SELECT id,cancelled FROM payments ORDER BY id")],
                         [(82, 0), (84, 1)])
        self.assertEqual(con.execute("SELECT status FROM subscriptions WHERE id=88").fetchone()[0], "cancelled")

        with patch.object(maintenance_356, "STUDENT", self.sid):
            con.execute("INSERT INTO fiscal_payment_checks VALUES (82,'2026-11-26','confirmed','{}')")
            self.assertEqual(maintenance_356.reset_payment(con), "dependencies-found")
            self.assertEqual(money.balance(con, self.sid)["deposit"], 10800)
            con.execute("DELETE FROM fiscal_payment_checks WHERE payment_id=82")
            self.assertEqual(maintenance_356.reset_payment(con), "complete")
            self.assertEqual(maintenance_356.reset_payment(con), "complete")
        self.assertEqual(money.balance(con, self.sid)["deposit"], 0)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM payments WHERE cancelled=0").fetchone()[0], 0)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM payments WHERE cancelled=1").fetchone()[0], 2)
