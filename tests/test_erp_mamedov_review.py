"""CRM-175 review on synthetic local data, never a working client export."""
from contextlib import ExitStack, redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crm import db, erpmamedovreview as review, erpbalancerecovery as recovery
from crm import fiscal, money, paymentallocation, paylinks, services


class MamedovReviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-mamedov-review-')
        self.addCleanup(self.temp.cleanup)
        self.con = db.connect(str(Path(self.temp.name)/'fixture.sqlite3'))
        self.addCleanup(self.con.close)
        with redirect_stdout(StringIO()):
            db.init_db(self.con)
        self.owner = {'id': 1, 'name': 'Synthetic Owner', 'role': 'admin'}
        branch = self.con.execute("INSERT INTO branches(name) VALUES('Небо')").lastrowid
        self.gid = self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time) VALUES(?,'Synthetic Mon/Thu','3,0','19:45')", (branch,)).lastrowid
        self.sid = services.create_student(self.con, {'name': 'Мамедов Даниил Синтетический',
            'parent_name': 'Synthetic Parent', 'phone': '+79000000876',
            'group_id': self.gid, 'funnel': 'members'}, self.owner)
        self.con.execute("UPDATE students SET erp_id='synthetic-mamedov-case' WHERE id=?", (self.sid,))
        self.eid = self.con.execute('SELECT id FROM enrollments WHERE student_id=?', (self.sid,)).fetchone()[0]
        self.previous = self.invoice('2026-11', 5985, 1)
        self.target = self.invoice('2026-12', 9450, 7)
        # Arbitrary synthetic gross receipts: old charge5985 leaves the
        # reported parts4015/4050/1385 and free12115. Parts are never income.
        self.pids = [self.payment(amount, day) for amount, day in
            ((10000, '2026-10-01'), (4050, '2026-10-02'), (13500, '2026-10-03'))]
        self.link = self.link_row('open', self.target, 9450)
        self.con.execute("INSERT INTO attendance(student_id,group_id,date,status,note) VALUES(?,?,'2026-11-19','present','Keep synthetic mark')", (self.sid, self.gid))
        self.con.execute('CREATE TABLE synthetic_full_copy_sentinel(value TEXT)')
        self.con.execute("INSERT INTO synthetic_full_copy_sentinel VALUES('Unrelated table must survive full cloning')")
        stack = self.enterContext(ExitStack())
        for module, function in ((fiscal, 'pk_call'), (fiscal, 'ofd_get'), (fiscal, 'run'),
                                 (paylinks, 'create'), (paylinks, 'create_bundle'), (paylinks, 'receipt_link')):
            if hasattr(module, function):
                stack.enter_context(patch.object(module, function, side_effect=AssertionError('External or checkout action forbidden')))

    def invoice(self, month, amount, lessons, eid=None):
        return self.con.execute('''INSERT INTO subscriptions(student_id,enrollment_id,month,kind,
            tariff,lessons,per_lesson,price,status,created_at,paid_at,ledger_managed)
            VALUES(?,?,?,'month','fixed',?,1350,?,'paid','2026-10-20','2026-10-20',1)''',
            (self.sid, eid or self.eid, month, lessons, amount)).lastrowid

    def payment(self, amount, day):
        return self.con.execute('''INSERT INTO payments(student_id,amount,method,paid_at,comment,
            user_name,confirmed,created_at) VALUES(?,?,'cash',?,'Synthetic source','ERP',1,?)''',
            (self.sid, amount, day, day)).lastrowid

    def link_row(self, status, subid, amount):
        key = 'synthetic-link-'+str(self.con.execute('SELECT COUNT(*) FROM pay_links').fetchone()[0])
        return self.con.execute('''INSERT INTO pay_links(student_id,amount,orderid,url,status,
            created_at,subscription_id,full_payment) VALUES(?,?,?, ?,?, ?,?,1)''',
            (self.sid, amount, key, 'https://example.invalid/pay/'+key, status,
             services.now_iso(), subid)).lastrowid

    def observed(self):
        return money.balance(self.con, self.sid)

    def run_review(self):
        before, changes = self.con.serialize(), self.con.total_changes
        result = review.review(self.con)
        self.assertEqual(self.con.serialize(), before)
        self.assertEqual(self.con.total_changes, changes)
        self.assertTrue(result['read_only'])
        self.assertTrue(result['snapshot_on_copy'])
        self.assertEqual(result['copy_scope'], 'full_server_database')
        self.assertFalse(result['can_apply'])
        self.assertFalse(result['production_apply_available'])
        self.assertFalse(result['production_apply'])
        self.assertFalse(result['independent_source_verified'])
        return result

    def test_unique_reported_shape_projects_zero_only_on_full_copy_and_captures_sources_first(self):
        original = recovery._preview_snapshot
        def preview(snapshot, *args, **kwargs):
            self.assertIsNot(snapshot, self.con)
            self.assertEqual(snapshot.execute('SELECT value FROM synthetic_full_copy_sentinel').fetchone()[0],
                             'Unrelated table must survive full cloning')
            return original(snapshot, *args, **kwargs)
        before_cases = dict(recovery.CASES)
        with patch.object(recovery, '_preview_snapshot', side_effect=preview):
            first, repeat = self.run_review(), self.run_review()
        self.assertTrue(first['plan_ready'], first['issues'])
        self.assertEqual(first['student']['id'], self.sid)
        self.assertEqual(first['erp_id'], 'synthetic-mamedov-case')
        self.assertEqual(first['observed_shape'], 'reported_fifo_shape')
        self.assertTrue(first['source_context_captured_before_projection'])
        self.assertEqual(first['source_context']['balance']['covered'][self.target], 9450)
        self.assertEqual(first['source_context']['balance']['deposit'], 12115)
        self.assertEqual([part['amount'] for part in first['invoice']['sources']], [4015, 4050, 1385])
        self.assertEqual([part['payment_id'] for part in first['invoice']['sources']], self.pids)
        self.assertEqual([part['payment_amount'] for part in first['invoice']['sources']], [10000, 4050, 13500])
        self.assertFalse(first['invoice']['source_parts_are_income'])
        self.assertEqual(first['invoice']['payment_remaining'][self.pids[-1]], 12115)
        self.assertFalse(first['payment_links'][0]['inspection']['valid'])
        self.assertIn('оплачен', first['payment_links'][0]['inspection']['reason'])
        projection = first['projection']
        for variant in ('without_balance', 'with_balance'):
            actual = projection[variant]
            self.assertEqual((actual['invoice']['price'], actual['invoice']['lessons'],
                actual['invoice']['covered'], actual['invoice']['payable'], actual['balance']['deposit']),
                (9450, 7, 0, 9450, 0))
            self.assertEqual(actual['funds'], [])
            self.assertEqual(actual['payments_count'], 3)
        self.assertTrue(projection['provisional'])
        self.assertFalse(projection['independent_source_verified'])
        self.assertIn('2026-11-20', projection['source_note'])
        self.assertEqual(first['projection']['without_balance'], repeat['projection']['without_balance'])
        self.assertEqual(first['projection']['with_balance'], repeat['projection']['with_balance'])
        self.assertEqual(recovery.CASES, before_cases)

    def test_already_correct_unpaid_invoice_and_zero_balance_stay_correct(self):
        self.con.execute('DELETE FROM payments WHERE student_id=?', (self.sid,))
        self.con.execute("UPDATE subscriptions SET status='cancelled' WHERE id=?", (self.previous,))
        self.con.execute("UPDATE subscriptions SET status='invoiced',paid_at=NULL WHERE id=?", (self.target,))
        result = self.run_review()
        self.assertEqual(result['observed_shape'], 'already_correct')
        self.assertTrue(result['plan_ready'], result['issues'])
        self.assertTrue(result['already_correct'])
        self.assertFalse(result['correction_required'])
        self.assertIsNone(result['projection'])
        self.assertEqual(result['before'], result['after'])
        self.assertEqual(result['after']['invoice']['payable'], 9450)
        self.assertEqual(result['after']['balance']['deposit'], 0)

    def test_missing_or_duplicate_student_tokens_never_select_first_card(self):
        self.con.execute("UPDATE students SET name='Мамедович Даниил Синтетический' WHERE id=?", (self.sid,))
        missing = self.run_review()
        self.assertEqual(missing['selection']['student_count'], 0)
        self.assertIsNone(missing['source_context'])
        self.con.execute("UPDATE students SET name='Даниил Мамедов Синтетический' WHERE id=?", (self.sid,))
        services.create_student(self.con, {'name': 'МАМЕДОВ, ДАНИИЛ Другой',
            'parent_name': 'Synthetic Other', 'phone': '+79000000877'}, self.owner)
        duplicate = self.run_review()
        self.assertEqual(duplicate['selection']['student_count'], 2)
        self.assertIsNone(duplicate['source_context'])
        self.assertIsNone(duplicate['projection'])

    def test_missing_or_conflicting_erp_identity_blocks_projection(self):
        self.con.execute("UPDATE students SET erp_id='' WHERE id=?", (self.sid,))
        missing = self.run_review()
        self.assertIsNone(missing['source_context'])
        self.assertIsNone(missing['projection'])
        self.con.execute("UPDATE students SET erp_id='synthetic-mamedov-case' WHERE id=?", (self.sid,))
        other = services.create_student(self.con, {'name': 'Synthetic Other Child',
            'parent_name': 'Synthetic Other', 'phone': '+79000000877'}, self.owner)
        self.con.execute("UPDATE students SET erp_id='synthetic-mamedov-case' WHERE id=?", (other,))
        conflict = self.run_review()
        self.assertIsNone(conflict['source_context'])
        self.assertIsNone(conflict['projection'])

    def test_schedule_and_price_or_lesson_changes_return_actual_source_without_projection(self):
        changes = [('groups', 'weekdays', '0,2'), ('groups', 'time', '18:45'),
                   ('subscriptions', 'price', 9400), ('subscriptions', 'lessons', 8)]
        for table, field, value in changes:
            native = self.gid if table == 'groups' else self.target
            old = self.con.execute('SELECT '+field+' FROM '+table+' WHERE id=?', (native,)).fetchone()[0]
            self.con.execute('UPDATE '+table+' SET '+field+'=? WHERE id=?', (value, native))
            with self.subTest(field=field):
                result = self.run_review()
                self.assertIsNotNone(result['source_context'])
                self.assertIsNone(result['projection'])
                self.assertTrue(result['issues'])
            self.con.execute('UPDATE '+table+' SET '+field+'=? WHERE id=?', (old, native))

    def test_duplicate_purchase_and_duplicate_matching_group_do_not_guess(self):
        eid = self.con.execute("INSERT INTO enrollments(student_id,group_id,tariff,active,created_at) VALUES(?,?,'fixed',0,'2026-10-01')", (self.sid, self.gid)).lastrowid
        duplicate = self.invoice('2026-12', 9450, 7, eid)
        result = self.run_review()
        self.assertEqual(result['selection']['purchase_count'], 2)
        self.assertIsNone(result['projection'])
        self.con.execute("UPDATE subscriptions SET status='cancelled' WHERE id=?", (duplicate,))
        branch = self.con.execute('SELECT branch_id FROM groups WHERE id=?', (self.gid,)).fetchone()[0]
        other = self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time) VALUES(?,'Synthetic duplicate','0,3','19:45')", (branch,)).lastrowid
        eid = self.con.execute("INSERT INTO enrollments(student_id,group_id,tariff,created_at) VALUES(?,?,'fixed','2026-11-20')", (self.sid, other)).lastrowid
        self.invoice('2026-12', 9450, 7, eid)
        result = self.run_review()
        self.assertEqual(result['selection']['group_count'], 2)
        self.assertIsNone(result['projection'])

    def test_changed_free_balance_or_source_parts_never_force_reported_projection(self):
        self.con.execute('UPDATE payments SET amount=amount+1 WHERE id=?', (self.pids[-1],))
        result = self.run_review()
        self.assertEqual(result['native_balance']['deposit'], 12116)
        self.assertIsNone(result['projection'])
        self.con.execute('UPDATE payments SET amount=amount-1 WHERE id=?', (self.pids[-1],))
        self.con.execute('UPDATE payments SET amount=amount+1 WHERE id=?', (self.pids[0],))
        self.con.execute('UPDATE payments SET amount=amount-1 WHERE id=?', (self.pids[1],))
        result = self.run_review()
        self.assertEqual(result['native_balance']['deposit'], 12115)
        self.assertEqual([row['amount'] for row in result['invoice']['sources']], [4016, 4049, 1385])
        self.assertIsNone(result['projection'])

    def test_later_operation_blocks_even_if_the_reported_amount_shape_still_matches(self):
        self.con.execute("UPDATE payments SET paid_at='2026-11-21' WHERE id=?", (self.pids[-1],))
        result = self.run_review()
        self.assertEqual(result['observed_shape'], 'reported_fifo_shape')
        self.assertIsNone(result['projection'])
        self.assertTrue(any('после 20.11' in issue for issue in result['issues']))

    def test_explicit_source_payment_links_are_not_erased_by_a_matching_amount_shape(self):
        for pid, amount in zip(self.pids, (4015, 4050, 1385)):
            paymentallocation.bind_payment(self.con, pid, self.target, amount, source_namespace='erp',
                source_invoice_id='synthetic-december-invoice', source_operation_id='synthetic-'+str(pid),
                evidence_id='synthetic-exact-source-'+str(pid))
        self.payment(18100, '2026-10-04')
        result = self.run_review()
        self.assertEqual(result['observed_shape'], 'reported_fifo_shape')
        self.assertIsNone(result['projection'])
        self.assertTrue(any('прямые связи' in issue for issue in result['issues']))

    def test_existing_paid_expired_and_bundle_links_are_inspected_locally_before_projection(self):
        paid = self.link_row('paid', self.target, 9450)
        expired = self.link_row('expired', self.target, 9450)
        bundle = self.link_row('open', None, 15435)
        for subid, amount in ((self.previous, 5985), (self.target, 9450)):
            self.con.execute('INSERT INTO pay_link_subscriptions VALUES(?,?,?)', (bundle, subid, amount))
        result = self.run_review()
        self.assertTrue(result['plan_ready'], result['issues'])
        links = {row['id']: row for row in result['payment_links']}
        self.assertEqual(set(links), {self.link, paid, expired, bundle})
        self.assertTrue(all(row['inspection']['read_only'] for row in links.values()))
        self.assertTrue(all(not row['inspection']['valid'] for row in links.values()))
        self.assertEqual([row['subscription_id'] for row in links[bundle]['parts']], [self.previous, self.target])

    def test_native_direct_targets_block_zeroing_even_when_coverage_and_free_amounts_match(self):
        self.con.execute('UPDATE payments SET amount=4015 WHERE id=?', (self.pids[0],))
        self.payment(5985, '2026-10-04')
        for pid, amount in zip(self.pids, (4015, 4050, 1385)):
            self.con.execute('INSERT INTO invoice_payment_targets VALUES(?,?,?)', (pid, self.target, amount))
        result = self.run_review()
        self.assertEqual(result['observed_shape'], 'reported_fifo_shape')
        self.assertEqual(result['native_balance']['deposit'], 12115)
        self.assertEqual(len(result['native_invoice_payment_targets']), 3)
        self.assertIsNone(result['projection'])
        self.assertTrue(any('прямые связи' in issue for issue in result['issues']))

    def test_branch_quotes_and_tc_prefix_match_but_another_branch_does_not(self):
        branch = self.con.execute('SELECT branch_id FROM groups WHERE id=?', (self.gid,)).fetchone()[0]
        self.con.execute('UPDATE branches SET name=? WHERE id=?', ('ТЦ «Небо»', branch))
        self.assertTrue(self.run_review()['plan_ready'])
        self.con.execute('UPDATE branches SET name=? WHERE id=?', ('Небо другой филиал', branch))
        self.assertIsNone(self.run_review()['projection'])

    def test_financial_history_and_exact_native_target_rows_are_returned_without_chat_messages(self):
        self.con.execute("INSERT INTO history(student_id,ts,user_name,kind,text) VALUES(?,'2026-11-20T11:51','Synthetic owner','payment','Synthetic financial entry')", (self.sid,))
        self.con.execute("INSERT INTO history(student_id,ts,user_name,kind,text) VALUES(?,'2026-11-20T11:52','Synthetic owner','message','Private chat must stay out')", (self.sid,))
        self.con.execute('INSERT INTO invoice_payment_targets VALUES(?,?,0)', (self.pids[0], self.target))
        self.con.execute("INSERT INTO invoice_balance_uses VALUES(?,'synthetic-zero-use','synthetic-fingerprint',0,'[]','{}','2026-11-20T11:51','Synthetic owner')", (self.target,))
        result = self.run_review()
        self.assertEqual([row['text'] for row in result['native_financial_history']], ['Synthetic financial entry'])
        self.assertEqual(result['native_financial_history'][0]['ts'], '2026-11-20T11:51')
        self.assertEqual(result['native_invoice_payment_targets'],
            [{'payment_id': self.pids[0], 'subscription_id': self.target, 'amount': 0}])
        self.assertEqual(result['native_invoice_balance_uses'][0]['request_id'], 'synthetic-zero-use')


if __name__ == '__main__':
    unittest.main()
