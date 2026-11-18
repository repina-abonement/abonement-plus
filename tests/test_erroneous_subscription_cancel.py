import unittest
from unittest.mock import patch
from tests import test_sale_paid_date as fixture
from crm import smart,services as sv,subcancel,money


class ErroneousCancelTest(unittest.TestCase):
    setUp=fixture.SaleDateTest.setUp

    def invoice(self):
        with patch.object(smart,'REQUIRE',False):
            result=sv.create_invoice(self.con,self.sid,'2026-11',self.u,group_id=self.gid,paid_date='2026-11-16',excluded_dates=['2026-11-16','2026-11-23','2026-11-25','2026-11-30'])
        self.con.execute('UPDATE subscriptions SET price=1350 WHERE id=?',(result['id'],))
        sv.pay_subscription(self.con,result['id'],self.u,'card_branch',950,'2026-11-16','synthetic-testpay-123')
        return result['id']

    def test_past_partial_subscription_cancels_without_changing_payments_or_marks(self):
        sub=self.invoice()
        self.con.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES (?,?,'2026-11-18','present')",(self.sid,self.gid))
        before=[dict(r) for r in self.con.execute('SELECT * FROM payments')]
        q=subcancel.error_preview(self.con,sub,self.u)
        self.assertEqual((q['paid'],q['balance_after']),(950,950))
        body={'token':q['token'],'reason':'Synthetic test subscription','confirmed_error':True}
        subcancel.cancel_error(self.con,sub,self.u,body)
        self.assertTrue(subcancel.cancel_error(self.con,sub,self.u,body)['already_cancelled'])
        self.assertEqual(before,[dict(r) for r in self.con.execute('SELECT * FROM payments')])
        self.assertEqual(money.balance(self.con,self.sid)['deposit'],950)
        self.assertEqual(money.balance(self.con,self.sid)['debt'],0)
        self.assertEqual(self.con.execute('SELECT status FROM attendance').fetchone()[0],'present')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscription_cancellations').fetchone()[0],1)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscription_refunds').fetchone()[0],0)

    def test_varya_only_not_every_senior_and_permission_uses_db_identity(self):
        uid=self.con.execute("INSERT INTO users(login,name,role,pw_hash) VALUES ('synthetic-varya','Варвара Батарева','operator','disabled')").lastrowid
        other=self.con.execute("INSERT INTO users(login,name,role,pw_hash,is_senior) VALUES ('synthetic-senior','Synthetic Senior','operator','disabled',1)").lastrowid
        sub=self.invoice()
        varya={'id':uid,'role':'operator','name':'Варвара Батарева'}
        self.assertTrue(subcancel.can_cancel_error(self.con,varya))
        forged={'id':other,'role':'operator','name':'Варвара Батарева','is_senior':1}
        with self.assertRaises(sv.ApiError):subcancel.error_preview(self.con,sub,forged)
        q=subcancel.error_preview(self.con,sub,varya)
        subcancel.cancel_error(self.con,sub,varya,{'token':q['token'],'reason':'Synthetic test error','confirmed_error':True})
        self.assertEqual(self.con.execute('SELECT status FROM subscriptions WHERE id=?',(sub,)).fetchone()[0],'cancelled')

    def test_stale_preview_and_missing_reason_rejected(self):
        sub=self.invoice();q=subcancel.error_preview(self.con,sub,self.u)
        with self.assertRaises(sv.ApiError):subcancel.cancel_error(self.con,sub,self.u,{'token':q['token'],'reason':'','confirmed_error':True})
        self.con.execute('UPDATE subscriptions SET price=1400 WHERE id=?',(sub,))
        with self.assertRaises(sv.ApiError):subcancel.cancel_error(self.con,sub,self.u,{'token':q['token'],'reason':'Synthetic stale error','confirmed_error':True})
        self.assertNotEqual(self.con.execute('SELECT status FROM subscriptions WHERE id=?',(sub,)).fetchone()[0],'cancelled')
