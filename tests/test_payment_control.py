import json,unittest
from unittest.mock import patch
from datetime import date
from crm import money,paymentcontrol,fiscal,tochkacheck
from tests.test_fiscal_payment_check import FiscalPaymentCheck
from tests.test_tochka_registry_probe import RegistryProbe

class PaymentControl(unittest.TestCase):
    setUp=FiscalPaymentCheck.setUp
    new=FiscalPaymentCheck.new
    payment=FiscalPaymentCheck.payment
    def test_all_methods_and_empty_period_read_only(self):
        ids=[money.add_payment(self.con,self.sid,100,key,self.user,paid_at='2026-11-18') for key in money.MANUAL_METHODS]
        before=self.con.total_changes
        result=money.registry(self.con,'2026-11-01','2026-11-30')
        self.assertEqual(result['control_summary']['total'],len(ids))
        self.assertEqual(result['control_summary']['attention'],len(ids))
        self.assertEqual({r['id'] for r in result['rows']},set(ids))
        self.assertTrue(all(r['control']['status']=='attention' for r in result['rows']))
        self.assertEqual(self.con.total_changes,before)
        self.assertEqual(money.registry(self.con,'2025-01-01','2025-01-02')['control_summary']['total'],0)
    def test_provider_and_receipt_never_substitute_bank_posting_and_refund_is_visible(self):
        pid=self.payment()
        cache={'checked_at':'2026-11-19T16:00','status':'fiscal_details','provider_payment':{'id':'synthetic-11534','available':True,'status':'obtained','pay_amount':'1350','refund_amount':'0'},'ofd_check':{'status':'unavailable','provider_message':'Kkt is not paid'}}
        self.con.execute('INSERT INTO fiscal_payment_checks VALUES(?,?,?,?)',(pid,cache['checked_at'],cache['status'],json.dumps(cache)))
        p=self.con.execute('SELECT * FROM payments WHERE id=?',(pid,)).fetchone()
        state=paymentcontrol.state(self.con,p)
        self.assertEqual(state['source']['status'],'confirmed');self.assertEqual(state['status'],'attention')
        self.assertIn('ОФД: Kkt is not paid',state['issues'])
        cache['provider_payment']['refund_amount']='100'
        self.con.execute('UPDATE fiscal_payment_checks SET result=?',(json.dumps(cache),))
        self.assertEqual(paymentcontrol.state(self.con,p)['source']['status'],'unverified')
        cache['provider_payment']['id']='different'
        self.con.execute('UPDATE fiscal_payment_checks SET result=?',(json.dumps(cache),))
        self.assertEqual(paymentcontrol.state(self.con,p)['source']['status'],'unverified')
        self.con.execute('UPDATE payments SET cancelled=1 WHERE id=?',(pid,))
        self.assertEqual(paymentcontrol.state(self.con,self.con.execute('SELECT * FROM payments WHERE id=?',(pid,)).fetchone())['status'],'excluded')
    def test_confirmed_receipt_does_not_stop_provider_polling(self):
        from crm import fiscalcheck
        from datetime import datetime
        pid=self.payment()
        fiscal.store_pk(self.con,[],[{'id':'synthetic-receipt','payment_id':'synthetic-11534','status':'success','type':'sale','fn':'1','fnd':'2','fpd':'3','sum_cashless':'1350','obtain_datetime':'2026-11-18 12:10:00'}],datetime(2026,11,18,12,10))
        self.con.execute('INSERT INTO fiscal_payment_checks VALUES(?,?,?,?)',(pid,'2026-11-18T12:20','confirmed','{"status":"confirmed","label":"Чек пробит"}'))
        with patch.object(fiscal,'pk_call',return_value=[]) as provider:
            result=fiscalcheck.run_automatic(self.con,now=datetime(2026,11,19,12,20))
        self.assertEqual(result['checked'],1)
        self.assertEqual(provider.call_count,3)

    def test_manual_paykeeper_remains_unlinked_without_creating_another_payment(self):
        pid=money.add_payment(self.con,self.sid,1350,'paykeeper_manual',self.user,paid_at='2026-11-18')
        p=self.con.execute('SELECT * FROM payments WHERE id=?',(pid,)).fetchone()
        state=paymentcontrol.state(self.con,p)
        self.assertEqual(state['source']['status'],'unlinked')
        self.assertIn('повторный зачёт',' '.join(state['issues']))
        self.assertEqual(self.con.execute('SELECT count(*) FROM payments').fetchone()[0],1)

class DelayedBankSettlement(unittest.TestCase):
    setUp=RegistryProbe.setUp
    payment=RegistryProbe.payment
    backend=RegistryProbe.backend
    def test_later_day_and_weekend_included_without_claiming_settlement(self):
        fiscal.comms.put(self.con,'fx_tochka_token','synthetic')
        seen=[]
        def statement(con,start,end):
            seen.append((start,end))
            return [{'transactionId':'synthetic-bank-operation','endToEndId':'synthetic-trx','status':'Booked','creditDebitIndicator':'Credit','Amount':{'amount':'1343.25'},'documentProcessDate':'2026-11-20'}]
        with patch.object(fiscal,'tochka_get',side_effect=self.backend),patch.object(fiscal,'fetch_tochka',side_effect=statement):
            r=tochkacheck.inspect(self.con,self.payment(),{'sbp_trxId':'synthetic-trx'},as_of=date(2026,11,23))
        self.assertEqual(seen,[(date(2026,11,18),date(2026,11,23))])
        self.assertEqual(r['statement_period']['to'],'2026-11-23')
        self.assertEqual(len(r['statement_candidates']),1)
        self.assertEqual(r['status'],'unverified')
    def test_historical_window_limit_is_explicit(self):
        fiscal.comms.put(self.con,'fx_tochka_token','synthetic')
        with patch.object(fiscal,'tochka_get',side_effect=self.backend),patch.object(fiscal,'fetch_tochka',return_value=[]):
            r=tochkacheck.inspect(self.con,self.payment(),{'sbp_trxId':'synthetic-trx'},as_of=date(2027,1,1))
        self.assertTrue(r['coverage_limited'])
        self.assertEqual(r['statement_period']['to'],'2026-12-19')
