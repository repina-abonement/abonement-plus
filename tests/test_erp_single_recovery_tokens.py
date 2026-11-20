"""Old approval must stay stale after an explicitly completed recovery rollback.

Only temporary fixture databases are opened. The base test class is not inherited
or imported by name, so its tests are not duplicated in discovery.
"""
import unittest
from crm import erpimport, erpsinglerecovery as engine
from tests import test_erp_single_recovery as fixture


class RecoveryApprovalTokens(unittest.TestCase):
    SALES=fixture.SingleAggregateRecoveryTest.SALES
    setUp=fixture.SingleAggregateRecoveryTest.setUp
    backup=fixture.SingleAggregateRecoveryTest.backup
    payments=fixture.SingleAggregateRecoveryTest.payments
    aggregate=fixture.SingleAggregateRecoveryTest.aggregate

    def recover_and_rollback(self):
        original=engine.plan(self.con,self.data)
        self.assertTrue(original['can_apply'],original.get('issues'))
        applied=engine.apply(self.con,self.data,original['token'],self.user,self.backup('before-first.sqlite3'))
        engine.rollback(self.con,applied['run_id'],self.user,self.backup('before-first-rollback.sqlite3'),token=applied['rollback_token'])
        return original,applied

    def test_completed_rollback_does_not_reactivate_a_delayed_original_apply(self):
        original,applied=self.recover_and_rollback()
        fresh=engine.plan(self.con,self.data)
        self.assertTrue(fresh['can_apply'],fresh.get('issues'))
        # Business data is restored exactly; the approval generation is not.
        self.assertEqual(fresh['state_fingerprint'],original['state_fingerprint'])
        self.assertNotEqual(fresh['recovery_history_fingerprint'],original['recovery_history_fingerprint'])
        self.assertNotEqual(fresh['token'],original['token'])
        path=self.backup('before-delayed-retry.sqlite3')
        before=self.con.serialize()
        with self.assertRaises(erpimport.ImportError_):
            engine.apply(self.con,self.data,original['token'],self.user,path)
        self.assertEqual(self.con.serialize(),before)
        self.assertEqual([tuple(r) for r in self.con.execute('SELECT id,state FROM erp_single_recovery_runs ORDER BY id')],[(applied['run_id'],'rolled_back')])
        self.assertEqual(self.aggregate()['status'],'paid')
        self.assertEqual(self.con.execute('SELECT count(*) FROM payments').fetchone()[0],4)

    def test_fresh_owner_approval_after_rollback_allows_a_new_recovery(self):
        original,applied=self.recover_and_rollback()
        payments=self.payments()
        fresh=engine.plan(self.con,self.data)
        result=engine.apply(self.con,self.data,fresh['token'],self.user,self.backup('before-new-approved-run.sqlite3'))
        self.assertNotEqual(result['run_id'],applied['run_id'])
        self.assertEqual(result['state'],'applied')
        self.assertEqual([r['id'] for r in self.payments()],[r['id'] for r in payments])
        self.assertEqual(sum(r['amount'] for r in self.payments()),10000)
        self.assertEqual(self.con.execute("SELECT count(*) FROM subscriptions WHERE kind='single'").fetchone()[0],4)
        self.assertEqual([tuple(r) for r in self.con.execute('SELECT id,state FROM erp_single_recovery_runs ORDER BY id')],[(applied['run_id'],'rolled_back'),(result['run_id'],'applied')])
        # A second explicit rollback also preserves the exact business snapshot.
        engine.rollback(self.con,result['run_id'],self.user,self.backup('before-second-rollback.sqlite3'),token=result['rollback_token'])
        newest=engine.plan(self.con,self.data)
        self.assertEqual(newest['state_fingerprint'],original['state_fingerprint'])
        self.assertNotEqual(newest['token'],fresh['token'])
        self.assertNotEqual(newest['token'],original['token'])

    def test_repeated_preview_without_new_run_keeps_a_stable_approval(self):
        first=engine.plan(self.con,self.data)
        second=engine.plan(self.con,self.data)
        self.assertEqual(first['token'],second['token'])
        self.assertEqual(first['recovery_history_fingerprint'],second['recovery_history_fingerprint'])
