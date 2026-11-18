import unittest
from unittest.mock import patch
from tests.test_sale_paid_date import SaleDateTest
from crm import services as sv, multimonth, smart, money, paylinks


class MultiMonthTest(unittest.TestCase):
    setUp=SaleDateTest.setUp

    def order(self,**kw):
        body={'months':['2026-11','2026-12'],'group_id':self.gid,'tariff':'fixed','paid_date':'2026-11-16',**kw}
        q=multimonth.preview(self.con,self.sid,body,self.u)
        return {**body,'expected_token':q['token'],'request_id':'synthetic-multi-123','mode':'invoice'},q

    def test_two_months_paid_once_with_same_totals_and_retry(self):
        body,q=self.order()
        body.update(mode='pay',amount=q['due'],method='card_branch')
        with patch.object(smart,'REQUIRE',False):
            result=multimonth.create(self.con,self.sid,body,self.u)
            again=multimonth.create(self.con,self.sid,body,self.u)
        self.assertEqual(result,again)
        self.assertEqual(result['total'],q['total']);self.assertEqual(result['due'],0)
        rows=list(self.con.execute('SELECT * FROM subscriptions ORDER BY month'))
        self.assertEqual([r['month'] for r in rows],['2026-11','2026-12'])
        self.assertEqual([r['price'] for r in rows],[x['quote']['total'] for x in q['months']])
        self.assertEqual(self.con.execute('SELECT SUM(amount) FROM payments').fetchone()[0],q['due'])
        self.assertEqual(len(rows),2)

    def test_existing_month_blocks_whole_sale(self):
        body,q=self.order()
        with patch.object(smart,'REQUIRE',False):
            sv.create_invoice(self.con,self.sid,'2026-11',self.u,group_id=self.gid,paid_date='2026-11-16')
            with self.assertRaises(sv.ApiError):multimonth.create(self.con,self.sid,body,self.u)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM subscriptions WHERE month='2026-12'").fetchone()[0],0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0],0)

    def test_failure_second_month_rolls_back_first(self):
        body,q=self.order()
        original=sv.create_invoice
        def create(*a,**kw):
            if a[2]=='2026-12':raise sv.ApiError('Synthetic second month failure')
            return original(*a,**kw)
        with patch.object(smart,'REQUIRE',False),patch.object(sv,'create_invoice',side_effect=create):
            with self.assertRaises(sv.ApiError):multimonth.create(self.con,self.sid,body,self.u)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0],0)

    def test_partial_amount_leaves_next_month_unpaid(self):
        body,q=self.order();body.update(mode='pay',amount=1000,method='card_branch')
        with patch.object(smart,'REQUIRE',False):result=multimonth.create(self.con,self.sid,body,self.u)
        self.assertEqual(result['due'],q['due']-1000)
        self.assertEqual(self.con.execute('SELECT SUM(amount) FROM payments').fetchone()[0],1000)

    def test_unauthorized_large_family_discount_and_changed_quote_rejected(self):
        many=self.con.execute("SELECT id FROM discounts WHERE rule='large_family'").fetchone()
        if not many:many=self.con.execute("SELECT id FROM discounts WHERE name='Многодетная семья'").fetchone()
        body={'months':['2026-11','2026-12'],'group_id':self.gid,'discount_id':many[0],'paid_date':'2026-11-16'}
        with self.assertRaises(sv.ApiError):multimonth.preview(self.con,self.sid,body,{'id':1,'role':'coach'})
        body,q=self.order();body['expected_token']='stale'
        with self.assertRaises(sv.ApiError):multimonth.create(self.con,self.sid,body,self.u)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0],0)

    def test_manual_discount_replaces_automatic_for_each_month(self):
        many=self.con.execute("SELECT id FROM discounts WHERE name='Многодетная семья'").fetchone()[0]
        _,base=self.order()
        body,q=self.order(discount_id=many)
        self.assertTrue(all(r['quote']['discount']['name']=='Многодетная семья' for r in q['months']))
        with patch.object(smart,'REQUIRE',False):result=multimonth.create(self.con,self.sid,body,self.u)
        self.assertEqual(result['total'],q['total'])

    def test_one_link_two_months_gateway_amount_and_callback_once(self):
        from crm import fiscal
        body,q=self.order(paid_date='2026-11-17')
        body['mode']='links'
        with patch.object(smart,'REQUIRE',False),patch.object(paylinks,'ready',return_value=True):
            result=multimonth.create(self.con,self.sid,body,self.u)
        link=result['link'];token=link['url'].rsplit('/',1)[1]
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM pay_links').fetchone()[0],1)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM pay_link_subscriptions').fetchone()[0],2)
        page=paylinks.payment_page(self.con,token)
        self.assertEqual([s['month'] for s in page['subscriptions']],['2026-11','2026-12'])
        self.assertEqual(page['amount'],q['due'])
        with patch.object(paylinks,'ready',return_value=True),patch.object(fiscal,'pk_call',side_effect=[{'token':'SYNTHETIC'},{'invoice_id':'BUNDLE1','invoice_url':'https://synthetic.paykeeper.ru/bill/BUNDLE1/'}]) as gateway:
            paylinks.checkout(self.con,token,'synthetic@example.com')
        self.assertEqual(gateway.call_args.kwargs['form']['pay_amount'],f"{q['due']:.2f}")
        order=self.con.execute('SELECT orderid FROM pay_links').fetchone()[0]
        payments=[{'id':'synthetic-provider-123','orderid':order,'status':'success','pay_amount':q['due'],'success_datetime':'2026-11-18 12:00'}]
        self.assertEqual(paylinks.apply_payments(self.con,payments),1)
        self.assertEqual(paylinks.apply_payments(self.con,payments),0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0],1)
        self.assertEqual(self.con.execute('SELECT SUM(amount) FROM payments').fetchone()[0],q['due'])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM subscriptions WHERE status='paid'").fetchone()[0],2)

    def test_bundle_link_rejects_changed_subscription(self):
        body,q=self.order(paid_date='2026-11-17');body['mode']='links'
        with patch.object(smart,'REQUIRE',False),patch.object(paylinks,'ready',return_value=True):
            result=multimonth.create(self.con,self.sid,body,self.u)
        self.con.execute('UPDATE subscriptions SET price=price+1 WHERE id=?',(result['subscriptions'][0]['id'],))
        with self.assertRaises(paylinks.PayLinkError):paylinks.receipt_link(self.con,result['link']['url'].rsplit('/',1)[1])

if __name__=='__main__':unittest.main()
