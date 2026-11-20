"""Explicit invoice balance use on isolated synthetic accounts, never providers."""
import sqlite3
import unittest
from datetime import date

from crm import balancecredit as credit, money, paymentallocation as pa


class InvoiceBalanceCreditTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(':memory:', isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.con.execute('PRAGMA foreign_keys=ON')
        self.con.executescript('''CREATE TABLE students(id INTEGER PRIMARY KEY);
            INSERT INTO students VALUES(1),(2);
            CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE subscriptions(id INTEGER PRIMARY KEY,student_id INTEGER,month TEXT,price INTEGER,
                status TEXT,paid_at TEXT,created_at TEXT,kind TEXT DEFAULT 'month',ledger_managed INTEGER DEFAULT 1);''')
        money.init(self.con)
        credit.init(self.con)
        self.addCleanup(self.con.close)
        self.user = {'id': 1, 'name': 'Synthetic Owner', 'role': 'admin'}

    def invoice(self, price=12150, month='2026-12', protected=True, status='invoiced', sid=1):
        subid = self.con.execute('''INSERT INTO subscriptions(student_id,month,price,status,created_at)
            VALUES(?,?,?,?,?)''', (sid, month, price, status, month+'-01')).lastrowid
        if protected:
            credit.protect_invoice(self.con, subid, self.user)
        return subid

    def payment(self, amount, subid=None, day='2026-11-20', name='Synthetic Owner', sid=1):
        return self.con.execute('''INSERT INTO payments(student_id,amount,method,paid_at,comment,
            user_name,confirmed,subscription_id,created_at) VALUES(?,?,'cash',?,'Synthetic payment',?,1,?,?)''',
            (sid, amount, day, name, subid, day)).lastrowid

    def fund(self, amount=2700, source='synthetic-existing-tournament', snapshot_id=None):
        return credit.add_existing_fund(self.con, 1, amount, source_ref=source,
            label='На следующий турнир', snapshot_id=snapshot_id, user=self.user)

    def balance(self):
        return money.balance(self.con, 1, date(2026, 11, 20))

    def apply(self, subid, request='synthetic-confirmed-request'):
        price = self.con.execute('SELECT price FROM subscriptions WHERE id=?', (subid,)).fetchone()[0]
        p = credit.preview(self.con, 1, price, True, subid)
        return credit.apply_to_invoice(self.con, subid, self.user, True, p['token'], request)

    def snapshot(self, payments, coverage, **kwargs):
        data = dict(amount=0, as_of_date='2026-11-20', cutoff_date='2026-11-02',
                    reviewed_payment_ids=payments, historical_coverage=coverage,
                    evidence_id='synthetic-reviewed-opening', user=self.user)
        data.update(kwargs)
        return credit.record_snapshot(self.con, 1, **data)

    def test_default_off_preserves_2700_and_leaves_full_invoice_payable(self):
        self.fund()
        sub = self.invoice()
        p = credit.preview(self.con, 1, 12150, False, sub)
        self.assertEqual((p['available_balance'], p['apply_amount'], p['payable'], p['remaining_balance']), (2700, 0, 12150, 2700))
        self.assertEqual(p['labels'], ['На следующий турнир'])
        self.assertEqual((self.balance()['deposit'], self.balance()['upcoming']), (2700, 12150))
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0], 0)

    def test_opt_in_once_makes_9450_payable_without_new_income_or_receipt(self):
        fid = self.fund()
        sub = self.invoice()
        result = self.apply(sub)
        self.assertEqual((result['apply_amount'], result['payable'], result['remaining_balance'], result['already_applied']), (2700, 9450, 0, 2700))
        a = pa.allocation(self.con, 1)
        self.assertEqual(a['sources'][sub], [])
        self.assertEqual(a['fund_sources'][sub][0]['fund_id'], fid)
        self.assertEqual(a['fund_sources'][sub][0]['amount'], 2700)
        self.assertEqual((self.balance()['deposit'], self.balance()['upcoming']), (0, 9450))
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0], 0)

    def test_repeat_request_and_reopen_cannot_spend_2700_twice(self):
        self.fund()
        sub = self.invoice()
        p = credit.preview(self.con, 1, 12150, True, sub)
        first = credit.apply_to_invoice(self.con, sub, self.user, True, p['token'], 'same-request')
        second = credit.apply_to_invoice(self.con, sub, self.user, True, p['token'], 'same-request')
        self.assertEqual(first, second)
        reopened = credit.preview(self.con, 1, 12150, True, sub)
        self.assertEqual((reopened['already_applied'], reopened['apply_amount'], reopened['payable']), (2700, 0, 9450))
        with self.assertRaises(ValueError):
            credit.apply_to_invoice(self.con, sub, self.user, True, reopened['token'], 'another-request')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM invoice_balance_uses').fetchone()[0], 1)

    def test_precreate_preview_token_survives_inserting_a_protected_invoice(self):
        self.fund()
        before = credit.preview(self.con, 1, 12150, True)
        sub = self.invoice()
        after = credit.preview(self.con, 1, 12150, True, sub)
        self.assertEqual(before['token'], after['token'])
        credit.apply_to_invoice(self.con, sub, self.user, True, before['token'], 'quote-to-invoice')
        self.assertEqual(self.balance()['upcoming'], 9450)

    def test_partial_balance_use_leaves_the_unused_1700_fully_available(self):
        self.fund()
        sub = self.invoice(1000)
        self.apply(sub)
        a = pa.allocation(self.con, 1)
        self.assertEqual((a['covered'][sub], a['free_deposit'], a['restricted_deposit']), (1000, 1700, 0))
        other = self.invoice(1000)
        self.assertEqual(pa.allocation(self.con, 1)['covered'][other], 0)

    def test_unlocked_cash_balance_is_used_without_erp_whole_payment_lock(self):
        pid = self.payment(2700)
        sub = self.invoice(1000)
        self.apply(sub)
        a = pa.allocation(self.con, 1)
        self.assertEqual(a['sources'][sub], [{'payment_id': pid, 'amount': 1000}])
        self.assertEqual((a['payment_remaining'][pid], a['cash_free_deposit'], a['restricted_deposit']), (1700, 1700, 0))

    def test_new_direct_cash_covers_chosen_invoice_without_spending_old_fund(self):
        self.fund()
        sub = self.invoice()
        pid = self.payment(5000, sub)
        a = pa.allocation(self.con, 1)
        self.assertEqual(a['sources'][sub], [{'payment_id': pid, 'amount': 5000}])
        self.assertEqual((a['covered'][sub], a['free_deposit']), (5000, 2700))
        self.assertEqual(credit.preview(self.con, 1, 12150, False, sub)['payable'], 7150)

    def test_direct_payment_after_explicit_balance_use_only_covers_remaining_9450(self):
        self.fund()
        sub = self.invoice()
        self.apply(sub)
        self.payment(9450, sub)
        money._reconcile(self.con, 1)
        self.assertEqual((self.balance()['covered'][sub], self.balance()['deposit'], self.balance()['upcoming']), (12150, 0, 0))
        self.assertEqual(self.con.execute('SELECT SUM(amount) FROM payments').fetchone()[0], 9450)
        self.assertEqual(self.con.execute('SELECT status FROM subscriptions WHERE id=?', (sub,)).fetchone()[0], 'paid')

    def test_direct_payment_reserves_chosen_invoice_before_unrelated_old_fifo(self):
        old = self.invoice(1000, '2026-10', protected=False)
        chosen = self.invoice(2000)
        self.payment(500, chosen)
        a = pa.allocation(self.con, 1)
        self.assertEqual(a['covered'], {old: 0, chosen: 500})

    def test_direct_overpayment_remains_free_without_automatically_paying_older_legacy_debt(self):
        old = self.invoice(1000, '2026-10', protected=False)
        chosen = self.invoice(500)
        pid = self.payment(2000, chosen)
        a = pa.allocation(self.con, 1)
        self.assertEqual((a['covered'][old], a['covered'][chosen], a['free_deposit']), (0, 500, 1500))
        self.assertEqual(a['payment_remaining'][pid], 1500)

    def test_saved_bundle_intent_caps_current_due_and_retains_canceled_part_as_free_cash(self):
        old = self.invoice(1000, '2026-10', protected=False)
        cancelled, covered, active = self.invoice(1000), self.invoice(1000), self.invoice(1000)
        prior = self.payment(1000, covered)
        self.con.execute("UPDATE subscriptions SET status='cancelled' WHERE id=?", (cancelled,))
        incoming = self.payment(3000, day='2026-11-19')  # late callback may report an older provider timestamp
        with self.assertRaises(ValueError):
            credit.target_payment(self.con, incoming, cancelled, 1000)
        for sub in (cancelled, covered, active):
            credit.target_payment(self.con, incoming, sub, 1000, allow_cancelled=True)
            credit.target_payment(self.con, incoming, sub, 1000, allow_cancelled=True)
        a = pa.allocation(self.con, 1)
        self.assertEqual(a['conflicts'], [])
        self.assertEqual((a['covered'][old], a['covered'][covered], a['covered'][active]), (0, 1000, 1000))
        self.assertEqual(a['sources'][covered], [{'payment_id': prior, 'amount': 1000}])
        self.assertEqual((a['payment_remaining'][incoming], a['free_deposit']), (2000, 2000))
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0], 2)

    def test_singleton_payment_to_canceled_protected_invoice_is_free_instead_of_old_fifo(self):
        old = self.invoice(1000, '2026-10', protected=False)
        cancelled = self.invoice(1000)
        self.con.execute("UPDATE subscriptions SET status='cancelled' WHERE id=?", (cancelled,))
        self.payment(1000, cancelled)
        a = pa.allocation(self.con, 1)
        self.assertEqual((a['covered'][old], a['free_deposit']), (0, 1000))

    def test_zero_checkout_intent_preserves_fully_covered_legacy_source_and_keeps_new_cash_free(self):
        first = self.invoice(1000, '2026-11', protected=False)
        other = self.invoice(1000, '2026-12', protected=False)
        prior = self.payment(1000)
        before = pa.allocation(self.con, 1)
        self.assertEqual((before['covered'][first], before['covered'][other]), (1000, 0))
        current_due = max(0, 1000 - before['covered'][first])
        incoming = self.payment(1000)
        with self.assertRaises(ValueError):
            credit.target_payment(self.con, incoming, first, current_due)
        credit.target_payment(self.con, incoming, first, current_due, allow_zero=True)
        after = pa.allocation(self.con, 1)
        self.assertEqual((after['covered'][first], after['covered'][other], after['free_deposit']), (1000, 0, 1000))
        self.assertEqual(after['sources'][first], [{'payment_id': prior, 'amount': 1000}])
        self.assertEqual(after['payment_remaining'][incoming], 1000)

    def test_zero_canceled_checkout_intent_remains_dedicated_cash_without_creating_coverage(self):
        old = self.invoice(1000, '2026-10', protected=False)
        cancelled = self.invoice(1000, protected=False)
        self.con.execute("UPDATE subscriptions SET status='cancelled' WHERE id=?", (cancelled,))
        incoming = self.payment(1000)
        credit.target_payment(self.con, incoming, cancelled, 0, allow_cancelled=True, allow_zero=True)
        a = pa.allocation(self.con, 1)
        self.assertEqual((a['covered'][old], a['free_deposit']), (0, 1000))
        self.assertEqual(a['conflicts'], [])

    def test_bundle_target_parts_are_exact_and_replay_does_not_duplicate_payment(self):
        self.fund()
        first, second = self.invoice(1000), self.invoice(2000)
        pid = self.payment(3000)
        for sub, amount in ((first, 1000), (second, 2000)):
            credit.target_payment(self.con, pid, sub, amount)
            credit.target_payment(self.con, pid, sub, amount)
        a = pa.allocation(self.con, 1)
        self.assertEqual((a['covered'][first], a['covered'][second], a['free_deposit']), (1000, 2000, 2700))
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0], 1)
        with self.assertRaises(ValueError):
            credit.target_payment(self.con, pid, first, 1100)

    def test_legacy_fifo_remains_for_old_invoices_but_not_for_new_explicit_invoices(self):
        old = self.invoice(1000, '2026-10', protected=False)
        new = self.invoice(1000)
        self.payment(2000)
        a = pa.allocation(self.con, 1)
        self.assertEqual((a['covered'][old], a['covered'][new], a['free_deposit']), (1000, 0, 1000))

    def test_applying_to_existing_partial_legacy_invoice_preserves_its_cash_parts(self):
        old = self.invoice(1000, '2026-10', protected=False)
        chosen = self.invoice(1000, '2026-11', protected=False)
        pid = self.payment(1500)
        self.fund()
        before = pa.allocation(self.con, 1)
        self.assertEqual((before['covered'][old], before['covered'][chosen]), (1000, 500))
        self.apply(chosen)
        after = pa.allocation(self.con, 1)
        self.assertTrue(credit.is_protected(self.con, chosen))
        self.assertEqual((after['covered'][old], after['covered'][chosen], after['free_deposit']), (1000, 1000, 2200))
        self.assertEqual(after['sources'][chosen], [{'payment_id': pid, 'amount': 500}])

    def test_snapshot_seals_old_payments_keeps_past_paid_and_excludes_december(self):
        old = self.invoice(10000, '2026-10', protected=False, status='paid')
        december = self.invoice(status='paid')
        pid = self.payment(30000, day='2026-09-28', name='ERP')
        original = tuple(self.con.execute('SELECT * FROM payments WHERE id=?', (pid,)).fetchone())
        snapshot = self.snapshot([pid], {old: 10000})
        fund = self.fund(snapshot_id=snapshot)
        money._reconcile(self.con, 1)
        a = pa.allocation(self.con, 1)
        self.assertEqual((a['covered'][old], a['covered'][december], a['free_deposit']), (10000, 0, 2700))
        self.assertEqual((self.balance()['upcoming'], self.balance()['debt']), (12150, 0))
        self.assertEqual(a['historical_sources'][old][0]['snapshot_id'], snapshot)
        self.assertEqual(a['sources'][old], [])
        self.assertEqual(a['fund_remaining'][fund], 2700)
        self.assertEqual(tuple(self.con.execute('SELECT * FROM payments WHERE id=?', (pid,)).fetchone()), original)
        self.assertEqual(self.con.execute('SELECT status FROM subscriptions WHERE id=?', (old,)).fetchone()[0], 'paid')
        self.assertEqual(self.snapshot([pid], {old: 10000}), snapshot)
        self.assertEqual(self.fund(snapshot_id=snapshot), fund)
        self.apply(december)
        self.assertEqual((self.balance()['upcoming'], self.balance()['deposit']), (9450, 0))

    def test_existing_fund_once_same_ref_cannot_be_changed_or_added_as_cash(self):
        first = self.fund()
        self.assertEqual(self.fund(), first)
        with self.assertRaises(ValueError):
            self.fund(3000)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM balance_credit_funds').fetchone()[0], 1)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0], 0)

    def test_new_historical_erp_import_after_snapshot_cannot_reopen_spendable_old_money(self):
        old = self.invoice(10000, '2026-10', protected=False, status='paid')
        pid = self.payment(10000, day='2026-09-28', name='ERP')
        snapshot = self.snapshot([pid], {old: 10000})
        self.fund(snapshot_id=snapshot)
        extra = self.payment(5000, day='2026-10-22', name='ERP')
        sub = self.invoice()
        a = pa.allocation(self.con, 1)
        self.assertIn(extra, a['quarantined_historical_payment_ids'])
        self.assertEqual((a['free_deposit'], a['covered'][sub]), (2700, 0))

    def test_cancelled_future_invoice_releases_fund_without_new_cash_or_erasing_audit(self):
        self.fund()
        sub = self.invoice(1000)
        self.apply(sub)
        self.con.execute("UPDATE subscriptions SET status='cancelled' WHERE id=?", (sub,))
        a = pa.allocation(self.con, 1)
        self.assertEqual(a['free_deposit'], 2700)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM invoice_balance_uses').fetchone()[0], 1)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0], 0)

    def test_unattributed_refund_cannot_silently_consume_non_cash_existing_credit(self):
        self.fund()
        self.payment(-1000)
        a = pa.allocation(self.con, 1)
        self.assertTrue(a['conflicts'])
        with self.assertRaises(ValueError):
            money._reconcile(self.con, 1)

    def test_strict_erp_source_remains_locked_and_is_not_spendable_generic_balance(self):
        old = self.invoice(3000, '2026-11', protected=False)
        pid = self.payment(10000)
        pa.bind_payment(self.con, pid, old, 3000, source_namespace='synthetic', source_invoice_id='old-source',
                        source_operation_id='old-operation', evidence_id='old-proof')
        self.fund()
        sub = self.invoice(1000)
        self.assertEqual(credit.preview(self.con, 1, 1000, True, sub)['available_balance'], 2700)
        self.apply(sub)
        a = pa.allocation(self.con, 1)
        self.assertEqual((a['restricted_deposit'], a['free_deposit']), (7000, 1700))

    def test_stale_preview_or_reused_request_for_other_invoice_does_not_spend_twice(self):
        self.fund()
        sub = self.invoice(1000)
        p = credit.preview(self.con, 1, 1000, True, sub)
        self.fund(100, source='independent-existing-credit')
        with self.assertRaises(ValueError):
            credit.apply_to_invoice(self.con, sub, self.user, True, p['token'], 'stale-request')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM invoice_balance_uses').fetchone()[0], 0)
        self.apply(sub, 'used-request')
        other = self.invoice(1000)
        next_preview = credit.preview(self.con, 1, 1000, True, other)
        with self.assertRaises(ValueError):
            credit.apply_to_invoice(self.con, other, self.user, True, next_preview['token'], 'used-request')

    def test_snapshot_validation_rejects_cross_card_future_coverage_and_stale_source(self):
        old = self.invoice(1000, '2026-10', protected=False, status='paid')
        future = self.invoice()
        pid = self.payment(1000, name='ERP')
        other = self.payment(1000, sid=2)
        for ids, coverage in (([other], {old: 1000}), ([pid], {future: 12150})):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.snapshot(ids, coverage)
        fingerprint = credit.snapshot_fingerprint(self.con, 1, [pid], {old: 1000})
        self.con.execute('UPDATE payments SET amount=900 WHERE id=?', (pid,))
        with self.assertRaises(ValueError):
            self.snapshot([pid], {old: 1000}, expected_fingerprint=fingerprint)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM balance_account_snapshots').fetchone()[0], 0)

    def test_initialization_and_balance_application_do_not_commit_callers_transaction(self):
        self.con.execute('BEGIN')
        self.fund()
        sub = self.invoice(1000)
        credit.init(self.con)
        pa.init(self.con)
        self.assertTrue(self.con.in_transaction)
        self.apply(sub)
        self.assertTrue(self.con.in_transaction)
        self.con.rollback()
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM balance_credit_funds').fetchone()[0], 0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM invoice_balance_uses').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
