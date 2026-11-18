import unittest
from unittest.mock import patch
from tests import test_sale_paid_date as fixture
from crm import services as sv,smart,paylinks,fiscal

class PaymentPurpose(unittest.TestCase):
    setUp=fixture.SaleDateTest.setUp

    def test_new_invoice_uses_child_and_brand_but_keeps_orderid(self):
        self.con.execute("UPDATE students SET name='Синтетическая Анна' WHERE id=?",(self.sid,))
        with patch.object(smart,'REQUIRE',False):sub=sv.create_invoice(self.con,self.sid,'2026-11',self.u,group_id=self.gid,paid_date='2026-11-16')['id']
        with patch.object(paylinks,'ready',return_value=True):link=paylinks.create(self.con,self.sid,5120,self.u,'Old noisy description',sub)
        self.assertEqual(link['purpose'],'Абонемент Синтетическая Анна. Футболика')
        row=self.con.execute('SELECT * FROM pay_links WHERE id=?',(link['id'],)).fetchone()
        orderid=row['orderid'];token=link['url'].rsplit('/',1)[1]
        with patch.object(paylinks,'ready',return_value=True),patch.object(fiscal,'pk_call',side_effect=[{'token':'SYNTHETIC'},{'invoice_id':'SYNTHETIC-1','invoice_url':'https://synthetic.paykeeper.ru/bill/1/'}]) as gateway:
            paylinks.checkout(self.con,token,'synthetic@example.com')
        sent=gateway.call_args.kwargs['form']
        self.assertEqual(sent['service_name'],link['purpose'])
        self.assertEqual(sent['orderid'],orderid)
        self.assertEqual(sent['pay_amount'],'5120.00')

    def test_existing_link_purpose_not_rewritten(self):
        with patch.object(paylinks,'ready',return_value=True):link=paylinks.create(self.con,self.sid,100,self.u,'Existing custom purpose')
        self.assertEqual(self.con.execute('SELECT purpose FROM pay_links WHERE id=?',(link['id'],)).fetchone()[0],'Existing custom purpose')
        self.assertEqual(paylinks.customer_payment_purpose('  Synthetic   Child  ',True),'Разовое занятие Synthetic Child. Футболика')
