"""Protected historical accounting on temporary SQLite data; no provider calls."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from crm import backup, db
from tests import test_erp_historical_refunds as fixture


class HistoricalRefundHttpTest(unittest.TestCase):
    BASE='/api/erp/historical-refunds'

    def setUp(self):
        self.fixture=fixture.HistoricalRefundTest();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.con=self.fixture.con;self.path=Path(self.fixture.tmp.name)/'history.sqlite3'
        self.backups=Path(self.fixture.tmp.name)/'verified-backups'
        owner=self.con.execute("SELECT id FROM users WHERE role='admin' AND active=1 ORDER BY id LIMIT 1").fetchone()[0]
        operator=self.con.execute("INSERT INTO users(login,name,role,pw_hash) VALUES('history-http-operator','Synthetic operator','operator','disabled')").lastrowid
        for key,uid in (('owner',owner),('operator',operator)):
            self.con.execute('INSERT INTO sessions(token,user_id,created_at) VALUES(?,?,?)',
                ('synthetic-historical-'+key,uid,datetime.now().isoformat()))
        self.con.commit()
        real_connect=db.connect
        def connect(path=None):return real_connect(str(self.path) if path is None else path)
        for change in (patch.object(db,'connect',side_effect=connect),patch.object(backup,'DIR',str(self.backups))):
            change.start();self.addCleanup(change.stop)
        from crm import server
        class Handler(server.Handler):
            def log_message(self,*args):pass
            def finish(self):
                try:super().finish()
                finally:db.reset_thread_conn()
        self.http=server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True)
        self.thread.start();self.addCleanup(self.stop_server)

    def stop_server(self):
        self.http.shutdown();self.http.server_close();self.thread.join(timeout=5)

    def call(self,method,path,body=None,actor='owner'):
        headers={'Content-Type':'application/json'}
        if actor:headers['Cookie']='crm_session=synthetic-historical-'+actor
        req=Request('http://127.0.0.1:'+str(self.http.server_port)+path,method=method,
            headers=headers,data=json.dumps(body).encode() if body is not None else None)
        try:
            with urlopen(req,timeout=10) as response:return response.status,json.load(response)
        except HTTPError as error:
            try:return error.code,json.load(error)
            finally:error.close()

    def body(self):return deepcopy(self.fixture.body)

    def plan(self,body=None):
        code,result=self.call('POST',self.BASE+'/plan',self.body() if body is None else body)
        self.assertEqual(code,200,result)
        return result

    def test_routes_require_owner_and_never_write_for_denied_users(self):
        before=self.con.serialize()
        for actor,status in ((None,401),('operator',403)):
            for method,path,body in (('GET',self.BASE+'/context?erp_id=120486',None),
                    ('GET','/api/erp/financial-audit?erp_id=120486',None),
                    ('GET','/api/erp/balance-recovery/plan?erp_id=117559',None),
                    ('GET','/api/erp/balance-recovery/mamedov-review',None),
                    ('POST',self.BASE+'/plan',self.body()),('POST',self.BASE+'/apply',self.body())):
                with self.subTest(actor=actor,path=path):
                    self.assertEqual(self.call(method,path,body,actor)[0],status)
        self.assertEqual(self.con.serialize(),before)

    def test_mamedov_review_is_owner_only_readonly_even_when_identity_is_missing(self):
        before=self.con.serialize()
        code,result=self.call('GET','/api/erp/balance-recovery/mamedov-review')
        self.assertEqual(code,200,result)
        self.assertTrue(result['read_only']);self.assertTrue(result['snapshot_on_copy'])
        self.assertEqual(result['copy_scope'],'full_server_database')
        self.assertFalse(result['can_apply']);self.assertFalse(result['production_apply_available'])
        self.assertFalse(result['live_provider_requests']);self.assertFalse(result['independent_source_verified'])
        self.assertIsNone(result['projection']);self.assertTrue(result['issues'])
        self.assertEqual(self.con.serialize(),before);self.assertFalse(self.backups.exists())

    def test_context_and_incomplete_receipt_preview_are_readonly(self):
        before=self.con.serialize()
        code,context=self.call('GET',self.BASE+'/context?erp_id=120486')
        self.assertEqual(code,200,context);self.assertTrue(context['read_only'])
        self.assertTrue(context['snapshot_on_copy'])
        self.assertFalse(context['live_provider_requests'])
        self.assertEqual(context['student']['id'],self.fixture.sid)
        self.assertEqual(len(context['payments']),2)
        body=self.body();body['receipt']['fn']='';body['receipt']['issued_at']=''
        plan=self.plan(body)
        self.assertFalse(plan['can_apply']);self.assertTrue(plan['preview_on_copy'])
        self.assertTrue(any('ФН' in issue for issue in plan['issues']))
        self.assertTrue(any('время' in issue for issue in plan['issues']))
        self.assertEqual(self.con.serialize(),before)
        self.assertFalse(self.backups.exists())

    def test_financial_audit_is_readonly_and_has_no_apply_action(self):
        before=self.con.serialize()
        code,result=self.call('GET','/api/erp/financial-audit?erp_id=120486&month=2026-11')
        self.assertEqual(code,200,result)
        self.assertTrue(result['read_only']);self.assertFalse(result['live_provider_requests'])
        self.assertTrue(result['snapshot_on_copy'])
        self.assertFalse(result['apply_allowed'])
        self.assertEqual(self.con.serialize(),before)
        self.assertFalse(self.backups.exists())

    def test_owner_erp_zero_is_a_separate_readonly_plan_not_a_working_balance_write(self):
        self.con.execute('UPDATE students SET erp_id=? WHERE id=?',('117559',self.fixture.sid))
        self.con.commit();before=self.con.serialize()
        code,result=self.call('GET','/api/erp/financial-audit?erp_id=117559&month=2026-12')
        self.assertEqual(code,200,result)
        snapshot=result['erp_balance_snapshot'];plan=result['authoritative_balance_plan']
        self.assertEqual((snapshot['amount'],snapshot['as_of_date'],snapshot['cutoff_date']),
            (0,'2026-11-20','2026-11-02'))
        self.assertEqual(snapshot['provenance'],'owner_confirmation')
        self.assertTrue(plan['plan_only']);self.assertFalse(plan['applied'])
        self.assertEqual(plan['free_balance'],0);self.assertIsNone(plan['current_free_balance'])
        self.assertFalse(result['apply_allowed']);self.assertIsNone(result['after'])
        self.assertTrue(result['snapshot_on_copy']);self.assertFalse(result['live_provider_requests'])
        self.assertEqual(self.con.serialize(),before);self.assertFalse(self.backups.exists())

    def test_balance_recovery_previews_full_server_copy_without_applying_working_records(self):
        from crm import erpbalancerecovery as recovery
        case={'erp_id':'120486','subscription_id':self.fixture.target,'as_of_date':'2026-11-20',
            'cutoff_date':'2026-11-02','base_amount':0,'existing_credit':2700,
            'credit_label':'На следующий турнир','credit_ref':'synthetic-http-existing-credit',
            'evidence_id':'synthetic-http-reviewed-balance',
            'reviewed_fingerprint':recovery.financial_fingerprint(self.con,self.fixture.sid,'120486')}
        before=self.con.serialize()
        with patch.dict(recovery.CASES,{'120486':case}):
            code,result=self.call('GET','/api/erp/balance-recovery/plan?erp_id=120486')
        self.assertEqual(code,200,result);self.assertTrue(result['plan_ready'],result['issues'])
        self.assertEqual(result['copy_scope'],'full_server_database')
        self.assertTrue(result['read_only']);self.assertTrue(result['snapshot_on_copy'])
        self.assertFalse(result['production_apply_available']);self.assertFalse(result['live_provider_requests'])
        off,on=result['without_balance'],result['with_balance']
        self.assertEqual((off['invoice']['payable'],off['balance']['deposit']),(9450,2700))
        self.assertEqual((on['invoice']['payable'],on['balance']['deposit']),(6750,0))
        self.assertTrue(result['payments_preserved']);self.assertTrue(result['attendance_preserved'])
        self.assertEqual(self.con.serialize(),before);self.assertFalse(self.backups.exists())

    def test_verified_apply_and_transport_token_replay_do_not_duplicate_money(self):
        before=[tuple(r) for r in self.con.execute('SELECT * FROM payments WHERE amount>0 ORDER BY id')]
        body=self.body();plan=self.plan(body)
        self.assertTrue(plan['can_apply'],plan['issues'])
        code,result=self.call('POST',self.BASE+'/apply',body|{'token':plan['token']})
        self.assertEqual(code,200,result)
        self.assertTrue(result['backup_created']);self.assertEqual(result['state'],'applied')
        path=self.backups/result['backup_filename']
        self.assertTrue(path.is_file());self.assertTrue(path.name.startswith('erp-historical-refund-'))
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as snap:
            self.assertEqual(snap.execute('SELECT COUNT(*) FROM payments').fetchone()[0],2)
            self.assertEqual(snap.execute('SELECT status FROM subscriptions WHERE id=?',
                (self.fixture.target,)).fetchone()[0],'paid')
        self.assertEqual([tuple(r) for r in self.con.execute('SELECT * FROM payments WHERE amount>0 ORDER BY id')],before)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments WHERE amount<0').fetchone()[0],1)
        files=set(self.backups.iterdir());snapshot=self.con.serialize()
        code,retry=self.call('POST',self.BASE+'/apply',body|{'token':plan['token']})
        self.assertEqual(code,200,retry);self.assertTrue(retry['already_applied'])
        self.assertEqual(retry['run_id'],result['run_id'])
        self.assertEqual(set(self.backups.iterdir()),files)
        self.assertEqual(self.con.serialize(),snapshot)

    def test_stale_plan_is_rejected_before_backup_or_financial_change(self):
        body=self.body();plan=self.plan(body)
        self.con.execute("UPDATE payments SET comment='Synthetic later edit' WHERE id=?",(self.fixture.october,))
        self.con.commit();before=self.con.serialize()
        code,result=self.call('POST',self.BASE+'/apply',body|{'token':plan['token']})
        self.assertGreaterEqual(code,400,result)
        self.assertEqual(self.con.serialize(),before);self.assertFalse(self.backups.exists())

    def test_backup_failure_does_not_register_a_refund(self):
        body=self.body();plan=self.plan(body);before=self.con.serialize()
        with patch.object(Path,'mkdir',side_effect=OSError('Synthetic unavailable backup')):
            code,result=self.call('POST',self.BASE+'/apply',body|{'token':plan['token']})
        self.assertGreaterEqual(code,400,result)
        self.assertEqual(self.con.serialize(),before)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM historical_refund_runs').fetchone()[0],0)
