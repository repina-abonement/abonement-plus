import os
import tempfile
import unittest
from datetime import date

from crm import db, erphistory, erpstatus, services


class PriorMembershipTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.con = db.connect(os.path.join(directory.name, 'crm.db'))
        self.addCleanup(self.con.close)
        db.init_db(self.con)
        self.user = {'name': 'Администратор', 'role': 'admin'}
        self.sid = services.create_student(self.con, {
            'name': 'Ребёнок Тестовый', 'parent_name': 'Родитель',
            'phone': '79001002030', 'funnel': 'members'}, self.user)
        self.con.execute('UPDATE students SET erp_id=? WHERE id=?', ('123', self.sid))
        self.con.execute("""INSERT INTO subscriptions(student_id,month,tariff,lessons,per_lesson,
            price,status,created_at,paid_at,kind)
            VALUES (?,'2026-11','fixed',4,1000,4000,'paid','2026-11-01','2026-11-01','month')""", (self.sid,))
        self.body = {'erp_student_id': '123', 'child': 'Тестовый Ребёнок', 'month': '2026-10',
                     'subscription_id': '42', 'evidence': 'Октябрьская выгрузка ERP'}

    def plan(self, **changes):
        return erphistory.prior_membership(self.con, {**self.body, **changes}, self.user)

    def test_preview_repeat_and_finances(self):
        plan = self.plan()
        self.assertFalse(plan['existing'])
        self.assertEqual(self.con.execute('SELECT count(*) FROM erp_prior_memberships').fetchone()[0], 0)
        for _ in range(2):
            erphistory.prior_membership(self.con, {**self.body, 'token': self.plan()['token']}, self.user, apply=True)
        self.assertEqual(erpstatus.sync_one(self.con, self.sid, date(2026, 11, 23)), 'Постоянный посетитель')
        self.assertEqual(self.con.execute('SELECT count(*) FROM erp_prior_memberships').fetchone()[0], 1)
        self.assertEqual(self.con.execute('SELECT count(*) FROM subscriptions').fetchone()[0], 1)
        self.assertEqual(self.con.execute('SELECT count(*) FROM payments').fetchone()[0], 0)

    def test_conflicts_and_wrong_month(self):
        for changes in ({'month': '2026-07'}, {'child': 'Другая Фамилия'}, {'erp_student_id': '999'}):
            with self.assertRaises(erphistory.HistoryError):
                self.plan(**changes)
        with self.assertRaises(erphistory.HistoryError):
            erphistory.prior_membership(self.con, self.body, self.user, apply=True)
        self.con.execute("INSERT INTO erp_prior_memberships VALUES ('42',?,'2026-09','источник','','')", (self.sid,))
        with self.assertRaises(erphistory.HistoryError):
            self.plan()

    def test_explicit_card_matches_family_but_does_not_replace_existing_erp_identity(self):
        self.con.execute("UPDATE students SET erp_id='' WHERE id=?", (self.sid,))
        checked = {'crm_student_id': self.sid, 'parent_phone': '+7 (900) 100-20-30'}
        self.assertEqual(self.plan(**checked)['student_id'], self.sid)
        with self.assertRaises(erphistory.HistoryError):
            self.plan(crm_student_id=self.sid, parent_phone='79000000000')
        self.con.execute('UPDATE students SET erp_id=? WHERE id=?', ('999', self.sid))
        with self.assertRaises(erphistory.HistoryError):
            self.plan(**checked)
