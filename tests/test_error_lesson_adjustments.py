import json
import unittest
from unittest.mock import patch
from tests import test_sale_paid_date as fixture
from crm import services as sv,smart,subcancel,money

class ErrorLessonAdjustments(unittest.TestCase):
    setUp=fixture.SaleDateTest.setUp

    def sale(self):
        with patch.object(smart,'REQUIRE',False):
            sub=sv.create_invoice(self.con,self.sid,'2026-11',self.u,group_id=self.gid,paid_date='2026-11-16')['id']
        sv.pay_subscription(self.con,sub,self.u,'card_branch',950,'2026-11-16','lesson-pay-123')
        return sub

    def body(self,sub,keep,key='adjustment-request-123'):
        q=subcancel.error_lesson_preview(self.con,sub,self.u,keep)
        return {'keep':keep,'token':q['token'],'reason':'Synthetic incorrect lesson','confirmed_error':True,'request_id':key},q

    def test_remove_one_preserves_payment_recalculates_and_is_idempotent(self):
        sub=self.sale();initial=subcancel.error_lesson_preview(self.con,sub,self.u)
        keep=initial['keep'][:-1];body,q=self.body(sub,keep)
        before=[dict(r) for r in self.con.execute('SELECT * FROM payments')]
        self.assertEqual(q['new_price'],3840)
        result=subcancel.adjust_error_lessons(self.con,sub,self.u,body)
        self.assertEqual(result,subcancel.adjust_error_lessons(self.con,sub,self.u,body))
        row=self.con.execute('SELECT * FROM subscriptions WHERE id=?',(sub,)).fetchone()
        self.assertEqual((row['price'],row['lessons']),(3840,3))
        self.assertEqual(before,[dict(r) for r in self.con.execute('SELECT * FROM payments')])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscription_lesson_adjustments').fetchone()[0],1)
        self.assertEqual(self.con.execute("SELECT due_at FROM tasks WHERE auto_key=?",(f'subscription-renew:{self.sid}',)).fetchone()[0],'2026-11-22T23:59')

    def test_remove_all_cancels_without_refund_or_payment_loss(self):
        sub=self.sale();body,q=self.body(sub,[])
        subcancel.adjust_error_lessons(self.con,sub,self.u,body)
        self.assertEqual(self.con.execute('SELECT status FROM subscriptions WHERE id=?',(sub,)).fetchone()[0],'cancelled')
        self.assertEqual(money.balance(self.con,self.sid)['deposit'],950)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscription_refunds').fetchone()[0],0)

    def test_foreign_date_stale_and_unprivileged_rejected(self):
        sub=self.sale();q=subcancel.error_lesson_preview(self.con,sub,self.u)
        with self.assertRaises(sv.ApiError):subcancel.error_lesson_preview(self.con,sub,self.u,[{'group_id':self.gid,'date':'2026-11-01'}])
        with self.assertRaises(sv.ApiError):subcancel.error_lesson_preview(self.con,sub,{'id':999,'role':'operator'})
        body,_=self.body(sub,q['keep'][:-1]);self.con.execute('UPDATE subscriptions SET price=price+1 WHERE id=?',(sub,))
        with self.assertRaises(sv.ApiError):subcancel.adjust_error_lessons(self.con,sub,self.u,body)

    def test_weighted_components_use_saved_rates(self):
        sub=self.sale()
        other=self.con.execute("INSERT INTO groups(branch_id,name,weekdays) SELECT branch_id,'Weighted synthetic','2' FROM groups WHERE id=?",(self.gid,)).lastrowid
        parts=[{'group_id':self.gid,'rate':1,'dates':['2026-11-18']},{'group_id':other,'rate':2,'dates':['2026-11-18']}]
        self.con.execute('UPDATE subscriptions SET lesson_schedule=?,price=1350,lessons=2 WHERE id=?',(json.dumps(parts),sub))
        body,q=self.body(sub,[{'group_id':self.gid,'date':'2026-11-18'}])
        self.assertEqual(q['new_price'],450)
        subcancel.adjust_error_lessons(self.con,sub,self.u,body)
        self.assertEqual(money.balance(self.con,self.sid)['deposit'],500)
