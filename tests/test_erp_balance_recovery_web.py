"""Reviewed accounting recovery on disposable data; no real finance/provider calls."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crm import balancecredit, erpbalancerecovery as engine, erpbalancerecoveryweb as web, money, server
from crm.services import ApiError
from tests import test_erp_balance_recovery_plan as fixture


class ERPBalanceRecoveryWebTest(unittest.TestCase):
    def setUp(self):
        helper = fixture.ERPBalanceRecoveryPlanTest()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        self.con = helper.con
        self.con.execute("UPDATE students SET erp_id='88202'")
        self.con.execute("UPDATE subscriptions SET price=8100,lessons=6 WHERE id=1")
        self.con.execute("UPDATE subscriptions SET price=9450,lessons=7 WHERE id=2")
        self.con.execute('DELETE FROM payments')
        for number, amount in enumerate((10000,8000,6000,4000,1665),1):
            self.con.execute('''INSERT INTO payments(student_id,amount,method,paid_at,comment,user_name,
                confirmed,created_at) VALUES(1,?,'unknown','2026-11-20',?,'Система',1,'2026-11-20')''',
                (amount,'Synthetic technical row '+str(number)))
        self.con.execute('''CREATE TABLE pay_links(id INTEGER PRIMARY KEY,student_id INTEGER,
            subscription_id INTEGER,amount INTEGER,url TEXT)''')
        self.con.execute("INSERT INTO pay_links VALUES(10,1,2,9450,'https://synthetic.invalid/pay/keep-token')")
        web.init(self.con)
        self.case=dict(helper.case,erp_id='88202',existing_credit=0,credit_ref='synthetic-no-credit',
            credit_label='Аванса не было',evidence_id='synthetic-reviewed-zero-web',
            reviewed_fingerprint=engine.financial_fingerprint(self.con,1,'88202'))
        # Production source checks remain in place. Synthetic source identities
        # are registered only inside this isolated fixture.
        self.case.update(student_id=1,reviewed_terms_fingerprint=engine._hash(engine._purchase_terms(
            self.con.execute('SELECT * FROM subscriptions WHERE id=2').fetchone())))
        change=patch.dict(engine.CASES,{'88202':self.case})
        change.start();self.addCleanup(change.stop)
        folder=tempfile.TemporaryDirectory(prefix='erp-balance-web-')
        self.addCleanup(folder.cleanup)
        self.folder=Path(folder.name)
        change=patch('crm.backup.DIR',str(self.folder))
        change.start();self.addCleanup(change.stop)
        self.user={'id':1,'name':'Synthetic owner','role':'admin'}

    def checked(self):
        result=web.plan(self.con,'88202')
        self.assertTrue(result['can_apply'],result['issues'])
        return result

    def body(self):
        return {'erp_id':'88202','token':self.checked()['token'],'request_id':'synthetic-web-apply-0001'}

    def test_plan_is_full_copy_readonly_and_does_not_create_cash_or_funds(self):
        before=self.con.serialize()
        result=self.checked()
        self.assertEqual(self.con.serialize(),before)
        self.assertEqual(result['before']['balance']['deposit'],12115)
        after=result['without_balance']
        self.assertEqual((after['invoice']['covered'],after['invoice']['payable'],after['balance']['deposit']),(0,9450,0))
        self.assertEqual((after['payments_count'],after['purchases_count'],after['invoice']['lessons']),(5,2,7))
        self.assertEqual(after['funds'],[])
        self.assertTrue(result['preview_on_copy']);self.assertTrue(result['production_apply_available'])
        self.assertEqual(result['copy_scope'],'full_server_database')

    def test_apply_keeps_all_sources_and_link_then_replays_without_new_backup_or_debit(self):
        before=web._state(self.con,1)
        body=self.body()
        result=web.apply(self.con,body,self.user)
        self.assertEqual(result['state'],'applied')
        self.assertTrue(result['backup_created']);self.assertTrue(result['last_run']['can_rollback'])
        self.assertEqual(result['after']['invoice']['payable'],9450)
        self.assertEqual(result['after']['balance']['deposit'],0)
        after=web._state(self.con,1)
        for table in ('payments','attendance','enrollments','pay_links'):
            self.assertEqual(after[table],before[table])
        self.assertEqual(len(after['balance_account_snapshots']),1)
        self.assertEqual(after['balance_credit_funds'],[])
        self.assertEqual(after['subscriptions'][0],before['subscriptions'][0])
        saved=self.con.serialize();files=sorted(self.folder.iterdir())
        again=web.apply(self.con,body,self.user)
        self.assertTrue(again['already_applied']);self.assertEqual(again['run_id'],result['run_id'])
        self.assertEqual(self.con.serialize(),saved);self.assertEqual(sorted(self.folder.iterdir()),files)
        reopened=web.plan(self.con,'88202')
        self.assertTrue(reopened['already_applied']);self.assertFalse(reopened['can_apply'])
        self.assertEqual(reopened['last_run']['run_id'],result['run_id'])

    def test_guarded_rollback_restores_only_original_card_and_retains_audit(self):
        before=web._state(self.con,1)
        result=web.apply(self.con,self.body(),self.user)
        undone=web.rollback(self.con,result['run_id'],result['rollback_token'],self.user)
        self.assertEqual(undone['state'],'rolled_back')
        self.assertEqual(web._state(self.con,1),before)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM '+web.RUNS).fetchone()[0],1)
        self.assertEqual(len(list(self.folder.iterdir())),2)
        self.assertFalse(web.plan(self.con,'88202')['can_apply'])
        with self.assertRaises(ApiError):
            web.rollback(self.con,result['run_id'],result['rollback_token'],self.user)

    def test_stale_native_payment_or_link_or_attendance_blocks_before_backup(self):
        for sql in ("UPDATE payments SET amount=amount+1 WHERE id=(SELECT MIN(id) FROM payments)",
                    'UPDATE pay_links SET amount=9449 WHERE id=10',
                    "UPDATE attendance SET note='Later actual mark' WHERE id=1"):
            with self.subTest(sql=sql):
                body=self.body()
                self.con.execute('SAVEPOINT synthetic_change')
                self.con.execute(sql);before=self.con.serialize()
                with self.assertRaises(ApiError):web.apply(self.con,body,self.user)
                self.assertEqual(self.con.serialize(),before)
                self.assertEqual(list(self.folder.iterdir()),[])
                self.con.execute('ROLLBACK TO synthetic_change');self.con.execute('RELEASE synthetic_change')

    def test_changed_after_state_blocks_rollback_without_touching_new_marks(self):
        result=web.apply(self.con,self.body(),self.user)
        self.con.execute("UPDATE attendance SET note='New actual note' WHERE id=1")
        before=self.con.serialize()
        self.assertFalse(web.plan(self.con,'88202')['last_run']['can_rollback'])
        with self.assertRaises(ApiError):web.rollback(self.con,result['run_id'],result['rollback_token'],self.user)
        self.assertEqual(self.con.serialize(),before)

    def test_reopen_after_actual_receipt_shows_current_summary_separately_from_saved_result(self):
        result=web.apply(self.con,self.body(),self.user)
        # A newly recorded, actually received payment is allowed after the
        # correction. It is not replayed/recreated by recovery or preview.
        self.con.execute('''INSERT INTO payments(student_id,amount,method,paid_at,comment,user_name,
            subscription_id,confirmed,created_at) VALUES(1,9450,'card_branch','2026-11-20',
            'Synthetic later received cash','Synthetic owner',2,1,'2026-11-20')''')
        money._reconcile(self.con,1)
        before=self.con.serialize()
        reopened=web.plan(self.con,'88202')
        self.assertTrue(reopened['state_changed_since_apply'])
        self.assertEqual(reopened['without_balance']['invoice']['payable'],9450)
        self.assertEqual(reopened['current_summary']['invoice']['payable'],0)
        self.assertEqual(reopened['current_summary']['payments_count'],6)
        self.assertFalse(reopened['last_run']['can_rollback'])
        self.assertTrue(reopened['summary_note'])
        self.assertEqual(self.con.serialize(),before)
        self.assertEqual(result['after']['invoice']['payable'],9450)

    def test_later_cancelled_invoice_has_zero_current_due_and_keeps_saved_history(self):
        result=web.apply(self.con,self.body(),self.user)
        self.con.execute("UPDATE subscriptions SET status='cancelled' WHERE id=2")
        before=self.con.serialize()
        reopened=web.plan(self.con,'88202')
        self.assertEqual(reopened['current_summary']['invoice']['status'],'cancelled')
        self.assertEqual(reopened['current_summary']['invoice']['payable'],0)
        self.assertEqual(reopened['without_balance']['invoice']['payable'],9450)
        self.assertEqual(web._json(reopened['last_run']['after_summary']),web._json(result['after_summary']))
        self.assertTrue(reopened['state_changed_since_apply'])
        self.assertFalse(reopened['last_run']['can_rollback'])
        self.assertEqual(self.con.serialize(),before)

    def test_no_apply_for_unapproved_cases_or_browser_supplied_source_overrides(self):
        for body in ({'erp_id':'117559'},{'erp_id':'120486'},dict(self.body(),base_amount=99999),
                     dict(self.body(),subscription_id=1)):
            with self.subTest(body=body):
                before=self.con.serialize()
                with self.assertRaises(ApiError):web.apply(self.con,body,self.user)
                self.assertEqual(self.con.serialize(),before)
        self.assertEqual(list(self.folder.iterdir()),[])

    def test_nonowner_or_invalid_request_or_token_is_rejected_without_writes(self):
        body=self.body()
        for user,request in ((dict(self.user,role='operator'),body),
                             (dict(self.user,name=''),body),
                             (self.user,dict(body,request_id='short')),
                             (self.user,dict(body,token='bad'))):
            with self.subTest(request=request,user=user):
                before=self.con.serialize()
                with self.assertRaises(ApiError):web.apply(self.con,request,user)
                self.assertEqual(self.con.serialize(),before)

    def test_exact_backup_must_match_current_card_and_failure_is_atomic(self):
        body=self.body()
        stale=web._backup_current(self.con,prefix='synthetic-stale')
        self.con.execute("UPDATE pay_links SET url='https://synthetic.invalid/pay/new-token' WHERE id=10")
        body=self.body();before=self.con.serialize()
        with patch.object(web,'_backup_current',return_value=stale):
            with self.assertRaises(ApiError):web.apply(self.con,body,self.user)
        self.assertEqual(self.con.serialize(),before)

    def test_executor_failure_or_unexpected_source_mutation_rolls_back_every_write(self):
        actual=engine.apply_reviewed
        def corrupt(con,erp_id,user):
            result=actual(con,erp_id,user)
            con.execute('UPDATE payments SET amount=amount+1 WHERE id=(SELECT MIN(id) FROM payments)')
            return result
        for substitute in (ValueError('Synthetic executor failure'),corrupt):
            body=self.body();before=web._state(self.con,1)
            with self.subTest(substitute=str(substitute)),patch.object(engine,'apply_reviewed',side_effect=substitute):
                with self.assertRaises((ApiError,ValueError)):web.apply(self.con,body,self.user)
                self.assertEqual(web._state(self.con,1),before)
                self.assertEqual(self.con.execute('SELECT COUNT(*) FROM '+web.RUNS).fetchone()[0],0)

    def test_routes_are_owner_only_nontransactional_and_use_exact_paths(self):
        for method,path in (('GET','/api/erp/balance-recovery/context'),
            ('POST','/api/erp/balance-recovery/plan'),('POST','/api/erp/balance-recovery/apply'),
            ('POST','/api/erp/balance-recovery/42/rollback')):
            matches=[(fn,role) for verb,rx,fn,role in server.ROUTES if verb==method and rx.match(path)]
            self.assertEqual(len(matches),1,path)
            fn,role=matches[0];self.assertEqual(role,'admin')
            self.assertFalse(getattr(fn,'transactional',True),path)


if __name__=='__main__':
    unittest.main()
