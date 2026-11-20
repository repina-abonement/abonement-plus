"""Historical accounting only: synthetic DBs, no financial provider requests."""
import copy
from contextlib import ExitStack, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from crm import db, fiscal, historicalrefunds as hr, money, services as sv, subcancel, paymentallocation


class HistoricalRefundTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.con = db.connect(str(Path(self.tmp.name)/'history.sqlite3')); self.addCleanup(self.con.close)
        with redirect_stdout(StringIO()): db.init_db(self.con)
        hr.init(self.con); paymentallocation.init(self.con)
        self.owner = {'id':1,'name':'Synthetic owner','role':'admin'}
        branch = self.con.execute("INSERT INTO branches(name) VALUES('Synthetic branch')").lastrowid
        self.gid = self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time,capacity) VALUES(?,'Synthetic group','2','18:45',12)",(branch,)).lastrowid
        self.sid = sv.create_student(self.con, {'name':'Synthetic Child','birth':'2021-01-01','parent_name':'Synthetic Parent','phone':'+79000000401','group_id':self.gid,'funnel':'members'},self.owner)
        self.con.execute("UPDATE students SET erp_id='120486' WHERE id=?",(self.sid,))
        self.eid = sv.enrollments_of(self.con,self.sid)[0]['id']
        self.first = self.invoice('2026-09',2700)
        self.target = self.invoice('2026-11',9450)
        self.september = self.payment(8100,'2026-09-01','income-september')
        self.october = self.payment(9450,'2026-10-21','income-october')
        money._reconcile(self.con,self.sid)
        self.invoices = {'716063':self.first,'726247':self.target}
        self.refunds = {'return-one':{'amount':9450,'executed_at':'2026-11-06','source_method':'online','source_invoice_id':'726247',
            'execution_confirmed':True,'evidence_id':'archive:3:1','source_signature':'synthetic-refund-signature'}}
        from crm import historicalrefundtrace
        self.real_invoice=historicalrefundtrace.resolve_invoice
        self.real_operation=historicalrefundtrace.resolve_payment_operation
        self.real_refund=historicalrefundtrace.resolve_refund_operation
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch.object(historicalrefundtrace,'resolve_invoice',side_effect=self.invoice_proof))
        stack.enter_context(patch.object(historicalrefundtrace,'resolve_payment_operation',side_effect=self.operation_proof))
        stack.enter_context(patch.object(historicalrefundtrace,'resolve_refund_operation',side_effect=self.refund_proof))
        # Even read requests are forbidden during historical plan/apply.
        for module, function in ((fiscal,'pk_call'),(fiscal,'ofd_get'),(fiscal,'fetch_ofd'),(fiscal,'run'),(subcancel,'cancel'),(subcancel,'confirm')):
            if hasattr(module,function):stack.enter_context(patch.object(module,function,side_effect=AssertionError('No external/cancellation action allowed')))
        self.body = {'erp_id':'120486','source_namespace':'erp','source_invoice_id':'726247','refund_operation_id':'return-one',
            'subscription_id':self.target,'amount_minor':945000,'executed_at':'2026-11-06','mapping_reviewed':True,
            'reason':'Verified synthetic source relations',
            'payment_allocations':[self.binding(self.september,self.first,2700,'716063','income-september'),self.binding(self.october,self.target,9450,'726247','income-october')],
            'refund_sources':[{'payment_id':self.october,'amount':9450,'source_operation_id':'income-october'}],
            'receipt':{'fn':'9999999999999999','fd':'3403','fp':'3535389344','operation':2,'total_minor':945000,'cashless_minor':945000,'issued_at':'2026-11-06T12:30:15'}}
        self.con.commit()

    def invoice(self,month,price):
        return self.con.execute('''INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at,ledger_managed)
            VALUES(?,?,?,'fixed',7,1350,?,'invoiced','2026-10-20',1)''',(self.sid,self.eid,month,price)).lastrowid

    def payment(self,amount,day,operation):
        pid = self.con.execute('''INSERT INTO payments(student_id,amount,method,paid_at,comment,user_name,confirmed,confirmed_by,created_at)
            VALUES(?,?,'online',?,'Synthetic source','ERP',1,'Source',?)''',(self.sid,amount,day,day)).lastrowid
        signature=json.dumps([self.sid,day,amount,'online'],ensure_ascii=False,sort_keys=True,separators=(',',':'))
        self.con.execute('INSERT INTO erp_full_operations VALUES(?,?,?)',(operation,pid,signature))
        self.con.execute("INSERT INTO pay_links(student_id,amount,orderid,status,created_at,payment_id,pk_payment_id) VALUES(?,?,?,'paid',?,?,?)",(self.sid,amount,operation,day,pid,operation))
        return pid

    def binding(self,pid,sub,amount,invoice,operation):
        return {'payment_id':pid,'subscription_id':sub,'amount':amount,'source_invoice_id':invoice,'source_operation_id':operation,'evidence_id':f'archive:{invoice}:{operation}'}

    def invoice_proof(self,con,sid,invoice):
        return {'subscription_ids':[self.invoices[invoice]] if sid==self.sid and invoice in self.invoices else [],'ambiguous':False,'candidates':[]}

    def operation_proof(self,con,sid,operation):
        row=con.execute('SELECT o.payment_id FROM erp_full_operations o JOIN payments p ON p.id=o.payment_id WHERE o.operation_id=? AND p.student_id=?',(operation,sid)).fetchone()
        if not row:return {'payment_ids':[],'ambiguous':False,'candidates':[]}
        p=con.execute('SELECT * FROM payments WHERE id=?',(row[0],)).fetchone()
        invoice={'income-september':'716063','income-october':'726247'}.get(operation,'')
        return {'payment_ids':[row[0]],'ambiguous':False,'candidates':[{'payment_id':row[0],'source_kind':'payments',
            'source_invoice_id':invoice,'amount':p['amount'],'paid_at':p['paid_at'],'source_method':'online',
            'evidence_id':f'archive:{invoice}:{operation}','source_signature':'synthetic-'+operation}]}

    def refund_proof(self,con,sid,operation):
        return {'candidates':[self.refunds[operation]] if sid==self.sid and operation in self.refunds else [],'ambiguous':False}

    def backup(self):
        self.con.commit()
        path=Path(self.tmp.name)/f'backup-{len(list(Path(self.tmp.name).glob("backup-*")))}.sqlite3'
        target=sqlite3.connect(path);self.con.backup(target);target.close()
        return str(path)

    def apply(self,body=None):
        body=body or self.body
        report=hr.plan(self.con,body)
        self.assertTrue(report['can_apply'],report['issues'])
        return hr.apply(self.con,body,report['token'],self.owner,self.backup())

    def fingerprint(self):
        return self.con.serialize()

    def assert_blocked_unchanged(self,body,fragment=None):
        before=self.fingerprint();report=hr.plan(self.con,body)
        self.assertFalse(report['can_apply'])
        if fragment:self.assertTrue(any(fragment in issue for issue in report['issues']),report['issues'])
        with self.assertRaises(hr.HistoricalRefundError):hr.apply(self.con,body,report['token'],self.owner,None)
        self.assertEqual(self.fingerprint(),before)
        return report

    def test_documented_fifo_split_requires_explicit_source_reconciliation(self):
        before=money.balance(self.con,self.sid)
        self.assertEqual((before['paid_total'],before['deposit']),(17550,5400))
        sources=subcancel._sources(self.con,self.con.execute('SELECT * FROM subscriptions WHERE id=?',(self.target,)).fetchone())
        self.assertEqual([(row['payment_id'],row['amount']) for row in sources],[(self.september,5400),(self.october,4050)])
        unresolved=copy.deepcopy(self.body);unresolved['payment_allocations']=[]
        self.assert_blocked_unchanged(unresolved,'распределение')

    def test_actual_missing_identifiers_is_only_a_blocked_preview(self):
        body=copy.deepcopy(self.body);body['refund_operation_id']='';body['subscription_id']=None
        body['receipt']['fn']='';body['receipt']['issued_at']=''
        result=self.assert_blocked_unchanged(body)
        self.assertTrue(any('ФН' in issue for issue in result['issues']))
        self.assertTrue(any('время' in issue for issue in result['issues']))
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM historical_refund_runs').fetchone()[0],0)

    def test_plan_is_read_only_and_full_refund_preserves_original_income_and_visits(self):
        self.con.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES(?,?,'2026-11-04','present')",(self.sid,self.gid));self.con.commit()
        before=self.fingerprint();plan=hr.plan(self.con,self.body)
        self.assertTrue(plan['can_apply'],plan['issues']);self.assertEqual(self.fingerprint(),before)
        original=[tuple(row) for row in self.con.execute('SELECT * FROM payments ORDER BY id')]
        attendance=[tuple(row) for row in self.con.execute('SELECT * FROM attendance ORDER BY id')]
        result=self.apply();self.assertEqual(result['state'],'applied')
        sub=self.con.execute('SELECT * FROM subscriptions WHERE id=?',(self.target,)).fetchone()
        self.assertEqual((sub['status'],sub['price']),('cancelled',9450))
        refund=self.con.execute('SELECT * FROM payments WHERE id=?',(result['refund_payment_id'],)).fetchone()
        self.assertEqual((refund['amount'],refund['method'],refund['paid_at']),(-9450,'refund','2026-11-06'))
        self.assertEqual([tuple(row) for row in self.con.execute('SELECT * FROM payments WHERE amount>0 ORDER BY id')],original)
        self.assertEqual([tuple(row) for row in self.con.execute('SELECT * FROM attendance ORDER BY id')],attendance)
        self.assertEqual(result['after_summary']['paid_total'],8100)
        self.assertNotIn(self.target,result['after_summary']['covered'])
        self.assertEqual(result['after_summary']['covered'][self.first],2700)
        self.assertEqual(result['after_summary']['restricted_deposit'],5400)
        target=result['after_summary']['invoice_totals']['726247']
        self.assertEqual(tuple(target[key] for key in ('source_payment_total','refunded_total','net_paid','current_charge','covered','due')),(9450,9450,0,0,0,0))
        september=result['after_summary']['invoice_totals']['716063']
        self.assertEqual((september['source_payment_total'],september['allocated_total'],september['source_remaining']),(8100,2700,5400))
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM historical_refund_exclusions WHERE subscription_id=?',(self.target,)).fetchone()[0],1)
        audit=self.con.execute('SELECT old_price,new_price FROM historical_refund_runs').fetchone();self.assertEqual(tuple(audit),(9450,0))

    def test_exact_retry_is_idempotent_and_changed_operation_payload_is_blocked(self):
        result=self.apply();self.con.commit();before=self.fingerprint()
        self.assertTrue(hr.plan(self.con,self.body)['already_applied'])
        retry=hr.apply(self.con,self.body,'old-token',self.owner,None)
        self.assertEqual(retry['run_id'],result['run_id']);self.assertTrue(retry['already_applied'])
        self.assertEqual(self.fingerprint(),before)
        changed=copy.deepcopy(self.body);changed['receipt']['fp']='3535389345'
        self.assert_blocked_unchanged(changed)

    def test_partial_then_second_distinct_return_uses_original_allocation_once(self):
        first=copy.deepcopy(self.body);first['amount_minor']=300000
        first['refund_sources'][0]['amount']=3000
        first['receipt'].update(total_minor=300000,cashless_minor=300000)
        self.refunds['return-one']['amount']=3000
        result=self.apply(first)
        sub=self.con.execute('SELECT price,status FROM subscriptions WHERE id=?',(self.target,)).fetchone()
        self.assertEqual(tuple(sub),(6450,'paid'));self.assertEqual(result['after_summary']['covered'][self.target],6450)
        second=copy.deepcopy(self.body);second['refund_operation_id']='return-two';second['amount_minor']=645000;second['executed_at']='2026-11-07'
        second['refund_sources'][0]['amount']=6450
        second['receipt'].update(fd='3404',fp='3535389345',total_minor=645000,cashless_minor=645000,issued_at='2026-11-07T09:01:00')
        self.refunds['return-two']={**self.refunds['return-one'],'amount':6450,'executed_at':'2026-11-07','evidence_id':'archive:3:2','source_signature':'second-source'}
        self.apply(second)
        self.assertEqual(self.con.execute('SELECT status FROM subscriptions WHERE id=?',(self.target,)).fetchone()[0],'cancelled')
        self.assertEqual(self.con.execute('SELECT SUM(amount) FROM payments WHERE method="refund"').fetchone()[0],-9450)
        self.assertEqual(self.con.execute('SELECT SUM(amount) FROM erp_refund_allocations').fetchone()[0],9450)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM historical_refund_runs').fetchone()[0],2)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM historical_refund_receipts').fetchone()[0],2)

    def test_existing_real_debt_is_not_paid_by_released_locked_source(self):
        debt=self.invoice('2026-08',1000);self.invoices['old-debt']=debt;self.con.commit()
        result=self.apply()
        self.assertEqual(result['after_summary']['debt'],1000)
        self.assertEqual(result['after_summary']['covered'][debt],0)
        self.assertEqual(self.con.execute('SELECT price,status FROM subscriptions WHERE id=?',(debt,)).fetchone()['price'],1000)

    def test_wrong_source_invoice_operation_and_unexecuted_request_are_blocked(self):
        for field,value in [('source_invoice_id','716063'),('refund_operation_id','invented-op'),('executed_at','2026-11-06T12:00:00')]:
            with self.subTest(field=field):
                body=copy.deepcopy(self.body);body[field]=value;self.assert_blocked_unchanged(body)
        self.refunds['return-one']['execution_confirmed']=False
        self.assert_blocked_unchanged(self.body,'фактическое исполнение')

    def test_receipt_identity_operation_total_and_real_time_are_required(self):
        for field,value in [('fn',''),('fd',''),('fp',''),('operation',1),('total_minor',945001),('cashless_minor',0),('issued_at','2026-11-06')]:
            with self.subTest(field=field):
                body=copy.deepcopy(self.body);body['receipt'][field]=value;self.assert_blocked_unchanged(body)

    def test_same_fiscal_identity_conflict_is_global_and_never_overwritten(self):
        self.con.execute('''INSERT INTO fx_receipts(source,ext_id,ts,amount,ecash,op,fiscal_fn,fiscal_fd,fiscal_fp)
            VALUES('ofd','synthetic-existing','2026-11-06T12:30:15',945000,945000,2,'9999999999999999','3403','wrong-fp')''');self.con.commit()
        self.assert_blocked_unchanged(self.body,'другими реквизитами')
        self.assertEqual(self.con.execute('SELECT fiscal_fp FROM fx_receipts').fetchone()[0],'wrong-fp')

    def test_existing_matching_ofd_receipt_is_reused_without_duplicate(self):
        self.con.execute('''INSERT INTO fx_receipts(source,ext_id,ts,amount,ecash,op,fiscal_fn,fiscal_fd,fiscal_fp)
            VALUES('ofd','synthetic-existing','2026-11-06T12:30:15',945000,945000,2,'9999999999999999','3403','3535389344')''');self.con.commit()
        self.apply();self.assertEqual(self.con.execute('SELECT COUNT(*) FROM fx_receipts').fetchone()[0],1)

    def test_same_receipt_cannot_debit_a_second_operation(self):
        first=copy.deepcopy(self.body);first['amount_minor']=300000;first['refund_sources'][0]['amount']=3000
        first['receipt'].update(total_minor=300000,cashless_minor=300000);self.refunds['return-one']['amount']=3000
        self.apply(first)
        second=copy.deepcopy(first);second['refund_operation_id']='return-two'
        self.refunds['return-two']={**self.refunds['return-one'],'source_signature':'another-source','evidence_id':'archive:3:2'}
        self.assert_blocked_unchanged(second,'другой операцией')

    def test_backup_and_stale_plan_fail_without_partial_writes(self):
        report=hr.plan(self.con,self.body);before=self.fingerprint()
        with self.assertRaises(hr.HistoricalRefundError):hr.apply(self.con,self.body,report['token'],self.owner,None)
        self.assertEqual(self.fingerprint(),before)
        backup=self.backup()
        self.con.execute("UPDATE payments SET comment='Reviewed later' WHERE id=?",(self.october,));self.con.commit();before=self.fingerprint()
        with self.assertRaises(hr.HistoricalRefundError):hr.apply(self.con,self.body,report['token'],self.owner,backup)
        self.assertEqual(self.fingerprint(),before)

    def test_late_error_rolls_back_every_financial_and_receipt_change(self):
        report=hr.plan(self.con,self.body);backup=self.backup();before=self.fingerprint()
        original=hr._execute
        def fail_after_write(*args,**kwargs):
            original(*args,**kwargs);raise sqlite3.IntegrityError('Synthetic late failure')
        # Initial replan must succeed; the second execution is the actual write.
        calls=0
        def fail_actual(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==1:return original(*args,**kwargs)
            return fail_after_write(*args,**kwargs)
        with patch.object(hr,'_execute',side_effect=fail_actual):
            with self.assertRaises(sqlite3.IntegrityError):hr.apply(self.con,self.body,report['token'],self.owner,backup)
        self.assertEqual(self.fingerprint(),before)

    def test_existing_exact_imported_negative_operation_is_adopted_not_duplicated(self):
        pid=self.con.execute('''INSERT INTO payments(student_id,amount,method,paid_at,comment,user_name,confirmed,created_at)
            VALUES(?,-9450,'refund','2026-11-06','Existing ERP refund','ERP',1,'2026-11-10')''',(self.sid,)).lastrowid
        signature=json.dumps([self.sid,'2026-11-06',-9450,'online'],ensure_ascii=False,separators=(',',':'))
        self.con.execute('INSERT INTO erp_full_operations VALUES(?,?,?)',('return-one',pid,signature));self.con.commit()
        result=self.apply()
        self.assertEqual(result['refund_payment_id'],pid)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0],3)
        self.assertEqual(result['before_summary']['paid_total'],8100);self.assertEqual(result['after_summary']['paid_total'],8100)

    def test_cross_order_source_amount_date_and_forged_evidence_are_rejected(self):
        for field,value in [('source_invoice_id','716063'),('source_operation_id','income-september'),('evidence_id','invented-evidence')]:
            with self.subTest(field=field):
                body=copy.deepcopy(self.body);body['payment_allocations'][1][field]=value
                self.assert_blocked_unchanged(body)
        original=self.operation_proof
        from crm import historicalrefundtrace
        for field,value in [('amount',9999),('paid_at','2026-10-22'),('source_invoice_id','716063')]:
            def wrong(con,sid,op,field=field,value=value):
                proof=original(con,sid,op)
                if op=='income-october':proof['candidates'][0][field]=value
                return proof
            with self.subTest(proof_field=field),patch.object(historicalrefundtrace,'resolve_payment_operation',side_effect=wrong):
                self.assert_blocked_unchanged(self.body)

    def test_real_archive_crosswalk_end_to_end_and_source_replay(self):
        from crm import historicalrefundtrace
        def archive(kind,headers,rows,targets):
            fid=self.con.execute('INSERT INTO erp_full_files(digest,filename,kind,headers,rows,mapping,imported_at) VALUES(?,?,?,?,?,?,?)',
                (f'synthetic-{kind}','synthetic.csv',kind,json.dumps(headers,ensure_ascii=False),json.dumps(rows,ensure_ascii=False),'{}','2026-11-20')).lastrowid
            for n,(table,target) in enumerate(targets,2):
                self.con.execute('INSERT INTO erp_full_rows VALUES(?,?,?,?,?,?,?)',(fid,n,self.sid,'native' if target else 'source','Synthetic source',table,target))
            return fid
        archive('subs',['ID клиента','ID счета','ID продажи','Стоимость'],
                [['120486','716063','source-sale-sep','2700'],['120486','726247','source-sale-oct','9450']],
                [('subscriptions',self.first),('subscriptions',self.target)])
        pf=archive('payments',['ID клиента','ID счета','ID операции','Дата оплаты','Сумма','Вид оплаты','Тип платежа','Статус возврата','Дата фактического возврата'],
                [['120486','716063','income-september','2026-09-01','8100','Онлайн','Приход','',''],
                 ['120486','726247','income-october','2026-10-21','9450','Онлайн','Приход','',''],
                 ['120486','726247','return-one','2026-11-06','-9450','Онлайн','Возврат','Исполнен','2026-11-06']],
                [('payments',self.september),('payments',self.october),('',None)])
        body=copy.deepcopy(self.body)
        body['payment_allocations'][0]['evidence_id']=f'erp-file:{pf}:row:2'
        body['payment_allocations'][1]['evidence_id']=f'erp-file:{pf}:row:3'
        self.con.commit()
        with patch.object(historicalrefundtrace,'resolve_invoice',self.real_invoice),patch.object(historicalrefundtrace,'resolve_payment_operation',self.real_operation),patch.object(historicalrefundtrace,'resolve_refund_operation',self.real_refund):
            result=self.apply(body)
            self.assertEqual(result['after_summary']['paid_total'],8100)
            self.assertEqual(result['after_summary']['restricted_deposit'],5400)
            # A later source replay adopts the operation ID, not a second debit.
            self.con.execute("UPDATE erp_full_rows SET state='native',target_table='payments',target_id=? WHERE file_id=? AND row_num=4",(result['refund_payment_id'],pf))
            self.con.commit()
            self.assertTrue(hr.plan(self.con,body)['already_applied'])
            self.assertFalse(self.real_invoice(self.con,self.sid,'726247')['ambiguous'])
            self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments WHERE amount<0').fetchone()[0],1)

    def test_receipt_must_match_any_identifiers_retained_in_raw_refund_source(self):
        self.refunds['return-one'].update(fn='9999999999999999',fd='3403',fp='3535389344')
        for field,value in [('fn','8888888888888888'),('fd','3404'),('fp','3535389345')]:
            with self.subTest(field=field):
                body=copy.deepcopy(self.body);body['receipt'][field]=value
                self.assert_blocked_unchanged(body,'исходной операцией ERP')

    def test_padded_fiscal_document_number_cannot_bypass_global_receipt_deduplication(self):
        first=copy.deepcopy(self.body);first['amount_minor']=300000;first['refund_sources'][0]['amount']=3000
        first['receipt'].update(total_minor=300000,cashless_minor=300000);self.refunds['return-one']['amount']=3000
        self.apply(first)
        second=copy.deepcopy(first);second['refund_operation_id']='return-two';second['receipt']['fd']='03403'
        self.refunds['return-two']={**self.refunds['return-one'],'source_signature':'second-source','evidence_id':'archive:3:2'}
        self.assert_blocked_unchanged(second,'другой операцией')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments WHERE amount<0').fetchone()[0],1)

    def test_existing_paykeeper_receipt_cannot_prove_another_original_payment(self):
        self.con.execute('''INSERT INTO fx_receipts(source,ext_id,ts,amount,ecash,op,fiscal_fn,fiscal_fd,fiscal_fp,pk_payment,pk_status)
            VALUES('paykeeper','return-receipt','2026-11-06T12:30:15',945000,945000,2,'9999999999999999','3403','3535389344','income-september','success')''');self.con.commit()
        self.assert_blocked_unchanged(self.body,'другой исходной оплатой')
        self.con.execute("UPDATE fx_receipts SET pk_payment='income-october',pk_status='sending'");self.con.commit()
        self.assert_blocked_unchanged(self.body,'успешным статусом')
        self.con.execute("UPDATE fx_receipts SET pk_status='success'");self.con.commit()
        result=self.apply()
        self.assertEqual(result['after_summary']['paid_total'],8100)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM fx_receipts').fetchone()[0],1)

    def test_receipt_utc_and_moscow_clocks_are_same_instant_but_wrong_hour_is_blocked(self):
        self.con.execute('''INSERT INTO fx_receipts(source,ext_id,ts,amount,ecash,op,fiscal_fn,fiscal_fd,fiscal_fp)
            VALUES('ofd','utc-source','2026-11-06T09:30:15Z',945000,945000,2,'9999999999999999','3403','3535389344')''');self.con.commit()
        self.assertTrue(hr.plan(self.con,self.body)['can_apply'])
        wrong=copy.deepcopy(self.body);wrong['receipt']['issued_at']='2026-11-06T13:30:15'
        self.assert_blocked_unchanged(wrong,'другими реквизитами')
        result=self.apply()
        self.assertEqual(result['after_summary']['invoice_totals']['726247']['net_paid'],0)
        self.assertEqual(self.con.execute('SELECT issued_at FROM historical_refund_receipts').fetchone()[0],'2026-11-06T12:30:15+03:00')
        self.assertFalse(hr._same_execution('2026-11-06','2026-11-06T00:00:00+03:00'))

    def test_operator_and_fractional_ruble_refund_are_rejected(self):
        report=hr.plan(self.con,self.body);before=self.fingerprint()
        with self.assertRaises(hr.HistoricalRefundError):hr.apply(self.con,self.body,report['token'],{'id':2,'name':'Synthetic operator','role':'operator'},None)
        self.assertEqual(self.fingerprint(),before)
        body=copy.deepcopy(self.body);body['amount_minor']=945001
        self.assert_blocked_unchanged(body,'округления')

    def test_partial_adjustment_is_explicit_and_receipt_document_not_api_proof(self):
        body=copy.deepcopy(self.body);body['amount_minor']=300000;body['refund_sources'][0]['amount']=3000
        body['receipt'].update(total_minor=300000,cashless_minor=300000);self.refunds['return-one']['amount']=3000
        plan=hr.plan(self.con,body)
        self.assertEqual(plan['receipt']['evidence_kind'],'owner_document')
        self.apply(body)
        sub=self.con.execute('SELECT corrected,ledger_managed FROM subscriptions WHERE id=?',(self.target,)).fetchone()
        self.assertEqual(tuple(sub),(1,1))
        receipt=self.con.execute('SELECT evidence_kind FROM historical_refund_receipts').fetchone()
        self.assertEqual(receipt['evidence_kind'],'owner_document')
        self.assertEqual(self.con.execute('SELECT pk_status FROM fx_receipts').fetchone()[0],'document')

    def test_exact_archived_student_identity_can_resolve_a_different_native_id(self):
        self.con.execute("UPDATE students SET erp_id='native-old-id' WHERE id=?",(self.sid,))
        self.con.execute("INSERT INTO erp_full_identities VALUES('student','120486',?)",(self.sid,));self.con.commit()
        report=hr.plan(self.con,self.body)
        self.assertTrue(report['can_apply'],report['issues'])
        self.assertEqual(report['student']['id'],self.sid)

    def test_restart_does_not_manufacture_a_payment_or_repeat_refund(self):
        self.con.execute('UPDATE subscriptions SET ledger_managed=0,erp_full_managed=0');self.con.commit()
        self.apply();self.con.commit();original=[tuple(row) for row in self.con.execute('SELECT * FROM payments ORDER BY id')]
        with redirect_stdout(StringIO()):db.init_db(self.con)
        self.assertEqual([tuple(row) for row in self.con.execute('SELECT * FROM payments ORDER BY id')],original)
        self.assertTrue(hr.plan(self.con,self.body)['already_applied'])


if __name__ == '__main__':unittest.main()
