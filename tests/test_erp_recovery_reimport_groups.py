"""A stale global ERP group alias cannot undo a reviewed child's recovery.

Only temporary synthetic databases are used. The confirmed ERP IDs and dates
come from the reviewed transfer case; names, local records and extra October
history are synthetic. Payment-register group text is never sale-date proof.
"""
import csv
from datetime import datetime
import io
import unittest

from crm import clientattendance, erpfull, erpimport
from tests import test_erp_single_recovery as recovery_fixture


class RecoveredChildGroupReimportTest(unittest.TestCase):
    def setUp(self):
        self.fixture = recovery_fixture.SingleAggregateRecoveryTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f = self.fixture
        self.con = self.f.con
        self.wrong_enrollment = self.con.execute("""INSERT INTO enrollments(student_id,group_id,
            tariff,active,created_at,start_date) VALUES (?,?,'fixed',0,'2026-10-01','2026-10-01')""",
            (self.f.sid, self.f.wrong_gid)).lastrowid
        october = self.con.execute("""INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,
            kind,lessons,per_lesson,price,status,created_at,paid_at,erp_full_managed)
            VALUES (?,?,'2026-10','fixed','month',1,1000,1000,'paid','2026-10-01',
            '2026-10-01T12:00',1)""", (self.f.sid, self.wrong_enrollment)).lastrowid
        self.con.execute("""INSERT INTO payments(student_id,amount,method,paid_at,comment,user_name,
            confirmed,subscription_id,created_at) VALUES (?,1000,'card','2026-10-01T12:00',
            'Синтетическая отдельная оплата октября','ERP',1,?,'2026-10-01T12:00')""", (self.f.sid, october))
        self.f.apply_recovery()
        self.con.execute("UPDATE students SET erp_status='Занимается' WHERE id=?", (self.f.sid,))
        # This is deliberately stale and must remain unchanged globally: a
        # reviewed student's source identity takes precedence only for them.
        erpimport.save_groups(self.con, {'Небо': self.f.wrong_gid})

    def upload(self, kind, headers, rows):
        text = io.StringIO()
        writer = csv.writer(text, delimiter=';')
        writer.writerow(headers)
        writer.writerows(rows)
        return erpimport.upload(self.con, 'synthetic-group-replay-'+kind+'.csv', text.getvalue().encode(), kind)['id']

    def stage(self, reverse=False, mutation=None):
        self.con.execute('DELETE FROM erp_uploads')
        self.upload('clients', ['ID', 'ФИО ребенка', 'ФИО родителя', 'Телефон', 'Филиал', 'Группа', 'Статус'],
            [['133513', 'Синтетический Ребёнок', 'Синтетический Родитель', '+79000000551',
              'Небо', 'Небо', 'Занимается']])
        sales, payments = [], []
        source = list(reversed(self.f.SALES)) if reverse else self.f.SALES
        for index, row in enumerate(source):
            sale_id = row['sale_id']
            operation_id = row['operation_id']
            price = row['amount']
            tariff = 'Разовое занятие'
            group = 'Небо'
            day = row['source_date']
            if mutation == 'changed_price':
                price = 2600
            elif mutation == 'changed_type':
                tariff = 'Базовый'
            elif mutation == 'unknown_group':
                group = 'Неизвестная группа из источника'
            elif mutation == 'missing_known_date':
                day = ''
            elif mutation == 'unknown_id_missing_fields':
                sale_id = 'synthetic-unknown-sale-'+str(index)
                operation_id = 'synthetic-unknown-operation-'+str(index)
                price, day = '', ''
            sales.append([sale_id, '133513', 'Синтетический Ребёнок', '2026-11', group,
                          price, 1, 'Оплачен', tariff, day, operation_id, ''])
            # Payment identities still refer to the four actual reviewed
            # operations; the misleading group is an unrelated export column.
            payments.append([row['operation_id'], row['sale_id'], '133513', 'Синтетический Ребёнок',
                             '2026-10-28', row['amount'], 'Карта', 'Небо 17:45'])
        self.upload('subs', ['ID продажи', 'ID клиента', 'Ученик', 'Месяц', 'Группа', 'Стоимость',
            'Занятий', 'Статус', 'Тариф', 'Дата занятия', 'ID операции', 'Дата продажи'], sales)
        self.upload('payments', ['ID операции', 'ID продажи', 'ID клиента', 'Плательщик', 'Дата оплаты',
            'Сумма', 'Способ оплаты', 'Группа из платёжного реестра'], payments)

    def current_state(self):
        return {
            'profile': dict(self.con.execute('SELECT * FROM students WHERE id=?', (self.f.sid,)).fetchone()),
            'enrollments': [tuple(row) for row in self.con.execute('SELECT * FROM enrollments WHERE student_id=? ORDER BY id', (self.f.sid,))],
            'payments': [tuple(row) for row in self.con.execute('SELECT * FROM payments ORDER BY id')],
            'subscriptions': [tuple(row) for row in self.con.execute('SELECT * FROM subscriptions ORDER BY id')],
            'visits': [tuple(row) for row in self.con.execute('SELECT * FROM single_visits ORDER BY id')],
            'operations': [tuple(row) for row in self.con.execute('SELECT * FROM erp_full_operations ORDER BY operation_id')],
            'source_links': [tuple(row) for row in self.con.execute('SELECT * FROM erp_single_payments ORDER BY operation_id')],
            'global_mapping': [tuple(row) for row in self.con.execute('SELECT * FROM erp_group_map ORDER BY erp_name')],
        }

    def assert_correct_profile_and_calendar(self):
        self.assertEqual(self.con.execute('SELECT group_id FROM students WHERE id=?', (self.f.sid,)).fetchone()[0], self.f.gid)
        active = {row['group_id']: row['active'] for row in self.con.execute('SELECT * FROM enrollments WHERE student_id=?', (self.f.sid,))}
        self.assertEqual(active[self.f.gid], 1)
        self.assertEqual(active[self.f.wrong_gid], 0)
        self.assertEqual(self.con.execute("SELECT group_id FROM erp_group_map WHERE erp_name='Небо'").fetchone()[0], self.f.wrong_gid)
        november = clientattendance.calendar(self.con, self.f.sid, '2026-11')
        december = clientattendance.calendar(self.con, self.f.sid, '2026-12')
        self.assertEqual([(row['date'], row['kind'], row['time']) for row in november['events']],
                         [('2026-11-11', 'single', '18:45'), ('2026-11-18', 'single', '18:45'),
                          ('2026-11-25', 'single', '18:45')])
        self.assertEqual([(row['date'], row['kind'], row['time']) for row in december['events']], [('2026-12-02', 'single', '18:45')])

    def test_same_and_reordered_legacy_update_keeps_reviewed_group_and_payment_identities(self):
        before = self.current_state()
        self.assert_correct_profile_and_calendar()
        for reverse in (False, True, False):
            self.stage(reverse=reverse)
            result = erpimport.update(self.con, self.f.user, now=datetime(2026, 11, 20, 12))
            with self.subTest(reverse=reverse):
                self.assertFalse(result.get('issues'), result.get('issues'))
                self.assertEqual(self.current_state(), before)
                self.assert_correct_profile_and_calendar()

    def full_replay(self, index):
        files=[r[0] for r in self.con.execute('SELECT id FROM erp_uploads ORDER BY id')]
        plan=erpfull.plan(self.con,files)
        return erpfull.apply(self.con,files,plan['token'],self.f.user,
            self.f.backup('before-full-replay-'+str(index)+'.sqlite3'))

    def test_full_replay_uses_confirmed_source_identity_despite_stale_alias(self):
        before=self.current_state()
        for index,reverse in enumerate((False,True)):
            self.stage(reverse=reverse)
            result=self.full_replay(index)
            with self.subTest(reverse=reverse):
                self.assertTrue(result['complete'],result.get('issues'))
                self.assertEqual(self.current_state(),before)
                self.assert_correct_profile_and_calendar()

    def test_full_replay_does_not_adopt_a_changed_unknown_source_group(self):
        before=self.current_state()
        self.stage(mutation='unknown_group')
        result=self.full_replay('conflicting-group')
        self.assertFalse(result['complete'])
        self.assertTrue(result.get('issues'))
        self.assertEqual(self.current_state(),before)
        self.assert_correct_profile_and_calendar()

    def assert_failed_source_keeps_existing_state(self, mutation):
        before = self.current_state()
        for reverse in (False, True):
            self.stage(reverse=reverse, mutation=mutation)
            result = erpimport.update(self.con, self.f.user, now=datetime(2026, 11, 20, 12))
            with self.subTest(mutation=mutation, reverse=reverse):
                self.assertTrue(result.get('issues'), 'Conflicting or incomplete ERP source must be retained for review')
                self.assertEqual(self.current_state(), before)
                self.assert_correct_profile_and_calendar()
                self.assertEqual(self.con.execute('SELECT COUNT(*) FROM single_visits').fetchone()[0], 4)

    def test_changed_price_is_reviewed_before_any_existing_profile_group_change(self):
        self.assert_failed_source_keeps_existing_state('changed_price')

    def test_changed_type_is_reviewed_before_any_existing_profile_group_change(self):
        self.assert_failed_source_keeps_existing_state('changed_type')

    def test_unknown_group_is_reviewed_before_any_existing_profile_group_change(self):
        self.assert_failed_source_keeps_existing_state('unknown_group')

    def test_missing_date_of_known_sale_keeps_profile_and_existing_bookings(self):
        self.assert_failed_source_keeps_existing_state('missing_known_date')

    def test_new_unknown_sales_with_missing_required_fields_keep_known_profile(self):
        self.assert_failed_source_keeps_existing_state('unknown_id_missing_fields')


if __name__ == '__main__':
    unittest.main()
