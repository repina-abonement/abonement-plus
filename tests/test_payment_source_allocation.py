"""Source-directed historical accounting, using synthetic in-memory money only."""
import sqlite3
import unittest
from datetime import date
from unittest.mock import patch

from crm import money, paymentallocation as pa, reconciliation, subcancel


class PaymentSourceAllocationTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(':memory:')
        self.con.row_factory = sqlite3.Row
        self.con.execute('PRAGMA foreign_keys=ON')
        self.con.executescript('''
          CREATE TABLE students(id INTEGER PRIMARY KEY);
          INSERT INTO students VALUES(1),(2);
          CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);
          CREATE TABLE subscriptions(id INTEGER PRIMARY KEY,student_id INTEGER,month TEXT,
            price INTEGER,status TEXT,paid_at TEXT,created_at TEXT,
            kind TEXT DEFAULT 'month',ledger_managed INTEGER DEFAULT 1);
          CREATE TABLE pay_links(id INTEGER PRIMARY KEY,payment_id INTEGER,pk_payment_id TEXT,status TEXT);
          CREATE TABLE subscription_refunds(id INTEGER PRIMARY KEY,pk_payment_id TEXT,status TEXT,reserve_id INTEGER,payment_id INTEGER);
        ''')
        money.init(self.con)
        self.addCleanup(self.con.close)

    def invoice(self, price, month='2026-11', sid=1, status='paid'):
        return self.con.execute('''INSERT INTO subscriptions(student_id,month,price,status,created_at)
            VALUES(?,?,?,?,?)''', (sid, month, price, status, month+'-01')).lastrowid

    def payment(self, amount, day='2026-10-01', sid=1, method='card'):
        return self.con.execute('''INSERT INTO payments(student_id,amount,method,paid_at,user_name,
            confirmed,created_at) VALUES(?,?,?,?,?,1,?)''', (sid, amount, method, day, 'Synthetic ERP', '2026-11-20')).lastrowid

    def bind(self, pid, subid, amount, **kw):
        defaults = dict(source_namespace='synthetic-erp', source_invoice_id='invoice-'+str(subid),
                        source_operation_id='operation-'+str(pid), evidence_id='fixture-evidence')
        defaults.update(kw)
        pa.bind_payment(self.con, pid, subid, amount, **defaults)

    def details(self):
        with patch.object(money, 'payment_receipt_info', return_value={}), \
                patch.object(reconciliation, 'payment_state', return_value={}):
            return money.subscription_payment_details(self.con, 1)

    def assert_consistent(self):
        allocated = pa.allocation(self.con, 1)
        self.assertEqual(allocated['conflicts'], [])
        bal = money.balance(self.con, 1, date(2026, 11, 20))
        self.assertEqual(bal['covered'], allocated['covered'])
        self.assertEqual(allocated['paid_total'], sum(allocated['covered'].values())
                         + allocated['free_deposit'] + allocated['restricted_deposit'])
        for subid, rows in self.details().items():
            self.assertEqual(sum(r['amount'] for r in rows), bal['covered'][subid])
        return allocated

    def test_ordinary_fifo_with_earlier_obligations_is_unchanged(self):
        first = self.invoice(2700, '2026-09')
        second = self.invoice(9450)
        p1 = self.payment(8100, '2026-09-01')
        p2 = self.payment(9450)
        a = self.assert_consistent()
        self.assertEqual(a['sources'][first], [{'payment_id': p1, 'amount': 2700}])
        self.assertEqual(a['sources'][second], [{'payment_id': p1, 'amount': 5400}, {'payment_id': p2, 'amount': 4050}])
        self.assertEqual(a['deposit'], 5400)

    def test_legacy_negative_budget_keeps_original_fifo_sources(self):
        sub = self.invoice(5000)
        first = self.payment(4000)
        second = self.payment(3000, '2026-10-02')
        self.payment(-1000, method='refund')
        a = self.assert_consistent()
        self.assertEqual(a['sources'][sub], [{'payment_id': first, 'amount': 4000}, {'payment_id': second, 'amount': 1000}])
        self.assertEqual(a['deposit'], 1000)

    def test_unbound_negative_deposit_preserves_existing_semantics(self):
        sub = self.invoice(1000)
        self.payment(-500, method='refund')
        a = pa.allocation(self.con, 1)
        self.assertEqual((a['covered'][sub], a['deposit'], a['conflicts']), (0, -500, []))

    def test_bound_later_invoice_cannot_pay_earlier_unrelated_debt(self):
        old = self.invoice(8000, '2026-09', status='invoiced')
        target = self.invoice(9450)
        pid = self.payment(9450)
        self.bind(pid, target, 9450)
        a = self.assert_consistent()
        self.assertEqual(a['covered'], {old: 0, target: 9450})
        money._reconcile(self.con, 1)
        self.assertEqual([r[0] for r in self.con.execute('SELECT status FROM subscriptions ORDER BY id')], ['invoiced', 'paid'])

    def test_refund_debits_first_exact_payment_instead_of_truncating_last(self):
        old = self.invoice(2700, '2026-09')
        target = self.invoice(9450)
        p1 = self.payment(8100, '2026-09-01')
        p2 = self.payment(9450)
        self.bind(p1, old, 2700)
        self.bind(p1, target, 5400)
        self.bind(p2, target, 4050)
        debit = self.payment(-2700, '2026-11-06', method='refund')
        self.con.execute("UPDATE subscriptions SET status='cancelled' WHERE id=?", (old,))
        pa.register_refund(self.con, debit, p1, old, 2700)
        a = self.assert_consistent()
        self.assertEqual(a['sources'][target], [{'payment_id': p1, 'amount': 5400}, {'payment_id': p2, 'amount': 4050}])
        self.assertEqual(a['payment_remaining'], {p1: 0, p2: 5400})
        self.assertEqual((a['free_deposit'], a['restricted_deposit']), (0, 5400))

    def test_full_source_refund_and_cancel_preserve_other_purchase_and_original_payment(self):
        old = self.invoice(8100, '2026-09')
        target = self.invoice(9450)
        p1, p2 = self.payment(8100, '2026-09-03'), self.payment(9450)
        self.bind(p1, old, 8100)
        self.bind(p2, target, 9450)
        originals = [tuple(r) for r in self.con.execute('SELECT * FROM payments ORDER BY id')]
        debit = self.payment(-9450, '2026-11-06', method='refund')
        self.con.execute("UPDATE subscriptions SET status='cancelled' WHERE id=?", (target,))
        pa.register_refund(self.con, debit, p2, target, 9450)
        a = self.assert_consistent()
        self.assertEqual((a['covered'], a['deposit'], a['restricted_deposit']), ({old: 8100}, 0, 0))
        self.assertEqual([tuple(r) for r in self.con.execute('SELECT * FROM payments WHERE amount>0 ORDER BY id')], originals)

    def test_multiple_partial_refunds_reduce_only_their_original_source(self):
        sub = self.invoice(9450)
        pid = self.payment(9450)
        self.bind(pid, sub, 9450)
        for amount in (2000, 950):
            debit = self.payment(-amount, '2026-11-06', method='refund')
            self.con.execute('UPDATE subscriptions SET price=price-? WHERE id=?', (amount, sub))
            pa.register_refund(self.con, debit, pid, sub, amount)
            pa.register_refund(self.con, debit, pid, sub, amount)
        a = self.assert_consistent()
        self.assertEqual(a['sources'][sub], [{'payment_id': pid, 'amount': 6500}])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM erp_refund_allocations').fetchone()[0], 2)

    def test_one_refund_can_link_multiple_original_payments(self):
        sub = self.invoice(6000)
        p1, p2 = self.payment(2000), self.payment(4000)
        self.bind(p1, sub, 2000)
        self.bind(p2, sub, 4000)
        debit = self.payment(-3000, method='refund')
        self.con.execute('UPDATE subscriptions SET price=3000 WHERE id=?', (sub,))
        pa.register_refund(self.con, debit, p1, sub, 1000)
        self.assertTrue(pa.allocation(self.con, 1)['conflicts'])
        pa.register_refund(self.con, debit, p2, sub, 2000)
        a = self.assert_consistent()
        self.assertEqual(a['sources'][sub], [{'payment_id': p1, 'amount': 1000}, {'payment_id': p2, 'amount': 2000}])

    def test_retained_charge_consumes_only_net_source_after_historical_refund(self):
        sub = self.invoice(9450, status='cancelled')
        pid = self.payment(9450)
        self.bind(pid, sub, 9450)
        self.con.execute('INSERT INTO cancelled_service_charges VALUES(?,?,?,?,?,?,?)',
                         (sub, 1, '2026-11', 3000, 2, '2026-11-20', '2026-11-06'))
        debit = self.payment(-6450, method='refund')
        pa.register_refund(self.con, debit, pid, sub, 6450)
        a = self.assert_consistent()
        self.assertEqual(a['covered'], {-sub: 3000})
        self.assertEqual(a['sources'][-sub], [{'payment_id': pid, 'amount': 3000}])
        self.assertEqual(self.details(), {})

    def test_unspent_locked_money_does_not_pay_other_invoice_or_become_free_balance(self):
        sub = self.invoice(5000)
        other = self.invoice(5000, '2026-12')
        pid = self.payment(7000)
        self.bind(pid, sub, 5000)
        a = self.assert_consistent()
        self.assertEqual((a['covered'][other], a['free_deposit'], a['restricted_deposit']), (0, 0, 2000))
        self.assertEqual(money.balance(self.con, 1)['deposit'], 0)

    def test_retained_cancellation_can_reserve_released_source_funds_without_shuffle(self):
        sub = self.invoice(9450, status='cancelled')
        other = self.invoice(5000, '2026-12')
        pid = self.payment(9450)
        self.bind(pid, sub, 9450)
        self.con.execute('INSERT INTO cancelled_service_charges VALUES(?,?,?,?,?,?,?)',
                         (sub, 1, '2026-11', 3000, 2, '2026-11-20', '2026-11-06'))
        a = self.assert_consistent()
        self.assertEqual((a['covered'][-sub], a['covered'][other], a['restricted_deposit']), (3000, 0, 6450))
        self.payment(-6450, method='refund_pending')
        a = self.assert_consistent()
        self.assertEqual((a['covered'][-sub], a['covered'][other], a['restricted_deposit']), (3000, 0, 0))

    def test_missing_source_metadata_is_rejected(self):
        sub = self.invoice(5000)
        pid = self.payment(5000)
        for value in ('', '   ', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.bind(pid, sub, 5000, source_operation_id=value)

    def test_mixed_unbound_fifo_only_covers_unpaid_remainder(self):
        old = self.invoice(2000, '2026-09')
        sub = self.invoice(5000)
        bound, ordinary = self.payment(3000), self.payment(5000, '2026-10-02')
        self.bind(bound, sub, 3000)
        a = self.assert_consistent()
        self.assertEqual(a['sources'][old], [{'payment_id': ordinary, 'amount': 2000}])
        self.assertEqual(a['sources'][sub], [{'payment_id': bound, 'amount': 3000}, {'payment_id': ordinary, 'amount': 2000}])
        self.assertEqual(a['deposit'], 1000)

    def test_binding_replay_is_idempotent_and_changed_evidence_is_rejected(self):
        sub = self.invoice(5000)
        pid = self.payment(5000)
        self.bind(pid, sub, 5000)
        self.bind(pid, sub, 5000)
        with self.assertRaises(ValueError):
            self.bind(pid, sub, 5000, source_invoice_id='different')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM erp_payment_allocations').fetchone()[0], 1)

    def test_bind_and_refund_reject_cross_student_and_excess_without_writes(self):
        sub, other = self.invoice(5000), self.invoice(5000, sid=2)
        pid = self.payment(5000)
        for amount in (0, -1, 1.5, True, '5000', 6000):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                self.bind(pid, sub, amount)
        with self.assertRaises(ValueError):
            self.bind(pid, other, 5000)
        self.bind(pid, sub, 5000)
        debit = self.payment(-1000, method='refund')
        with self.assertRaises(ValueError):
            pa.register_refund(self.con, debit, pid, sub, 1001)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM erp_refund_allocations').fetchone()[0], 0)

    def test_malformed_source_links_are_readable_but_never_green_or_reconciled(self):
        sub = self.invoice(5000)
        pid = self.payment(5000)
        self.bind(pid, sub, 5000)
        self.con.execute('UPDATE erp_payment_allocations SET amount=6000')
        before = self.con.total_changes
        a = pa.allocation(self.con, 1)
        bal = money.balance(self.con, 1)
        self.assertTrue(a['conflicts'])
        self.assertTrue(bal['allocation_conflicts'])
        self.assertEqual((bal['covered'][sub], bal['deposit']), (0, 0))
        self.assertEqual(self.details()[sub], [])
        self.assertEqual(self.con.total_changes, before)
        with self.assertRaises(ValueError):
            money._reconcile(self.con, 1)

    def test_unattributed_debit_cannot_consume_source_bound_coverage(self):
        sub = self.invoice(5000)
        pid = self.payment(5000)
        self.bind(pid, sub, 5000)
        self.payment(-1000, method='refund')
        a = pa.allocation(self.con, 1)
        self.assertTrue(a['conflicts'])
        self.assertEqual(a['covered'][sub], 0)

    def test_cancelled_source_or_mismatched_refund_total_cannot_create_coverage(self):
        sub = self.invoice(5000)
        pid = self.payment(5000)
        self.bind(pid, sub, 5000)
        debit = self.payment(-2000, method='refund')
        pa.register_refund(self.con, debit, pid, sub, 1000)
        self.assertTrue(pa.allocation(self.con, 1)['conflicts'])

    def test_transfer_debit_cannot_be_relabelled_as_a_proved_historical_refund(self):
        sub = self.invoice(5000)
        pid = self.payment(5000)
        self.bind(pid, sub, 5000)
        debit = self.payment(-1000, method='balance')
        with self.assertRaises(ValueError):
            pa.register_refund(self.con, debit, pid, sub, 1000)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM erp_refund_allocations').fetchone()[0], 0)

    def test_bound_original_and_historical_return_cannot_be_cancelled_as_ordinary_rows(self):
        sub = self.invoice(5000)
        pid = self.payment(5000)
        self.bind(pid, sub, 5000)
        debit = self.payment(-1000, method='refund')
        pa.register_refund(self.con, debit, pid, sub, 1000)
        self.con.execute('UPDATE subscriptions SET price=4000 WHERE id=?', (sub,))
        before = [tuple(r) for r in self.con.execute('SELECT * FROM payments ORDER BY id')]
        for target in (pid, debit):
            with self.subTest(target=target), self.assertRaises(ValueError):
                money.cancel_payment(self.con, target, {'name': 'Synthetic Owner'})
        self.assertEqual([tuple(r) for r in self.con.execute('SELECT * FROM payments ORDER BY id')], before)
        self.assert_consistent()
        self.con.execute('UPDATE payments SET cancelled=1 WHERE id=?', (pid,))
        self.assertTrue(pa.allocation(self.con, 1)['conflicts'])

    def test_cancellation_sources_use_pinned_parts_and_keep_legacy_negative_blocker(self):
        self.invoice(3000, '2026-09')
        sub = self.invoice(5000)
        pid = self.payment(5000)
        self.bind(pid, sub, 5000)
        row = self.con.execute('SELECT * FROM subscriptions WHERE id=?', (sub,)).fetchone()
        self.assertEqual(subcancel._sources(self.con, row, amount=3000, retain=2000),
                         [{'payment_id': pid, 'amount': 3000, 'method': 'card', 'pk_payment_id': ''}])
        self.payment(-100, method='refund_pending')
        with self.assertRaises(subcancel.sv.ApiError):
            subcancel._sources(self.con, row)

    def test_restart_does_not_create_payments_from_source_bound_paid_status(self):
        sub = self.invoice(5000)
        pid = self.payment(5000)
        self.bind(pid, sub, 5000)
        before = [tuple(r) for r in self.con.execute('SELECT * FROM payments')]
        money.init(self.con)
        self.assertEqual([tuple(r) for r in self.con.execute('SELECT * FROM payments')], before)
        self.assert_consistent()

    def test_restart_does_not_manufacture_income_beside_unbound_real_payment(self):
        sub = self.invoice(9450)
        self.con.execute('UPDATE subscriptions SET ledger_managed=0 WHERE id=?', (sub,))
        real = self.payment(9450, '2026-10-28')
        before = [tuple(r) for r in self.con.execute('SELECT * FROM payments')]
        for _ in range(2):
            money.init(self.con)
        self.assertEqual([tuple(r) for r in self.con.execute('SELECT * FROM payments')], before)
        self.assertEqual(self.con.execute('SELECT subscription_id FROM payments WHERE id=?', (real,)).fetchone()[0], None)

    def test_genuine_legacy_account_without_any_income_is_bootstrapped_once(self):
        sub = self.invoice(8100, '2026-09')
        self.con.execute('UPDATE subscriptions SET ledger_managed=0,paid_at=? WHERE id=?', ('2026-08-28', sub))
        money.init(self.con)
        row = dict(self.con.execute('SELECT * FROM payments').fetchone())
        self.assertEqual((row['amount'], row['subscription_id'], row['paid_at']), (8100, sub, '2026-08-28'))
        self.assertEqual(row['comment'], 'Оплата до реестра платежей')
        money.init(self.con)
        self.assertEqual([dict(r) for r in self.con.execute('SELECT * FROM payments')], [row])

    def test_multiple_legacy_paid_invoices_bootstrap_from_same_initial_snapshot(self):
        first = self.invoice(8100, '2026-09')
        second = self.invoice(9450, '2026-10')
        self.con.execute('UPDATE subscriptions SET ledger_managed=0')
        money.init(self.con)
        self.assertEqual([tuple(r) for r in self.con.execute('SELECT subscription_id,amount FROM payments ORDER BY subscription_id')], [(first, 8100), (second, 9450)])
        before = [tuple(r) for r in self.con.execute('SELECT * FROM payments ORDER BY id')]
        money.init(self.con)
        self.assertEqual([tuple(r) for r in self.con.execute('SELECT * FROM payments ORDER BY id')], before)

    def test_existing_technical_marker_does_not_spawn_more_income_for_other_months(self):
        first = self.invoice(8100, '2026-09')
        second = self.invoice(9450, '2026-10')
        self.con.execute('UPDATE subscriptions SET ledger_managed=0')
        existing = self.payment(8100, '2026-09-01', method='unknown')
        self.con.execute("UPDATE payments SET subscription_id=?,comment='Оплата до реестра платежей',user_name='Система' WHERE id=?", (first, existing))
        before = [tuple(r) for r in self.con.execute('SELECT * FROM payments')]
        money.init(self.con)
        self.assertEqual([tuple(r) for r in self.con.execute('SELECT * FROM payments')], before)
        self.assertNotIn(second, [r[0] for r in self.con.execute('SELECT subscription_id FROM payments')])


if __name__ == '__main__':
    unittest.main()
