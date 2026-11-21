"""Системные статусы нативных карточек совпадают с подтверждёнными правилами ERP."""
import os
import tempfile
import unittest
from datetime import date

from crm import db, erpstatus, services


class ErpStatusLogicTest(unittest.TestCase):
    TODAY = date(2026, 11, 21)

    def setUp(self):
        self.con = db.connect(os.path.join(tempfile.mkdtemp(), "erp-status.sqlite3"))
        db.init_db(self.con)
        self.user = {"name": "Тест", "role": "admin"}
        self.seq = 0

    def child(self, funnel="sales"):
        self.seq += 1
        return services.create_student(self.con, {
            "name": f"Ребёнок {self.seq}", "parent_name": "Мама",
            "phone": f"+7 900 300-00-{self.seq:02d}", "funnel": funnel,
        }, self.user)

    def paid(self, sid, month, kind="month"):
        self.con.execute("""INSERT INTO subscriptions(student_id,month,tariff,lessons,per_lesson,price,status,created_at,paid_at,kind)
                            VALUES (?,?,'fixed',1,1000,1000,'paid',?||'-01T10:00',?||'-01T10:00',?)""",
                         (sid, month, month, month, kind))

    def status(self, sid):
        return erpstatus.sync_one(self.con, sid, self.TODAY, log=False)

    def test_lead_and_trial_system_statuses(self):
        sid = self.child()
        self.assertEqual(self.status(sid), "Новый лид")
        self.con.execute("UPDATE students SET stage='trial_booked',trial_at='2026-11-25T18:00' WHERE id=?", (sid,))
        self.assertEqual(self.status(sid), "Записан на ПУ")
        self.con.execute("UPDATE students SET stage='how_was',trial_came=1 WHERE id=?", (sid,))
        self.assertEqual(self.status(sid), "Был на пробном, не купил абонемент")
        self.con.execute("UPDATE students SET stage='missed_trial',trial_came=0 WHERE id=?", (sid,))
        self.assertEqual(self.status(sid), "Не пришел на пробный урок")

    def test_all_purchase_system_statuses(self):
        new = self.child("members"); self.paid(new, "2026-11")
        self.assertEqual(self.status(new), "Новый оплативший посетитель")

        regular = self.child("members"); self.paid(regular, "2026-10"); self.paid(regular, "2026-11")
        self.assertEqual(self.status(regular), "Постоянный посетитель")

        returned = self.child("members"); self.paid(returned, "2026-09"); self.paid(returned, "2026-11")
        self.assertEqual(self.status(returned), "Вернувшийся")

        unpaid = self.child("members"); self.paid(unpaid, "2026-10")
        self.assertEqual(self.status(unpaid), "Не оплачен текущий месяц")

        old = self.child("members"); self.paid(old, "2026-08")
        self.assertEqual(self.status(old), "Нет абонемента")

        single = self.child("members"); self.paid(single, "2026-11", "single")
        self.assertEqual(self.status(single), "Ходит разово")

        future = self.child("members"); self.paid(future, "2026-12")
        self.assertEqual(self.status(future), "Куплен на будущий период")

    def test_verified_user_statuses_and_imported_card_protection(self):
        sid = self.child()
        for stage, expected in (("no_answer", "Не удалось связаться"), ("later", "Думают"), ("refused", "Клиент отказался")):
            self.con.execute("UPDATE students SET stage=? WHERE id=?", (stage, sid))
            self.assertEqual(self.status(sid), expected)
        self.con.execute("UPDATE students SET erp_id='84894',erp_status='Постоянный посетитель',stage='lead' WHERE id=?", (sid,))
        self.assertEqual(erpstatus.sync_one(self.con, sid, self.TODAY), "Постоянный посетитель")

    def test_waitlist_is_the_confirmed_user_status(self):
        sid = self.child()
        branch = self.con.execute("INSERT INTO branches(name) VALUES ('Филиал')").lastrowid
        group = self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time) VALUES (?,'Группа','0','18:00')", (branch,)).lastrowid
        from crm import waitlist
        wid = waitlist.add(self.con, sid, group, "2026-12", {}, self.user)
        self.assertEqual(self.con.execute("SELECT erp_status FROM students WHERE id=?", (sid,)).fetchone()[0], "Лист ожидания")
        waitlist.set_status(self.con, wid, "declined", self.user)
        self.assertEqual(self.con.execute("SELECT erp_status FROM students WHERE id=?", (sid,)).fetchone()[0], "Новый лид")

    def test_new_lead_task_and_status_are_immediate(self):
        sid = services.create_new_lead(self.con, {
            "name": "Свежий Лид", "parent_name": "Мама", "phone": "+7 900 400-00-01",
        }, self.user)
        row = self.con.execute("SELECT created_at,erp_status FROM students WHERE id=?", (sid,)).fetchone()
        task = self.con.execute("SELECT due_at,title FROM tasks WHERE student_id=? AND status='open'", (sid,)).fetchone()
        self.assertEqual(row["erp_status"], "Новый лид")
        self.assertEqual(tuple(task), (row["created_at"][:16], "Новый лид"))


if __name__ == "__main__":
    unittest.main()
