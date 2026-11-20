"""Receipt email gate, legacy links, idempotence and actual payment reconciliation."""
import json,tempfile,unittest,threading,urllib.request,urllib.error
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch
from crm import db,comms,fiscal,money,paylinks,services as sv,smart,server

class PayLinksTest(unittest.TestCase):
 def setUp(self):
  self.path=str(Path(tempfile.mkdtemp())/'paylinks.sqlite3');self.con=db.connect(self.path);db.init_db(self.con);self.addCleanup(self.con.close)
  self.user={'id':1,'name':'Test','role':'admin'}
  bid=self.con.execute("INSERT INTO branches(name) VALUES ('Synthetic')").lastrowid
  self.gid=self.con.execute("INSERT INTO groups(branch_id,name,weekdays) VALUES (?,'Saturday','5')",(bid,)).lastrowid
  self.sid=sv.create_student(self.con,{'name':'Synthetic Child','birth':'2020-01-01','parent_name':'Parent','phone':'+79000000001','email':'saved@example.com','funnel':'members','group_id':self.gid},self.user)
  self.con.execute("UPDATE parents SET email='saved@example.com' WHERE family_id=(SELECT family_id FROM students WHERE id=?)",(self.sid,))
  self.con.execute('UPDATE discounts SET active=0')
  for k,v in [('fx_pk_server','https://school.server.paykeeper.ru'),('fx_pk_user','FAKE'),('fx_pk_password','FAKE')]:comms.put(self.con,k,v)
  self.sent=[]
  def gateway(con,path,params=None,form=None):
   self.sent.append((path,form))
   return {'token':'FAKE_TOKEN'} if path.endswith('token/') else {'invoice_id':'INV1','invoice_url':'https://school.server.paykeeper.ru/bill/INV1/'}
  self.mock=patch.object(fiscal,'pk_call',side_effect=gateway);self.provider=self.mock.start();self.addCleanup(self.mock.stop)
 def new(self,amount=5400,sub=None):
  link=paylinks.create(self.con,self.sid,amount,self.user,subscription_id=sub)
  return link,link['url'].rsplit('/',1)[1]
 def test_no_provider_invoice_until_valid_customer_email(self):
  link,token=self.new();self.assertIn('/pay/',link['url']);self.assertFalse(self.sent)
  for email in ('','bad','x@bad','x@@a.ru','a..b@example.com','x@example.com\nBcc: x@evil.ru',None):
   with self.assertRaises(paylinks.PayLinkError):paylinks.checkout(self.con,token,email)
  self.assertFalse(self.sent)
  result=paylinks.checkout(self.con,token,'customer@example.com')
  self.assertEqual(result,paylinks.checkout(self.con,token,'another@example.com'))
  self.assertEqual(len(self.sent),2)
  form=self.sent[-1][1];self.assertEqual(form['client_email'],'customer@example.com');self.assertEqual(form['pay_amount'],'5400.00')
  self.assertEqual(self.con.execute('SELECT email FROM parents').fetchone()[0],'saved@example.com')
  self.assertEqual(self.con.execute('SELECT receipt_email FROM pay_links').fetchone()[0],'customer@example.com')
 def test_checkout_fills_empty_card_and_prefills_next_link(self):
  self.con.execute("UPDATE parents SET email=''")
  _,token=self.new()
  paylinks.checkout(self.con,token,'  receipt@example.org  ')
  self.assertEqual(self.con.execute('SELECT email FROM parents').fetchone()[0],'receipt@example.org')
  self.assertEqual(paylinks.payment_page(self.con,self.new()[1])['email'],'receipt@example.org')
  paylinks.checkout(self.con,token,'different@example.org')
  self.assertEqual(self.con.execute('SELECT email FROM parents').fetchone()[0],'receipt@example.org')

 def test_multiple_parents_only_designated_primary_receives_email(self):
  self.con.execute("UPDATE parents SET email='',is_primary=0")
  fid=self.con.execute('SELECT family_id FROM students WHERE id=?',(self.sid,)).fetchone()[0]
  second=self.con.execute("INSERT INTO parents(family_id,name) VALUES (?,'Second')",(fid,)).lastrowid
  paylinks.checkout(self.con,self.new()[1],'receipt@example.org')
  self.assertEqual(self.con.execute("SELECT COUNT(*) FROM parents WHERE email!=''").fetchone()[0],0)
  self.con.execute("UPDATE parents SET is_primary=1 WHERE id=?",(second,))
  paylinks.checkout(self.con,self.new()[1],'primary@example.org')
  self.assertEqual([r[0] for r in self.con.execute('SELECT email FROM parents ORDER BY id')],['','primary@example.org'])

 def test_cancelled_expired_paid_or_cancelled_subscription_do_not_open(self):
  link,token=self.new();paylinks.cancel(self.con,link['id'])
  with self.assertRaises(paylinks.PayLinkError):paylinks.checkout(self.con,token,'a@example.com')
  link,token=self.new();self.con.execute('UPDATE pay_links SET created_at=? WHERE id=?',((datetime.now()-timedelta(days=15)).isoformat(),link['id']))
  with self.assertRaises(paylinks.PayLinkError):paylinks.checkout(self.con,token,'a@example.com')
  link,token=self.new();self.con.execute("UPDATE pay_links SET status='paid' WHERE id=?",(link['id'],))
  with self.assertRaises(paylinks.PayLinkError):paylinks.checkout(self.con,token,'a@example.com')
  with patch.object(smart,'REQUIRE',False):invoice=sv.create_invoice(self.con,self.sid,'2026-12',self.user)
  link,token=self.new(invoice['quote']['total'],invoice['id'])
  self.con.execute("UPDATE subscriptions SET status='cancelled' WHERE id=?",(invoice['id'],))
  with self.assertRaises(paylinks.PayLinkError):paylinks.checkout(self.con,token,'a@example.com')
  self.assertFalse(self.sent)
 def test_legacy_url_and_payment_reconciliation_are_preserved(self):
  self.con.execute("INSERT INTO pay_links(student_id,amount,orderid,url,created_at) VALUES (?,1,'legacy','https://school.server.paykeeper.ru/bill/OLD/',?)",(self.sid,datetime.now().isoformat()))
  self.assertEqual(paylinks.listing(self.con,self.sid)[0]['url'],'https://school.server.paykeeper.ru/bill/OLD/')
  with patch.object(smart,'REQUIRE',False):invoice=sv.create_invoice(self.con,self.sid,'2026-12',self.user)
  link,token=self.new(invoice['quote']['total'],invoice['id']);paylinks.checkout(self.con,token,'receipt@example.com')
  order=self.con.execute('SELECT orderid FROM pay_links WHERE id=?',(link['id'],)).fetchone()[0]
  payment={'id':'fake-payment','orderid':order,'status':'success','pay_amount':str(link['amount']),'success_datetime':datetime.now().isoformat()}
  self.assertEqual(paylinks.apply_payments(self.con,[payment]),1);self.assertEqual(paylinks.apply_payments(self.con,[payment]),0)
  self.assertEqual(self.con.execute('SELECT status FROM subscriptions WHERE id=?',(invoice['id'],)).fetchone()[0],'paid')
  self.assertEqual(self.con.execute('SELECT count(*) FROM payments WHERE subscription_id=?',(invoice['id'],)).fetchone()[0],1)
  with self.assertRaises(paylinks.PayLinkError):paylinks.checkout(self.con,token,'receipt@example.com')
 def test_public_http_gate_and_uncertain_attempt_do_not_duplicate(self):
  old=db.DB_PATH;db.DB_PATH=self.path;self.addCleanup(setattr,db,'DB_PATH',old)
  service=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);thread=threading.Thread(target=service.serve_forever,daemon=True);thread.start()
  self.addCleanup(service.server_close);self.addCleanup(service.shutdown)
  link,token=self.new();base=f'http://127.0.0.1:{service.server_port}'
  with urllib.request.urlopen(base+'/pay/'+token) as response:
   page=response.read().decode();self.assertIn('Email для чека',page);self.assertIn('required',page);self.assertNotIn('saved@example.com',page)
  def post(email):
   req=urllib.request.Request(base+'/api/public/pay-links/'+token+'/checkout',data=json.dumps({'email':email}).encode(),headers={'Content-Type':'application/json'})
   try:
    with urllib.request.urlopen(req) as response:return response.status,json.load(response)
   except urllib.error.HTTPError as error:return error.code,json.load(error)
  self.assertEqual(post('')[0],400);self.assertFalse(self.sent)
  self.provider.side_effect=[{'token':'FAKE_TOKEN'},fiscal.FxError('timeout')]
  self.assertEqual(post('a@example.com')[0],400)
  self.assertEqual(self.con.execute('SELECT checkout_state FROM pay_links WHERE id=?',(link['id'],)).fetchone()[0],'uncertain')
  count=self.provider.call_count;self.assertEqual(post('a@example.com')[0],400);self.assertEqual(self.provider.call_count,count)

 def test_payment_summary_prefill_and_no_other_family_data(self):
  with patch.object(smart,'REQUIRE',False):invoice=sv.create_invoice(self.con,self.sid,'2026-12',self.user)
  link,token=self.new(400,invoice['id'])
  summary=paylinks.payment_page(self.con,token)
  self.assertEqual(summary['child'],'Synthetic Child');self.assertEqual(summary['amount'],400)
  self.assertEqual(summary['email'],'saved@example.com');self.assertEqual(summary['subscription']['month'],'2026-12')
  self.assertEqual(summary['subscription']['price'],invoice['quote']['total'])
  self.assertTrue(summary['subscription']['groups'][0]['dates'])
  self.assertNotIn('phone',json.dumps(summary));self.assertNotIn('parents',json.dumps(summary))
  self.assertFalse(self.sent)
  paylinks.checkout(self.con,token,'chosen@example.com')
  second,second_token=self.new(400,invoice['id'])
  self.assertEqual(paylinks.payment_page(self.con,second_token)['email'],'chosen@example.com')
  self.assertEqual(self.con.execute('SELECT email FROM parents').fetchone()[0],'saved@example.com')
  # An already-created invoice keeps the same fiscal address even if the card changes.
  self.con.execute("UPDATE parents SET email='changed@example.com'")
  self.assertEqual(paylinks.payment_page(self.con,token)['email'],'chosen@example.com')
  other=sv.create_student(self.con,{'name':'Other Child','birth':'2020-01-01','parent_name':'Other Parent','phone':'+79000000002','email':'other@example.com','funnel':'members','group_id':self.gid},self.user)
  self.con.execute("UPDATE parents SET email='other@example.com' WHERE family_id=(SELECT family_id FROM students WHERE id=?)",(other,))
  other_link=paylinks.create(self.con,other,400,self.user)
  self.assertEqual(paylinks.payment_page(self.con,other_link['url'].rsplit('/',1)[1])['email'],'other@example.com')
  self.con.execute('UPDATE pay_links SET subscription_id=? WHERE id=?',(invoice['id'],other_link['id']))
  with self.assertRaises(paylinks.PayLinkError):paylinks.payment_page(self.con,other_link['url'].rsplit('/',1)[1])
 def test_invalid_saved_email_stays_empty_and_closed_link_has_no_summary(self):
  link,token=self.new(400)
  self.con.execute("UPDATE parents SET email='invalid'")
  self.assertEqual(paylinks.payment_page(self.con,token)['email'],'')
  paylinks.cancel(self.con,link['id'])
  with self.assertRaises(paylinks.PayLinkError):paylinks.payment_page(self.con,token)

if __name__=='__main__':unittest.main()
