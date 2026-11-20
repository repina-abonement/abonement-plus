"""Independent guarded-apply tests on synthetic SQLite data only.

The native IDs, receipts and old charge are invented. The reviewed ERP key
selects the permitted code path; no working client's transaction rows are kept.
"""
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from crm import erpbalancerecovery as recovery, money, services
from tests import test_erp_mamedov_review as fixture


class ReviewedBalanceApplyTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.MamedovReviewTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.con, self.sid, self.target = self.fixture.con, self.fixture.sid, self.fixture.target
        self.path = Path(self.fixture.temp.name)/'fixture.sqlite3'
        self.con.execute("UPDATE students SET erp_id='88202' WHERE id=?", (self.sid,))
        dates = '[{"group_id":'+str(self.fixture.gid)+',"dates":["2026-12-03","2026-12-07","2026-12-10","2026-12-14","2026-12-17","2026-12-21","2026-12-24"]}]'
        self.con.execute('UPDATE subscriptions SET lesson_schedule=? WHERE id=?', (dates, self.target))
        self.con.execute("INSERT INTO attendance(student_id,group_id,date,status,note) VALUES(?,?,'2026-11-16','absent','Synthetic preserved absence')", (self.sid, self.fixture.gid))
        self.case = dict(erp_id='88202', student_id=self.sid, subscription_id=self.target,
            as_of_date='2026-11-20', cutoff_date='2026-11-20', base_amount=0, existing_credit=0,
            credit_label='Аванса не было', credit_ref='synthetic-reviewed-88202:no-credit',
            evidence_id='synthetic-reviewed-88202:zero-baseline',
            past_source_note='Synthetic past coverage retained; source invoice identity remains unverified',
            reviewed_fingerprint=recovery.financial_fingerprint(self.con, self.sid, '88202'),
            reviewed_terms_fingerprint=recovery._hash(recovery._purchase_terms(
                self.con.execute('SELECT * FROM subscriptions WHERE id=?', (self.target,)).fetchone())))
        self.enterContext(patch.dict(recovery.CASES, {'88202': self.case}))
        # The fixture already forbids PayKeeper/OFD/checkout entry points.
        # Block transport as well, including any accidentally introduced bank
        # or messaging request not covered by an individual provider mock.
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('All network actions forbidden')))
        self.enterContext(patch('socket.create_connection', side_effect=AssertionError('All network actions forbidden')))

    def native(self):
        queries = {
            'payments': ('SELECT * FROM payments WHERE student_id=? ORDER BY id', (self.sid,)),
            'other_purchases': ('SELECT * FROM subscriptions WHERE student_id=? AND id!=? ORDER BY id', (self.sid, self.target)),
            'attendance': ('SELECT * FROM attendance WHERE student_id=? ORDER BY id', (self.sid,)),
            'single_visits': ('SELECT * FROM single_visits WHERE student_id=? ORDER BY id', (self.sid,)),
            'enrollments': ('SELECT * FROM enrollments WHERE student_id=? ORDER BY id', (self.sid,)),
            'pay_links': ('SELECT * FROM pay_links WHERE student_id=? ORDER BY id', (self.sid,)),
            'link_parts': ('SELECT a.* FROM pay_link_subscriptions a JOIN pay_links p ON p.id=a.link_id WHERE p.student_id=? ORDER BY a.link_id,a.subscription_id', (self.sid,)),
        }
        return {key: [tuple(row) for row in self.con.execute(sql, args)]
                for key, (sql, args) in queries.items()}

    def metadata(self, con=None):
        con = con or self.con
        return {table: [tuple(row) for row in con.execute('SELECT * FROM '+table+' ORDER BY 1')]
                for table in ('invoice_funding_policies', 'invoice_payment_targets',
                    'invoice_balance_uses', 'balance_account_snapshots', 'balance_credit_funds')}

    def apply(self):
        self.con.execute('BEGIN IMMEDIATE')
        try:
            result = recovery.apply_reviewed(self.con, '88202', self.fixture.owner)
            self.assertTrue(self.con.in_transaction, 'Core must not commit the web adapter transaction')
            self.con.commit()
            return result
        except Exception:
            self.con.rollback()
            raise

    def assert_rejected_without_writes(self, erp='88202', fragment=None):
        before = self.con.serialize()
        self.con.execute('BEGIN IMMEDIATE')
        try:
            with self.assertRaises(ValueError) as error:
                recovery.apply_reviewed(self.con, erp, self.fixture.owner)
            if fragment:
                self.assertIn(fragment, str(error.exception))
            self.assertTrue(self.con.in_transaction)
        finally:
            self.con.rollback()
        self.assertEqual(self.con.serialize(), before)

    def test_reviewed_zero_preserves_all_original_money_history_links_and_purchase_terms(self):
        original = self.native()
        terms = recovery._purchase_terms(self.con.execute('SELECT * FROM subscriptions WHERE id=?', (self.target,)).fetchone())
        before = money.balance(self.con, self.sid)
        self.assertEqual((before['covered'][self.target], before['deposit']), (9450, 12115))
        result = self.apply()
        self.assertEqual(self.native(), original)
        self.assertEqual(recovery._purchase_terms(self.con.execute('SELECT * FROM subscriptions WHERE id=?', (self.target,)).fetchone()), terms)
        after = money.balance(self.con, self.sid)
        self.assertEqual((after['covered'][self.target], after['upcoming'], after['deposit']), (0, 9450, 0))
        self.assertEqual(after['covered'][self.fixture.previous], before['covered'][self.fixture.previous])
        row = self.con.execute('SELECT status,paid_at,price,lessons FROM subscriptions WHERE id=?', (self.target,)).fetchone()
        self.assertEqual(tuple(row), ('invoiced', None, 9450, 7))
        self.assertEqual(result['after']['invoice']['payable'], 9450)
        self.assertTrue(result['past_coverage_preserved'])
        self.assertTrue(result['payments_preserved'])
        self.assertTrue(result['attendance_preserved'])
        self.assertTrue(result['purchase_terms_preserved'])
        self.assertFalse(result['external_actions'])
        meta = self.metadata()
        self.assertEqual(len(meta['balance_account_snapshots']), 1)
        self.assertEqual(len(meta['invoice_funding_policies']), 1)
        self.assertEqual(meta['balance_credit_funds'], [])
        self.assertEqual(meta['invoice_balance_uses'], [])
        self.assertEqual(meta['invoice_payment_targets'], [])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments WHERE student_id=?', (self.sid,)).fetchone()[0], 3)

    def test_restart_bootstrap_does_not_recreate_money_for_unbound_legacy_coverage(self):
        self.con.execute('UPDATE subscriptions SET ledger_managed=0,erp_full_managed=0 WHERE id=?',
            (self.fixture.previous,))
        self.apply()
        original,metadata=self.native(),self.metadata()
        for _ in range(3):
            money.init(self.con)
            self.assertEqual(self.native(),original)
            self.assertEqual(self.metadata(),metadata)
            current=money.balance(self.con,self.sid)
            self.assertEqual((current['covered'][self.target],current['upcoming'],current['deposit']),
                (0,9450,0))
        reopened=sqlite3.connect(self.path,isolation_level=None)
        reopened.row_factory=sqlite3.Row
        try:
            money.init(reopened)
            self.assertEqual(self.metadata(reopened),metadata)
            self.assertEqual([tuple(row) for row in reopened.execute('SELECT * FROM payments WHERE student_id=? ORDER BY id',
                (self.sid,))],original['payments'])
            self.assertEqual(money.balance(reopened,self.sid)['deposit'],0)
        finally:
            reopened.close()

    def test_core_does_not_commit_and_caller_can_rollback_every_metadata_and_status_change(self):
        before = self.con.serialize()
        self.con.execute('BEGIN IMMEDIATE')
        try:
            recovery.apply_reviewed(self.con, '88202', self.fixture.owner)
            self.assertTrue(self.con.in_transaction)
            self.assertEqual(self.con.execute('SELECT COUNT(*) FROM balance_account_snapshots').fetchone()[0], 1)
            with sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True) as observer:
                self.assertEqual(observer.execute('SELECT status FROM subscriptions WHERE id=?', (self.target,)).fetchone()[0], 'paid')
                self.assertEqual(observer.execute('SELECT COUNT(*) FROM balance_account_snapshots').fetchone()[0], 0)
        finally:
            self.con.rollback()
        self.assertEqual(self.con.serialize(), before)

    def test_second_core_call_and_fresh_connection_refuse_stale_review_without_duplicate_metadata(self):
        self.apply()
        before, metadata = self.con.serialize(), self.metadata()
        self.assert_rejected_without_writes(fragment='изменились')
        self.assertEqual(self.metadata(), metadata)
        fresh = sqlite3.connect(str(self.path), isolation_level=None)
        fresh.row_factory = sqlite3.Row
        try:
            fresh.execute('BEGIN IMMEDIATE')
            with self.assertRaisesRegex(ValueError, 'изменились'):
                recovery.apply_reviewed(fresh, '88202', self.fixture.owner)
            fresh.rollback()
            self.assertEqual(self.metadata(fresh), metadata)
        finally:
            fresh.close()
        self.assertEqual(self.con.serialize(), before)

    def test_changed_payment_amount_date_comment_or_confirmation_rejects_before_application(self):
        pid = self.fixture.pids[-1]
        for field, value in (('amount', 13501), ('paid_at', '2026-11-21'),
                             ('comment', 'Synthetic later correction'), ('confirmed', 0)):
            original = self.con.execute('SELECT '+field+' FROM payments WHERE id=?', (pid,)).fetchone()[0]
            self.con.execute('UPDATE payments SET '+field+'=? WHERE id=?', (value, pid))
            with self.subTest(field=field):
                self.assert_rejected_without_writes(fragment='изменились')
            self.con.execute('UPDATE payments SET '+field+'=? WHERE id=?', (original, pid))

    def test_full_target_terms_hash_blocks_schedule_changes_absent_from_financial_hash(self):
        self.con.execute("UPDATE subscriptions SET lesson_schedule='[]' WHERE id=?", (self.target,))
        self.assertEqual(recovery.financial_fingerprint(self.con, self.sid, '88202'), self.case['reviewed_fingerprint'])
        self.assert_rejected_without_writes(fragment='Условия декабрьской покупки')

    def test_wrong_native_student_and_duplicate_erp_identity_cannot_select_a_different_card(self):
        wrong = dict(self.case, student_id=self.sid+10000)
        with patch.dict(recovery.CASES, {'88202': wrong}):
            self.assert_rejected_without_writes(fragment='Карточка CRM')
        other = services.create_student(self.con, {'name': 'Synthetic Other Child',
            'parent_name': 'Synthetic Other Parent', 'phone': '+79000000878'}, self.fixture.owner)
        self.con.execute("UPDATE students SET erp_id='88202' WHERE id=?", (other,))
        self.assert_rejected_without_writes(fragment='единственной карточкой')

    def test_other_erp_cases_including_117559_remain_read_only(self):
        with patch.object(recovery, 'preview', side_effect=AssertionError('Wrong ERP must fail before preview')):
            for erp in ('117559', 'synthetic-unknown', ''):
                with self.subTest(erp=erp):
                    self.assert_rejected_without_writes(erp, 'рабочее применение не подготовлено')

    def test_caller_rollback_restores_source_rows_after_a_mid_apply_preservation_failure(self):
        original, observed = recovery._record_reviewed, {}
        def corrupt_only_working_transaction(con, *args):
            result = original(con, *args)
            if con is self.con:
                observed['metadata_count'] = con.execute('SELECT COUNT(*) FROM balance_account_snapshots').fetchone()[0]
                con.execute("UPDATE payments SET comment='Synthetic injected failure' WHERE id=?", (self.fixture.pids[0],))
            return result
        before = self.con.serialize()
        with patch.object(recovery, '_record_reviewed', side_effect=corrupt_only_working_transaction):
            self.assert_rejected_without_writes(fragment='изменены исходные записи')
        self.assertEqual(observed['metadata_count'], 1)
        self.assertEqual(self.con.serialize(), before)
        self.assertEqual(self.metadata()['balance_account_snapshots'], [])

    def test_retained_past_coverage_cannot_be_dropped_by_the_working_apply(self):
        self.con.execute("UPDATE subscriptions SET status='cancelled' WHERE id=?", (self.fixture.previous,))
        self.con.execute('''INSERT INTO cancelled_service_charges(subscription_id,student_id,month,
            amount,lessons,created_at,cutoff) VALUES(?,?,'2026-11',5985,1,'2026-11-20','2026-11-20')''',
            (self.fixture.previous, self.sid))
        changed = dict(self.case, reviewed_fingerprint=recovery.financial_fingerprint(self.con, self.sid, '88202'))
        with patch.dict(recovery.CASES, {'88202': changed}):
            self.assert_rejected_without_writes(fragment='покрытие прошлых покупок')


if __name__ == '__main__':
    unittest.main()
