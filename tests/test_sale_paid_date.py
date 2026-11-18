import tempfile,unittest
from pathlib import Path
from datetime import date
from unittest.mock import patch
from crm import db,services as sv,schedule,groupterms,money,smart,server

class SaleDateTest(unittest.TestCase):
 def setUp(self):
  self.con=db.connect(str(Path(tempfile.mkdtemp())/'sale.sqlite3'));db.init_db(self.con);self.addCleanup(self.con.close)
  self.u={'id':1,'role':'admin','name':'Synthetic'}
  bid=schedule.save_branch(self.con,{'name':'ТЦ Небо'})
  self.gid=schedule.save_group(self.con,{'name':'Малыши Пн/Ср 10:00','branch_id':bid,'weekdays':[0,2],'time':'10:00','age':'1.5-3'})
  groupterms.provision_nebo_morning(self.con)
  self.sid=sv.create_student(self.con,{'name':'Synthetic Child','birth':'2025-01-01','parent_name':'Parent','phone':'+79000000101','group_id':self.gid,'funnel':'sales'},self.u)
  self.con.execute("UPDATE students SET trial_at='2026-11-16T10:00',trial_group_id=?,trial_came=1 WHERE id=?",(self.gid,self.sid))
  self.clock=patch.object(sv,'today',return_value=date(2026,11,17));self.clock.start();self.addCleanup(self.clock.stop)
 def quote(self,**kw):return sv.quote_for(self.con,self.sid,'2026-11',group_id=self.gid,paid_date='2026-11-16',**kw)
 def test_default_dates_after_trial_and_manual_override(self):
  q=self.quote();self.assertEqual(q['dates'],['2026-11-18','2026-11-23','2026-11-25','2026-11-30'])
  self.assertIn('2026-11-16',q['calendar_dates']);self.assertEqual(q['total'],5120)
  self.assertEqual(self.quote(excluded_dates=[])['lessons_paid'],5)
  self.assertEqual(self.quote(excluded_dates=[])['total'],6400)
  manual=server.quote(self.con,None,self.u,{}, {'month':'2026-11','group_id':str(self.gid),'paid_date':'2026-11-16','dates_selected':'1'}, str(self.sid))
  self.assertEqual(manual['lessons_paid'],5)
  self.assertEqual(self.con.execute('SELECT count(*) FROM subscriptions').fetchone()[0],0)
 def test_receipt_date_and_entry_date_are_separate_and_retry_is_idempotent(self):
  with patch.object(smart,'REQUIRE',False):invoice=sv.create_invoice(self.con,self.sid,'2026-11',self.u,group_id=self.gid,paid_date='2026-11-16')
  sv.pay_subscription(self.con,invoice['id'],self.u,'card_branch',5120,'2026-11-16','same-request-123')
  sv.pay_subscription(self.con,invoice['id'],self.u,'card_branch',5120,'2026-11-16','same-request-123')
  payments=list(self.con.execute('SELECT * FROM payments'));self.assertEqual(len(payments),1)
  p=payments[0];self.assertEqual(p['paid_at'],'2026-11-16');self.assertNotEqual(p['created_at'][:10],p['paid_at'])
  self.assertEqual(p['method'],'card_branch');self.assertEqual(p['amount'],5120)
  sub=self.con.execute('SELECT * FROM subscriptions WHERE id=?',(invoice['id'],)).fetchone();self.assertEqual(sub['status'],'paid');self.assertEqual(sub['paid_at'],'2026-11-16')
  with self.assertRaises(sv.ApiError):sv.pay_subscription(self.con,invoice['id'],self.u,'card_branch',4000,'2026-11-16','same-request-123')
 def test_paid_lead_appears_in_day_and_month_journal(self):
  with patch.object(smart,'REQUIRE',False):invoice=sv.create_invoice(self.con,self.sid,'2026-11',self.u,group_id=self.gid,paid_date='2026-11-16')
  def ids(day):return [r['id'] for r in sv.journal(self.con,self.gid,day,paid_only=True)['rows']]
  self.assertNotIn(self.sid,ids('2026-11-18'))
  sv.pay_subscription(self.con,invoice['id'],self.u,'card_branch',5120,'2026-11-16','journal-paid-lead-123')
  self.assertEqual(self.con.execute('SELECT funnel FROM students WHERE id=?',(self.sid,)).fetchone()[0],'sales')
  self.assertIn(self.sid,ids('2026-11-18'))
  self.assertEqual(sv.journal_month(self.con,self.gid,'2026-11')['subs']['paid'],1)
  self.assertNotIn(self.sid,ids('2026-12-02'))
  self.con.execute("UPDATE students SET archived=1 WHERE id=?",(self.sid,))
  self.assertNotIn(self.sid,ids('2026-11-18'))
 def test_future_date_rejected_without_invoice(self):
  with self.assertRaises(sv.ApiError):sv.quote_for(self.con,self.sid,'2026-11',group_id=self.gid,paid_date='2026-11-18')
  self.assertEqual(self.con.execute('SELECT count(*) FROM subscriptions').fetchone()[0],0)
 def test_partial_payment_keeps_balance_and_repeat_does_not_double(self):
  with patch.object(smart,'REQUIRE',False):invoice=sv.create_invoice(self.con,self.sid,'2026-11',self.u,group_id=self.gid,paid_date='2026-11-16')
  for _ in range(2):sv.pay_subscription(self.con,invoice['id'],self.u,'card_branch',2000,'2026-11-16','partial-request-123')
  self.assertEqual(self.con.execute('SELECT sum(amount) FROM payments').fetchone()[0],2000)
  self.assertEqual(money.balance(self.con,self.sid)['debt'],3120)

if __name__=='__main__':unittest.main()
