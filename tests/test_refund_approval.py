import tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
from crm import db,services as sv,money,smart,paylinks,subcancel,refundapproval,tasks,server

class RefundApprovalTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.con=db.connect(str(Path(self.tmp.name)/'approval.sqlite3'));self.addCleanup(self.con.close);db.init_db(self.con)
        self.owner={'id':1,'role':'admin','name':'Synthetic owner'};self.admin={'id':1,'role':'operator','is_senior':1,'name':'Synthetic operator'}
        branch=self.con.execute("INSERT INTO branches(name) VALUES ('Synthetic')").lastrowid
        gid=self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time,capacity) VALUES (?,'Synthetic','2','10:00',12)",(branch,)).lastrowid
        self.sid=sv.create_student(self.con,{'name':'Synthetic Child','birth':'2021-01-01','parent_name':'Parent','phone':'+79000000101','group_id':gid,'funnel':'members'},self.owner)
        with patch.object(smart,'REQUIRE',False):self.sub=sv.create_invoice(self.con,self.sid,'2026-12',self.owner,group_id=gid,paid_date='2026-11-18')['id']
        self.con.execute('UPDATE subscriptions SET lessons=1,price=1350,lesson_schedule=? WHERE id=?',(json.dumps([{'group_id':gid,'rate':1,'dates':['2026-12-02']}]),self.sub))
        pid=money.add_payment(self.con,self.sid,1350,'paykeeper',{'id':0,'role':'admin','name':'Synthetic gateway'},subscription_id=self.sub,paid_at='2026-11-18T12:05')
        with patch.object(paylinks,'ready',return_value=True):link=paylinks.create(self.con,self.sid,1350,self.owner)
        self.con.execute("UPDATE pay_links SET status='paid',payment_id=?,pk_payment_id='synthetic-payment' WHERE id=?",(pid,link['id']))
        self.other=tasks.add_task(self.con,'manual',self.sid,'2026-11-20T23:59',title='Keep this existing task')
        self.other_sale=tasks.add_task(self.con,'why_not_bought',self.sid,'2026-11-21T23:59',title='Keep existing sales task')
        self.body={'mode':'refund','reason':'Synthetic refund test','effective_date':'2026-12-01','expected_releasable':1350,'approved_by_yulia':True,'approval':'2026-11-18'}

    def request(self):return server.cancel(self.con,None,self.admin,self.body,{},str(self.sub))['request_id']

    def test_only_owner_approves_and_spoofed_checkbox_does_not_prepare(self):
        rid=self.request()
        self.assertEqual(self.con.execute('SELECT status FROM subscriptions WHERE id=?',(self.sub,)).fetchone()[0],'paid')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscription_cancellations').fetchone()[0],0)
        with self.assertRaises(sv.ApiError):refundapproval.review(self.con,rid,self.admin,{'decision':'approve'})
        with self.assertRaises(sv.ApiError):refundapproval.prepare(self.con,rid,self.admin)
        self.assertEqual(self.request(),rid)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM tasks WHERE source='refund'").fetchone()[0],1)

    def test_approval_prepare_customer_confirmation_preserve_other_tasks(self):
        rid=self.request();refundapproval.review(self.con,rid,self.owner,{'decision':'approve'})
        with patch.object(subcancel,'_refunds',return_value=[]):result=refundapproval.prepare(self.con,rid,self.admin)
        self.assertTrue(result['ok']);self.assertEqual(result['state'],'prepared')
        tid=self.con.execute('SELECT control_task_id FROM refund_requests WHERE id=?',(rid,)).fetchone()[0]
        with self.assertRaises(Exception):tasks.complete(self.con,tid,self.admin,'done without proof')
        with self.assertRaises(sv.ApiError):refundapproval.received(self.con,rid,self.admin,{'customer_received':True,'proof':'Synthetic client confirmation'})
        self.con.execute("UPDATE subscription_refunds SET status='receipt_pending'")
        refundapproval.received(self.con,rid,self.admin,{'customer_received':True,'proof':'Synthetic client confirmed receipt'})
        self.assertEqual(self.con.execute('SELECT status FROM tasks WHERE id=?',(tid,)).fetchone()[0],'done')
        self.assertEqual(self.con.execute('SELECT status FROM tasks WHERE id=?',(self.other,)).fetchone()[0],'open')
        self.assertEqual(self.con.execute('SELECT status FROM tasks WHERE id=?',(self.other_sale,)).fetchone()[0],'open')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0],2)

    def test_bad_provider_response_keeps_approved_request_and_no_financial_mutation(self):
        rid=self.request();refundapproval.review(self.con,rid,self.owner,{'decision':'approve'})
        with patch.object(subcancel,'_refunds',side_effect=sv.ApiError('Provider response not verified')):
            result=refundapproval.prepare(self.con,rid,self.owner)
        self.assertFalse(result['ok']);self.assertEqual(result['state'],'approved')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscription_cancellations').fetchone()[0],0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0],1)
        self.assertEqual(self.con.execute('SELECT status FROM tasks WHERE id=?',(self.other,)).fetchone()[0],'open')
        self.assertEqual(self.con.execute('SELECT status FROM tasks WHERE id=?',(self.other_sale,)).fetchone()[0],'open')

    def test_reject_closes_only_own_control_task(self):
        rid=self.request();refundapproval.review(self.con,rid,self.owner,{'decision':'reject','comment':'Synthetic rejection'})
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE source='refund'").fetchone()[0],'cancelled')
        self.assertEqual(self.con.execute('SELECT status FROM tasks WHERE id=?',(self.other,)).fetchone()[0],'open')
        self.assertEqual(self.con.execute('SELECT status FROM tasks WHERE id=?',(self.other_sale,)).fetchone()[0],'open')
