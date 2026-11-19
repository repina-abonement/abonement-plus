"""Synthetic ERP single purchases retain identity, dates and payment relations."""
import os
import json
import tempfile
import unittest

from crm import db, erpfull, erpimport, erpsingles, money, services


class SinglePurchaseImportTest(unittest.TestCase):
    def setUp(self):
        os.environ['CRM_QUIET'] = '1'
        os.environ['CRM_ADMIN_PASSWORD'] = 'synthetic-test-password'
        self.tmp = tempfile.TemporaryDirectory()
        self.con = db.connect(os.path.join(self.tmp.name, 'test.sqlite3'))
        db.init_db(self.con)
        self.user = {'id': None, 'name': 'Test', 'role': 'admin'}
        self.sid = services.create_student(self.con, {'name': 'Тестовый Ребёнок', 'parent_name': 'Родитель', 'phone': '79000000001'}, self.user)
        self.con.execute("UPDATE students SET erp_id='101' WHERE id=?", (self.sid,))
        branch = self.con.execute("INSERT INTO branches(name) VALUES ('Тест')").lastrowid
        self.gid = self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time) VALUES (?,'Тестовая группа','2,6','10:00')", (branch,)).lastrowid

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def upload(self, body, kind, name=None):
        return erpimport.upload(self.con, name or kind+'.csv', body.encode(), kind)['id']

    def apply(self, ids):
        plan = erpfull.plan(self.con, ids)
        return erpfull.apply(self.con, ids, plan['token'], self.user, 'synthetic-backup')

    def sales(self, dates=True, ids=True, order=None):
        header = 'ID продажи;ID клиента;ФИО;Группа;Месяц;Тип;Дата занятия;Стоимость;Занятий;Статус;ID операции;Дата продажи\n'
        days = ['04.11.2026', '08.11.2026', '11.11.2026', '15.11.2026']
        rows = [f"{'sale-'+str(i+1) if ids else ''};101;Тестовый Ребёнок;Тестовая группа;11.2026;Разовое;{days[i] if dates else ''};2500;1;Оплачен;op-{i+1};01.11.2026" for i in range(4)]
        return header+'\n'.join(rows[i] for i in (order or range(4)))+'\n'

    def payments(self):
        return ('ID операции;ID продажи;ID клиента;ФИО;Дата оплаты;Сумма;Вид оплаты\n'+
                '\n'.join(f'op-{i};sale-{i};101;Тестовый Ребёнок;01.11.2026;2500;Наличные' for i in range(1, 5))+'\n')

    def test_four_dated_singles_and_identical_payments_replay(self):
        sale_file = self.upload(self.sales(), 'subs')
        payment_file = self.upload(self.payments(), 'payments')
        result = self.apply([sale_file, payment_file])
        self.assertTrue(result['complete'], result['issues'])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM subscriptions WHERE kind='single' AND tariff='single' AND lessons=1 AND price=2500").fetchone()[0], 4)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM subscriptions WHERE kind='month'").fetchone()[0], 0)
        self.assertEqual([r[0] for r in self.con.execute('SELECT date FROM single_visits ORDER BY date')], ['2026-11-04', '2026-11-08', '2026-11-11', '2026-11-15'])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments WHERE subscription_id IS NOT NULL').fetchone()[0], 4)
        identities = list(self.con.execute('SELECT source_id,subscription_id FROM erp_single_sales ORDER BY source_id'))
        payment_ids = list(self.con.execute('SELECT operation_id,payment_id FROM erp_full_operations ORDER BY operation_id'))
        self.con.execute("UPDATE single_visits SET note='Ручная заметка' WHERE date='2026-11-04'")
        self.con.execute("UPDATE payments SET comment='Ручная заметка об оплате' WHERE id=(SELECT MIN(id) FROM payments)")
        reordered = self.upload(self.sales(order=[3, 1, 0, 2]), 'subs', 'renamed-sales.csv')
        self.apply([payment_file, reordered])
        self.assertEqual(list(self.con.execute('SELECT source_id,subscription_id FROM erp_single_sales ORDER BY source_id')), identities)
        self.assertEqual(list(self.con.execute('SELECT operation_id,payment_id FROM erp_full_operations ORDER BY operation_id')), payment_ids)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM single_visits').fetchone()[0], 4)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0], 4)
        self.assertEqual(self.con.execute("SELECT note FROM single_visits WHERE date='2026-11-04'").fetchone()[0], 'Ручная заметка')
        self.assertEqual(self.con.execute('SELECT comment FROM payments ORDER BY id LIMIT 1').fetchone()[0], 'Ручная заметка об оплате')
        money.init(self.con)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0], 4)
        self.con.close()
        self.con = db.connect(os.path.join(self.tmp.name, 'test.sqlite3'))
        db.init_db(self.con)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0], 4)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0], 4)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM payments WHERE comment='Оплата до реестра платежей'").fetchone()[0], 0)
        self.assertEqual(list(self.con.execute('SELECT operation_id,payment_id FROM erp_full_operations ORDER BY operation_id')), payment_ids)
        payload = json.loads(self.con.execute("SELECT source_payload FROM erp_single_sales WHERE source_id='sale-1'").fetchone()[0])
        self.assertEqual((payload['tariff'], payload['status']), ('Разовое', 'Оплачен'))
        self.assertEqual(payload['_source_row']['headers'][0], 'ID продажи')

    def test_missing_lesson_dates_do_not_generate_schedule_or_payment_dates(self):
        sale_file = self.upload(self.sales(dates=False), 'subs')
        payment_file = self.upload(self.payments(), 'payments')
        result = self.apply([sale_file, payment_file])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM subscriptions WHERE kind='single'").fetchone()[0], 4)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM single_visits').fetchone()[0], 0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM erp_single_reviews').fetchone()[0], 4)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments WHERE subscription_id IS NOT NULL').fetchone()[0], 4)
        self.assertFalse(result['complete'])
        self.assertTrue(all('дата занятия' in x['reason'] for x in result['issues']))
        self.apply([sale_file, payment_file])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM erp_single_reviews').fetchone()[0], 4)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0], 4)
        money.init(self.con)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0], 4)

    def test_missing_sale_id_retains_each_row_for_review_without_monthly_bill(self):
        source = self.upload(self.sales(dates=False, ids=False), 'subs')
        result = self.apply([source])
        self.assertEqual(result['issue_count'], 4)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0], 0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM erp_single_reviews').fetchone()[0], 4)
        self.apply([source])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM erp_single_reviews').fetchone()[0], 4)

    def test_preexisting_month_aggregate_is_not_rewritten_or_added_again(self):
        enrollment = self.con.execute("INSERT INTO enrollments(student_id,group_id,tariff,created_at) VALUES (?,?,'fixed','2026-11-01')", (self.sid, self.gid)).lastrowid
        old = self.con.execute("INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at) VALUES (?,?,'2026-11','fixed',4,2500,10000,'paid','2026-11-01')", (self.sid, enrollment)).lastrowid
        before = dict(self.con.execute('SELECT * FROM subscriptions WHERE id=?', (old,)).fetchone())
        result = self.apply([self.upload(self.sales(), 'subs')])
        self.assertEqual(result['issue_count'], 4)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0], 1)
        self.assertEqual(dict(self.con.execute('SELECT * FROM subscriptions WHERE id=?', (old,)).fetchone()), before)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM single_visits').fetchone()[0], 0)

    def test_human_changes_and_deleted_bookings_are_preserved(self):
        source = self.upload(self.sales(), 'subs')
        self.apply([source])
        self.con.execute("UPDATE subscriptions SET price=2600 WHERE id=(SELECT subscription_id FROM erp_single_sales WHERE source_id='sale-1')")
        self.con.execute("UPDATE single_visits SET status='cancelled' WHERE date='2026-11-08'")
        self.con.execute("DELETE FROM single_visits WHERE date='2026-11-11'")
        result = self.apply([source])
        self.assertGreaterEqual(result['issue_count'], 3)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0], 4)
        self.assertEqual(self.con.execute("SELECT price FROM subscriptions WHERE id=(SELECT subscription_id FROM erp_single_sales WHERE source_id='sale-1')").fetchone()[0], 2600)
        self.assertEqual(self.con.execute("SELECT status FROM single_visits WHERE date='2026-11-08'").fetchone()[0], 'cancelled')
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM single_visits WHERE date='2026-11-11'").fetchone()[0], 0)

    def test_existing_manual_undated_single_prevents_duplicate_purchase(self):
        self.con.execute("INSERT INTO subscriptions(student_id,month,tariff,kind,lessons,per_lesson,price,status,created_at) VALUES (?,'2026-11','single','single',1,2500,2500,'invoiced','2026-11-01')", (self.sid,))
        result = self.apply([self.upload(self.sales(), 'subs')])
        self.assertEqual(result['issue_count'], 4)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0], 1)

    def test_explicit_partial_operations_are_not_linked_when_total_exceeds_purchase(self):
        record = {'sale_id': 'one-sale', 'lesson_date': '2026-11-04', 'month': '2026-11', 'price': '2500', 'lessons': '1', 'status': 'Оплачен'}
        sale = erpsingles.import_sale(self.con, record, self.sid, self.gid, '2026-11-01')
        for op in ('part-1', 'part-2'):
            pid = self.con.execute("INSERT INTO payments(student_id,amount,method,paid_at,created_at) VALUES (?,1500,'cash','2026-11-01','2026-11-01')", (self.sid,)).lastrowid
            erpsingles.register_payment(self.con, pid, op, 'one-sale')
        result = erpsingles.reconcile_links(self.con)
        self.assertTrue(result['issues'])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments WHERE subscription_id=?', (sale['target_id'],)).fetchone()[0], 0)

    def test_old_equal_payments_without_operation_identity_are_not_adopted(self):
        for _ in range(4):
            self.con.execute("INSERT INTO payments(student_id,amount,method,paid_at,user_name,created_at) VALUES (?,2500,'cash','2026-11-01T12:00','ERP','2026-11-01')", (self.sid,))
        # Payment file arrives first; the later single-sale file must still
        # prevent a weak amount/day adoption of an unrelated legacy payment.
        payments = self.upload(self.payments(), 'payments')
        sales = self.upload(self.sales(), 'subs')
        result = self.apply([payments, sales])
        self.assertFalse(result['complete'])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0], 4)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM erp_full_operations').fetchone()[0], 0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments WHERE subscription_id IS NOT NULL').fetchone()[0], 0)

    def test_month_sale_id_keeps_existing_legacy_payment_reconciliation(self):
        previous = self.con.execute("INSERT INTO payments(student_id,amount,method,paid_at,user_name,created_at) VALUES (?,10000,'cash','2026-11-01T12:00','ERP','2026-11-01')", (self.sid,)).lastrowid
        payment = self.upload('ID операции;ID продажи;ID клиента;ФИО;Дата оплаты;Сумма;Вид оплаты\nmonth-op;month-sale;101;Тестовый Ребёнок;01.11.2026;10000;Наличные\n', 'payments')
        sale = self.upload('ID продажи;ID клиента;ФИО;Группа;Месяц;Тип;Стоимость;Занятий;Статус;ID операции\nmonth-sale;101;Тестовый Ребёнок;Тестовая группа;11.2026;Абонемент;10000;4;Оплачен;month-op\n', 'subs')
        result = self.apply([payment, sale])
        self.assertTrue(result['complete'], result['issues'])
        self.assertEqual(result['counts']['matched_payments'], 1)
        self.assertEqual(self.con.execute('SELECT payment_id FROM erp_full_operations WHERE operation_id=?', ('month-op',)).fetchone()[0], previous)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0], 1)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM erp_single_sales').fetchone()[0], 0)

    def test_native_edit_invalidates_dry_run_token_even_when_counts_stay_equal(self):
        source = self.upload(self.sales(), 'subs')
        self.apply([source])
        plan = erpfull.plan(self.con, [source])
        self.con.execute("UPDATE single_visits SET note='Новая ручная заметка' WHERE date='2026-11-04'")
        with self.assertRaises(erpimport.ImportError_):
            erpfull.apply(self.con, [source], plan['token'], self.user, 'synthetic-backup')
        self.assertEqual(self.con.execute("SELECT note FROM single_visits WHERE date='2026-11-04'").fetchone()[0], 'Новая ручная заметка')

    def test_later_explicit_dates_supplement_undated_sales_without_new_purchases(self):
        unknown = self.upload(self.sales(dates=False), 'subs')
        self.apply([unknown])
        identities = list(self.con.execute('SELECT source_id,subscription_id FROM erp_single_sales ORDER BY source_id'))
        self.apply([self.upload(self.sales(), 'subs', 'dated-sales.csv')])
        self.assertEqual(list(self.con.execute('SELECT source_id,subscription_id FROM erp_single_sales ORDER BY source_id')), identities)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM single_visits').fetchone()[0], 4)
        payload = json.loads(self.con.execute("SELECT source_payload FROM erp_single_sales WHERE source_id='sale-1'").fetchone()[0])
        self.assertEqual(payload['lesson_date'], '')
        self.assertEqual(payload['_source_updates'][0]['lesson_date'], '04.11.2026')

    def test_existing_single_sale_id_cannot_be_reimported_as_monthly_charge(self):
        self.apply([self.upload(self.sales(), 'subs'), self.upload(self.payments(), 'payments')])
        before_subs = list(self.con.execute('SELECT * FROM subscriptions ORDER BY id'))
        before_visits = list(self.con.execute('SELECT * FROM single_visits ORDER BY id'))
        before_payments = list(self.con.execute('SELECT * FROM payments ORDER BY id'))
        source = self.sales().splitlines()
        changed = source[0]+'\n'+source[1].replace(';Разовое;', ';Абонемент;')+'\n'
        result = self.apply([self.upload(changed, 'subs', 'type-changed.csv')])
        self.assertFalse(result['complete'])
        self.assertTrue(any('Тип продажи ERP изменён' in issue['reason'] for issue in result['issues']))
        self.assertEqual(list(self.con.execute('SELECT * FROM subscriptions ORDER BY id')), before_subs)
        self.assertEqual(list(self.con.execute('SELECT * FROM single_visits ORDER BY id')), before_visits)
        self.assertEqual(list(self.con.execute('SELECT * FROM payments ORDER BY id')), before_payments)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM subscriptions WHERE kind='month'").fetchone()[0], 0)

    def test_month_row_before_same_id_single_row_cannot_create_second_charge(self):
        lines = self.sales().splitlines()
        single = lines[1].replace(';Оплачен;', ';Не оплачен;')
        monthly = single.replace(';Разовое;', ';Абонемент;')
        # A whole-file scan must spot the conflicting type before this earlier
        # monthly row can reach deferred monthly aggregation.
        source = self.upload(lines[0]+'\n'+monthly+'\n'+single+'\n', 'subs')
        result = self.apply([source])
        self.assertFalse(result['complete'])
        self.assertTrue(any('Тип продажи ERP изменён' in issue['reason'] for issue in result['issues']))
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM subscriptions WHERE kind='single'").fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM subscriptions WHERE kind='month'").fetchone()[0], 0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM single_visits').fetchone()[0], 1)
        self.apply([source])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
