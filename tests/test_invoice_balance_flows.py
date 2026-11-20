"""Explicit balance choices on isolated invoices; no provider commands."""
from contextlib import redirect_stdout
from datetime import date
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crm import db, services as sv, money, balancecredit, fiscal, smart, paylinks, multimonth, server


class InvoiceBalanceFlowsTest(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory(prefix='invoice-balance-flow-')
        self.addCleanup(folder.cleanup)
        self.con=db.connect(str(Path(folder.name)/'isolated.sqlite3'))
        self.addCleanup(self.con.close)
        with redirect_stdout(io.StringIO()):
            db.init_db(self.con)
        self.u={'id':1,'name':'Synthetic Owner','role':'admin'}
        branch=self.con.execute("INSERT INTO branches(name) VALUES ('Synthetic balance branch')").lastrowid
        self.gid=self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time) VALUES (?,'Synthetic Wed Sun','2,6','18:45')",(branch,)).lastrowid
        self.sid=sv.create_student(self.con,{'name':'Synthetic Balance Child','parent_name':'Synthetic Parent',
            'birth':'2020-01-01','phone':'+79000000871','email':'synthetic@example.com',
            'group_id':self.gid,'funnel':'members'},self.u)
        self.con.execute('UPDATE discounts SET active=0')
        self.con.execute('UPDATE price_list SET fixed=1350,flex=1550,late_pct=0')
        for change in (patch.object(sv,'today',return_value=date(2026,11,20)),
                patch.object(smart,'REQUIRE',False),
                patch.object(fiscal,'pk_call',side_effect=AssertionError('Provider commands forbidden'))):
            change.start();self.addCleanup(change.stop)
        self.deposit_id=money.add_payment(self.con,self.sid,2700,'card_branch',self.u,comment='Synthetic existing received funds')

    def quote(self,use=False,month='2026-12'):
        return sv.quote_for(self.con,self.sid,month,group_id=self.gid,paid_date='2026-11-20',
            user=self.u,use_balance=use)

    def invoice(self,use=False,month='2026-12',key='synthetic-new-invoice-001',expected=None):
        quoted=self.quote(use,month)
        if expected is None:
            expected=quoted['balance_preview']['token']
        return sv.create_invoice(self.con,self.sid,month,self.u,group_id=self.gid,
            paid_date='2026-11-20',use_balance=use,expected_balance_token=expected,request_id=key)

    def assert_balance(self,sub,due,deposit):
        balance=money.balance(self.con,self.sid,date(2026,11,20))
        price=self.con.execute('SELECT price FROM subscriptions WHERE id=?',(sub,)).fetchone()[0]
        self.assertEqual(price-balance['covered'].get(sub,0),due)
        self.assertEqual(balance['deposit'],deposit)
        return balance

    def test_default_off_keeps_2700_and_full_12150_invoice(self):
        snapshot=self.con.serialize()
        q=self.quote()
        self.assertEqual(q['total'],12150)
        self.assertEqual({k:q['balance_preview'][k] for k in ('use_balance','available_balance','apply_amount','invoice_total','payable','remaining_balance')},
            {'use_balance':False,'available_balance':2700,'apply_amount':0,'invoice_total':12150,'payable':12150,'remaining_balance':2700})
        self.assertEqual(self.con.serialize(),snapshot)
        result=self.invoice()
        self.assert_balance(result['id'],12150,2700)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0],1)
        self.assertEqual(self.con.execute('SELECT status FROM subscriptions').fetchone()[0],'invoiced')

    def test_selected_preview_does_not_spend_and_confirm_spends_once_on_retry_or_reopen(self):
        q=self.quote(True);before=self.con.serialize()
        p=q['balance_preview']
        self.assertEqual((p['apply_amount'],p['payable'],p['remaining_balance']),(2700,9450,0))
        self.assertEqual(money.balance(self.con,self.sid)['deposit'],2700)
        result=self.invoice(True,expected=p['token'])
        again=self.invoice(True,expected=p['token'])
        self.assertEqual(result,again)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0],1)
        self.assert_balance(result['id'],9450,0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0],1)
        after=self.con.serialize()
        for selected in (False,True,False):
            current=sv.payment_quote(self.con,result['id'],'2026-11-20',selected)
            self.assertEqual((current['price'],current['total']),(12150,9450))
            self.assertEqual(current['balance_preview']['already_applied'],2700)
            self.assertEqual(current['balance_preview']['apply_amount'],0)
        self.assertEqual(self.con.serialize(),after)
        self.assertNotEqual(before,after)

    def test_explicit_existing_invoice_confirmation_and_repeated_click_are_idempotent(self):
        result=self.invoice();sub=result['id']
        preview=sv.payment_quote(self.con,sub,'2026-11-20',True)['balance_preview']
        path=f'/api/subscriptions/{sub}/balance-apply'
        route=next(fn for method,rx,fn,role in server.ROUTES if method=='POST' and rx.match(path))
        body={'use_balance':True,'expected_balance_token':preview['token'],'request_id':'synthetic-existing-confirm-001'}
        route(self.con,None,self.u,body,{},str(sub))
        first=self.con.serialize()
        route(self.con,None,self.u,body,{},str(sub))
        self.assertEqual(self.con.serialize(),first)
        self.assert_balance(sub,9450,0)
        with self.assertRaises(sv.ApiError):
            route(self.con,None,self.u,dict(body,use_balance=False),{},str(sub))

    def test_direct_invoice_cash_does_not_use_existing_balance(self):
        sub=self.invoice()['id']
        sv.pay_subscription(self.con,sub,self.u,'card_branch',12150,'2026-11-20','synthetic-direct-full-001')
        self.assert_balance(sub,0,2700)
        self.assertEqual(self.con.execute('SELECT price FROM subscriptions WHERE id=?',(sub,)).fetchone()[0],12150)

    def test_selected_balance_plus_9450_cash_pays_invoice_without_another_debit(self):
        sub=self.invoice(True)['id']
        sv.pay_subscription(self.con,sub,self.u,'card_branch',9450,'2026-11-20','synthetic-direct-rest-001')
        self.assert_balance(sub,0,0)
        self.assertEqual(self.con.execute('SELECT SUM(amount) FROM payments').fetchone()[0],12150)

    def test_partial_and_unbound_cash_stay_distinct_from_balance_application(self):
        sub=self.invoice()['id']
        sv.pay_subscription(self.con,sub,self.u,'card_branch',1000,'2026-11-20','synthetic-direct-partial-001')
        self.assert_balance(sub,11150,2700)
        money.add_payment(self.con,self.sid,500,'card_branch',self.u,comment='Synthetic unbound receipt')
        self.assert_balance(sub,11150,3200)

    def test_newer_invoice_direct_cash_does_not_pay_older_protected_invoice(self):
        old=self.invoice(month='2026-11',key='synthetic-old-protected-001')['id']
        new=self.invoice(key='synthetic-new-protected-001')['id']
        sv.pay_subscription(self.con,new,self.u,'card_branch',12150,'2026-11-20','synthetic-direct-newer-001')
        self.assert_balance(new,0,2700)
        older=self.con.execute('SELECT price FROM subscriptions WHERE id=?',(old,)).fetchone()[0]
        self.assertEqual(money.balance(self.con,self.sid)['covered'].get(old),0)
        self.assertGreater(older,0)

    def test_changed_balance_and_non_boolean_selection_cannot_create_or_debit_invoice(self):
        token=self.quote(True)['balance_preview']['token']
        money.add_payment(self.con,self.sid,1000,'card_branch',self.u,comment='Synthetic intervening receipt')
        with self.assertRaises(sv.ApiError):
            self.invoice(True,expected=token)
        for value in ('true','1',1,None):
            with self.subTest(value=value),self.assertRaises(sv.ApiError):
                sv.create_invoice(self.con,self.sid,'2026-12',self.u,group_id=self.gid,use_balance=value)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0],0)
        self.assertEqual(money.balance(self.con,self.sid)['deposit'],3700)

    def test_reusing_creation_key_for_another_choice_is_rejected(self):
        first=self.invoice()
        with self.assertRaises(sv.ApiError):
            self.invoice(True)
        self.assert_balance(first['id'],12150,2700)

    def multi(self,use=False,mode='invoice'):
        body={'months':['2026-12','2027-01'],'group_id':self.gid,'tariff':'fixed',
            'paid_date':'2026-11-20','use_balance':use,'mode':mode,'request_id':'synthetic-multi-balance-001'}
        q=multimonth.preview(self.con,self.sid,body,self.u)
        body['expected_token']=q['token']
        with patch.object(paylinks,'ready',return_value=True):
            return body,q,multimonth.create(self.con,self.sid,body,self.u)

    def test_multi_month_default_off_does_not_consume_2700(self):
        body,q,result=self.multi()
        self.assertEqual((q['credit'],q['due']),(0,q['total']))
        self.assertEqual(result['due'],q['total'])
        self.assertEqual(money.balance(self.con,self.sid)['deposit'],2700)
        self.assertEqual(result,multimonth.create(self.con,self.sid,body,self.u))

    def test_multi_month_explicit_credit_is_applied_across_months_only_once(self):
        body,q,result=self.multi(True)
        self.assertEqual((q['credit'],q['due']),(2700,q['total']-2700))
        self.assertEqual(result['due'],q['due'])
        self.assertEqual(money.balance(self.con,self.sid)['deposit'],0)
        self.assertEqual(result,multimonth.create(self.con,self.sid,body,self.u))
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0],1)

    def _callback(self,link,amount):
        order=self.con.execute('SELECT orderid FROM pay_links WHERE id=?',(link['id'],)).fetchone()[0]
        return [{'id':'synthetic-bundle-provider-001','orderid':order,'status':'success',
            'pay_amount':str(amount),'success_datetime':'2026-11-20 12:00'}]

    def _legacy_debt(self,amount=4000):
        return self.con.execute("""INSERT INTO subscriptions(student_id,month,tariff,kind,lessons,
            per_lesson,price,status,created_at,ledger_managed)
            VALUES (?,'2026-01','fixed','month',1,?,?,'invoiced','2026-01-01',1)""",
            (self.sid,amount,amount)).lastrowid

    def _link(self,sub):
        price=self.con.execute('SELECT price FROM subscriptions WHERE id=?',(sub,)).fetchone()[0]
        with patch.object(paylinks,'ready',return_value=True):
            return paylinks.create(self.con,self.sid,price,self.u,subscription_id=sub)

    def _confirm_balance(self,sub,key='synthetic-confirm-after-link-001'):
        preview=sv.payment_quote(self.con,sub,'2026-11-20',True)['balance_preview']
        return balancecredit.apply_to_invoice(self.con,sub,self.u,True,preview['token'],key)

    def test_bundle_underpayment_only_covers_exact_parts_and_retry_does_not_duplicate(self):
        body,q,result=self.multi(False,'links')
        received=q['due']-1000;callback=self._callback(result['link'],received)
        self.assertEqual(paylinks.apply_payments(self.con,callback),1)
        self.assertEqual(paylinks.apply_payments(self.con,callback),0)
        self.assertEqual(sum(sv.payment_quote(self.con,s['id'],'2026-11-20')['total'] for s in result['subscriptions']),1000)
        self.assertEqual(money.balance(self.con,self.sid)['deposit'],2700)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0],2)

    def test_bundle_overpayment_keeps_extra_money_free_without_spending_previous_balance(self):
        body,q,result=self.multi(False,'links')
        callback=self._callback(result['link'],q['due']+1000)
        self.assertEqual(paylinks.apply_payments(self.con,callback),1)
        self.assertEqual(paylinks.apply_payments(self.con,callback),0)
        self.assertEqual(sum(sv.payment_quote(self.con,s['id'],'2026-11-20')['total'] for s in result['subscriptions']),0)
        self.assertEqual(money.balance(self.con,self.sid)['deposit'],3700)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0],2)

    def test_bundle_created_before_balance_application_caps_current_due_and_keeps_surplus_free(self):
        body,q,result=self.multi(False,'links')
        first=result['subscriptions'][0]['id']
        self._confirm_balance(first)
        old=self._legacy_debt(1000)
        self.assertEqual(money.balance(self.con,self.sid)['covered'][old],0)
        callback=self._callback(result['link'],q['due'])
        self.assertEqual(paylinks.apply_payments(self.con,callback),1)
        self.assertEqual(paylinks.apply_payments(self.con,callback),0)
        balance=money.balance(self.con,self.sid)
        self.assertEqual(balance['covered'][old],0)
        self.assertEqual(balance['deposit'],2700)
        self.assertTrue(all(sv.payment_quote(self.con,s['id'],'2026-11-20')['total']==0 for s in result['subscriptions']))
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0],2)

    def test_late_callback_for_already_paid_single_invoice_records_received_money_as_free(self):
        sub=self.invoice()['id'];link=self._link(sub)
        self._confirm_balance(sub)
        sv.pay_subscription(self.con,sub,self.u,'card_branch',9450,'2026-11-20','synthetic-paid-after-link-001')
        old=self._legacy_debt(1000)
        callback=self._callback(link,12150)
        self.assertEqual(paylinks.apply_payments(self.con,callback),1)
        self.assertEqual(paylinks.apply_payments(self.con,callback),0)
        balance=self.assert_balance(sub,0,12150)
        self.assertEqual(balance['covered'][old],0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0],3)

    def test_received_payment_for_cancelled_single_invoice_is_not_discarded_or_reallocated(self):
        sub=self.invoice()['id'];link=self._link(sub)
        self.con.execute("UPDATE subscriptions SET status='cancelled' WHERE id=?",(sub,))
        old=self._legacy_debt()
        before=money.balance(self.con,self.sid)['covered'][old]
        callback=self._callback(link,12150)
        self.assertEqual(paylinks.apply_payments(self.con,callback),1)
        self.assertEqual(paylinks.apply_payments(self.con,callback),0)
        balance=money.balance(self.con,self.sid)
        self.assertEqual(balance['covered'][old],before)
        self.assertEqual(balance['deposit'],12150)
        self.assertEqual(self.con.execute('SELECT status FROM subscriptions WHERE id=?',(sub,)).fetchone()[0],'cancelled')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0],2)

    def test_mixed_cancelled_and_active_bundle_preserves_cancelled_portion_as_free(self):
        body,q,result=self.multi(False,'links')
        cancelled,active=result['subscriptions']
        self.con.execute("UPDATE subscriptions SET status='cancelled' WHERE id=?",(cancelled['id'],))
        old=self._legacy_debt()
        before=money.balance(self.con,self.sid)['covered'][old]
        callback=self._callback(result['link'],q['due'])
        self.assertEqual(paylinks.apply_payments(self.con,callback),1)
        self.assertEqual(paylinks.apply_payments(self.con,callback),0)
        balance=self.assert_balance(active['id'],0,cancelled['price'])
        self.assertEqual(balance['covered'][old],before)
        self.assertEqual(self.con.execute('SELECT status FROM subscriptions WHERE id=?',(cancelled['id'],)).fetchone()[0],'cancelled')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0],2)

    def test_legacy_single_already_covered_after_link_keeps_original_payment_sources(self):
        self.con.execute('UPDATE payments SET cancelled=1 WHERE id=?',(self.deposit_id,))
        paid=self._legacy_debt(1000);link=self._link(paid)
        original=money.add_payment(self.con,self.sid,2000,'card_branch',self.u,comment='Synthetic old unbound receipt')
        old=self._legacy_debt(4000)
        before=money.subscription_payment_details(self.con,self.sid)
        self.assertEqual(before[paid][0]['payment_id'],original)
        callback=self._callback(link,1000)
        self.assertEqual(paylinks.apply_payments(self.con,callback),1)
        self.assertEqual(paylinks.apply_payments(self.con,callback),0)
        after=money.subscription_payment_details(self.con,self.sid)
        self.assertEqual(before[paid],after[paid]);self.assertEqual(before[old],after[old])
        balance=money.balance(self.con,self.sid)
        self.assertEqual((balance['covered'][paid],balance['covered'][old],balance['deposit']),(1000,1000,1000))
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments WHERE cancelled=0').fetchone()[0],2)

    def test_legacy_bundle_covered_and_cancelled_parts_do_not_change_old_fifo_sources(self):
        self.con.execute('UPDATE payments SET cancelled=1 WHERE id=?',(self.deposit_id,))
        paid=self._legacy_debt(1000);cancelled=self._legacy_debt(1000)
        with patch.object(paylinks,'ready',return_value=True):
            link=paylinks.create_bundle(self.con,self.sid,[paid,cancelled],self.u)
        self.con.execute("UPDATE subscriptions SET status='cancelled' WHERE id=?",(cancelled,))
        original=money.add_payment(self.con,self.sid,1000,'card_branch',self.u,comment='Synthetic old receipt for first invoice')
        old=self._legacy_debt(4000)
        before=money.subscription_payment_details(self.con,self.sid)
        callback=self._callback(link,2000)
        self.assertEqual(paylinks.apply_payments(self.con,callback),1)
        self.assertEqual(paylinks.apply_payments(self.con,callback),0)
        after=money.subscription_payment_details(self.con,self.sid)
        self.assertEqual(before[paid],after[paid]);self.assertEqual(after[paid][0]['payment_id'],original)
        self.assertEqual(after[old],[])
        self.assertEqual(money.balance(self.con,self.sid)['deposit'],2000)
        self.assertEqual(self.con.execute('SELECT status FROM subscriptions WHERE id=?',(cancelled,)).fetchone()[0],'cancelled')

    def test_existing_balance_confirmation_persists_same_repricing_as_preview_and_replays_before_pricing(self):
        sub=self.invoice()['id']
        self.con.execute('UPDATE price_list SET late_pct=10')
        self.con.execute("UPDATE subscriptions SET created_at='2026-11-20T10:00' WHERE id=?",(sub,))
        path=f'/api/subscriptions/{sub}/balance-apply'
        route=next(fn for method,rx,fn,role in server.ROUTES if method=='POST' and rx.match(path))
        with patch.object(sv,'today',return_value=date(2026,11,26)):
            before=self.con.serialize()
            q=sv.payment_quote(self.con,sub,'2026-11-26',True)
            self.assertEqual((q['price'],q['total']),(13365,10665))
            self.assertEqual(self.con.serialize(),before)
            body={'use_balance':True,'expected_balance_token':q['balance_preview']['token'],
                'request_id':'synthetic-repriced-credit-001','paid_date':'2026-11-26'}
            route(self.con,None,self.u,body,{},str(sub))
            self.assertEqual(self.con.execute('SELECT price FROM subscriptions WHERE id=?',(sub,)).fetchone()[0],13365)
            self.assert_balance(sub,10665,0)
            self.con.execute('UPDATE price_list SET late_pct=20')
            applied=self.con.serialize()
            route(self.con,None,self.u,body,{},str(sub))
            self.assertEqual(self.con.serialize(),applied)
            self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0],1)

    def test_stale_balance_confirmation_rolls_back_its_repricing_and_allocation(self):
        sub=self.invoice()['id']
        self.con.execute('UPDATE price_list SET late_pct=10')
        self.con.execute("UPDATE subscriptions SET created_at='2026-11-20T10:00' WHERE id=?",(sub,))
        path=f'/api/subscriptions/{sub}/balance-apply'
        route=next(fn for method,rx,fn,role in server.ROUTES if method=='POST' and rx.match(path))
        body={'use_balance':True,'expected_balance_token':'synthetic-stale-token',
            'request_id':'synthetic-stale-repricing-001','paid_date':'2026-11-26'}
        before=self.con.serialize()
        with patch.object(sv,'today',return_value=date(2026,11,26)),self.assertRaises(sv.ApiError):
            route(self.con,None,self.u,body,{},str(sub))
        self.assertEqual(self.con.serialize(),before)
        self.assert_balance(sub,12150,2700)


if __name__=='__main__':
    unittest.main()
