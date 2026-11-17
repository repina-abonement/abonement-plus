"""One authoritative discount, manual-only family benefit, role guard and unchanged old invoices."""
import tempfile,unittest,json,threading,urllib.request,urllib.error
from pathlib import Path
from datetime import date,datetime
from unittest.mock import patch
from crm import db,services as sv,money,schedule,smart,paylinks,fiscal,comms,server

class DiscountSelectionTest(unittest.TestCase):
 def setUp(self):
  self.path=str(Path(tempfile.mkdtemp())/'discounts.sqlite3');self.con=db.connect(self.path);db.init_db(self.con);self.addCleanup(self.con.close)
  self.user=dict(self.con.execute("SELECT * FROM users WHERE role='admin' AND active=1 LIMIT 1").fetchone())
  bid=schedule.save_branch(self.con,{'name':'СК Западный'})
  self.gid=schedule.save_group(self.con,{'branch_id':bid,'weekdays':[5,6],'time':'10:00','age':'3–5','capacity':12})
  self.sid=sv.create_student(self.con,{'name':'Synthetic Child','birth':'2020-01-01','phone':'+79000000441','parent_name':'Parent','group_id':self.gid,'funnel':'members'},self.user)
  self.con.execute('UPDATE discounts SET active=0')
  self.many=self.con.execute("SELECT id FROM discounts WHERE name='Многодетная семья'").fetchone()[0]
  self.con.execute('UPDATE discounts SET active=1 WHERE id=?',(self.many,))
  for attribute,value in [('today',date(2026,11,17))]:
   clock=patch.object(sv,attribute,return_value=value);clock.start();self.addCleanup(clock.stop)
  required=patch.object(smart,'REQUIRE',False);required.start();self.addCleanup(required.stop)
  self.exclude=[d for d in sv.quote_for(self.con,self.sid,'2026-11',group_id=self.gid)['calendar_dates'] if d!='2026-11-21']
 def quote(self,discount=None,user=None):
  return sv.quote_for(self.con,self.sid,'2026-11',group_id=self.gid,excluded_dates=self.exclude,discount_id=discount,user=user)
 def add_other_auto(self):
  self.con.execute("UPDATE discounts SET active=1 WHERE rule='first_on_trial_day'")
  self.con.execute("UPDATE students SET trial_at='2026-11-17T10:00' WHERE id=?",(self.sid,))
 def test_default_1350_manual_1215_and_manual_wins_over_auto_20(self):
  q=self.quote();self.assertEqual(q['total'],1350);self.assertIsNone(q['discount'])
  expected={r[0] for r in self.con.execute('SELECT id FROM discounts WHERE active=1')}
  self.assertEqual({r['id'] for r in sv.student_detail(self.con,self.sid)['discounts']},expected)
  self.add_other_auto();self.assertEqual(self.quote()['total'],1080)
  expected={r[0] for r in self.con.execute('SELECT id FROM discounts WHERE active=1')}
  self.assertEqual({r['id'] for r in sv.student_detail(self.con,self.sid)['discounts']},expected)
  manual=self.quote(self.many,self.user)
  self.assertEqual(manual['total'],1215);self.assertEqual(manual['discount']['amount'],135)
  self.assertFalse(manual['discount']['auto']);self.assertEqual(manual['discount']['id'],self.many)
  self.assertEqual(self.quote(self.many,{'role':'operator'})['total'],1215)
 def test_misclassified_large_family_never_automatic_and_no_stacking(self):
  self.add_other_auto()
  self.con.execute("UPDATE discounts SET active=0 WHERE rule='first_on_trial_day'")
  self.con.execute("UPDATE discounts SET rule='first_on_trial_day' WHERE id=?",(self.many,))
  self.assertEqual(self.quote()['total'],1350);self.assertFalse(money.auto_discounts(self.con,self.sid,date(2026,11,17)))
  self.assertEqual(self.quote(self.many,self.user)['total'],1215)
  for bad in ([self.many,123],str(self.many)+',123'):
   with self.assertRaises(sv.ApiError):self.quote(bad,self.user)
 def test_non_administrator_denied_at_service_and_direct_http(self):
  for user in [None,{'role':'coach'},{'role':'trainee'},{'role':'lawyer'}]:
   with self.assertRaises(sv.ApiError) as e:self.quote(self.many,user)
   self.assertEqual(e.exception.status,403)
  uid=self.con.execute("INSERT INTO users(login,name,role,pw_hash) VALUES ('test-coach','Synthetic Coach','coach','fake')").lastrowid
  self.con.execute('INSERT INTO sessions(token,user_id,created_at) VALUES (?,?,?)',('synthetic-coach-session',uid,datetime.now().isoformat()))
  old=db.DB_PATH;db.DB_PATH=self.path;self.addCleanup(setattr,db,'DB_PATH',old)
  http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);threading.Thread(target=http.serve_forever,daemon=True).start();self.addCleanup(http.server_close);self.addCleanup(http.shutdown)
  for method,path,payload in [('GET',f'/api/students/{self.sid}/quote?month=2026-11&discount_id={self.many}',None),('POST',f'/api/students/{self.sid}/invoice',{'month':'2026-11','discount_id':self.many,'group_id':self.gid,'excluded_dates':self.exclude})]:
   req=urllib.request.Request(f'http://127.0.0.1:{http.server_port}'+path,data=json.dumps(payload).encode() if payload else None,headers={'Cookie':'crm_session=synthetic-coach-session','Content-Type':'application/json'},method=method)
   with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(req)
   self.assertEqual(e.exception.code,403)
  self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0],0)
 def test_quote_saved_link_and_gateway_match_old_records_unchanged(self):
  self.add_other_auto()
  q=self.quote(self.many,self.user)
  invoice=sv.create_invoice(self.con,self.sid,'2026-11',self.user,group_id=self.gid,excluded_dates=self.exclude,discount_id=self.many)
  saved=dict(self.con.execute('SELECT * FROM subscriptions WHERE id=?',(invoice['id'],)).fetchone())
  self.assertEqual(q['total'],invoice['quote']['total']);self.assertEqual(saved['price'],1215);self.assertEqual(saved['discount_amount'],135)
  for k,v in [('fx_pk_server','https://school.server.paykeeper.ru'),('fx_pk_user','FAKE'),('fx_pk_password','FAKE')]:comms.put(self.con,k,v)
  calls=[]
  def gateway(con,path,params=None,form=None):
   calls.append((path,form));return {'token':'FAKE'} if path.endswith('token/') else {'invoice_id':'FAKE1','invoice_url':'https://school.server.paykeeper.ru/bill/FAKE1/'}
  with patch.object(fiscal,'pk_call',side_effect=gateway):
   link=paylinks.create(self.con,self.sid,invoice['quote']['total'],self.user,subscription_id=invoice['id'],full_payment=True)
   token=link['url'].rsplit('/',1)[1]
   self.assertEqual(paylinks.payment_page(self.con,token)['amount'],1215)
   paylinks.checkout(self.con,token,'receipt@example.com')
  self.assertEqual(calls[-1][1]['pay_amount'],'1215.00')
  before=[tuple(r) for r in self.con.execute('SELECT * FROM subscriptions')]
  payments=[tuple(r) for r in self.con.execute('SELECT * FROM payments')]
  links=[tuple(r) for r in self.con.execute('SELECT * FROM pay_links')]
  self.con.execute('UPDATE discounts SET percent=35 WHERE id=?',(self.many,))
  self.quote();self.quote(self.many,self.user)
  self.assertEqual(before,[tuple(r) for r in self.con.execute('SELECT * FROM subscriptions')])
  self.assertEqual(payments,[tuple(r) for r in self.con.execute('SELECT * FROM payments')])
  self.assertEqual(links,[tuple(r) for r in self.con.execute('SELECT * FROM pay_links')])

 def test_program_manual_choice_is_one_discount_too(self):
  from crm import program
  bid=schedule.save_branch(self.con,{'name':'Арктика'})
  pid=program.ensure_sbornaya(self.con)
  gid=schedule.save_group(self.con,{'branch_id':bid,'weekdays':[1,6],'time':'18:30','age':'6–8','name':'Сборная Арктика'})
  self.con.execute("UPDATE groups SET program_id=?,lesson_kind='arena' WHERE id=?",(pid,gid))
  program.set_plan(self.con,self.sid,pid,'arena_only',None,self.user)
  q=sv.quote_for(self.con,self.sid,'2026-12',group_id=gid,discount_id=self.many,user=self.user)
  self.assertEqual(q['discount']['id'],self.many);self.assertFalse(q['discount']['auto'])
  self.assertEqual(q['discount']['amount'],int(q['base']*0.1))
  self.assertEqual(q['total'],q['base']-q['discount']['amount'])
  with self.assertRaises(sv.ApiError) as e:sv.quote_for(self.con,self.sid,'2026-12',group_id=gid,discount_id=self.many,user={'role':'coach'})
  self.assertEqual(e.exception.status,403)

if __name__=='__main__':unittest.main()

