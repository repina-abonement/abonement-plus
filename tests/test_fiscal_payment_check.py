import unittest
from unittest.mock import patch
from tests import test_paylinks as fixture
from crm import money,fiscal,fiscalcheck

class FiscalPaymentCheck(unittest.TestCase):
    setUp=fixture.PayLinksTest.setUp
    new=fixture.PayLinksTest.new

    def payment(self):
        pid=money.add_payment(self.con,self.sid,1350,'paykeeper',{'id':0,'role':'admin','name':'Synthetic gateway'},paid_at='2026-11-18T12:05')
        link,_=self.new(1350)
        self.con.execute("UPDATE pay_links SET payment_id=?,pk_payment_id='synthetic-11534',status='paid' WHERE id=?",(pid,link['id']))
        return pid

    def test_no_receipt_is_time_bounded_not_fiscal_failure(self):
        pid=self.payment()
        with patch.object(fiscal,'pk_call',return_value=[]) as api:result=fiscalcheck.check(self.con,pid,self.user)
        self.assertEqual(result['status'],'not_found');self.assertIn('на момент проверки',result['label']);self.assertTrue(result['checked_at'])
        self.assertEqual(api.call_args.args[1],'/info/receipts/bypaymentid/')
        self.assertEqual(api.call_args.args[2],{'payment_id':'synthetic-11534'})
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0],1)

    def test_success_requires_right_payment_and_fiscal_fields(self):
        pid=self.payment()
        receipt={'id':'synthetic-receipt','payment_id':'synthetic-11534','status':'success','type':'sale','fn':'1','fnd':'2','fpd':'3','sum_cashless':'1350','obtain_datetime':'2026-11-18 12:06:00'}
        with patch.object(fiscal,'pk_call',return_value=[receipt]):result=fiscalcheck.check(self.con,pid,self.user)
        self.assertEqual(result['status'],'confirmed')
        self.assertEqual(result['receipt_id'],'synthetic-receipt')
        with patch.object(fiscal,'pk_call',side_effect=fiscal.FxError('Synthetic source unavailable')):later=fiscalcheck.check(self.con,pid,self.user)
        self.assertEqual(later['status'],'confirmed');self.assertFalse(later['source_available'])

    def test_unavailable_and_pending_are_distinct_and_no_print_api(self):
        pid=self.payment()
        with patch.object(fiscal,'pk_call',return_value={'error':'unauthorized'}) as api:result=fiscalcheck.check(self.con,pid,self.user)
        self.assertEqual(result['status'],'unavailable')
        self.assertNotIn('/change/',str(api.call_args))
        with patch.object(fiscal,'pk_call',return_value=[{'id':'r','payment_id':'synthetic-11534','status':'sending'}]):result=fiscalcheck.check(self.con,pid,self.user)
        self.assertEqual(result['status'],'pending')
        with self.assertRaises(Exception):fiscalcheck.check(self.con,pid,{'id':99,'role':'operator'})
