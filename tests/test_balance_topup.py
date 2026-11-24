import os
import tempfile
import unittest
from unittest.mock import patch

from crm import db, money, services as sv


class BalanceTopupTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(os.path.join(tempfile.mkdtemp(), "balance-topup.sqlite3"))
        db.init_db(self.con)
        self.user = {"id": 7, "name": "Варя", "role": "operator"}
        self.sid = sv.create_student(self.con, {
            "name": "Тест Балансов", "parent_name": "Анна", "phone": "+79990000001"
        }, {"id": 1, "name": "Юлия", "role": "admin"})

    @patch("crm.money.date")
    def test_historical_balance_is_past_confirmed_and_idempotent(self, fake_date):
        from datetime import date
        fake_date.today.return_value = date(2026, 11, 24)
        fake_date.fromisoformat.side_effect = date.fromisoformat
        sub = self.con.execute("""INSERT INTO subscriptions(student_id,month,tariff,lessons,per_lesson,price,status,created_at)
            VALUES (?,'2026-11','fixed',1,1000,1000,'invoiced','2026-11-20')""", (self.sid,)).lastrowid
        first = money.top_up_balance(self.con, self.sid, 2700, "historical", "", "2026-10-31",
                                     "Остаток подтверждён старой карточкой", "opening-1", self.user)
        second = money.top_up_balance(self.con, self.sid, 2700, "historical", "", "2026-10-31",
                                      "Остаток подтверждён старой карточкой", "opening-1", self.user)
        self.assertFalse(first["duplicate"]); self.assertTrue(second["duplicate"])
        self.assertEqual(2700, first["balance"]["deposit"])
        self.assertEqual(0, first["balance"]["covered"][sub])
        self.assertEqual("invoiced", self.con.execute("SELECT status FROM subscriptions WHERE id=?", (sub,)).fetchone()[0])
        row = self.con.execute("SELECT amount,method,paid_at,confirmed FROM payments").fetchone()
        self.assertEqual((2700, "opening_balance", "2026-10-31T12:00", 1), tuple(row))
        self.assertEqual(1, self.con.execute("SELECT count(*) FROM payments").fetchone()[0])

    @patch("crm.money.date")
    def test_new_payment_uses_every_manual_channel_and_historical_requires_evidence(self, fake_date):
        from datetime import date
        fake_date.today.return_value = date(2026, 11, 24)
        fake_date.fromisoformat.side_effect = date.fromisoformat
        for i, method in enumerate(money.MANUAL_METHODS):
            money.top_up_balance(self.con, self.sid, 100, "payment", method, "2026-11-24", "", f"new-{i}", self.user)
        self.assertEqual(set(money.MANUAL_METHODS), {r[0] for r in self.con.execute(
            "SELECT method FROM payments WHERE method!='opening_balance'")})
        with self.assertRaisesRegex(ValueError, "откуда известен"):
            money.top_up_balance(self.con, self.sid, 100, "historical", "", "2026-11-23", "", "bad-1", self.user)
        with self.assertRaisesRegex(ValueError, "прошедшую дату"):
            money.top_up_balance(self.con, self.sid, 100, "historical", "", "2026-11-24", "Источник", "bad-2", self.user)

    @patch("crm.money.date")
    def test_new_confirmed_topup_does_not_pay_an_existing_invoice(self, fake_date):
        from datetime import date
        fake_date.today.return_value = date(2026, 11, 24)
        fake_date.fromisoformat.side_effect = date.fromisoformat
        sub = self.con.execute("""INSERT INTO subscriptions(student_id,month,tariff,lessons,per_lesson,price,status,created_at)
            VALUES (?,'2026-11','fixed',1,1500,1500,'invoiced','2026-11-20')""", (self.sid,)).lastrowid
        result = money.top_up_balance(self.con, self.sid, 2000, "payment", "card_branch", "2026-11-24",
                                      "Оплата через терминал", "new-confirmed", {"name": "Юлия", "role": "admin"})
        self.assertEqual(2000, result["balance"]["deposit"])
        self.assertEqual(0, result["balance"]["covered"][sub])
        self.assertEqual("invoiced", self.con.execute("SELECT status FROM subscriptions WHERE id=?", (sub,)).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
