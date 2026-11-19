"""Independent server control with durable retries, no live financial operations."""
import copy
import json
import os
import socket
import subprocess
import sys
import threading
import unittest
from datetime import datetime,timedelta
from unittest.mock import patch
from crm import fiscal, fiscalcheck, bankcheck, paykeepercheck, paymentworker, settlementproof, db, paymentcontrol
from tests.test_fiscal_payment_check import FiscalPaymentCheck


class SettlementProof(unittest.TestCase):
    def setUp(self):
        self.payment={'available':True,'status':'obtained','pay_amount':'1350','refund_amount':'0'}
        self.block={'paymentId':'exact-batch-reference','paymentType':'sbp','totalAmount':'2350','payments':[
            {'operationId':'9191886','status':'APPROVED','amount':'1350','commission':'6.75','enrollmentAmount':'1343.25'},
            {'operationId':'another-operation','status':'APPROVED','amount':'1000','commission':'5','enrollmentAmount':'995'}]}
        self.transaction={'paymentId':'exact-batch-reference','transactionId':'bank-booked-id','status':'Booked',
            'creditDebitIndicator':'Credit','Amount':{'amount':'2338.25','currency':'RUB'},'documentProcessDate':'2026-11-19'}
    def prove(self):return settlementproof.prove([self.block],[self.transaction],{'9191886'},self.payment)
    def test_complete_batch_fee_and_net(self):
        result=self.prove();self.assertEqual(result['status'],'confirmed')
        self.assertEqual((result['gross_minor'],result['fee_minor'],result['net_minor']),(235000,1175,233825))
        self.assertEqual(result['operation_count'],2)
    def test_amount_and_date_are_not_enough(self):
        self.transaction['paymentId']='other';self.assertEqual(self.prove()['status'],'unverified')
    def test_missing_part_fee_duplicate_currency_pending_or_refund_never_green(self):
        for mutation in ('missing_part','fee','duplicate','currency','pending','refund','partial_refund','missing_refund_total'):
            with self.subTest(mutation=mutation):
                block=copy.deepcopy(self.block);transaction=copy.deepcopy(self.transaction);payment=dict(self.payment)
                if mutation=='missing_part':block['payments'].pop()
                if mutation=='fee':block['payments'][1]['commission']='4'
                if mutation=='duplicate':block['payments'][1]['operationId']='9191886'
                if mutation=='currency':transaction['Amount']['currency']='USD'
                if mutation=='pending':transaction['status']='Pending'
                if mutation=='refund':block['payments'][1]['status']='REFUNDED'
                if mutation=='partial_refund':payment['refund_amount']='100'
                if mutation=='missing_refund_total':del payment['refund_amount']
                self.assertEqual(settlementproof.prove([block],[transaction],{'9191886'},payment)['status'],'unverified')
    def test_ambiguous_bank_or_registry_never_green(self):
        self.assertEqual(settlementproof.prove([self.block],[self.transaction,self.transaction],{'9191886'},self.payment)['status'],'unverified')
        self.assertEqual(settlementproof.prove([self.block,self.block],[self.transaction],{'9191886'},self.payment)['status'],'unverified')


class PaymentWorker(unittest.TestCase):
    def setUp(self):
        FiscalPaymentCheck.setUp(self)
        source=patch('crm.payoutcheck.refresh_source',return_value={'days_checked':0,'days_unavailable':0})
        source.start();self.addCleanup(source.stop)
    new=FiscalPaymentCheck.new
    payment=FiscalPaymentCheck.payment
    current=datetime(2026,11,19,14)
    def configure(self):
        fiscal.comms.put(self.con,'fx_tochka_token','synthetic')
        fiscal.comms.put(self.con,'fx_ofd_token','synthetic')
    def cache(self,pid):
        data={'provider_payment_id':'synthetic-11534','primary_receipt':{'status':'fiscal_details','fn':'synthetic-fn','fd':'3483','fp':'synthetic-fp','total_minor':135000,'issued_at':'2026-11-18T12:10:00'},
            'bank_payment':{'available':True,'id':'synthetic-11534','status':'obtained','pay_amount':'1350','refund_amount':'0','system_description':'SBP-TOCHKA','obtain_datetime':'2026-11-18 12:05:19'},
            'sbp_identifiers':{'sbp_operationId':'9191886'}}
        self.con.execute('INSERT INTO fiscal_payment_checks VALUES(?,?,?,?)',(pid,'2026-11-18T12:20','fiscal_details',json.dumps(data)))
        return data
    def test_access_errors_and_restoration_retry_without_pk_call(self):
        pid=self.payment();self.configure();self.cache(pid)
        denied={'status':'unverified','registry_status':'permission_denied','http_status':403}
        with patch('crm.payoutcheck.run_bank',side_effect=lambda con,*a,**kw: (con.execute('INSERT OR REPLACE INTO bank_payment_checks VALUES(?,?,?,?)',(pid,'synthetic-11534','2026-11-19T14:00:00',json.dumps(denied))) and {'checked':1})),patch.object(paykeepercheck,'inspect',side_effect=AssertionError('Bank must not read PK')):
            paymentworker.cycle(self.con,'tochka',self.current)
        self.assertIn('банковскую выписку',bankcheck.state(self.con,pid)['note'])
        with patch.object(paykeepercheck,'primary_ofd',return_value={'status':'unavailable','http_status':403,'provider_message':'Kkt is not paid'}):
            paymentworker.cycle(self.con,'ofd',self.current)
        p=self.con.execute('SELECT * FROM payments WHERE id=?',(pid,)).fetchone()
        self.assertIn('ОФД: Kkt is not paid',paymentcontrol.state(self.con,p)['issues'])
        later=self.current+timedelta(minutes=11)
        # No manual retry or settings edit is required once provider access is restored.
        with patch('crm.payoutcheck.run_bank',return_value={'checked':1}) as bank:
            paymentworker.cycle(self.con,'tochka',later);bank.assert_called_once()
        good={'status':'confirmed','note':'Exact receipt','fn':'synthetic-fn','fd':'3483','fp':'synthetic-fp','total_minor':135000,'issued_at':'2026-11-18T12:10:00'}
        with patch.object(paykeepercheck,'primary_ofd',return_value=good):paymentworker.cycle(self.con,'ofd',later)
        self.assertEqual(self.con.execute("SELECT count(*) FROM fx_receipts WHERE source='ofd'").fetchone()[0],1)
        self.assertEqual(self.con.execute('SELECT count(*) FROM payments').fetchone()[0],1)
    def test_durable_schedule_and_expired_lease(self):
        self.payment();self.configure()
        with patch.object(fiscalcheck,'run_automatic',return_value={'checked':1}) as check:
            paymentworker.cycle(self.con,'paykeeper',self.current)
            path=self.con.execute('PRAGMA database_list').fetchone()[2]
            second=db.connect(path)
            try:
                self.assertTrue(paymentworker.cycle(second,'paykeeper',self.current+timedelta(minutes=1))['skipped'])
                self.assertEqual(check.call_count,1)
                paymentworker.cycle(second,'paykeeper',self.current+timedelta(minutes=11))
                self.assertEqual(check.call_count,2)
                second.execute("UPDATE payment_control_jobs SET next_at=NULL,lease_until='2000-01-01',status='running' WHERE source='paykeeper'")
                paymentworker.cycle(second,'paykeeper',self.current+timedelta(minutes=22))
                self.assertEqual(check.call_count,3)
            finally:second.close()
    def test_crashed_process_lease_is_recovered_without_waiting_an_hour(self):
        self.payment();self.configure()
        # A genuinely exited process, not a guessed PID.
        proc=subprocess.Popen([sys.executable,'-c','pass']);proc.wait()
        dead=f'{socket.gethostname()}:{proc.pid}:test'
        self.con.execute("INSERT INTO payment_control_jobs(source,status,lease_owner,lease_until) VALUES('paykeeper','running',?,'2099-01-01')",(dead,))
        with patch.object(fiscalcheck,'run_automatic',return_value={'checked':1}) as check:
            paymentworker.cycle(self.con,'paykeeper',self.current);check.assert_called_once()
    def test_slow_source_does_not_block_other_threads_or_database_writer(self):
        self.payment();self.configure()
        path=self.con.execute('PRAGMA database_list').fetchone()[2]
        entered=threading.Event();release=threading.Event();bank_done=threading.Event();ofd_done=threading.Event();stop=threading.Event()
        def slow(con,*a,**kw):
            self.assertFalse(con.in_transaction);entered.set();release.wait(4);return {'checked':1}
        def bank(*a,**kw):bank_done.set();return {'checked':1}
        def ofd(*a,**kw):ofd_done.set();return {'checked':1}
        with patch.object(fiscalcheck,'run_automatic',side_effect=slow),patch.object(bankcheck,'run_automatic',side_effect=bank),patch.object(paymentworker,'run_ofd',side_effect=ofd),patch.object(fiscal,'run',side_effect=AssertionError('Legacy fiscal calls forbidden')):
            threads=paymentworker.start_worker(lambda:db.connect(path),stop,sources=('paykeeper','tochka','ofd'))
            try:
                self.assertTrue(entered.wait(2));self.assertTrue(bank_done.wait(2));self.assertTrue(ofd_done.wait(2))
                self.con.execute("INSERT INTO hook_log(ts,source,payload) VALUES('2026-11-19','wazzup','{}')")
            finally:
                stop.set();release.set()
                for thread in threads:thread.join(3)
            self.assertTrue(all(not t.is_alive() for t in threads))
    def test_bank_and_ofd_continue_with_cached_identity_when_pk_is_down(self):
        pid=self.payment();self.configure();self.cache(pid)
        with patch.object(fiscal,'pk_call',side_effect=fiscal.FxError('offline')):
            paymentworker.cycle(self.con,'paykeeper',self.current)
        cache=json.loads(self.con.execute('SELECT result FROM fiscal_payment_checks WHERE payment_id=?',(pid,)).fetchone()[0])
        self.assertEqual(cache['primary_receipt']['fd'],'3483')
        with patch('crm.payoutcheck.run_bank',return_value={'checked':1}) as bank,patch.object(paykeepercheck,'primary_ofd',return_value={'status':'unavailable'}) as ofd:
            paymentworker.cycle(self.con,'tochka',self.current);paymentworker.cycle(self.con,'ofd',self.current)
            bank.assert_called_once();self.assertEqual(ofd.call_args.args[1]['fd'],'3483')

    def test_end_to_end_three_independent_cycles_only_read_existing_payment(self):
        from tests.test_primary_receipt_options import PrimaryOptions
        pid=self.payment();self.configure()
        order=self.con.execute('SELECT orderid FROM pay_links WHERE payment_id=?',(pid,)).fetchone()[0]
        data=SettlementProof();data.setUp()
        def pk(con,path,params):
            if path=='/info/payments/byid/':return [data.payment|{'id':'synthetic-11534','orderid':order,'system_description':'SBP-TOCHKA','obtain_datetime':'2026-11-18 12:05:19'}]
            if path=='/info/options/byid/':return [PrimaryOptions.options(self,order)|{'sbp_operationId':'9191886'}]
            if path=='/info/receipts/bypaymentid/':return []
            raise AssertionError('Only read endpoints allowed')
        def bank(con,path):
            if path=='/open-banking/v1.0/accounts':return {'Data':{'Account':[{'accountId':'synthetic-account','customerCode':'synthetic-customer','currency':'RUB'}]}}
            if path.startswith('/acquiring/v1.0/retailers?'):return {'Data':{'Retailer':[{'merchantId':'synthetic-merchant'}]}}
            if path.startswith('/acquiring/v1.0/registry?'):return {'Data':{'Registry':[data.block]},'Meta':{'totalPages':1}}
            raise AssertionError('Unexpected bank endpoint')
        receipt={'fiscalDriveNumber':'1234567890123456','fiscalDocumentNumber':'123','fiscalSign':'1234567890','totalSum':135000,'operationType':1}
        before=self.con.execute('SELECT count(*) FROM payments').fetchone()[0]
        with patch.object(fiscal,'pk_call',side_effect=pk),patch.object(fiscal,'tochka_get',side_effect=bank),patch.object(fiscal,'fetch_tochka',return_value=[data.transaction]),patch.object(fiscal,'fetch_ofd_kkts',return_value=['synthetic-kkt']),patch.object(fiscal,'ofd_get',return_value={'receipts':[receipt]}),patch.object(fiscal,'run',side_effect=AssertionError('No legacy printing')):
            from tests.test_payout_matching import PayoutMatching
            item={'id':'synthetic-11534','client_name':'Parent','amount_minor':135000,'refund_minor':0,'orderid':order,'status':'obtained','paid_at':'2026-11-18 12:05:19','system_id':'94','rail':'sbp','acquirer':'tochka'}
            PayoutMatching.snapshot(self,[item])
            data.transaction.update(Amount={'amount':'1350','currency':'RUB'},DebtorParty={'name':'Parent'},documentProcessDate='2026-11-18')
            for source in ('paykeeper','tochka','ofd'):paymentworker.cycle(self.con,source,self.current)
        payment=self.con.execute('SELECT * FROM payments WHERE id=?',(pid,)).fetchone()
        control=paymentcontrol.state(self.con,payment)
        self.assertEqual(control['status'],'complete',control)
        self.assertEqual([control[k]['status'] for k in ('source','bank','ofd')],['confirmed']*3)
        self.assertEqual(self.con.execute('SELECT count(*) FROM payments').fetchone()[0],before)
        # A newly detected refund must immediately remove the all-confirmed status.
        with patch.object(fiscal,'pk_call',side_effect=lambda con,path,params: [data.payment|{'id':'synthetic-11534','orderid':order,'refund_amount':'100'}] if path=='/info/payments/byid/' else []):
            paymentworker.cycle(self.con,'paykeeper',self.current+timedelta(minutes=10))
        self.assertEqual(paymentcontrol.state(self.con,payment)['status'],'attention')

    def test_real_process_restart_recovers_unfinished_check(self):
        import tempfile
        from pathlib import Path
        self.payment();self.configure()
        database=self.con.execute('PRAGMA database_list').fetchone()[2]
        marker=Path(tempfile.mkdtemp())/'started'
        script='''import sys,time
from pathlib import Path
from unittest.mock import patch
from crm import db,fiscalcheck,paymentworker
con=db.connect(sys.argv[1])
def slow(*args,**kwargs):
 Path(sys.argv[2]).write_text('started')
 time.sleep(30)
 return {'checked':1}
with patch.object(fiscalcheck,'run_automatic',side_effect=slow):paymentworker.cycle(con,'paykeeper')
'''
        proc=subprocess.Popen([sys.executable,'-c',script,database,str(marker)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            import time
            deadline=time.monotonic()+4
            while not marker.exists() and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(marker.exists())
        finally:
            proc.kill();proc.wait()
        restart='''import sys
from unittest.mock import patch
from crm import db,fiscalcheck,paymentworker
con=db.connect(sys.argv[1])
with patch.object(fiscalcheck,'run_automatic',return_value={'checked':1}),patch('crm.payoutcheck.refresh_source',return_value={'days_checked':0}):
 result=paymentworker.cycle(con,'paykeeper')
 assert result.get('checked')==1,result
assert con.execute("SELECT status FROM payment_control_jobs WHERE source='paykeeper'").fetchone()[0]=='ok'
'''
        result=subprocess.run([sys.executable,'-c',restart,database],capture_output=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr.decode())

    def test_cached_existing_inspection_has_no_live_calls_and_no_mutations(self):
        self.payment()
        before=self.con.total_changes
        with patch.object(fiscal,'pk_call',side_effect=AssertionError('No provider calls')),patch.object(fiscal,'tochka_get',side_effect=AssertionError('No provider calls')),patch.object(fiscal,'ofd_get',side_effect=AssertionError('No provider calls')):
            result=paymentcontrol.inspect_existing(self.con,'synthetic-11534')
        self.assertTrue(result['read_only']);self.assertFalse(result['live_provider_requests'])
        self.assertEqual(result['amount_minor'],135000)
        self.assertEqual(self.con.total_changes,before)

    def test_restart_before_due_does_not_add_ten_minutes(self):
        pid=self.payment();self.configure()
        cached=self.cache(pid)
        self.con.execute('UPDATE fiscal_payment_checks SET checked_at=? WHERE payment_id=?',('2026-11-19T13:54:00',pid))
        with patch.object(fiscal,'pk_call',return_value=[]) as calls:
            result=paymentworker.cycle(self.con,'paykeeper',self.current)
            self.assertEqual(result['checked'],0);calls.assert_not_called()
            self.assertEqual(self.con.execute("SELECT status FROM payment_control_jobs WHERE source='paykeeper'").fetchone()[0],'idle')
            result=paymentworker.cycle(self.con,'paykeeper',self.current+timedelta(minutes=4))
            self.assertEqual(result['checked'],1);self.assertEqual(calls.call_count,3)

    def test_stuck_means_provider_received_but_not_bank_or_ofd_confirmed(self):
        pid=self.payment();cached=self.cache(pid)
        cached['provider_payment']=cached['bank_payment']|{'status':'stuck'}
        self.con.execute('UPDATE fiscal_payment_checks SET result=? WHERE payment_id=?',(json.dumps(cached),pid))
        payment=self.con.execute('SELECT * FROM payments WHERE id=?',(pid,)).fetchone()
        result=paymentcontrol.state(self.con,payment)
        self.assertEqual(result['source']['status'],'confirmed')
        self.assertEqual(result['status'],'attention')
        self.assertEqual(result['bank']['status'],'unverified')
        self.assertNotEqual(result['ofd']['status'],'confirmed')
        proof=SettlementProof();proof.setUp();proof.payment['status']='stuck'
        self.assertEqual(proof.prove()['status'],'confirmed')
        cached['provider_payment']['refund_amount']='100'
        self.con.execute('UPDATE fiscal_payment_checks SET result=? WHERE payment_id=?',(json.dumps(cached),pid))
        self.assertEqual(paymentcontrol.state(self.con,payment)['source']['status'],'unverified')

    def test_bank_waits_for_daily_snapshot_without_acquiring_calls(self):
        self.payment();self.configure()
        with patch.object(fiscal,'tochka_get',side_effect=AssertionError('No acquiring endpoint')):
            result=paymentworker.cycle(self.con,'tochka',self.current)
        self.assertEqual(result['checked'],0);self.assertEqual(result['waiting'],1)
