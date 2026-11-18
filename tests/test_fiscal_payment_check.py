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
        self.assertEqual(result['status'],'unknown');self.assertEqual(result['label'],'Первичный чек не проверен');self.assertIn('Пустой ответ не означает',result['note']);self.assertTrue(result['checked_at'])
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

    def test_automatic_backlog_without_button_only_reads_and_is_bounded(self):
        from datetime import datetime
        pid=self.payment()
        with patch.object(fiscal,'pk_call',return_value=[]) as api:
            first=fiscalcheck.run_automatic(self.con)
            second=fiscalcheck.run_automatic(self.con)
        self.assertEqual(first['checked'],1)
        self.assertEqual(second['checked'],0)
        self.assertEqual(api.call_count,1)
        self.assertEqual(api.call_args.args[1],'/info/receipts/bypaymentid/')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0],1)

    def test_wrong_receipt_amount_does_not_confirm(self):
        pid=self.payment()
        receipt={'id':'wrong-amount','payment_id':'synthetic-11534','status':'success',
                 'fn':'1','fnd':'2','fpd':'3','sum_cashless':'1351','type':'sale'}
        with patch.object(fiscal,'pk_call',return_value=[receipt]):
            result=fiscalcheck.check(self.con,pid,self.user)
        self.assertNotEqual(result['status'],'confirmed')

    def test_old_success_cache_without_fiscal_identity_is_rechecked(self):
        pid=self.payment()
        self.con.execute("INSERT INTO fx_receipts(source,ext_id,ts,amount,pk_payment,pk_status) VALUES('paykeeper','legacy','2026-11-18',135000,'synthetic-11534','success')")
        self.con.execute("INSERT INTO fiscal_payment_checks VALUES(?, '2000-01-01T00:00','confirmed',?)",(pid,'{"status":"confirmed","label":"Чек пробит"}'))
        self.assertNotEqual(money.payment_receipt_info(self.con,self.con.execute('SELECT * FROM payments WHERE id=?',(pid,)).fetchone())['status'],'confirmed')
        with patch.object(fiscal,'pk_call',return_value=[]) as api:
            result=fiscalcheck.run_automatic(self.con)
        self.assertEqual(result['checked'],1)
        self.assertEqual(api.call_count,1)
