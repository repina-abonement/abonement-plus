"""Read-only financial audit fixtures; all amounts and identities are synthetic."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crm import db, erpfinancialaudit as audit, fiscal, money, paymentallocation, services


class ERPFinancialAuditTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='erp-financial-audit-')
        self.addCleanup(self.temp.cleanup)
        self.con = db.connect(str(Path(self.temp.name)/'fixture.sqlite3'))
        self.addCleanup(self.con.close)
        with redirect_stdout(io.StringIO()):
            db.init_db(self.con)
        self.erp = 'synthetic-audit-client'
        self.sid = services.create_student(self.con, {'name': 'Synthetic Audit Child',
            'parent_name': 'Synthetic Parent', 'phone': '+79000000431'},
            {'id': 1, 'name': 'Synthetic Owner', 'role': 'admin'})
        self.con.execute('UPDATE students SET erp_id=? WHERE id=?', (self.erp, self.sid))
        self.files = 0

    def invoice(self, amount, month='2026-11', status='paid'):
        return self.con.execute('''INSERT INTO subscriptions(student_id,month,tariff,kind,
            lessons,per_lesson,price,status,created_at,ledger_managed,erp_full_managed)
            VALUES (?,?,'fixed','month',1,?,?,?,? ,1,1)''',
            (self.sid, month, amount, amount, status, month+'-01')).lastrowid

    def payment(self, amount, day='2026-10-28', subid=None, technical=False):
        return self.con.execute('''INSERT INTO payments(student_id,amount,method,paid_at,comment,
            user_name,confirmed,subscription_id,created_at) VALUES (?,?,?,?,?,?,1,?,?)''',
            (self.sid, amount, 'unknown' if technical else 'card', day+'T12:00',
             'Оплата до реестра платежей' if technical else 'Synthetic real ERP receipt',
             'Система' if technical else 'ERP', subid, '2026-11-20')).lastrowid

    def archive(self, kind, headers, rows, targets=None):
        self.files += 1
        fid = self.con.execute('''INSERT INTO erp_full_files(digest,filename,kind,headers,rows,mapping,imported_at)
            VALUES (?,?,?,?,?,'{}','2026-11-20T12:00')''',
            (hashlib.sha256(str(self.files).encode()).hexdigest(), 'fixture-'+str(self.files)+'.csv',
             kind, json.dumps(headers), json.dumps(rows))).lastrowid
        for index, (table, native) in enumerate(targets or [('', None)]*len(rows), 2):
            self.con.execute('''INSERT INTO erp_full_rows(file_id,row_num,student_id,state,reason,target_table,target_id)
                VALUES (?,?,?,'native','Synthetic evidence',?,?)''', (fid, index, self.sid, table, native))
        return fid

    def sources(self, sub, pid, invoice='invoice-one', operation='operation-one', duplicate=False):
        purchase = self.con.execute('SELECT * FROM subscriptions WHERE id=?', (sub,)).fetchone()
        payment = self.con.execute('SELECT * FROM payments WHERE id=?', (pid,)).fetchone()
        sale_headers = ['ID клиента', 'ID счёта', 'ID продажи', 'Тип', 'Стоимость']
        sale_rows = [[self.erp, invoice, 'sale-'+invoice, 'Абонемент', str(purchase['price'])]]
        pay_headers = ['ID клиента', 'ID счёта', 'ID операции', 'Дата оплаты', 'Сумма', 'Вид оплаты']
        pay_rows = [[self.erp, invoice, operation, payment['paid_at'][:10], str(payment['amount']), 'Картой']]
        self.archive('subs', sale_headers, sale_rows, [('subscriptions', sub)])
        self.archive('payments', pay_headers, pay_rows, [('payments', pid)])
        self.con.execute('INSERT OR IGNORE INTO erp_full_operations VALUES (?,?,?)',
                         (operation, pid, json.dumps([self.sid, payment['paid_at'][:10], payment['amount'], payment['method']])))
        if duplicate:
            self.archive('payments', pay_headers, pay_rows, [('payments', pid)])
            self.archive('subs', sale_headers, sale_rows, [('subscriptions', sub)])

    def current_rows(self):
        return {table: [tuple(r) for r in self.con.execute('SELECT * FROM '+table+' ORDER BY 1')]
                for table in ('payments', 'subscriptions', 'attendance', 'erp_payment_allocations', 'erp_refund_allocations')}

    def run_audit(self, month=None, **kwargs):
        before, changes = self.current_rows(), self.con.total_changes
        with patch.object(fiscal, 'pk_call', side_effect=AssertionError('Provider call forbidden')):
            result = audit.audit(self.con, self.erp, month, **kwargs)
        self.assertEqual(self.current_rows(), before)
        self.assertEqual(self.con.total_changes, changes)
        self.assertTrue(result['read_only'])
        self.assertFalse(result['apply_allowed'])
        return result

    def owner_case(self):
        self.erp = '117559'
        self.con.execute('UPDATE students SET erp_id=? WHERE id=?', (self.erp, self.sid))

    def snapshot(self, **kwargs):
        result = {'erp_id': self.erp, 'amount': 0, 'currency': 'RUB', 'as_of_date': '2026-11-20',
            'cutoff_date': '2026-11-02', 'provenance': 'owner_confirmation',
            'confirmed': True, 'evidence_id': 'synthetic-explicit-balance-evidence'}
        result.update(kwargs)
        return result

    def test_exact_marker_without_source_proof_is_not_automatically_a_duplicate(self):
        sub = self.invoice(8100)
        technical = self.payment(8100, subid=sub, technical=True)
        result = self.run_audit()
        row = result['synthetic_candidates'][0]
        self.assertEqual((row['payment_id'], row['classification']), (technical, 'unproven_legacy'))
        self.assertTrue(row['blockers'])
        self.assertIsNone(result['after'])
        self.assertEqual(result['proposed_changes'], [])

    def test_same_amount_date_or_comment_does_not_prove_duplicate(self):
        sub = self.invoice(8100)
        self.payment(8100, subid=sub, technical=True)
        self.payment(8100)
        result = self.run_audit()
        self.assertEqual(result['synthetic_candidates'][0]['classification'], 'unproven_legacy')
        self.assertIsNone(result['after'])

    def test_proved_technical_duplicate_has_after_state_only_in_isolated_copy(self):
        sub = self.invoice(8100)
        technical = self.payment(8100, subid=sub, technical=True)
        real = self.payment(8100, '2026-09-28')
        self.sources(sub, real)
        result = self.run_audit()
        self.assertEqual(result['synthetic_candidates'][0]['classification'], 'source_supported_duplicate')
        self.assertEqual(result['before']['balance']['deposit'], 8100)
        self.assertEqual(result['after']['balance']['deposit'], 0)
        self.assertEqual(result['after']['allocation']['sources'][sub], [{'payment_id': real, 'amount': 8100}])
        self.assertEqual(result['after'], result['proposed_after'])
        self.assertTrue(any(r['action'] == 'exclude_proved_technical_duplicate' and r['payment_id'] == technical for r in result['proposed_changes']))
        self.assertEqual(self.con.execute('SELECT cancelled FROM payments WHERE id=?', (technical,)).fetchone()[0], 0)

    def test_a_real_operation_already_using_the_technical_row_is_preserved(self):
        sub = self.invoice(8100)
        technical = self.payment(8100, subid=sub, technical=True)
        self.sources(sub, technical)
        result = self.run_audit()
        self.assertEqual(result['synthetic_candidates'][0]['classification'], 'unproven_legacy')
        self.assertIsNone(result['after'])

    def test_partial_real_payment_cannot_erase_entire_old_opening_payment(self):
        sub = self.invoice(8100)
        self.payment(8100, subid=sub, technical=True)
        real = self.payment(2100)
        self.sources(sub, real)
        result = self.run_audit()
        self.assertEqual(result['synthetic_candidates'][0]['classification'], 'unproven_legacy')
        self.assertIsNone(result['after'])

    def test_partial_real_payments_with_distinct_ids_can_jointly_prove_same_invoice(self):
        sub = self.invoice(8100)
        self.payment(8100, subid=sub, technical=True)
        first, second = self.payment(2100), self.payment(6000, '2026-10-29')
        self.sources(sub, first, operation='partial-one')
        # Same source sale repeats, not a different sale with a shared amount.
        self.sources(sub, second, operation='partial-two')
        result = self.run_audit()
        self.assertEqual(result['synthetic_candidates'][0]['classification'], 'source_supported_duplicate')
        self.assertEqual(result['after']['balance']['paid_total'], 8100)
        self.assertEqual(result['after']['balance']['covered'][sub], 8100)

    def test_repeated_and_reordered_source_rows_do_not_double_count_real_payments(self):
        sub = self.invoice(8100)
        self.payment(8100, subid=sub, technical=True)
        real = self.payment(8100)
        self.sources(sub, real, duplicate=True)
        first = self.run_audit()
        second = self.run_audit()
        self.assertEqual(first['synthetic_candidates'][0]['related_payment_ids'], [real])
        self.assertEqual(first['after']['balance']['paid_total'], 8100)
        self.assertEqual(first['after'], second['after'])
        self.assertEqual(first['proposed_changes'], second['proposed_changes'])

    def test_wrong_source_amount_blocks_after_even_when_sum_and_native_id_match(self):
        sub = self.invoice(8100)
        self.payment(8100, subid=sub, technical=True)
        real = self.payment(8100)
        self.sources(sub, real)
        self.archive('payments', ['ID клиента', 'ID счёта', 'ID операции', 'Дата оплаты', 'Сумма', 'Вид оплаты'],
            [[self.erp, 'invoice-one', 'operation-one', '2026-10-28', '8000', 'Картой']], [('payments', real)])
        result = self.run_audit()
        self.assertIsNone(result['after'])
        self.assertEqual(result['synthetic_candidates'][0]['classification'], 'unproven_legacy')

    def test_changed_native_purchase_cost_does_not_prove_an_opening_payment_duplicate(self):
        sub = self.invoice(8100)
        self.payment(8000, subid=sub, technical=True)
        real = self.payment(8000)
        self.sources(sub, real)
        self.con.execute('UPDATE subscriptions SET price=8000 WHERE id=?', (sub,))
        result = self.run_audit()
        self.assertIsNone(result['after'])
        self.assertEqual(result['synthetic_candidates'][0]['classification'], 'unproven_legacy')
        self.assertTrue(any('Стоимость исходной' in r for r in result['blockers']))

    def test_december_fifo_split_is_explained_without_claiming_earlier_money_is_wrong(self):
        december = self.invoice(12150, '2026-12')
        august = self.payment(2100, '2026-08-28')
        september = self.payment(10050, '2026-09-28')
        result = self.run_audit('2026-12')
        parts = result['cross_period_allocations']
        self.assertEqual([(r['payment_id'], r['source_month'], r['amount']) for r in parts],
                         [(august, '2026-08', 2100), (september, '2026-09', 10050)])
        self.assertTrue(all(r['subscription_id'] == december and r['kind'] == 'fifo' and r['proof_status'] == 'unverified' for r in parts))
        self.assertIsNone(result['after'])

    def test_missing_december_source_purchase_blocks_planned_reallocation(self):
        prior = self.invoice(8100, '2026-09')
        self.invoice(12150, '2026-12')
        self.payment(8100, subid=prior, technical=True)
        real = self.payment(8100)
        self.sources(prior, real)
        result = self.run_audit('2026-12')
        self.assertIsNone(result['after'])
        self.assertTrue(any('Состав покупок' in r for r in result['blockers']))

    def event(self, amount='2700', target='next-invoice', event='event-one'):
        self.archive('subs', ['ID клиента', 'ID мероприятия', 'ID продажи', 'ID счёта', 'Тип мероприятия',
            'Дата мероприятия', 'Стоимость', 'Статус мероприятия', 'ID целевого счёта'],
            [[self.erp, event, 'event-sale-one', 'event-invoice-one', 'Турнир', '2026-11-14', amount, 'Пропуск', target]])

    def test_event_carry_source_repeats_fold_once_without_creating_cash_or_credits(self):
        self.event()
        self.event()
        first, second = self.run_audit(), self.run_audit()
        carry = first['event_carry']
        self.assertEqual(len(carry['source_candidates']), 1)
        self.assertEqual(len(carry['plans']), 1)
        self.assertEqual((carry['source_candidates'][0]['amount'], carry['state']), (2700, 'blocked'))
        self.assertFalse(carry['plans'][0]['financial_effect_applied'])
        self.assertEqual(carry, second['event_carry'])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0], 0)

    def test_conflicting_event_amount_or_target_blocks_once_instead_of_summing(self):
        for kind in ('amount', 'target'):
            with self.subTest(kind=kind):
                self.con.execute('DELETE FROM erp_full_rows')
                self.con.execute('DELETE FROM erp_full_files')
                self.event()
                self.event(amount='2800' if kind == 'amount' else '2700', target='other' if kind == 'target' else 'next-invoice')
                result = self.run_audit()
                candidates = result['event_carry']['source_candidates']
                self.assertEqual(len(candidates), 1)
                self.assertIsNone(candidates[0]['amount'])
                self.assertTrue(any('различаются' in r for r in candidates[0]['blockers']))

    def test_reported_case_expectations_are_never_promoted_to_observed_finances(self):
        self.erp = '117559'
        self.con.execute('UPDATE students SET erp_id=? WHERE id=?', (self.erp, self.sid))
        result = self.run_audit('2026-12')
        self.assertEqual(result['before']['balance']['paid_total'], 0)
        self.assertEqual(result['before']['balance']['deposit'], 0)
        self.assertEqual({r['amount'] for r in result['expectations'] if 'amount' in r}, {39300, 12150})
        self.assertTrue(all(r['verified'] is False for r in result['expectations']))
        self.assertTrue(result['event_carry']['owner_requirement']['confirmed'])
        self.assertEqual(result['event_carry']['owner_requirement']['amount'], 2700)
        self.assertEqual(result['event_carry']['source_candidates'], [])
        self.assertEqual(result['event_carry']['state'], 'destination_pending')
        self.assertIsNone(result['after'])

    def test_owner_snapshot_supersedes_history_clone_while_duplicate_evidence_is_preserved(self):
        self.erp = '117559'
        self.con.execute('UPDATE students SET erp_id=? WHERE id=?', (self.erp, self.sid))
        sub = self.invoice(8100)
        self.payment(8100, subid=sub, technical=True)
        real = self.payment(8100)
        self.sources(sub, real)
        result = self.run_audit('2026-12')
        self.assertEqual(result['synthetic_candidates'][0]['classification'], 'source_supported_duplicate')
        self.assertIsNone(result['after'])
        self.assertFalse(result['plan_complete'])
        self.assertEqual(result['after_scope'], 'owner_confirmed_balance_snapshot_plan')
        self.assertIsNone(result['proposed_after'])
        self.assertEqual(result['before']['balance']['paid_total'], 16200)
        self.assertEqual(result['before']['balance']['deposit'], 8100)
        self.assertEqual(result['authoritative_balance_plan']['free_balance'], 0)
        self.assertTrue(any('Перенос подтверждён владельцем' in r for r in result['event_carry']['notes']))
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0], 2)

    def test_owner_zero_snapshot_has_fixed_provenance_and_never_changes_runtime_balance(self):
        self.owner_case()
        self.payment(39300, '2026-08-28')
        actual = money.balance(self.con, self.sid)
        first, second = self.run_audit(), self.run_audit()
        snapshot = first['erp_balance_snapshot']
        self.assertEqual((snapshot['amount'], snapshot['as_of_date'], snapshot['cutoff_date']),
                         (0, '2026-11-20', '2026-11-02'))
        self.assertEqual(snapshot['provenance'], 'owner_confirmation')
        self.assertTrue(snapshot['confirmed'])
        self.assertFalse(snapshot['live_erp_read'])
        self.assertFalse(snapshot['history_complete'])
        self.assertEqual(first['crm_observed']['balance'], actual)
        self.assertEqual(first['before']['balance']['deposit'], 39300)
        self.assertEqual(first['authoritative_balance_plan']['free_balance'], 0)
        self.assertEqual(first['authoritative_balance_plan']['available_balance'], 2700)
        self.assertIsNone(first['authoritative_balance_plan']['current_free_balance'])
        self.assertEqual(first['erp_balance_snapshot'], second['erp_balance_snapshot'])
        self.assertEqual(first['authoritative_balance_plan'], second['authoritative_balance_plan'])
        self.assertEqual(money.balance(self.con, self.sid), actual)

    def test_old_money_does_not_fund_december_in_plan_and_does_not_create_new_debt(self):
        self.owner_case()
        december = self.invoice(12150, '2026-12')
        self.payment(2100, '2026-08-28')
        self.payment(10050, '2026-09-28')
        result = self.run_audit('2026-12')
        plan = result['authoritative_balance_plan']
        row = plan['protected_invoices'][0]
        self.assertEqual(result['before']['balance']['covered'][december], 12150)
        self.assertEqual((plan['free_balance'], plan['historical_funds_available_for_future']), (0, 0))
        self.assertEqual((row['crm_covered'], row['historical_covered'], row['planned_covered']), (12150, 12150, 0))
        self.assertEqual(row['coverage_unverified'], 12150)
        self.assertEqual(row['planned_payable'], 12150)
        self.assertFalse(plan['balance_use_selected'])
        self.assertIsNone(row['planned_unpaid'])
        self.assertEqual(row['debt_created'], 0)
        self.assertTrue(row['plan_only'])
        self.assertEqual(self.con.execute('SELECT status FROM subscriptions WHERE id=?', (december,)).fetchone()[0], 'paid')

    def test_even_exact_old_payment_ids_do_not_override_owner_balance_cutoff(self):
        self.owner_case()
        december = self.invoice(12150, '2026-12')
        august, september = self.payment(2100, '2026-08-28'), self.payment(10050, '2026-09-28')
        self.sources(december, august, invoice='december', operation='august-old')
        self.sources(december, september, invoice='december', operation='september-old')
        result = self.run_audit('2026-12')
        row = result['authoritative_balance_plan']['protected_invoices'][0]
        self.assertEqual(row['confirmed_post_cutoff_covered'], 0)
        self.assertEqual(result['post_cutoff']['operations'], [])
        self.assertIsNone(result['proposed_after'])

    def test_confirmed_post_cutoff_receipt_is_checked_once_and_not_added_to_snapshot(self):
        self.owner_case()
        december = self.invoice(12150, '2026-12')
        pid = self.payment(6500, '2026-11-05')
        self.sources(december, pid, duplicate=True)
        result = self.run_audit('2026-12')
        post = result['post_cutoff']
        self.assertEqual(len(post['operations']), 1)
        self.assertEqual(post['operations'][0]['confirmation'], 'source_confirmed')
        self.assertTrue(post['operations'][0]['included_in_snapshot'])
        self.assertEqual(post['totals']['confirmed_income'], 6500)
        self.assertFalse(post['apply_as_new_income'])
        row = result['authoritative_balance_plan']['protected_invoices'][0]
        self.assertEqual(row['confirmed_post_cutoff_covered'], 6500)
        self.assertEqual(result['authoritative_balance_plan']['free_balance'], 0)
        self.assertIsNone(row['planned_unpaid'])

    def test_unconfirmed_post_cutoff_and_technical_backfill_cannot_pay_future_purchase(self):
        self.owner_case()
        december = self.invoice(12150, '2026-12')
        self.payment(6500, '2026-11-05')
        self.payment(12150, '2026-11-20', subid=december, technical=True)
        result = self.run_audit('2026-12')
        self.assertTrue(all(r['confirmation'] == 'unverified' for r in result['post_cutoff']['operations']))
        row = result['authoritative_balance_plan']['protected_invoices'][0]
        self.assertEqual(row['planned_covered'], 0)
        self.assertEqual(result['authoritative_balance_plan']['free_balance'], 0)
        self.assertTrue(any('Не все операции после' in r for r in result['blockers']))

    def test_cutoff_is_exclusive_and_newer_operations_require_fresh_reconciliation(self):
        self.owner_case()
        before_cutoff = self.invoice(1000, '2026-12')
        after_cutoff = self.invoice(1000, '2026-12')
        newer = self.invoice(1000, '2026-12')
        for sub, day, key in ((before_cutoff, '2026-11-02', 'on-cutoff'),
                              (after_cutoff, '2026-11-03', 'after-cutoff'),
                              (newer, '2026-11-21', 'after-snapshot')):
            pid = self.payment(1000, day)
            self.sources(sub, pid, invoice=key, operation=key)
        result = self.run_audit('2026-12')
        post = result['post_cutoff']
        self.assertEqual({r['operation_id'] for r in post['operations']}, {'after-cutoff', 'after-snapshot'})
        self.assertTrue(post['requires_new_snapshot'])
        self.assertEqual([r['operation_id'] for r in post['operations_after_snapshot']], ['after-snapshot'])
        plan = result['authoritative_balance_plan']
        self.assertTrue(plan['requires_new_snapshot'])
        self.assertIsNone(plan['current_free_balance'])
        self.assertEqual({r['subscription_id']: r['planned_covered'] for r in plan['protected_invoices']},
                         {before_cutoff: 0, after_cutoff: 1000, newer: 1000})
        self.assertEqual(next(r for r in plan['protected_invoices'] if r['subscription_id'] == newer)['planned_payable'], 0)
        self.assertEqual(plan['available_balance'], 2700)
        self.assertEqual(self.con.execute('SELECT SUM(amount) FROM payments').fetchone()[0], 3000)

    def test_confirmed_post_cutoff_refund_is_reported_separately_without_new_debit(self):
        self.owner_case()
        debit = self.payment(-2700, '2026-11-06')
        self.con.execute("UPDATE payments SET method='refund' WHERE id=?", (debit,))
        headers = ['ID клиента', 'ID счёта', 'ID операции', 'Дата оплаты', 'Сумма', 'Вид оплаты',
                   'Тип платежа', 'Статус возврата', 'Дата исполнения возврата']
        self.archive('payments', headers, [[self.erp, 'refunded-order', 'executed-return', '2026-11-06',
            '-2700', 'Безналичными', 'Возврат', 'Исполнен', '2026-11-06']], [('payments', debit)])
        self.con.execute('INSERT INTO erp_full_operations VALUES (?,?,?)',
                         ('executed-return', debit, json.dumps([self.sid, '2026-11-06', -2700, 'refund'])))
        result = self.run_audit()
        post = result['post_cutoff']
        self.assertEqual((post['totals']['confirmed_refunds'], post['totals']['confirmed_income']), (2700, 0))
        self.assertEqual(post['operations'][0]['direction'], 'refund')
        self.assertTrue(post['operations'][0]['included_in_snapshot'])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0], 1)

    def test_exact_source_refund_reduces_future_coverage_and_unlinked_refund_blocks_it(self):
        for linked in (False, True):
            with self.subTest(linked=linked):
                # Each run uses its own current card so all operations below
                # have independent IDs and no prior synthetic financial state.
                self.owner_case()
                december = self.invoice(10000, '2026-12')
                pid = self.payment(10000, '2026-11-05')
                self.sources(december, pid, invoice='dec-'+str(linked), operation='income-'+str(linked))
                debit = self.payment(-2000, '2026-11-07')
                self.con.execute("UPDATE payments SET method='refund' WHERE id=?", (debit,))
                self.archive('payments', ['ID клиента', 'ID счёта', 'ID операции', 'Дата оплаты', 'Сумма',
                    'Вид оплаты', 'Тип платежа', 'Статус возврата', 'Дата исполнения возврата'],
                    [[self.erp, 'dec-'+str(linked), 'return-'+str(linked), '2026-11-07', '-2000',
                      'Безналичными', 'Возврат', 'Исполнен', '2026-11-07']], [('payments', debit)])
                self.con.execute('INSERT INTO erp_full_operations VALUES(?,?,?)',
                    ('return-'+str(linked), debit, json.dumps([self.sid, '2026-11-07', -2000, 'refund'])))
                if linked:
                    # Clear the unlinked previous fixture's money from the
                    # active view; all rows remain present for audit history.
                    self.con.execute('UPDATE payments SET cancelled=1 WHERE id NOT IN (?,?)', (pid, debit))
                    paymentallocation.bind_payment(self.con, pid, december, 10000,
                        source_namespace='erp', source_invoice_id='dec-'+str(linked),
                        source_operation_id='income-'+str(linked), evidence_id='synthetic-source-link')
                    paymentallocation.register_refund(self.con, debit, pid, december, 2000)
                result = self.run_audit('2026-12')
                row = next(r for r in result['authoritative_balance_plan']['protected_invoices'] if r['subscription_id'] == december)
                self.assertEqual(result['post_cutoff']['totals']['confirmed_refunds'], 2000)
                self.assertEqual(row['planned_covered'], 8000 if linked else 0)
                self.assertEqual(row['status'], 'requires_source_reconciliation')
                self.assertIsNone(row['planned_unpaid'])
                self.assertEqual(row['debt_created'], 0)
                self.assertTrue(row['coverage_blockers'] if not linked else not row['coverage_blockers'])

    def test_same_day_operation_is_not_asserted_to_be_in_a_day_precision_snapshot(self):
        self.owner_case()
        december = self.invoice(1000, '2026-12')
        pid = self.payment(1000, '2026-11-20')
        self.sources(december, pid)
        result = self.run_audit('2026-12')
        row = result['post_cutoff']['operations'][0]
        self.assertEqual(row['confirmation'], 'source_confirmed')
        self.assertIsNone(row['included_in_snapshot'])
        self.assertEqual(row['snapshot_inclusion'], 'requires_same_day_reconciliation')
        self.assertFalse(row['snapshot_coverage_confirmed'])
        self.assertTrue(result['post_cutoff']['requires_same_day_reconciliation'])
        self.assertEqual(result['post_cutoff']['operations_after_snapshot'], [])
        self.assertEqual(result['authoritative_balance_plan']['protected_invoices'][0]['planned_covered'], 1000)
        self.assertEqual(result['authoritative_balance_plan']['protected_invoices'][0]['planned_payable'], 0)
        self.assertIsNone(result['authoritative_balance_plan']['current_free_balance'])
        self.assertEqual(result['authoritative_balance_plan']['available_balance'], 2700)

    def test_exact_direct_payment_and_refund_after_snapshot_confirm_only_their_net_purchase_coverage(self):
        self.owner_case()
        december = self.invoice(10000, '2026-12')
        pid = self.payment(10000, '2026-11-21')
        self.sources(december, pid, invoice='synthetic-newer-december', operation='synthetic-newer-income')
        debit = self.payment(-2000, '2026-11-22')
        self.con.execute("UPDATE payments SET method='refund' WHERE id=?", (debit,))
        self.archive('payments', ['ID клиента', 'ID счёта', 'ID операции', 'Дата оплаты', 'Сумма',
            'Вид оплаты', 'Тип платежа', 'Статус возврата', 'Дата исполнения возврата'],
            [[self.erp, 'synthetic-newer-december', 'synthetic-newer-return', '2026-11-22', '-2000',
              'Безналичными', 'Возврат', 'Исполнен', '2026-11-22']], [('payments', debit)])
        self.con.execute('INSERT INTO erp_full_operations VALUES(?,?,?)',
            ('synthetic-newer-return', debit, json.dumps([self.sid, '2026-11-22', -2000, 'refund'])))
        paymentallocation.bind_payment(self.con, pid, december, 10000,
            source_namespace='erp', source_invoice_id='synthetic-newer-december',
            source_operation_id='synthetic-newer-income', evidence_id='synthetic-newer-exact-source')
        paymentallocation.register_refund(self.con, debit, pid, december, 2000)
        result = self.run_audit('2026-12')
        row = result['authoritative_balance_plan']['protected_invoices'][0]
        self.assertEqual((row['planned_covered'], row['planned_payable']), (8000, 2000))
        self.assertTrue(result['post_cutoff']['requires_new_snapshot'])
        self.assertEqual(result['post_cutoff']['totals']['confirmed_income'], 10000)
        self.assertEqual(result['post_cutoff']['totals']['confirmed_refunds'], 2000)
        self.assertIsNone(result['authoritative_balance_plan']['current_free_balance'])
        self.assertEqual(result['authoritative_balance_plan']['available_balance'], 2700)
        self.assertFalse(result['post_cutoff']['apply_as_new_income'])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0], 2)

    def test_unposted_source_operation_after_cutoff_remains_visible_without_creating_payment(self):
        self.owner_case()
        self.archive('payments', ['ID клиента', 'ID счёта', 'ID операции', 'Дата оплаты', 'Сумма', 'Вид оплаты'],
            [[self.erp, 'missing-native-order', 'missing-native-operation', '2026-11-10', '2700', 'Картой']])
        result = self.run_audit()
        row = result['post_cutoff']['operations'][0]
        self.assertEqual(row['operation_id'], 'missing-native-operation')
        self.assertFalse(row['native_present'])
        self.assertEqual(row['confirmation'], 'unverified')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0], 0)

    def test_once_owner_confirmed_tournament_requirement_is_not_free_money_or_income(self):
        self.owner_case()
        self.event()
        self.event()
        first, second = self.run_audit(), self.run_audit()
        carry = first['event_carry']
        self.assertEqual(carry['owner_requirement']['provenance'], 'owner_confirmation')
        self.assertTrue(carry['owner_requirement']['confirmed'])
        self.assertTrue(carry['owner_requirement']['once'])
        self.assertEqual(carry['owner_requirement']['destination'], 'next_tournament')
        self.assertEqual((carry['owner_requirement']['income_effect'], carry['owner_requirement']['free_balance_effect']), (0, 0))
        self.assertEqual(len(carry['plans']), 1)
        self.assertEqual(carry['plans'][0]['amount'], 2700)
        self.assertEqual(carry['plans'][0]['label'], 'На следующий турнир')
        self.assertTrue(carry['plans'][0]['destination_pending'])
        self.assertIsNone(carry['plans'][0]['target_invoice_id'])
        self.assertEqual(carry['plans'][0]['state'], 'destination_pending')
        self.assertEqual(carry['plans'][0]['blockers'], [])
        self.assertFalse(carry['plans'][0]['apply_as_new_income'])
        self.assertTrue(carry['plans'][0]['source_place_unknown'])
        self.assertEqual(first['authoritative_balance_plan']['existing_carry_balance'], 2700)
        self.assertEqual(first['authoritative_balance_plan']['available_balance'], 2700)
        self.assertEqual(carry, second['event_carry'])
        self.assertFalse(first['plan_complete'])
        self.assertIsNone(first['after'])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0], 0)

    def test_imported_shape_separates_technical_money_owner_carry_and_december_plan(self):
        # Reproduce the supplied response's arithmetic with synthetic native
        # and operation IDs. No source invoice identity is invented here.
        self.owner_case()
        # Only aggregate counts/totals reproduce the working response.
        # Individual historical amounts and dates below are deliberately
        # invented, so this fixture does not retain a client's transaction list.
        charges = [(6000, '2026-10')]*23 + [(21780, '2026-11')]
        purchases = [self.invoice(amount, month) for amount, month in charges]
        december = self.invoice(12150, '2026-12')
        real = [(6000, '2026-01-15')]*20 + [(17150, '2026-03-12'),
            (11830, '2026-07-12'), (10800, '2026-10-22')]
        source, targets = [], []
        for index, (amount, day) in enumerate(real):
            pid = self.payment(amount, day)
            opid = 'synthetic-retained-operation-'+str(index)
            source.append([self.erp, opid, amount, day, 'Картой'])
            targets.append(('payments', pid))
            self.con.execute('INSERT INTO erp_full_operations VALUES(?,?,?)',
                (opid, pid, json.dumps([self.sid, day, amount, 'card'])))
        self.archive('payments', ['ID клиента', 'ID операции', 'Сумма', 'Дата оплаты', 'Вид оплаты'], source, targets)
        markers = [(4200, '2026-01-02', index) for index in range(5)]
        markers += [(5000, '2026-11-20', index) for index in range(5, 10)]
        markers.append((5450, '2026-11-20', 10))
        for amount, day, index in markers:
            self.payment(amount, day, subid=purchases[index], technical=True)
        first, second = self.run_audit('2026-12'), self.run_audit('2026-12')
        self.assertEqual(sum(amount for amount, _ in real), 159780)
        self.assertEqual(sum(amount for amount, _ in charges), 159780)
        self.assertEqual(first['before']['balance']['deposit'], 39300)
        self.assertEqual(first['before']['balance']['covered'][december], 12150)
        coverage = first['source_coverage']
        self.assertEqual((coverage['technical_count'], coverage['technical_total']), (11, 51450))
        self.assertEqual((coverage['native_real_count'], coverage['native_real_net']), (23, 159780))
        self.assertEqual(coverage['last_retained_payment_date'], '2026-10-22')
        self.assertFalse(coverage['complete'])
        self.assertFalse(coverage['since_cutoff']['covered'])
        self.assertEqual(coverage['since_cutoff']['retained_operation_count'], 0)
        post = first['post_cutoff']
        self.assertEqual((post['technical_count'], post['technical_total']), (6, 30450))
        self.assertEqual(post['totals']['unverified_income'], 0)
        self.assertEqual(post['totals']['confirmed_income'], 0)
        self.assertTrue(all(row['origin'] == 'technical_backfill' for row in post['operations']))
        self.assertFalse(post['requires_same_day_reconciliation'])
        self.assertFalse(any('Не все операции после' in r for r in first['blockers']))
        plan = first['authoritative_balance_plan']
        self.assertEqual((first['erp_balance_snapshot']['amount'], plan['free_balance'], plan['available_balance']), (0, 0, 2700))
        self.assertEqual(plan['existing_carry_balance'], 2700)
        self.assertFalse(plan['enabled'])
        self.assertFalse(plan['balance_use_selected'])
        self.assertIsNone(plan['current_free_balance'])
        row = next(r for r in plan['protected_invoices'] if r['subscription_id'] == december)
        self.assertEqual((row['planned_covered'], row['planned_payable']), (0, 12150))
        self.assertEqual(plan['carry_credit_plan'], second['authoritative_balance_plan']['carry_credit_plan'])
        self.assertEqual(plan['carry_credit_plan']['income_effect'], 0)
        self.assertEqual(len(first['event_carry']['plans']), 1)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0], 34)

    def test_source_date_fallback_is_visible_without_claiming_a_real_payment_timestamp(self):
        self.owner_case()
        pid = self.payment(4050, '2026-10-22')
        self.archive('payments', ['ID клиента', 'ID операции', 'Сумма', 'Дата события', 'Вид оплаты'],
            [[self.erp, 'synthetic-fallback-operation', '4050', '22/10/2026', 'Картой']], [('payments', pid)])
        first = self.run_audit()
        coverage = first['source_coverage']
        self.assertEqual(coverage['last_retained_payment_date'], '2026-10-22')
        self.assertEqual(coverage['source_date_fallback_count'], 1)
        self.assertEqual(coverage['last_retained_payment']['date_origin'], 'Дата события')
        self.assertTrue(coverage['last_retained_payment']['date_is_fallback'])
        self.assertFalse(coverage['last_retained_payment']['payment_date_confirmed'])
        self.assertFalse(coverage['since_cutoff']['covered'])

    def test_newer_technical_timestamp_does_not_claim_new_receipts_or_a_new_balance_snapshot(self):
        self.owner_case()
        sub = self.invoice(2700)
        self.payment(2700, '2026-11-21', subid=sub, technical=True)
        result = self.run_audit()
        self.assertEqual(result['post_cutoff']['totals']['unverified_income'], 0)
        self.assertEqual(result['post_cutoff']['technical_total'], 2700)
        self.assertFalse(result['post_cutoff']['requires_new_snapshot'])
        self.assertEqual(result['post_cutoff']['operations_after_snapshot'], [])

    def test_sold_purchases_and_actual_attendance_survive_snapshot_plan(self):
        self.owner_case()
        november = self.invoice(8100)
        december = self.invoice(12150, '2026-12')
        self.payment(39300, '2026-09-28')
        bid = self.con.execute("INSERT INTO branches(name) VALUES('Synthetic Branch')").lastrowid
        gid = self.con.execute("INSERT INTO groups(branch_id,name,weekdays) VALUES(?,'Synthetic Group','0,2')", (bid,)).lastrowid
        self.con.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES(?,?,'2026-11-18','present')", (self.sid, gid))
        result = self.run_audit('2026-12')
        self.assertEqual(result['authoritative_balance_plan']['preserve_subscription_ids'], [november, december])
        self.assertTrue(result['authoritative_balance_plan']['attendance_rows_preserved'])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM attendance').fetchone()[0], 1)

    def test_raw_balance_without_explicit_evidence_does_not_create_universal_snapshot(self):
        self.archive('clients', ['ID клиента', 'Баланс'], [[self.erp, '0']])
        result = self.run_audit()
        self.assertIsNone(result['erp_balance_snapshot'])
        self.assertIsNone(result['authoritative_balance_plan'])

    def test_explicit_snapshot_policy_is_general_and_does_not_require_the_known_case_id(self):
        december = self.invoice(12150, '2026-12')
        self.payment(39300, '2026-09-28')
        result = self.run_audit('2026-12', balance_snapshot=self.snapshot())
        self.assertEqual(result['erp_balance_snapshot']['erp_id'], self.erp)
        self.assertEqual(result['erp_balance_snapshot']['amount'], 0)
        self.assertEqual(result['authoritative_balance_plan']['free_balance'], 0)
        self.assertEqual(result['authoritative_balance_plan']['historical_funds_available_for_future'], 0)
        self.assertEqual(result['authoritative_balance_plan']['protected_invoices'][0]['subscription_id'], december)
        self.assertEqual(result['authoritative_balance_plan']['protected_invoices'][0]['planned_covered'], 0)
        self.assertNotIn('owner_requirement', result['event_carry'])
        self.assertEqual(result['authoritative_balance_plan']['available_balance'], 0)
        self.assertEqual(result['authoritative_balance_plan']['existing_carry_balance'], 0)
        self.assertIsNone(result['authoritative_balance_plan']['carry_credit_plan'])

    def test_positive_explicit_export_balance_is_preserved_without_manufacturing_a_payment(self):
        snapshot = self.snapshot(amount=3200, provenance='erp_export', source_field='Баланс', source_value='3 200')
        result = self.run_audit(balance_snapshot=snapshot)
        self.assertEqual(result['authoritative_balance_plan']['free_balance'], 3200)
        self.assertEqual(result['authoritative_balance_plan']['snapshot_funds_available_for_future'], 3200)
        self.assertEqual(result['authoritative_balance_plan']['provenance'], 'erp_export')
        self.assertEqual(result['proposed_changes'][0]['amount'], 3200)
        self.assertEqual(result['proposed_changes'][0]['provenance'], 'erp_export')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0], 0)

    def test_missing_or_conflicting_explicit_snapshot_fields_never_become_zero_evidence(self):
        invalid = [self.snapshot(erp_id='other'), self.snapshot(amount=True), self.snapshot(amount=0.5),
            self.snapshot(currency='USD'), self.snapshot(confirmed=False), self.snapshot(as_of_date=None),
            self.snapshot(as_of_date='2026-11-01'), self.snapshot(provenance='guessed_ledger'),
            self.snapshot(provenance='erp_export'), self.snapshot(provenance='erp_export', source_field='Баланс', source_value='999')]
        for evidence in invalid:
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                audit.resolve_balance_snapshot(self.erp, evidence)
        self.assertIsNone(audit.resolve_balance_snapshot(self.erp))

    def test_older_explicit_snapshot_cannot_override_the_later_owner_confirmation(self):
        self.owner_case()
        old = self.snapshot(amount=39300, as_of_date='2026-11-01', cutoff_date='2026-10-01',
            provenance='erp_export', source_field='Баланс', source_value='39300')
        result = self.run_audit(balance_snapshot=old)
        self.assertEqual((result['erp_balance_snapshot']['amount'], result['erp_balance_snapshot']['as_of_date']),
                         (0, '2026-11-20'))
        self.assertEqual(result['erp_balance_snapshot']['provenance'], 'owner_confirmation')
        with self.assertRaises(ValueError):
            audit.resolve_balance_snapshot(self.erp, self.snapshot(amount=39300))

    def test_comment_or_regular_absence_is_not_a_tournament_credit_source(self):
        self.archive('visits', ['ID клиента', 'Комментарий', 'Дата занятия', 'Сумма'],
            [[self.erp, 'Турнир, перенос 2700', '2026-11-14', '2700']])
        self.assertEqual(self.run_audit()['event_carry']['source_candidates'], [])

    def test_other_card_rows_are_never_emitted(self):
        other = services.create_student(self.con, {'name': 'Private Other Child',
            'parent_name': 'Private Parent', 'phone': '+79000000432'},
            {'id': 1, 'name': 'Synthetic Owner', 'role': 'admin'})
        self.con.execute("UPDATE students SET erp_id='private-other' WHERE id=?", (other,))
        self.archive('payments', ['ID клиента', 'ID счёта', 'ID операции', 'Сумма'],
            [['private-other', 'private-invoice', 'private-operation', '999999']])
        # Correct the retained row owner to the other card as real import does.
        self.con.execute('UPDATE erp_full_rows SET student_id=?', (other,))
        output = json.dumps(self.run_audit(), ensure_ascii=False)
        self.assertNotIn('private-operation', output)
        self.assertNotIn('Private Other Child', output)

    def test_invalid_month_is_rejected_without_side_effects(self):
        before = self.current_rows()
        for month in ('2026-13', '2026-2', 'yesterday'):
            with self.subTest(month=month), self.assertRaises(ValueError):
                audit.audit(self.con, self.erp, month)
        self.assertEqual(self.current_rows(), before)


if __name__ == '__main__':
    unittest.main()
