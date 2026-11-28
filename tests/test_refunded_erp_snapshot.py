import hashlib
import sqlite3
import unittest
from unittest.mock import patch

from crm import erprevenue


class RefundedSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(self.con.close)
        self.con.executescript("""
            CREATE TABLE erp_revenue_history(row_key PRIMARY KEY,student_id,erp_id,month,kind,sale,start,end,amount);
            CREATE TABLE erp_revenue_months(month PRIMARY KEY,amount);
            CREATE TABLE erp_subscription_months(month PRIMARY KEY,holders,subscriptions,subscription_revenue);
            CREATE TABLE students(id,erp_id);
            CREATE TABLE erp_full_identities(student_id,source_id,namespace);
            INSERT INTO students VALUES(197,'120486'),(198,'120487');
        """)
        self.csv = ("file,row,erp,kind,sale,start,end,amount\n"
                    "f,1,120486,Абонемент (стандарт /8),23.10.2026,07.11.2026,29.11.2026,9450\n"
                    "f,2,120487,Абонемент (стандарт /8),23.10.2026,07.11.2026,29.11.2026,9450\n"
                    "f,3,120486,Абонемент (стандарт /8),01.03.2026,07.03.2026,29.03.2026,9450\n")

    def transfer(self):
        with patch.object(erprevenue, "DIGEST", hashlib.sha256(self.csv.encode()).hexdigest()):
            plan = erprevenue.transfer(self.con, self.csv)
            erprevenue.transfer(self.con, self.csv, plan["token"])
            self.con.commit()

    def assert_totals(self):
        self.assertEqual(self.con.execute("SELECT amount FROM erp_revenue_history ORDER BY row_key").fetchall(),
                         [(0,), (9450,), (9450,)])
        self.assertEqual(dict(self.con.execute("SELECT * FROM erp_revenue_months")),
                         {"2026-03": 9450, "2026-11": 9450})
        self.assertEqual(self.con.execute("SELECT * FROM erp_subscription_months WHERE month='2026-11'").fetchone(),
                         ("2026-11", 2, 2, 9450))

    def test_existing_and_reimport(self):
        with patch.object(erprevenue, "recognized_amount", side_effect=lambda *args: args[-1]):
            self.transfer()
        erprevenue.correct_refunded_snapshot(self.con)
        self.assert_totals()
        erprevenue.correct_refunded_snapshot(self.con)
        self.transfer()
        self.assert_totals()

    def test_atomic_if_aggregate_update_fails(self):
        with patch.object(erprevenue, "recognized_amount", side_effect=lambda *args: args[-1]):
            self.transfer()
        self.con.execute("""CREATE TRIGGER reject_change BEFORE UPDATE ON erp_subscription_months
                            BEGIN SELECT RAISE(ABORT, 'test rejection'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            erprevenue.correct_refunded_snapshot(self.con)
        self.assertEqual(self.con.execute("SELECT amount FROM erp_revenue_history WHERE row_key='f/1'").fetchone(),
                         (9450,))
        self.assertEqual(self.con.execute("SELECT amount FROM erp_revenue_months WHERE month='2026-11'").fetchone(),
                         (18900,))

    def test_fresh_import(self):
        self.transfer()
        self.assert_totals()


if __name__ == "__main__":
    unittest.main()
