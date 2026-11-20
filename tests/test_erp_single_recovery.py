"""Targeted recovery uses confirmed source IDs and only synthetic local data.

The supplied ERP identifiers and lesson dates reproduce the reviewed case;
names, local payment IDs and times are synthetic. No production database,
bank, payment provider or fiscal endpoint is opened by these tests.
"""
from contextlib import redirect_stdout
from copy import deepcopy
import csv
from datetime import date
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from crm import clientattendance, db, erpfull, erpimport, erpsingles, erpsinglerecovery, services


class SingleAggregateRecoveryTest(unittest.TestCase):
    SALES = [
        {'sale_id': '828081', 'invoice_id': '729454', 'operation_id': '1197471',
         'source_date': '2026-11-04', 'effective_date': '2026-12-02', 'amount': 2500},
        {'sale_id': '828083', 'invoice_id': '729456', 'operation_id': '1197475',
         'source_date': '2026-11-11', 'effective_date': '2026-11-11', 'amount': 2500},
        {'sale_id': '828084', 'invoice_id': '729457', 'operation_id': '1197477',
         'source_date': '2026-11-18', 'effective_date': '2026-11-18', 'amount': 2500},
        {'sale_id': '828085', 'invoice_id': '729458', 'operation_id': '1197479',
         'source_date': '2026-11-25', 'effective_date': '2026-11-25', 'amount': 2500},
    ]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='single-aggregate-recovery-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'synthetic.sqlite3'
        self.con = db.connect(str(self.path))
        self.addCleanup(lambda: self.con.close())
        with redirect_stdout(io.StringIO()):
            db.init_db(self.con)
        erpsinglerecovery.init(self.con)
        clock = patch.object(services, 'today', return_value=date(2026, 11, 20))
        clock.start()
        self.addCleanup(clock.stop)
        self.user = {'id': 1, 'name': 'Синтетический руководитель', 'role': 'admin'}
        branch = self.con.execute("INSERT INTO branches(name) VALUES ('Небо')").lastrowid
        self.gid = self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time) VALUES (?,'Небо','2','18:45')", (branch,)).lastrowid
        self.wrong_gid = self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time) VALUES (?,'Небо, неверное время','2','17:45')", (branch,)).lastrowid
        self.sid = services.create_student(self.con, {'name': 'Синтетический Ребёнок',
            'parent_name': 'Синтетический Родитель', 'phone': '+79000000551', 'funnel': 'members',
            'group_id': self.gid}, self.user)
        self.con.execute("UPDATE students SET erp_id='133513' WHERE id=?", (self.sid,))
        enrollment = self.con.execute('SELECT id FROM enrollments WHERE student_id=? AND group_id=?', (self.sid, self.gid)).fetchone()[0]
        self.con.execute("UPDATE enrollments SET start_date='2026-11-01',created_at='2026-11-01' WHERE id=?", (enrollment,))
        self.aggregate_id = self.con.execute("""INSERT INTO subscriptions(student_id,enrollment_id,
            month,tariff,kind,lessons,per_lesson,price,status,created_at,paid_at,erp_full_managed)
            VALUES (?,?,'2026-11','fixed','month',4,2500,10000,'paid',
            '2026-10-28T12:00','2026-10-28T12:00',1)""", (self.sid, enrollment)).lastrowid
        self.payment_mapping = {}
        for index, sale in enumerate(self.SALES):
            payment_id = self.con.execute("""INSERT INTO payments(student_id,amount,method,paid_at,
                comment,user_name,confirmed,confirmed_by,confirmed_at,subscription_id,created_at)
                VALUES (?,2500,'card','2026-10-28T12:00',?,'ERP',1,'Исходный перенос',
                '2026-10-28T12:00',?,'2026-10-28T12:00')""",
                (self.sid, 'Синтетическая исходная оплата '+str(index+1), self.aggregate_id)).lastrowid
            self.payment_mapping[sale['sale_id']] = payment_id
        self.data = {'erp_id': '133513', 'group_id': self.gid, 'aggregate_id': self.aggregate_id,
                     'sales': deepcopy(self.SALES), 'payment_mapping': dict(self.payment_mapping),
                     'mapping_reviewed': True, 'reason': 'Подтверждённая сверка исходных продаж ERP',
                     'payment_date': '2026-10-28', 'payment_method': 'card',
                     'confirmed_group': {'branch': 'Небо', 'weekday': 2, 'time': '18:45'}}

    def backup(self, name):
        target = Path(self.temp.name)/name
        backup = sqlite3.connect(str(target))
        try:
            self.con.backup(backup)
        finally:
            backup.close()
        return str(target)

    def apply_recovery(self):
        preview = erpsinglerecovery.plan(self.con, self.data)
        self.assertTrue(preview['can_apply'], preview.get('issues'))
        return erpsinglerecovery.apply(self.con, self.data, preview['token'], self.user, self.backup('before-apply.sqlite3'))

    def payments(self):
        return [dict(row) for row in self.con.execute('SELECT * FROM payments ORDER BY id')]

    def aggregate(self):
        return dict(self.con.execute('SELECT * FROM subscriptions WHERE id=?', (self.aggregate_id,)).fetchone())

    def test_plan_is_readonly_and_four_equal_payments_are_never_auto_selected(self):
        before = self.con.serialize()
        changes = self.con.total_changes
        no_mapping = deepcopy(self.data)
        no_mapping.pop('payment_mapping')
        no_mapping['mapping_reviewed'] = False
        missing = erpsinglerecovery.plan(self.con, no_mapping)
        self.assertFalse(missing['can_apply'])
        self.assertTrue(missing['issues'])
        ready = erpsinglerecovery.plan(self.con, self.data)
        self.assertTrue(ready['can_apply'], ready.get('issues'))
        self.assertEqual(self.con.total_changes, changes)
        self.assertEqual(self.con.serialize(), before)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0], 1)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM single_visits').fetchone()[0], 0)

    def test_exact_operation_identity_can_resolve_existing_payments_without_manual_guess(self):
        for sale in self.SALES:
            signature = json.dumps([self.sid, '2026-10-28', 2500, 'card'], ensure_ascii=False,
                                   sort_keys=True, separators=(',', ':'))
            self.con.execute('INSERT INTO erp_full_operations(operation_id,payment_id,signature) VALUES (?,?,?)',
                             (sale['operation_id'], self.payment_mapping[sale['sale_id']], signature))
        data = deepcopy(self.data)
        data.pop('payment_mapping')
        data['mapping_reviewed'] = False
        preview = erpsinglerecovery.plan(self.con, data)
        self.assertTrue(preview['can_apply'], preview.get('issues'))
        self.assertEqual({str(key): int(value) for key, value in preview['payment_mapping'].items()}, self.payment_mapping)

    def test_wrong_payment_register_group_time_cannot_drive_recovery_calendar(self):
        data = deepcopy(self.data)
        data['group_id'] = self.wrong_gid
        preview = erpsinglerecovery.plan(self.con, data)
        self.assertFalse(preview['can_apply'])
        self.assertTrue(preview['issues'])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0], 1)

    def test_confirmed_sale_group_replaces_wrong_legacy_aggregate_group_for_new_bookings(self):
        wrong_enrollment = self.con.execute("""INSERT INTO enrollments(student_id,group_id,tariff,
            active,created_at) VALUES (?,?,'fixed',0,'2026-11-01')""", (self.sid, self.wrong_gid)).lastrowid
        self.con.execute('UPDATE subscriptions SET enrollment_id=? WHERE id=?', (wrong_enrollment, self.aggregate_id))
        before_payments = self.payments()
        preview = erpsinglerecovery.plan(self.con, self.data)
        self.assertTrue(preview['can_apply'], preview.get('issues'))
        erpsinglerecovery.apply(self.con, self.data, preview['token'], self.user, self.backup('before-wrong-legacy-group.sqlite3'))
        self.assertEqual({row[0] for row in self.con.execute('SELECT group_id FROM single_visits')}, {self.gid})
        december = clientattendance.calendar(self.con, self.sid, '2026-12')
        self.assertEqual([(row['date'], row['kind'], row['time']) for row in december['events']], [('2026-12-02', 'single', '18:45')])
        for old, new in zip(before_payments, self.payments()):
            self.assertEqual({key: value for key, value in new.items() if key != 'subscription_id'},
                             {key: value for key, value in old.items() if key != 'subscription_id'})

    def test_explicit_payment_mapping_requires_review_and_distinct_existing_payments(self):
        for mutation in ('not_reviewed', 'duplicate_payment'):
            data = deepcopy(self.data)
            if mutation == 'not_reviewed':
                data['mapping_reviewed'] = False
            else:
                payment_id = next(iter(self.payment_mapping.values()))
                data['payment_mapping'] = {sale['sale_id']: payment_id for sale in self.SALES}
            with self.subTest(mutation=mutation):
                preview = erpsinglerecovery.plan(self.con, data)
                self.assertFalse(preview['can_apply'])
                self.assertTrue(preview['issues'])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0], 1)

    def test_manual_mapping_cannot_override_a_known_operation_identity(self):
        sale = self.SALES[0]
        signature = json.dumps([self.sid, '2026-10-28', 2500, 'card'], ensure_ascii=False,
                               sort_keys=True, separators=(',', ':'))
        self.con.execute('INSERT INTO erp_full_operations(operation_id,payment_id,signature) VALUES (?,?,?)',
                         (sale['operation_id'], self.payment_mapping[sale['sale_id']], signature))
        data = deepcopy(self.data)
        data['payment_mapping']['828081'], data['payment_mapping']['828083'] = (
            data['payment_mapping']['828083'], data['payment_mapping']['828081'])
        preview = erpsinglerecovery.plan(self.con, data)
        self.assertFalse(preview['can_apply'])
        self.assertTrue(preview['issues'])
        self.assertEqual(self.con.execute('SELECT payment_id FROM erp_full_operations WHERE operation_id=?',
                         (sale['operation_id'],)).fetchone()[0], self.payment_mapping[sale['sale_id']])

    def test_confirmed_card_method_conflict_is_reported_without_rewriting_payment(self):
        payment_id = self.payment_mapping['828081']
        self.con.execute("UPDATE payments SET method='cash' WHERE id=?", (payment_id,))
        payments = self.payments()
        snapshot = self.con.serialize()
        preview = erpsinglerecovery.plan(self.con, self.data)
        self.assertFalse(preview['can_apply'])
        self.assertTrue(any('способ' in issue.lower() for issue in preview['issues']))
        self.assertEqual(self.payments(), payments)
        self.assertEqual(self.con.serialize(), snapshot)
        self.assertEqual(self.con.execute('SELECT method FROM payments WHERE id=?', (payment_id,)).fetchone()[0], 'cash')

    def test_recovery_preserves_payments_and_builds_only_the_confirmed_lesson_dates(self):
        before_payments = self.payments()
        before_aggregate = self.aggregate()
        result = self.apply_recovery()
        self.assertTrue(result['run_id'])
        self.assertEqual(self.aggregate()['status'], 'cancelled')
        old_other = {key: value for key, value in before_aggregate.items() if key != 'status'}
        new_other = {key: value for key, value in self.aggregate().items() if key != 'status'}
        self.assertEqual(new_other, old_other)
        singles = [dict(row) for row in self.con.execute("SELECT * FROM subscriptions WHERE kind='single' ORDER BY id")]
        self.assertEqual(len(singles), 4)
        self.assertTrue(all(row['price'] == 2500 and row['lessons'] == 1 and row['month'] == '2026-11' for row in singles))
        self.assertEqual(sum(row['price'] for row in singles), 10000)
        after_payments = self.payments()
        self.assertEqual(len(after_payments), 4)
        for old, new in zip(before_payments, after_payments):
            self.assertEqual({key: value for key, value in new.items() if key != 'subscription_id'},
                             {key: value for key, value in old.items() if key != 'subscription_id'})
            self.assertNotEqual(new['subscription_id'], self.aggregate_id)
        self.assertEqual(len({row['subscription_id'] for row in after_payments}), 4)
        source_rows = {row['source_id']: dict(row) for row in self.con.execute('SELECT * FROM erp_single_sales')}
        self.assertEqual(set(source_rows), {row['sale_id'] for row in self.SALES})
        for sale in self.SALES:
            source = source_rows[sale['sale_id']]
            self.assertEqual(source['lesson_date'], sale['source_date'])
            self.assertEqual(source['payment_operation_id'], sale['operation_id'])
            self.assertIn(sale['invoice_id'], source['source_payload'])
            native_payment = self.con.execute('SELECT subscription_id FROM payments WHERE id=?', (self.payment_mapping[sale['sale_id']],)).fetchone()[0]
            self.assertEqual(native_payment, source['subscription_id'])
        audit = self.con.execute('SELECT source_id,from_date,to_date FROM erp_single_reschedules').fetchall()
        self.assertEqual([tuple(row) for row in audit], [('828081', '2026-11-04', '2026-12-02')])
        november = clientattendance.calendar(self.con, self.sid, '2026-11')
        december = clientattendance.calendar(self.con, self.sid, '2026-12')
        self.assertEqual([(row['date'], row['kind']) for row in november['events']],
                         [('2026-11-11', 'single'), ('2026-11-18', 'single'), ('2026-11-25', 'single')])
        self.assertEqual([(row['date'], row['kind']) for row in december['events']], [('2026-12-02', 'single')])
        moved = [row for row in services.journal(self.con, self.gid, '2026-12-02')['rows'] if row['id'] == self.sid]
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0]['tag'], 'single')
        self.assertFalse(moved[0]['unpaid'])
        self.con.close()
        self.con = db.connect(str(self.path))
        with redirect_stdout(io.StringIO()):
            db.init_db(self.con)
        erpsinglerecovery.init(self.con)
        self.assertEqual(self.payments(), after_payments)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM subscriptions WHERE kind='single'").fetchone()[0], 4)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM payments WHERE comment='Оплата до реестра платежей'").fetchone()[0], 0)

    def test_same_approved_preview_cannot_create_duplicate_recovery(self):
        preview = erpsinglerecovery.plan(self.con, self.data)
        erpsinglerecovery.apply(self.con, self.data, preview['token'], self.user, self.backup('before-first.sqlite3'))
        count_before = self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0]
        payments_before = self.payments()
        with self.assertRaises(erpimport.ImportError_):
            erpsinglerecovery.apply(self.con, self.data, preview['token'], self.user, self.backup('before-repeat.sqlite3'))
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0], count_before)
        self.assertEqual(self.payments(), payments_before)

    def test_original_erp_rows_replay_without_duplicates_or_losing_audited_move(self):
        self.apply_recovery()
        before_payments = self.payments()
        before_subscriptions = [tuple(row) for row in self.con.execute('SELECT * FROM subscriptions ORDER BY id')]
        before_source_ids = [tuple(row) for row in self.con.execute('SELECT source_id,subscription_id FROM erp_single_sales ORDER BY source_id')]
        erpimport.save_groups(self.con, {'Небо': self.gid})
        def upload(kind, headers, rows):
            buffer = io.StringIO()
            writer = csv.writer(buffer, delimiter=';')
            writer.writerow(headers)
            writer.writerows(rows)
            return erpimport.upload(self.con, 'synthetic-replay-'+kind+'.csv', buffer.getvalue().encode(), kind)['id']
        for reverse in (False, True):
            source = list(reversed(self.SALES)) if reverse else self.SALES
            sales_id = upload('subs', ['ID продажи', 'ID клиента', 'Ученик', 'Группа', 'Месяц',
                'Стоимость', 'Занятий', 'Статус', 'Тариф', 'Дата занятия', 'ID операции', 'Дата продажи'],
                [[row['sale_id'], '133513', 'Синтетический Ребёнок', 'Небо', '2026-11',
                  row['amount'], 1, 'Оплачен', 'Разовое занятие', row['source_date'], row['operation_id'], ''] for row in source])
            payments_id = upload('payments', ['ID операции', 'ID продажи', 'ID клиента', 'Плательщик',
                'Дата оплаты', 'Сумма', 'Способ оплаты', 'Группа из платёжного реестра'],
                [[row['operation_id'], row['sale_id'], '133513', 'Синтетический Ребёнок',
                  '2026-10-28', row['amount'], 'Карта', 'Небо 17:45'] for row in source])
            plan = erpfull.plan(self.con, [sales_id, payments_id])
            replay = erpfull.apply(self.con, [sales_id, payments_id], plan['token'], self.user, self.backup('before-source-replay.sqlite3'))
            with self.subTest(reverse=reverse):
                self.assertTrue(replay['complete'], replay['issues'])
                self.assertEqual(self.payments(), before_payments)
                self.assertEqual([tuple(row) for row in self.con.execute('SELECT * FROM subscriptions ORDER BY id')], before_subscriptions)
                self.assertEqual([tuple(row) for row in self.con.execute('SELECT source_id,subscription_id FROM erp_single_sales ORDER BY source_id')], before_source_ids)
                self.assertEqual(self.con.execute("SELECT COUNT(*) FROM single_visits WHERE date='2026-11-04'").fetchone()[0], 0)
                december = clientattendance.calendar(self.con, self.sid, '2026-12')
                self.assertEqual([(row['date'], row['kind'], row['time']) for row in december['events']], [('2026-12-02', 'single', '18:45')])

    def test_changed_payment_after_preview_blocks_apply_and_keeps_current_data(self):
        preview = erpsinglerecovery.plan(self.con, self.data)
        payment_id = next(iter(self.payment_mapping.values()))
        self.con.execute("UPDATE payments SET comment='Ручная правка после проверки' WHERE id=?", (payment_id,))
        before = self.payments()
        with self.assertRaises(erpimport.ImportError_):
            erpsinglerecovery.apply(self.con, self.data, preview['token'], self.user, self.backup('before-stale.sqlite3'))
        self.assertEqual(self.payments(), before)
        self.assertEqual(self.aggregate()['status'], 'paid')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0], 1)

    def test_unrelated_existing_debt_is_preserved_in_preview_and_recovery(self):
        other_branch = self.con.execute("INSERT INTO branches(name) VALUES ('Синтетический другой филиал')").lastrowid
        other_group = self.con.execute("""INSERT INTO groups(branch_id,name,weekdays,time)
            VALUES (?,'Синтетическая другая группа','0','10:00')""", (other_branch,)).lastrowid
        enrollment = self.con.execute("""INSERT INTO enrollments(student_id,group_id,tariff,active,created_at)
            VALUES (?,?,'fixed',0,'2026-10-01')""", (self.sid, other_group)).lastrowid
        unpaid = self.con.execute("""INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,
            kind,lessons,per_lesson,price,status,created_at,erp_full_managed)
            VALUES (?,?,'2026-10','fixed','month',1,1000,1000,'invoiced','2026-10-01',1)""",
            (self.sid, enrollment)).lastrowid
        original = dict(self.con.execute('SELECT * FROM subscriptions WHERE id=?', (unpaid,)).fetchone())
        payments = self.payments()
        # The balance clock is fixed separately from the planner clock; this
        # invoice must stay historical even when the test is run years later.
        with patch('crm.money.date') as balance_clock:
            balance_clock.today.return_value = date(2026, 11, 20)
            preview = erpsinglerecovery.plan(self.con, self.data)
            self.assertTrue(preview['can_apply'], preview.get('issues'))
            self.assertEqual(preview['before_summary']['debt'], 1000)
            self.assertEqual(preview['after_summary']['debt'], 1000)
            result = erpsinglerecovery.apply(self.con, self.data, preview['token'], self.user,
                                            self.backup('before-existing-debt.sqlite3'))
        self.assertEqual(result['before_summary']['debt'], 1000)
        self.assertEqual(result['after_summary']['debt'], 1000)
        self.assertEqual(dict(self.con.execute('SELECT * FROM subscriptions WHERE id=?', (unpaid,)).fetchone()), original)
        self.assertEqual(len(self.payments()), 4)
        for old, new in zip(payments, self.payments()):
            self.assertEqual({key: value for key, value in new.items() if key != 'subscription_id'},
                             {key: value for key, value in old.items() if key != 'subscription_id'})

    def assert_prior_paid_october_month_does_not_generate_recovered_month_schedule(self, wrong_legacy_group=False):
        if wrong_legacy_group:
            enrollment = self.con.execute("""INSERT INTO enrollments(student_id,group_id,tariff,
                active,created_at,start_date) VALUES (?,?,'fixed',1,'2026-10-01','2026-10-01')""",
                (self.sid, self.wrong_gid)).lastrowid
            self.con.execute('UPDATE subscriptions SET enrollment_id=? WHERE id=?', (enrollment, self.aggregate_id))
        else:
            enrollment = self.aggregate()['enrollment_id']
            self.con.execute("UPDATE enrollments SET start_date='2026-10-01',created_at='2026-10-01' WHERE id=?", (enrollment,))
        october = self.con.execute("""INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,
            kind,lessons,per_lesson,price,status,created_at,paid_at,erp_full_managed)
            VALUES (?,?,'2026-10','fixed','month',1,1000,1000,'paid','2026-10-01',
            '2026-10-01T12:00',1)""", (self.sid, enrollment)).lastrowid
        separate_payment = self.con.execute("""INSERT INTO payments(student_id,amount,method,paid_at,
            comment,user_name,confirmed,subscription_id,created_at) VALUES (?,1000,'card',
            '2026-10-01T12:00','Синтетическая отдельная оплата октября','ERP',1,?,'2026-10-01T12:00')""",
            (self.sid, october)).lastrowid
        old_october = dict(self.con.execute('SELECT * FROM subscriptions WHERE id=?', (october,)).fetchone())
        old_payment = dict(self.con.execute('SELECT * FROM payments WHERE id=?', (separate_payment,)).fetchone())
        old_october_calendar = [(row['date'], row['kind'], row['group_id'])
                                for row in clientattendance.calendar(self.con, self.sid, '2026-10')['events']]
        preview = erpsinglerecovery.plan(self.con, self.data)
        self.assertTrue(preview['can_apply'], preview.get('issues'))
        result = erpsinglerecovery.apply(self.con, self.data, preview['token'], self.user,
                                        self.backup('before-paid-october.sqlite3'))
        self.assertEqual(result['before_summary']['sales_total'], 11000)
        self.assertEqual(result['after_summary']['sales_total'], 11000)
        self.assertEqual(result['before_summary']['payment_total'], 11000)
        self.assertEqual(result['after_summary']['payment_total'], 11000)
        self.assertEqual(result['after_summary']['sales_count'], 5)
        self.assertEqual(result['after_summary']['payment_count'], 5)
        self.assertEqual(dict(self.con.execute('SELECT * FROM subscriptions WHERE id=?', (october,)).fetchone()), old_october)
        self.assertEqual(dict(self.con.execute('SELECT * FROM payments WHERE id=?', (separate_payment,)).fetchone()), old_payment)
        self.assertEqual([(row['date'], row['kind'], row['group_id'])
                          for row in clientattendance.calendar(self.con, self.sid, '2026-10')['events']], old_october_calendar)
        november = clientattendance.calendar(self.con, self.sid, '2026-11')
        december = clientattendance.calendar(self.con, self.sid, '2026-12')
        self.assertEqual([(row['date'], row['kind'], row['group_id']) for row in november['events']],
                         [('2026-11-11', 'single', self.gid), ('2026-11-18', 'single', self.gid),
                          ('2026-11-25', 'single', self.gid)])
        self.assertEqual([(row['date'], row['kind'], row['group_id']) for row in december['events']],
                         [('2026-12-02', 'single', self.gid)])
        self.assertTrue(all(row['time'] == '18:45' for row in november['events']+december['events']))

    def test_prior_paid_october_subscription_cannot_create_phantom_recovered_month_dates(self):
        self.assert_prior_paid_october_month_does_not_generate_recovered_month_schedule()

    def test_prior_october_wrong_legacy_group_cannot_create_phantom_recovered_month_dates(self):
        self.assert_prior_paid_october_month_does_not_generate_recovered_month_schedule(wrong_legacy_group=True)

    def test_rollback_restores_original_aggregate_and_payment_links_without_deleting_payments(self):
        old_aggregate = self.aggregate()
        old_payments = self.payments()
        result = self.apply_recovery()
        erpsinglerecovery.rollback(self.con, result['run_id'], self.user, self.backup('before-rollback.sqlite3'))
        self.assertEqual(self.aggregate(), old_aggregate)
        self.assertEqual(self.payments(), old_payments)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0], 1)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM single_visits').fetchone()[0], 0)
        for table in ('erp_single_sales', 'erp_single_payments', 'erp_single_reschedules'):
            with self.subTest(table=table):
                self.assertEqual(self.con.execute('SELECT COUNT(*) FROM '+table).fetchone()[0], 0)
        self.assertEqual(self.con.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_rollback_refuses_to_overwrite_a_later_human_edit(self):
        result = self.apply_recovery()
        payment_id = next(iter(self.payment_mapping.values()))
        self.con.execute("UPDATE payments SET comment='Новая ручная заметка после восстановления' WHERE id=?", (payment_id,))
        payments_before = self.payments()
        visits_before = [tuple(row) for row in self.con.execute('SELECT * FROM single_visits ORDER BY id')]
        with self.assertRaises(erpimport.ImportError_):
            erpsinglerecovery.rollback(self.con, result['run_id'], self.user, self.backup('before-blocked-rollback.sqlite3'))
        self.assertEqual(self.payments(), payments_before)
        self.assertEqual([tuple(row) for row in self.con.execute('SELECT * FROM single_visits ORDER BY id')], visits_before)
        self.assertEqual(self.aggregate()['status'], 'cancelled')
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM subscriptions WHERE kind='single'").fetchone()[0], 4)

    def test_rollback_also_protects_a_human_booking_note(self):
        result = self.apply_recovery()
        self.con.execute("UPDATE single_visits SET note='Ручная заметка после восстановления' WHERE date='2026-12-02'")
        visits = [tuple(row) for row in self.con.execute('SELECT * FROM single_visits ORDER BY id')]
        with self.assertRaises(erpimport.ImportError_):
            erpsinglerecovery.rollback(self.con, result['run_id'], self.user, self.backup('before-note-rollback.sqlite3'))
        self.assertEqual([tuple(row) for row in self.con.execute('SELECT * FROM single_visits ORDER BY id')], visits)
        self.assertEqual(self.aggregate()['status'], 'cancelled')

    def test_new_payment_link_referencing_a_recovered_purchase_blocks_rollback(self):
        result = self.apply_recovery()
        single = self.con.execute("SELECT id FROM subscriptions WHERE kind='single' ORDER BY id LIMIT 1").fetchone()[0]
        link = self.con.execute("""INSERT INTO pay_links(student_id,amount,orderid,status,
            created_at,created_by,subscription_id) VALUES (?,2500,'synthetic-post-recovery-link',
            'open','2026-11-20T12:00','Синтетический руководитель',?)""", (self.sid, single)).lastrowid
        link_before = dict(self.con.execute('SELECT * FROM pay_links WHERE id=?', (link,)).fetchone())
        payments = self.payments()
        with self.assertRaises(erpimport.ImportError_):
            erpsinglerecovery.rollback(self.con, result['run_id'], self.user,
                                       self.backup('before-link-rollback.sqlite3'))
        self.assertEqual(dict(self.con.execute('SELECT * FROM pay_links WHERE id=?', (link,)).fetchone()), link_before)
        self.assertEqual(self.payments(), payments)
        self.assertIsNotNone(self.con.execute('SELECT id FROM subscriptions WHERE id=?', (single,)).fetchone())
        self.assertEqual(self.aggregate()['status'], 'cancelled')

    def test_recovery_does_not_reconcile_an_unrelated_childs_ready_payment_link(self):
        other_sid = services.create_student(self.con, {'name': 'Синтетический Другой Ребёнок',
            'parent_name': 'Синтетический Другой Родитель', 'phone': '+79000000552',
            'funnel': 'members'}, self.user)
        self.con.execute("UPDATE students SET erp_id='synthetic-other-child' WHERE id=?", (other_sid,))
        other_record = {'sale_id': 'synthetic-other-sale', 'operation_id': 'synthetic-other-operation',
            'tariff': 'Разовое занятие', 'lessons': '1', 'price': '2500', 'status': 'Оплачен',
            'month': '2026-11', 'lesson_date': '2026-11-18', 'sale_at': ''}
        other_sale = erpsingles.import_sale(self.con, other_record, other_sid, self.gid, '2026-11-01T12:00')
        self.assertFalse(other_sale['reason'])
        other_payment = self.con.execute("""INSERT INTO payments(student_id,amount,method,paid_at,
            comment,user_name,confirmed,created_at) VALUES (?,2500,'card','2026-10-28T12:00',
            'Синтетическая оплата другой карточки','ERP',1,'2026-10-28T12:00')""", (other_sid,)).lastrowid
        link = erpsingles.register_payment(self.con, other_payment, 'synthetic-other-operation', 'synthetic-other-sale')
        self.assertFalse(link['reason'])
        signature = json.dumps([other_sid, '2026-10-28', 2500, 'card'], ensure_ascii=False,
                               sort_keys=True, separators=(',', ':'))
        self.con.execute('INSERT INTO erp_full_operations(operation_id,payment_id,signature) VALUES (?,?,?)',
                         ('synthetic-other-operation', other_payment, signature))
        def other_snapshot():
            state = {'students': [dict(row) for row in self.con.execute('SELECT * FROM students WHERE id=?', (other_sid,))]}
            for table in ('subscriptions', 'payments', 'single_visits', 'history', 'tasks', 'erp_single_sales'):
                state[table] = [dict(row) for row in self.con.execute('SELECT * FROM '+table+' WHERE student_id=? ORDER BY 1', (other_sid,))]
            for table in ('erp_full_operations', 'erp_single_payments'):
                state[table] = [dict(row) for row in self.con.execute('SELECT * FROM '+table+' WHERE payment_id=? ORDER BY 1', (other_payment,))]
            return state
        before = other_snapshot()
        self.assertIsNone(self.con.execute('SELECT subscription_id FROM payments WHERE id=?', (other_payment,)).fetchone()[0])
        self.apply_recovery()
        self.assertEqual(other_snapshot(), before)
        self.assertIsNone(self.con.execute('SELECT subscription_id FROM payments WHERE id=?', (other_payment,)).fetchone()[0])


if __name__ == '__main__':
    unittest.main()
