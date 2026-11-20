"""Historical refund tracing reads exact local source IDs without side effects.

Only invoice IDs supplied for the case are reused. Names, client IDs,
operations, payment records and local archives are synthetic temporary data.
No production database, provider, bank or messaging endpoint is opened.
"""
from contextlib import ExitStack, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from crm import comms, db, fiscal, historicalrefundtrace, services


class HistoricalRefundTraceTest(unittest.TestCase):
    ERP_ID = 'synthetic-refund-child-1'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='historical-refund-trace-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'synthetic.sqlite3'
        self.con = db.connect(str(self.path))
        self.addCleanup(lambda: self.con.close())
        with redirect_stdout(io.StringIO()):
            db.init_db(self.con)
        self.user = {'id': 1, 'name': 'Синтетический руководитель', 'role': 'admin'}
        self.sid = services.create_student(self.con, {'name': 'Синтетический Ребёнок',
            'parent_name': 'Синтетический Родитель', 'phone': '+79000000771'}, self.user)
        self.con.execute('UPDATE students SET erp_id=? WHERE id=?', (self.ERP_ID, self.sid))
        self.other_erp = 'synthetic-private-other-child'
        self.other_name = 'Синтетическая Чужая Карточка'
        self.other_sid = services.create_student(self.con, {'name': self.other_name,
            'parent_name': 'Синтетический Другой Родитель', 'phone': '+79000000772'}, self.user)
        self.con.execute('UPDATE students SET erp_id=? WHERE id=?', (self.other_erp, self.other_sid))
        self.paid_sub = self.subscription(self.sid, 'paid')
        self.invoiced_sub = self.subscription(self.sid, 'invoiced')
        self.cancelled_sub = self.subscription(self.sid, 'cancelled')
        self.paid_payment = self.payment(self.sid, 2500, 'card', self.paid_sub, 'Синтетическая оплата')
        self.refund_payment = self.payment(self.sid, -1000, 'refund', self.cancelled_sub, 'Синтетическая запись возврата')
        self.cancelled_payment = self.payment(self.sid, 500, 'card', None, 'Синтетическая отменённая запись', cancelled=1)
        self.other_sub = self.subscription(self.other_sid, 'paid')
        self.other_payment = self.payment(self.other_sid, 2500, 'card', self.other_sub, 'Чужие данные не выдавать')
        self.con.execute('''INSERT INTO cancelled_service_charges(subscription_id,student_id,month,
            amount,lessons,created_at,cutoff) VALUES (?,?,'2026-10',1000,1,'2026-11-01','2026-11-01')''',
            (self.cancelled_sub, self.sid))
        self.file_counter = 0

    def subscription(self, sid, status):
        return self.con.execute('''INSERT INTO subscriptions(student_id,month,tariff,kind,lessons,
            per_lesson,price,status,created_at,erp_full_managed) VALUES (?,'2026-10','fixed','month',
            4,2500,10000,?,'2026-10-01',1)''', (sid, status)).lastrowid

    def payment(self, sid, amount, method, subscription_id, comment, cancelled=0):
        return self.con.execute('''INSERT INTO payments(student_id,amount,method,paid_at,comment,
            user_name,confirmed,subscription_id,cancelled,created_at) VALUES (?,?,?,
            '2026-10-28T12:00',?,'ERP',1,?,?,'2026-10-28T12:00')''',
            (sid, amount, method, comment, subscription_id, cancelled)).lastrowid

    def archive(self, kind, headers, rows, targets=None, owner='self', state='native', mapping=None):
        self.file_counter += 1
        file_id = self.con.execute('''INSERT INTO erp_full_files(digest,filename,kind,headers,rows,
            mapping,imported_at) VALUES (?,?,?,?,?,?,?)''',
            (hashlib.sha256(str(self.file_counter).encode()).hexdigest(),
             'synthetic-source-'+str(self.file_counter)+'.csv', kind,
             json.dumps(headers, ensure_ascii=False), json.dumps(rows, ensure_ascii=False),
             json.dumps(mapping or {}), '2026-11-20T12:00')).lastrowid
        sid = self.sid if owner == 'self' else owner
        targets = targets or [('', None)]*len(rows)
        for row_num, (table, target) in enumerate(targets, 2):
            self.con.execute('''INSERT INTO erp_full_rows(file_id,row_num,student_id,state,reason,
                target_table,target_id) VALUES (?,?,?,?,?,?,?)''',
                (file_id, row_num, sid, state, 'Синтетическая исходная строка', table, target))
        return file_id

    def operation(self, operation_id, payment_id, sid=None, amount=2500, method='card'):
        signature = json.dumps([sid or self.sid, '2026-10-28', amount, method],
                               ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        self.con.execute('INSERT INTO erp_full_operations(operation_id,payment_id,signature) VALUES (?,?,?)',
                         (operation_id, payment_id, signature))

    def known_sources(self):
        sales = self.archive('subs', ['ID посетителя', ' ID счёта ', 'ID продажи', 'Тип', 'Стоимость'],
            [[self.ERP_ID, '716063', 'synthetic-sale-paid', 'Абонемент', '10000'],
             [self.ERP_ID, '726247', 'synthetic-sale-cancelled', 'Абонемент', '10000']],
            [('subscriptions', self.paid_sub), ('subscriptions', self.cancelled_sub)])
        payments = self.archive('payments', ['ID клиента', 'ID счета', 'ID оплаты', 'Дата оплаты', 'Сумма'],
            [[self.ERP_ID, '716063', 'synthetic-paid-operation', '2026-10-28', '2500'],
             [self.ERP_ID, '726247', 'synthetic-refund-operation', '2026-10-28', '-1000']],
            [('payments', self.paid_payment), ('payments', self.refund_payment)])
        self.operation('synthetic-paid-operation', self.paid_payment)
        self.operation('synthetic-refund-operation', self.refund_payment, amount=-1000, method='refund')
        return sales, payments

    def assert_evidence_value(self, result, value):
        evidence = [item for candidate in result['candidates'] for item in candidate.get('evidence', [])]
        self.assertTrue(any(str(item.get('value')) == value and item.get('field') for item in evidence), evidence)

    def test_empty_erp_id_never_selects_a_card_with_an_empty_source_id(self):
        self.con.execute("UPDATE students SET erp_id='' WHERE id=?", (self.other_sid,))
        before = self.con.serialize()
        for missing in ('', '   ', None):
            with self.subTest(missing=missing):
                result = historicalrefundtrace.trace(self.con, missing)
                self.assertIsNone(result['student'])
                self.assertEqual(result['payments'], [])
                self.assertEqual(result['purchases'], [])
                self.assertTrue(result['issues'])
                self.assertNotIn(self.other_name, json.dumps(result, ensure_ascii=False))
        self.assertEqual(self.con.serialize(), before)

    def test_local_receipt_candidates_use_exact_source_fiscal_ids_not_equal_total_and_date(self):
        self.archive('payments', ['ID клиента', 'ID операции', 'ID счета', 'Тип платежа', 'Сумма',
            'Статус возврата', 'Дата фактического возврата', 'Способ возврата', 'ФН', 'ФД', 'ФПД'],
            [[self.ERP_ID, 'synthetic-fiscal-refund', '726247', 'Возврат', '-1000', 'Исполнен',
              '2026-11-12 15:30', 'Карта', '1111111111111111', '11', '1111']], owner=None, state='unmatched')
        exact_id = self.con.execute("""INSERT INTO fx_receipts(source,ext_id,kkt,ts,amount,ecash,op,fiscal_fn,fiscal_fd,fiscal_fp)
            VALUES ('ofd','synthetic-exact-receipt','synthetic-kkt','2026-11-12T15:31',100000,100000,2,'1111111111111111','11','1111')""").lastrowid
        self.con.execute("""INSERT INTO fx_receipts(source,ext_id,kkt,ts,amount,ecash,op,fiscal_fn,fiscal_fd,fiscal_fp)
            VALUES ('ofd','synthetic-unrelated-receipt','synthetic-kkt','2026-11-12T15:31',100000,100000,2,'2222222222222222','22','2222')""")
        before = self.con.serialize()
        result = historicalrefundtrace.trace(self.con, self.ERP_ID)
        self.assertEqual([receipt['id'] for receipt in result['receipt_candidates']], [exact_id])
        receipt = result['receipt_candidates'][0]
        self.assertEqual((receipt['fn'], receipt['fd'], receipt['fp']), ('1111111111111111', '11', '1111'))
        self.assertEqual((receipt['total_minor'], receipt['cashless_minor'], receipt['operation']), (100000, 100000, 2))
        self.assertNotIn('synthetic-unrelated-receipt', json.dumps(result, ensure_ascii=False))
        self.assertEqual(self.con.serialize(), before)

    def test_exact_invoice_and_operation_headers_prove_native_links(self):
        self.known_sources()
        invoice = historicalrefundtrace.resolve_invoice(self.con, self.sid, '716063')
        self.assertFalse(invoice['ambiguous'], invoice)
        self.assertEqual(invoice['subscription_ids'], [self.paid_sub])
        self.assertEqual(invoice['payment_ids'], [self.paid_payment])
        self.assert_evidence_value(invoice, '716063')
        operation = historicalrefundtrace.resolve_payment_operation(self.con, self.sid, 'synthetic-paid-operation')
        self.assertFalse(operation['ambiguous'], operation)
        self.assertEqual(operation['payment_ids'], [self.paid_payment])
        self.assertEqual(operation['subscription_ids'], [self.paid_sub])
        self.assert_evidence_value(operation, 'synthetic-paid-operation')

    def test_supported_exact_invoice_sale_and_operation_aliases(self):
        self.archive('subs', ['ID клиента', 'ID заказа', 'ID покупки'],
            [[self.ERP_ID, '716063', 'synthetic-alias-sale']], [('subscriptions', self.paid_sub)])
        self.archive('payments', ['ID посетителя', 'ID заказа', 'ID операции'],
            [[self.ERP_ID, '716063', 'synthetic-alias-operation']], [('payments', self.paid_payment)])
        self.operation('synthetic-alias-operation', self.paid_payment)
        invoice = historicalrefundtrace.resolve_invoice(self.con, self.sid, '716063')
        self.assertEqual(invoice['subscription_ids'], [self.paid_sub])
        self.assertEqual(invoice['payment_ids'], [self.paid_payment])
        self.assertFalse(invoice['ambiguous'])
        operation = historicalrefundtrace.resolve_payment_operation(self.con, self.sid, 'synthetic-alias-operation')
        self.assertEqual(operation['payment_ids'], [self.paid_payment])

    def test_comment_amount_date_and_paykeeper_invoice_id_are_not_erp_invoice_proof(self):
        self.con.execute("UPDATE payments SET comment='Счёт716063, дата28.10.2026' WHERE id=?", (self.paid_payment,))
        self.archive('payments', ['ID клиента', 'Комментарий', 'Сумма', 'Дата оплаты'],
            [[self.ERP_ID, '716063', '716063', '2026-10-28']], [('payments', self.paid_payment)],
            mapping={'invoice_id': 1})
        self.con.execute('''INSERT INTO pay_links(student_id,amount,orderid,invoice_id,status,
            created_at,subscription_id,payment_id) VALUES (?,2500,'synthetic-paykeeper-order',
            '716063','paid','2026-10-28',?,?)''', (self.sid, self.paid_sub, self.paid_payment))
        result = historicalrefundtrace.resolve_invoice(self.con, self.sid, '716063')
        self.assertEqual(result['subscription_ids'], [])
        self.assertEqual(result['payment_ids'], [])
        self.assertEqual(result['candidates'], [])

    def test_hyperlink_label_without_explicit_invoice_id_is_not_invented(self):
        self.archive('subs', ['ID клиента', 'Счёт', 'Комментарий'],
            [[self.ERP_ID, 'Счёт', 'Ссылка без исходного ID']], [('subscriptions', self.paid_sub)])
        result = historicalrefundtrace.resolve_invoice(self.con, self.sid, '716063')
        self.assertEqual(result['subscription_ids'], [])
        self.assertEqual(result['payment_ids'], [])
        self.assertEqual(result['candidates'], [])

    def test_same_invoice_attached_to_different_native_purchases_is_ambiguous(self):
        self.archive('subs', ['ID клиента', 'ID счета', 'ID продажи'],
            [[self.ERP_ID, '716063', 'synthetic-sale-a'], [self.ERP_ID, '716063', 'synthetic-sale-b']],
            [('subscriptions', self.paid_sub), ('subscriptions', self.invoiced_sub)])
        result = historicalrefundtrace.resolve_invoice(self.con, self.sid, '716063')
        self.assertTrue(result['ambiguous'])
        self.assertEqual(set(result['subscription_ids']), {self.paid_sub, self.invoiced_sub})

    def test_multiple_sales_under_one_aggregate_are_explicitly_ambiguous(self):
        self.archive('subs', ['ID клиента', 'ID счета', 'ID продажи'],
            [[self.ERP_ID, '716063', 'synthetic-part-a'], [self.ERP_ID, '716063', 'synthetic-part-b']],
            [('subscriptions', self.paid_sub), ('subscriptions', self.paid_sub)])
        result = historicalrefundtrace.resolve_invoice(self.con, self.sid, '716063')
        self.assertEqual(result['subscription_ids'], [self.paid_sub])
        self.assertTrue(result['ambiguous'], 'Two source sales must not become an exact one-sale invoice match')

    def test_two_invoice_ids_sharing_a_native_aggregate_are_both_ambiguous(self):
        self.archive('subs', ['ID клиента', 'ID счета', 'ID продажи'],
            [[self.ERP_ID, '716063', 'synthetic-first-invoice-sale'],
             [self.ERP_ID, '726247', 'synthetic-second-invoice-sale']],
            [('subscriptions', self.paid_sub), ('subscriptions', self.paid_sub)])
        for invoice_id in ('716063', '726247'):
            with self.subTest(invoice_id=invoice_id):
                result = historicalrefundtrace.resolve_invoice(self.con, self.sid, invoice_id)
                self.assertEqual(result['subscription_ids'], [self.paid_sub])
                self.assertTrue(result['ambiguous'], 'An aggregate shared by two invoice IDs is not an exact invoice allocation')

    def test_one_invoice_keeps_partial_payments_and_later_refund_as_distinct_operations(self):
        second_payment = self.payment(self.sid, 7500, 'card', self.paid_sub, 'Синтетическая вторая часть оплаты')
        later_refund = self.payment(self.sid, -1000, 'refund', self.paid_sub, 'Синтетическая последующая запись возврата')
        self.con.execute("UPDATE payments SET paid_at='2026-10-29T12:00' WHERE id=?", (second_payment,))
        self.con.execute("UPDATE payments SET paid_at='2026-11-03T12:00' WHERE id=?", (later_refund,))
        operations = [
            ('synthetic-partial-operation-a', self.paid_payment, 2500, '2026-10-28', 'card'),
            ('synthetic-partial-operation-b', second_payment, 7500, '2026-10-29', 'card'),
            ('synthetic-later-refund-operation', later_refund, -1000, '2026-11-03', 'refund'),
        ]
        self.archive('subs', ['ID клиента', 'ID счета', 'ID продажи'],
            [[self.ERP_ID, '716063', 'synthetic-one-invoice-sale']], [('subscriptions', self.paid_sub)])
        self.archive('payments', ['ID клиента', 'ID счета', 'ID операции', 'Дата оплаты',
            'Сумма', 'Способ оплаты', 'Тип платежа'],
            [[self.ERP_ID, '716063', operation_id, day, str(amount), 'Карта',
              'Возврат' if amount < 0 else 'Оплата'] for operation_id, _, amount, day, _ in operations],
            [('payments', payment_id) for _, payment_id, _, _, _ in operations])
        for operation_id, payment_id, amount, day, method in operations:
            signature = json.dumps([self.sid, day, amount, method], ensure_ascii=False,
                                   sort_keys=True, separators=(',', ':'))
            self.con.execute('INSERT INTO erp_full_operations(operation_id,payment_id,signature) VALUES (?,?,?)',
                             (operation_id, payment_id, signature))
        invoice = historicalrefundtrace.resolve_invoice(self.con, self.sid, '716063')
        self.assertFalse(invoice['ambiguous'], invoice)
        self.assertEqual(invoice['subscription_ids'], [self.paid_sub])
        self.assertEqual(set(invoice['payment_ids']), {payment_id for _, payment_id, _, _, _ in operations})
        for operation_id, payment_id, amount, _, _ in operations:
            with self.subTest(operation_id=operation_id):
                result = historicalrefundtrace.resolve_payment_operation(self.con, self.sid, operation_id)
                self.assertFalse(result['ambiguous'], result)
                self.assertEqual(result['payment_ids'], [payment_id])
                self.assertEqual(result['subscription_ids'], [self.paid_sub])
                raw = [candidate for candidate in result['candidates'] if candidate.get('store') == 'erp_full_files']
                self.assertEqual(len(raw), 1)
                self.assertEqual(raw[0]['operation_id'], operation_id)
                self.assertEqual(raw[0]['amount'], amount)
                self.assertEqual(raw[0]['source_invoice_id'], '716063')

    def test_positive_operation_source_fields_stay_on_the_same_raw_evidence_row(self):
        self.archive('subs', ['ID клиента', 'ID счета', 'ID продажи'],
            [[self.ERP_ID, '716063', 'synthetic-positive-proof-sale']], [('subscriptions', self.paid_sub)])
        file_id = self.archive('payments', ['ID клиента', 'ID счета', 'ID операции',
            'Дата оплаты', 'Сумма', 'Способ оплаты'],
            [[self.ERP_ID, '716063', 'synthetic-positive-raw-operation', '2026-10-28 13:45', '2500', 'Карта'],
             [self.ERP_ID, '726247', 'synthetic-unrelated-raw-operation', '2026-12-31 23:59', '7777', 'Наличные']],
            owner=None, state='unmatched')
        self.operation('synthetic-positive-raw-operation', self.paid_payment)
        result = historicalrefundtrace.resolve_payment_operation(self.con, self.sid, 'synthetic-positive-raw-operation')
        raw = [candidate for candidate in result['candidates']
               if candidate.get('store') == 'erp_full_files' and candidate.get('file_id') == file_id]
        self.assertEqual(len(raw), 1)
        candidate = raw[0]
        self.assertEqual(candidate['row_num'], 2)
        self.assertEqual(candidate['source_invoice_id'], '716063')
        self.assertEqual(candidate['amount'], 2500)
        self.assertEqual(candidate['paid_at'], '2026-10-28T13:45')
        self.assertEqual(candidate['source_method'], 'Карта')
        self.assertTrue(candidate['evidence_id'])
        self.assertTrue(candidate['source_signature'])
        evidence_values = {str(item['value']) for item in candidate['evidence']}
        self.assertTrue({'716063', 'synthetic-positive-raw-operation', '2026-10-28 13:45', '2500', 'Карта'} <= evidence_values)
        self.assertFalse({'726247', 'synthetic-unrelated-raw-operation', '2026-12-31 23:59', '7777', 'Наличные'} & evidence_values)
        self.assertEqual(self.con.execute('SELECT paid_at FROM payments WHERE id=?', (self.paid_payment,)).fetchone()[0], '2026-10-28T12:00')

    def test_repeated_logical_row_keeps_provenance_without_false_ambiguity(self):
        files = []
        for note in ('Первый исходный файл', 'Повторный исходный файл'):
            files.append(self.archive('subs', ['ID клиента', 'ID счета', 'ID продажи', 'Примечание'],
                [[self.ERP_ID, '716063', 'synthetic-repeat-sale', note]], [('subscriptions', self.paid_sub)]))
        result = historicalrefundtrace.resolve_invoice(self.con, self.sid, '716063')
        self.assertFalse(result['ambiguous'], result)
        self.assertEqual(result['subscription_ids'], [self.paid_sub])
        trace = historicalrefundtrace.trace(self.con, self.ERP_ID)
        self.assertEqual({row['file_id'] for row in trace['source_rows']}, set(files))

    def test_unmatched_archive_row_can_be_attributed_by_exact_client_id_without_native_guess(self):
        source = self.archive('subs', ['ID клиента', 'ID счета', 'ID продажи'],
            [[self.ERP_ID, '716063', 'synthetic-unmatched-sale']], owner=None, state='unmatched')
        result = historicalrefundtrace.resolve_invoice(self.con, self.sid, '716063')
        self.assertEqual(result['subscription_ids'], [])
        self.assertEqual(result['payment_ids'], [])
        self.assertTrue(result['candidates'])
        self.assertTrue(all(candidate.get('subscription_id') is None and candidate.get('payment_id') is None for candidate in result['candidates']))
        trace = historicalrefundtrace.trace(self.con, self.ERP_ID)
        self.assertIn(source, {row['file_id'] for row in trace['source_rows']})

    def test_other_child_operation_mapping_and_source_rows_never_leak_native_data(self):
        self.operation('synthetic-foreign-operation', self.other_payment, sid=self.other_sid)
        self.archive('payments', ['ID клиента', 'ID счета', 'ID операции'],
            [[self.ERP_ID, '716063', 'synthetic-foreign-operation']], owner=None, state='unmatched')
        self.archive('payments', ['ID клиента', 'ID счета', 'ID операции', 'ФИО'],
            [[self.other_erp, '716063', 'synthetic-foreign-operation', self.other_name]],
            [('payments', self.other_payment)], owner=self.other_sid)
        operation = historicalrefundtrace.resolve_payment_operation(self.con, self.sid, 'synthetic-foreign-operation')
        self.assertNotIn(self.other_payment, operation['payment_ids'])
        self.assertNotIn(self.other_sub, operation['subscription_ids'])
        self.assertTrue(all(candidate.get('payment_id') != self.other_payment and candidate.get('subscription_id') != self.other_sub for candidate in operation['candidates']))
        trace = historicalrefundtrace.trace(self.con, self.ERP_ID)
        self.assertNotIn(self.other_payment, {row['id'] for row in trace['payments']})
        output = json.dumps({'trace': trace, 'operation': operation}, ensure_ascii=False)
        self.assertNotIn(self.other_name, output)
        self.assertNotIn(self.other_erp, output)

    def test_trace_keeps_original_positive_negative_cancelled_rows_and_retained_cost(self):
        self.known_sources()
        native_payments = {row['id']: dict(row) for row in self.con.execute('SELECT * FROM payments WHERE student_id=?', (self.sid,))}
        native_purchases = {row['id']: dict(row) for row in self.con.execute('SELECT * FROM subscriptions WHERE student_id=?', (self.sid,))}
        trace = historicalrefundtrace.trace(self.con, self.ERP_ID)
        self.assertEqual(trace['student']['id'], self.sid)
        self.assertEqual(trace['student']['erp_id'], self.ERP_ID)
        self.assertTrue(trace['read_only'])
        self.assertEqual(trace['provenance'], 'current_connection')
        self.assertEqual({row['id'] for row in trace['payments']}, set(native_payments))
        self.assertEqual({row['id'] for row in trace['purchases']}, set(native_purchases))
        for row in trace['payments']:
            for key, value in native_payments[row['id']].items():
                self.assertEqual(row[key], value)
            self.assertIsInstance(row['source_operations'], list)
            self.assertIsInstance(row['source_invoice_candidates'], list)
        for row in trace['purchases']:
            for key, value in native_purchases[row['id']].items():
                self.assertEqual(row[key], value)
            self.assertIsInstance(row['source_invoice_candidates'], list)
        self.assertEqual(next(row for row in trace['payments'] if row['id'] == self.refund_payment)['amount'], -1000)
        retained = self.con.execute('SELECT * FROM cancelled_service_charges WHERE subscription_id=?', (self.cancelled_sub,)).fetchone()
        self.assertEqual(len(trace['retained_charges']), 1)
        for key, value in dict(retained).items():
            self.assertEqual(trace['retained_charges'][0][key], value)
        self.assertIn('716063', json.dumps(next(row for row in trace['purchases'] if row['id'] == self.paid_sub)['source_invoice_candidates']))

    def test_repeated_trace_and_resolvers_are_readonly_and_do_not_call_providers(self):
        self.known_sources()
        before, changes = self.con.serialize(), self.con.total_changes
        with ExitStack() as stack:
            for name in ('pk_call', 'ofd_get', 'tochka_get'):
                stack.enter_context(patch.object(fiscal, name, side_effect=AssertionError('No provider requests from local trace')))
            stack.enter_context(patch.object(comms, '_http', side_effect=AssertionError('No messages from local trace')))
            stack.enter_context(patch('urllib.request.urlopen', side_effect=AssertionError('No network from local trace')))
            for _ in range(2):
                historicalrefundtrace.trace(self.con, self.ERP_ID)
                historicalrefundtrace.resolve_invoice(self.con, self.sid, '716063')
                historicalrefundtrace.resolve_payment_operation(self.con, self.sid, 'synthetic-paid-operation')
            self.assertEqual(self.con.total_changes, changes)
            self.assertEqual(self.con.serialize(), before)
            self.con.close()
            self.con = sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True)
            self.con.row_factory = sqlite3.Row
            trace = historicalrefundtrace.trace(self.con, self.ERP_ID)
            self.assertTrue(trace['read_only'])
            self.assertEqual(self.con.total_changes, 0)

    def refund_source(self, operation_id, *, executed=True, status='Исполнен', amount='-1000', receipt_only=False):
        headers = ['ID клиента', 'ID операции', 'ID счета', 'Тип платежа', 'Сумма', 'Статус возврата', 'Способ возврата']
        row = [self.ERP_ID, operation_id, '726247', 'Возврат', amount, status, 'Карта']
        if executed:
            headers.append('Дата фактического возврата')
            row.append('2026-11-12 15:30')
        if receipt_only:
            headers.extend(['Дата заявления на возврат', 'Фискальный чек возврата'])
            row.extend(['2026-11-10', 'Синтетические реквизиты чека'])
        return self.archive('payments', headers, [row], owner=None, state='unmatched')

    def test_refund_execution_requires_explicit_source_type_negative_amount_status_and_actual_date(self):
        self.refund_source('synthetic-executed-refund')
        result = historicalrefundtrace.resolve_refund_operation(self.con, self.sid, 'synthetic-executed-refund')
        self.assertTrue(result['execution_confirmed'], result)
        self.assertTrue(result['candidates'])
        candidate = result['candidates'][0]
        self.assertEqual(candidate['amount'], 1000)
        self.assertTrue(candidate['executed_at'].startswith('2026-11-12'))
        self.assertIn('15:30', candidate['executed_at'])
        self.assertEqual(candidate['source_invoice_id'], '726247')
        self.assertEqual(candidate['source_method'], 'Карта')
        self.assertTrue(candidate['source_signature'])
        self.assert_evidence_value(result, 'synthetic-executed-refund')

    def test_request_date_and_fiscal_receipt_alone_do_not_confirm_money_was_refunded(self):
        self.refund_source('synthetic-request-only-refund', executed=False, status='Заявление получено', receipt_only=True)
        result = historicalrefundtrace.resolve_refund_operation(self.con, self.sid, 'synthetic-request-only-refund')
        self.assertFalse(result['execution_confirmed'], result)

    def test_pending_status_and_positive_amount_do_not_confirm_executed_refund(self):
        for operation_id, status, amount in (
                ('synthetic-pending-refund', 'Ожидает исполнения', '-1000'),
                ('synthetic-positive-refund', 'Исполнен', '1000')):
            self.refund_source(operation_id, status=status, amount=amount)
            with self.subTest(operation_id=operation_id):
                result = historicalrefundtrace.resolve_refund_operation(self.con, self.sid, operation_id)
                self.assertFalse(result['execution_confirmed'], result)

    def test_conflicting_refund_fiscal_facts_or_aliases_block_execution_confirmation(self):
        headers = ['ID клиента', 'ID операции', 'ID счета', 'Тип платежа', 'Сумма',
                   'Дата фактического возврата', 'Статус возврата', 'Способ возврата', 'ФН', 'ФД', 'ФП']
        operation_id = 'synthetic-refund-conflicting-receipts'
        base = [self.ERP_ID, operation_id, '726247', 'Возврат', '-1000',
                '2026-11-12 15:30', 'Исполнен', 'Карта']
        self.archive('payments', headers, [base+['9999000011112222', '3483', '1315067523']],
                     owner=None, state='unmatched')
        self.archive('payments', headers, [base+['9999000011113333', '3484', '1315067524']],
                     owner=None, state='unmatched')
        result = historicalrefundtrace.resolve_refund_operation(self.con, self.sid, operation_id)
        self.assertTrue(result['ambiguous'], result)
        self.assertFalse(result['execution_confirmed'], result)
        self.assertEqual({candidate['fn'] for candidate in result['candidates']},
                         {'9999000011112222', '9999000011113333'})
        alias_operation = 'synthetic-refund-conflicting-aliases'
        self.archive('payments', ['ID клиента', 'ID операции', 'ID счета', 'Тип платежа', 'Сумма',
            'Дата исполнения', 'Статус возврата', 'Способ возврата',
            'ФН', 'Номер ФН', 'ФД', 'Номер ФД', 'ФП', 'ФПД'],
            [[self.ERP_ID, alias_operation, '726247', 'Возврат', '-1000', '2026-11-12 15:30',
              'Исполнен', 'Карта', '9999000011112222', '9999000011113333', '3483', '3484',
              '1315067523', '1315067524']], owner=None, state='unmatched')
        alias = historicalrefundtrace.resolve_refund_operation(self.con, self.sid, alias_operation)
        self.assertTrue(alias['ambiguous'], alias)
        self.assertFalse(alias['execution_confirmed'], alias)

    def test_single_sale_ledger_pointing_to_another_childs_bill_is_not_source_proof(self):
        branch = self.con.execute("INSERT INTO branches(name) VALUES ('Синтетический филиал')").lastrowid
        group = self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time) VALUES (?,'Синтетическая группа','2','18:45')", (branch,)).lastrowid
        payload = {'invoice_id': '716063', 'source_invoice_id': '716063',
                   'sale_id': 'synthetic-corrupt-ledger-sale', 'operation_id': 'synthetic-corrupt-ledger-operation'}
        # Deliberately reproduce a corrupt cross-client pointer without using
        # an import helper that would normally reject the relationship.
        self.con.execute('''INSERT INTO erp_single_sales(source_id,student_id,group_id,
            subscription_id,source_signature,source_payload,lesson_date,payment_operation_id,
            native_signature) VALUES (?,?,?,?,?,?,'2026-10-28',?,?)''',
            ('synthetic-corrupt-ledger-sale', self.sid, group, self.other_sub,
             'synthetic-source-signature', json.dumps(payload),
             'synthetic-corrupt-ledger-operation', 'synthetic-native-signature'))
        invoice = historicalrefundtrace.resolve_invoice(self.con, self.sid, '716063')
        self.assertEqual(invoice['subscription_ids'], [])
        self.assertEqual(invoice['payment_ids'], [])
        self.assertTrue(invoice['issues'])
        self.assertTrue(all(candidate.get('subscription_id') != self.other_sub
                            for candidate in invoice['candidates']))
        trace = historicalrefundtrace.context(self.con, self.ERP_ID)
        self.assertNotIn(self.other_sub, {row['id'] for row in trace['purchases']})
        self.assertTrue(all(row.get('target_id') != self.other_sub for row in trace['source_rows']))
        output = json.dumps({'invoice': invoice, 'context': trace}, ensure_ascii=False)
        self.assertNotIn(self.other_name, output)
        self.assertNotIn(self.other_erp, output)


if __name__ == '__main__':
    unittest.main()
