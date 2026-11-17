import tempfile,unittest
from pathlib import Path
from datetime import date
from unittest.mock import patch
from crm import db,services as sv,schedule,server,clientattendance

class TrialCardTest(unittest.TestCase):
 def setUp(self):
  self.con=db.connect(str(Path(tempfile.mkdtemp())/'trial.sqlite3'));db.init_db(self.con)
  self.u={'id':1,'role':'admin','name':'Test'}
  bid=schedule.save_branch(self.con,{'name':'Synthetic'})
  self.g=schedule.save_group(self.con,{'name':'Mon','branch_id':bid,'weekdays':[0,2],'time':'10:00','age':'3-5'})
  self.sid=sv.create_student(self.con,{'name':'Synthetic Child','parent_name':'Parent','phone':'+79000000001','group_id':self.g,'funnel':'sales'},self.u)
  self.con.execute("UPDATE students SET trial_at='2026-11-16T10:00',trial_group_id=?,trial_came=0 WHERE id=?",(self.g,self.sid))
  self.clock=patch.object(sv,'today',return_value=date(2026,11,17));self.clock.start();self.addCleanup(self.clock.stop);self.addCleanup(self.con.close)
 def body(self):return {'came':True,'expected_date':'2026-11-16','expected_group_id':self.g}
 def test_current_trial_updates_calendar_and_journal(self):
  e=next(e for e in clientattendance.calendar(self.con,self.sid,'2026-11')['events'] if e['kind']=='trial')
  self.assertTrue(e['trial_editable'])
  server.trial_mark(self.con,None,self.u,self.body(),{},str(self.sid))
  e=next(e for e in clientattendance.calendar(self.con,self.sid,'2026-11')['events'] if e['kind']=='trial');self.assertEqual(e['status'],'present')
  rows=sv.journal(self.con,self.g,'2026-11-16')['rows'];self.assertTrue(any(r['id']==self.sid and r.get('trial_came')==1 for r in rows))
 def test_historical_trial_is_exact_and_keeps_later_trial(self):
  tid=self.con.execute("INSERT INTO erp_trial_visits(source_id,student_id,group_id,visit_at,came) VALUES ('synthetic',?,?,'2026-11-16T10:00',0)",(self.sid,self.g)).lastrowid
  self.con.execute("UPDATE students SET trial_at='2026-11-23T10:00',trial_came=NULL WHERE id=?",(self.sid,))
  server.historical_trial_mark(self.con,None,self.u,self.body()|{'visit_id':tid},{},str(self.sid))
  e=next(e for e in clientattendance.calendar(self.con,self.sid,'2026-11')['events'] if e.get('trial_visit_id')==tid)
  self.assertTrue(e['trial_editable']);self.assertEqual(e['status'],'present')
  self.assertIsNone(sv.get_student_row(self.con,self.sid)['trial_came'])
  self.assertEqual(self.con.execute('SELECT count(*) FROM attendance').fetchone()[0],0)
  self.assertTrue(any(r.get('historical_trial_id')==tid and r['trial_came']==1 for r in sv.journal(self.con,self.g,'2026-11-16')['rows']))
 def test_stale_date_rejected(self):
  with self.assertRaises(sv.ApiError):server.trial_mark(self.con,None,self.u,self.body()|{'expected_date':'2026-11-15'},{},str(self.sid))
