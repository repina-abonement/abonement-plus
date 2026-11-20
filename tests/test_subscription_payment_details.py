import unittest
from unittest.mock import patch
from tests import test_sale_paid_date as fixture
from crm import money,smart,services as sv,paylinks

class SubscriptionPaymentDetails(unittest.TestCase):
    setUp=fixture.SaleDateTest.setUp

    def invoice(self,month='2026-11'):
        with patch.object(smart,'REQUIRE',False):return sv.create_invoice(self.con,self.sid,month,self.u,group_id=self.gid,paid_date='2026-11-16')['id']

    def test_partial_manual_payment_has_method_date_and_unknown_receipt(self):
        sub=self.invoice();sv.pay_subscription(self.con,sub,self.u,'card_branch',950,'2026-11-16','payment-details-123')
        details=money.subscription_payment_details(self.con,self.sid)[sub]
        self.assertEqual(len(details),1)
        self.assertEqual((details[0]['amount'],details[0]['method'],details[0]['paid_at']),(950,'card_branch','2026-11-16'))
        self.assertEqual(details[0]['receipt']['status'],'unknown')

    def test_successful_receipt_requires_exact_payment_id_and_success(self):
        sub=self.invoice()
        pid=money.add_payment(self.con,self.sid,5120,'paykeeper',{'id':0,'role':'admin','name':'Synthetic Gateway'},subscription_id=sub,paid_at='2026-11-18T12:02:31')
        with patch.object(paylinks,'ready',return_value=True):link=paylinks.create(self.con,self.sid,5120,self.u)
        self.con.execute("UPDATE pay_links SET payment_id=?,pk_payment_id='provider-one',status='paid' WHERE id=?",(pid,link['id']))
        self.con.execute("INSERT INTO fx_receipts(source,ext_id,ts,amount,pk_payment,pk_status) VALUES ('paykeeper','unrelated','2026-11-18T12:02',512000,'provider-other','success')")
        self.assertEqual(money.subscription_payment_details(self.con,self.sid)[sub][0]['receipt']['status'],'unknown')
        self.con.execute("INSERT INTO fx_receipts(source,ext_id,ts,amount,pk_payment,pk_status) VALUES ('paykeeper','receipt-one','2026-11-18T12:03',512000,'provider-one','sending')")
        self.assertEqual(money.subscription_payment_details(self.con,self.sid)[sub][0]['receipt']['status'],'pending')
        self.con.execute("UPDATE fx_receipts SET pk_status='failed' WHERE ext_id='receipt-one'")
        self.assertEqual(money.subscription_payment_details(self.con,self.sid)[sub][0]['receipt']['status'],'failed')
        self.con.execute("UPDATE fx_receipts SET pk_status='success',fiscal_fn='fn',fiscal_fd='fd',fiscal_fp='fp' WHERE ext_id='receipt-one'")
        info=money.subscription_payment_details(self.con,self.sid)[sub][0]
        self.assertEqual(info['receipt']['status'],'confirmed')
        self.assertEqual(info['receipt']['receipt_id'],'receipt-one')
        self.assertEqual(info['paid_at'],'2026-11-18T12:02:31')

    def test_single_payment_allocates_to_months_without_duplicate_total(self):
        first=self.invoice();second=self.invoice('2026-12')
        # New invoices do not consume an unbound balance automatically. This
        # scenario is one received bundle with explicitly identified invoices.
        parts=[{'subscription_id':sub,'amount':self.con.execute('SELECT price FROM subscriptions WHERE id=?',(sub,)).fetchone()[0]}
               for sub in (first,second)]
        money.add_payment(self.con,self.sid,20000,'card_branch',self.u,paid_at='2026-11-18T12:02',invoice_parts=parts)
        details=money.subscription_payment_details(self.con,self.sid)
        covered=money.balance(self.con,self.sid)['covered']
        for sub in (first,second):self.assertEqual(sum(p['amount'] for p in details[sub]),covered[sub])
        self.assertEqual({p['payment_id'] for rows in details.values() for p in rows},{self.con.execute('SELECT id FROM payments').fetchone()[0]})
        self.assertEqual(sum(p['amount'] for rows in details.values() for p in rows),sum(covered.values()))
