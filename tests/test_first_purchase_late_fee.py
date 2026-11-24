import os
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

from crm import db, paylinks, services as sv, smart


class FirstPurchaseLateFeeTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(os.path.join(tempfile.mkdtemp(), "first-purchase.sqlite3"))
        db.init_db(self.con)
        self.user = dict(self.con.execute("SELECT * FROM users WHERE role='admin'").fetchone())
        branch = self.con.execute("INSERT INTO branches(name,active) VALUES ('Небо',1)").lastrowid
        # December 2026 has four Mondays and four Sundays: exactly 8 lessons.
        self.group = self.con.execute("""INSERT INTO groups(branch_id,name,weekdays,time,active)
            VALUES (?,'Синтетическая группа 8 занятий','0,6','18:00',1)""", (branch,)).lastrowid
        self._phone = 0

    def tearDown(self):
        self.con.close()

    def student(self, name):
        self._phone += 1
        return sv.create_student(self.con, {"name": name, "birth": "2020-01-01", "parent_name": "Родитель",
            "phone": f"+7999001{self._phone:04d}", "funnel": "members", "group_id": self.group}, self.user)

    def enrollment(self, sid):
        return self.con.execute("SELECT id FROM enrollments WHERE student_id=?", (sid,)).fetchone()[0]

    def prior_month(self, sid, kind="month"):
        return self.con.execute("""INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,
            per_lesson,credit_lessons,late_fee,price,status,created_at,paid_at,kind)
            VALUES (?,?,?,'fixed',1,1350,0,0,1350,'paid','2026-11-01T10:00','2026-11-01T10:00',?)""",
            (sid, self.enrollment(sid), "2026-11", kind)).lastrowid

    def quote(self, sid, day):
        with patch.object(sv, "today", return_value=day), patch.object(smart, "REQUIRE", False):
            return sv.quote_for(self.con, sid, "2026-12")

    def invoice(self, sid, day):
        with patch.object(sv, "today", return_value=day), patch.object(smart, "REQUIRE", False):
            return sv.create_invoice(self.con, sid, "2026-12", self.user)

    def test_first_december_purchase_stays_10800_after_trial_or_single(self):
        sid = self.student("Первый Ребёнок")
        self.con.execute("UPDATE students SET trial_at='2026-11-20T18:00',trial_came=1 WHERE id=?", (sid,))
        self.prior_month(sid, kind="single")
        q = self.quote(sid, date(2026, 11, 26))
        self.assertEqual((8, 10800, 0, True),
                         (q["lessons_paid"], q["total"], q["late_fee"], q["pricing"]["first_purchase"]))

    def test_ordinary_renewal_is_11340_late_and_10800_on_time(self):
        sid = self.student("Обычный Ребёнок")
        self.prior_month(sid)
        on_time = self.quote(sid, date(2026, 11, 25))
        late = self.quote(sid, date(2026, 11, 26))
        self.assertEqual((10800, 0), (on_time["total"], on_time["late_fee"]))
        self.assertEqual((11340, 540), (late["total"], late["late_fee"]))

    def test_bare_renewal_draft_reprices_but_first_purchase_link_keeps_price(self):
        draft_sid = self.student("Черновик Ребёнок")
        self.prior_month(draft_sid)
        draft = self.invoice(draft_sid, date(2026, 11, 20))
        self.assertEqual(10800, draft["quote"]["total"])
        with patch.object(sv, "today", return_value=date(2026, 11, 26)):
            self.assertEqual(11340, sv.payment_quote(self.con, draft["id"], "2026-11-26")["price"])

        linked_sid = self.student("Ссылка Ребёнок")
        self.con.execute("UPDATE students SET trial_at='2026-11-19T18:00',trial_came=1 WHERE id=?", (linked_sid,))
        linked = self.invoice(linked_sid, date(2026, 11, 20))
        with patch.object(paylinks, "ready", return_value=True), patch.object(paylinks, "_now", return_value="2026-11-20T12:00"), \
                patch.object(sv, "today", return_value=date(2026, 11, 20)):
            link = paylinks.create(self.con, linked_sid, 10800, self.user,
                subscription_id=linked["id"], full_payment=True)
        with patch.object(sv, "today", return_value=date(2026, 11, 26)):
            self.assertEqual(10800, sv.payment_quote(self.con, linked["id"], "2026-11-26")["price"])
        added = paylinks.apply_payments(self.con, [{"id": "synthetic-pk", "orderid": self.con.execute(
            "SELECT orderid FROM pay_links WHERE id=?", (link["id"],)).fetchone()[0], "status": "success",
            "pay_amount": "10800", "success_datetime": "2026-11-26 12:00:00"}])
        row = self.con.execute("SELECT price,status FROM subscriptions WHERE id=?", (linked["id"],)).fetchone()
        self.assertEqual((1, 10800, "paid"), (added, row["price"], row["status"]))
        with patch.object(sv, "today", return_value=date(2026, 11, 26)):
            self.assertEqual(0, sv.payment_quote(self.con, linked["id"], "2026-11-26")["total"])


if __name__ == "__main__":
    unittest.main()
