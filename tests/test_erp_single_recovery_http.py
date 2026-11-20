"""Owner-only HTTP recovery over synthetic data, with local verified backups.

Known ERP identifiers reproduce the explicitly reviewed case. No production
connection, private settings, provider requests or real outgoing money is used.
"""
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

from crm import backup, db, erpsinglerecovery
from tests import test_erp_single_recovery as fixture


class SingleRecoveryHttpTest(unittest.TestCase):
    SALES=fixture.SingleAggregateRecoveryTest.SALES
    BASE='/api/erp/singles/recovery'

    def setUp(self):
        fixture.SingleAggregateRecoveryTest.setUp(self)
        from crm import server
        self.server_module=server
        self.owner=dict(self.con.execute("SELECT * FROM users WHERE role='admin' AND active=1 ORDER BY id LIMIT 1").fetchone())
        self.con.execute("UPDATE users SET name='Синтетический руководитель' WHERE id=?",(self.owner['id'],))
        self.owner=dict(self.con.execute('SELECT * FROM users WHERE id=?',(self.owner['id'],)).fetchone())
        self.user=self.owner
        self.actors={'owner':self.owner}
        for key,role,offline in [('offline','operator',1),('coach','coach',0)]:
            uid=self.con.execute("INSERT INTO users(login,name,role,pw_hash,is_offline_admin) VALUES (?,?,?,'disabled',?)",
                ('http-recovery-'+key,'Синтетический '+key,role,offline)).lastrowid
            self.actors[key]=dict(self.con.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())
        for key,actor in self.actors.items():
            self.con.execute('INSERT INTO sessions(token,user_id,created_at) VALUES (?,?,?)',
                ('synthetic-recovery-session-'+key,actor['id'],datetime.now().isoformat()))
        self.backups=Path(self.temp.name)/'backups'
        # A buggy global backup must never open the real configured database.
        # The deliberately unrelated decoy proves the web wrapper copies its
        # actual request connection rather than backup.DB_PATH.
        self.decoy=Path(self.temp.name)/'wrong-global.sqlite3'
        with sqlite3.connect(self.decoy) as wrong:
            wrong.execute('CREATE TABLE wrong_database_marker(value TEXT)')
            wrong.execute("INSERT INTO wrong_database_marker VALUES ('not-the-request-database')")
        real_connect=db.connect
        def isolated_connect(path=None):return real_connect(str(self.path) if path is None else path)
        for setting in [patch.object(db,'connect',side_effect=isolated_connect),
                        patch.object(backup,'DIR',str(self.backups)),
                        patch.object(backup,'DB_PATH',str(self.decoy))]:
            setting.start();self.addCleanup(setting.stop)
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
        if actor is not None:headers['Cookie']='crm_session=synthetic-recovery-session-'+actor
        request=Request('http://127.0.0.1:'+str(self.http.server_port)+path,method=method,
            headers=headers,data=json.dumps(body).encode() if body is not None else None)
        try:
            with urlopen(request,timeout=8) as response:return response.status,json.load(response)
        except HTTPError as error:
            try:return error.code,json.load(error)
            finally:error.close()

    def body(self):
        return {key:deepcopy(self.data[key]) for key in ('erp_id','group_id','aggregate_id','payment_mapping','mapping_reviewed','reason')}

    def preview(self,body=None):
        code,result=self.call('POST',self.BASE+'/plan',self.body() if body is None else body)
        self.assertEqual(code,200,result)
        return result

    def approved_apply(self):
        body=self.body();plan=self.preview(body)
        self.assertTrue(plan['can_apply'],plan.get('issues'))
        code,result=self.call('POST',self.BASE+'/apply',body|{'token':plan['token']})
        self.assertEqual(code,200,result)
        return result

    def financial_snapshot(self):
        return {table:[tuple(row) for row in self.con.execute('SELECT * FROM '+table+' ORDER BY 1')]
            for table in ('subscriptions','payments','erp_full_operations','erp_single_sales','erp_single_payments','single_visits','erp_single_reschedules')}

    def test_all_recovery_routes_require_login_and_owner_role(self):
        before=self.con.serialize()
        routes=[('GET',self.BASE+'/context?erp_id=133513',None),
                ('POST',self.BASE+'/plan',self.body()),
                ('POST',self.BASE+'/apply',self.body()|{'token':'not-a-plan'}),
                ('POST',self.BASE+'/1/rollback',{'token':'not-a-rollback'})]
        for actor,expected in [(None,401),('offline',403),('coach',403)]:
            for method,path,body in routes:
                with self.subTest(actor=actor,path=path):self.assertEqual(self.call(method,path,body,actor=actor)[0],expected)
        self.assertEqual(self.con.serialize(),before)

    def test_context_and_clone_preview_do_not_change_the_request_database(self):
        before=self.con.serialize()
        code,context=self.call('GET',self.BASE+'/context?erp_id=133513')
        self.assertEqual(code,200,context)
        self.assertEqual(context['student']['id'],self.sid)
        self.assertEqual(len(context['payments']),4)
        self.assertEqual(len(context['aggregates']),1)
        self.assertEqual([{key:row[key] for key in ('sale_id','invoice_id','operation_id','source_date','effective_date','amount')} for row in context['sales']],self.SALES)
        preview=self.preview()
        self.assertTrue(preview['can_apply'],preview.get('issues'))
        self.assertEqual(preview['before_summary']['payment_total'],10000)
        self.assertEqual(preview['after_summary']['payment_total'],10000)
        self.assertEqual(preview['after_summary']['sales_total'],10000)
        self.assertEqual(preview['after_summary']['calendar_dates'],['2026-11-11','2026-11-18','2026-11-25','2026-12-02'])
        self.assertEqual(self.con.serialize(),before)
        self.assertFalse(self.backups.exists())

    def test_wrong_payment_register_group_is_refused_without_changes(self):
        before=self.financial_snapshot()
        preview=self.preview(self.body()|{'group_id':self.wrong_gid})
        self.assertFalse(preview['can_apply'])
        self.assertTrue(any('18:45' in issue for issue in preview['issues']))
        code,result=self.call('POST',self.BASE+'/apply',self.body()|{'group_id':self.wrong_gid,'token':preview['token']})
        self.assertGreaterEqual(code,400,result)
        self.assertEqual(self.financial_snapshot(),before)

    def test_manual_mapping_needs_owner_review_and_never_guesses_equal_payments(self):
        before=self.con.serialize()
        for body in [self.body()|{'mapping_reviewed':False},self.body()|{'payment_mapping':{}}]:
            preview=self.preview(body)
            self.assertFalse(preview['can_apply'])
            self.assertTrue(preview['issues'])
        self.assertEqual(self.con.serialize(),before)

    def test_caller_cannot_replace_the_confirmed_case_rows(self):
        before=self.con.serialize()
        rows=deepcopy(self.SALES);rows[0]['amount']=1;rows[0]['effective_date']='2026-11-04'
        body=self.body()|{'sales':rows,'payment_date':'2026-11-20','payment_method':'cash'}
        code,result=self.call('POST',self.BASE+'/plan',body)
        self.assertIn(code,(200,400),result)
        if code==200:
            expected={row['sale_id']:row for row in self.SALES}
            self.assertEqual({row['sale_id']:{key:row[key] for key in expected[row['sale_id']]} for row in result['sales']},expected)
            if result['can_apply']:self.assertEqual(result['after_summary']['sales_total'],10000)
        self.assertEqual(self.con.serialize(),before)

    def test_apply_uses_a_verified_backup_of_this_connection_and_creates_no_payments(self):
        before=[dict(row) for row in self.con.execute('SELECT * FROM payments ORDER BY id')]
        result=self.approved_apply()
        self.assertTrue(result.get('backup_created'))
        run=self.con.execute('SELECT * FROM erp_single_recovery_runs WHERE id=?',(result['run_id'],)).fetchone()
        location=Path(run['backup_path'])
        self.assertTrue(location.is_file());self.assertEqual(location.parent,self.backups)
        with sqlite3.connect('file:'+str(location)+'?mode=ro',uri=True) as copy:
            self.assertEqual(copy.execute('SELECT erp_id FROM students WHERE id=?',(self.sid,)).fetchone()[0],'133513')
            self.assertEqual(copy.execute('SELECT count(*) FROM payments').fetchone()[0],4)
            self.assertEqual(copy.execute('SELECT status FROM subscriptions WHERE id=?',(self.aggregate_id,)).fetchone()[0],'paid')
            self.assertIsNone(copy.execute("SELECT name FROM sqlite_master WHERE name='wrong_database_marker'").fetchone())
        after=[dict(row) for row in self.con.execute('SELECT * FROM payments ORDER BY id')]
        self.assertEqual(len(after),4)
        for old,new in zip(before,after):
            self.assertEqual({k:v for k,v in new.items() if k!='subscription_id'},{k:v for k,v in old.items() if k!='subscription_id'})
        self.assertEqual(self.con.execute("SELECT count(*) FROM subscriptions WHERE kind='single'").fetchone()[0],4)
        self.assertEqual(self.con.execute("SELECT sum(price) FROM subscriptions WHERE status!='cancelled'").fetchone()[0],10000)

    def test_backup_failure_blocks_apply_before_any_business_change(self):
        plan=self.preview();before=self.con.serialize()
        with patch.object(Path,'mkdir',side_effect=OSError('Synthetic backup unavailable')):
            code,result=self.call('POST',self.BASE+'/apply',self.body()|{'token':plan['token']})
        self.assertGreaterEqual(code,400,result)
        self.assertEqual(self.con.serialize(),before)

    def test_stale_preview_is_rejected_without_overwriting_a_later_edit(self):
        plan=self.preview()
        payment=next(iter(self.payment_mapping.values()))
        self.con.execute("UPDATE payments SET comment='Synthetic later owner edit' WHERE id=?",(payment,))
        before=self.con.serialize()
        code,result=self.call('POST',self.BASE+'/apply',self.body()|{'token':plan['token']})
        self.assertGreaterEqual(code,400,result)
        self.assertEqual(self.con.serialize(),before)

    def test_http_rollback_restores_existing_finances_and_rejects_later_edits(self):
        original=self.financial_snapshot();result=self.approved_apply()
        code,rolled=self.call('POST',self.BASE+'/'+str(result['run_id'])+'/rollback',{'run_id':result['run_id'],'token':result['rollback_token']})
        self.assertEqual(code,200,rolled)
        self.assertEqual(rolled['state'],'rolled_back')
        self.assertEqual(self.financial_snapshot(),original)
        result=self.approved_apply()
        self.con.execute("UPDATE payments SET comment='Synthetic edit after recovery' WHERE id=?",(next(iter(self.payment_mapping.values())),))
        before=self.con.serialize()
        code,blocked=self.call('POST',self.BASE+'/'+str(result['run_id'])+'/rollback',{'run_id':result['run_id'],'token':result['rollback_token']})
        self.assertGreaterEqual(code,400,blocked)
        self.assertEqual(self.con.serialize(),before)

    def test_delayed_apply_cannot_undo_a_completed_rollback(self):
        original=self.financial_snapshot();body=self.body();plan=self.preview(body)
        code,result=self.call('POST',self.BASE+'/apply',body|{'token':plan['token']})
        self.assertEqual(code,200,result)
        code,rolled=self.call('POST',self.BASE+'/'+str(result['run_id'])+'/rollback',
            {'token':result['rollback_token']})
        self.assertEqual(code,200,rolled)
        self.assertEqual(self.financial_snapshot(),original)
        backups=set(self.backups.iterdir());before=self.con.serialize()

        # A browser/network retry can arrive after the owner has cancelled the
        # operation. Restored business rows must not revive its old approval.
        code,blocked=self.call('POST',self.BASE+'/apply',body|{'token':plan['token']})
        self.assertGreaterEqual(code,400,blocked)
        self.assertEqual(self.con.serialize(),before)
        self.assertEqual(set(self.backups.iterdir()),backups)
        self.assertEqual(self.con.execute('SELECT state FROM erp_single_recovery_runs WHERE id=?',
            (result['run_id'],)).fetchone()[0],'rolled_back')

        fresh=self.preview(body)
        self.assertTrue(fresh['can_apply'],fresh.get('issues'))
        self.assertNotEqual(fresh['token'],plan['token'])
        code,reapplied=self.call('POST',self.BASE+'/apply',body|{'token':fresh['token']})
        self.assertEqual(code,200,reapplied)
        self.assertNotEqual(reapplied['run_id'],result['run_id'])
        self.assertEqual(self.con.execute('SELECT count(*) FROM payments').fetchone()[0],4)
