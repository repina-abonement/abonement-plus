import tempfile,unittest
from pathlib import Path
from datetime import date
from unittest.mock import patch
from crm import db,services as sv,schedule,clientattendance,server

class ChatMakeupOfferTest(unittest.TestCase):
 def setUp(self):
  self.con=db.connect(str(Path(tempfile.mkdtemp())/'offers.sqlite3'));db.init_db(self.con);self.addCleanup(self.con.close)
  self.u={'id':1,'name':'Synthetic','role':'admin'}
  b=schedule.save_branch(self.con,{'name':'Synthetic'})
  self.own=schedule.save_group(self.con,{'name':'Own','branch_id':b,'weekdays':[0,2],'time':'18:00','age':'3-5','capacity':12})
  self.target=schedule.save_group(self.con,{'name':'Extra','branch_id':b,'weekdays':[1,3],'time':'12:00','age':'3-5','capacity':12})
  self.sid=sv.create_student(self.con,{'name':'Synthetic Child','birth':'2022-01-01','parent_name':'Parent','phone':'+79000000222','group_id':self.own,'funnel':'members'},self.u)
  eid=self.con.execute('SELECT id FROM enrollments WHERE student_id=?',(self.sid,)).fetchone()[0]
  self.con.execute("UPDATE enrollments SET start_date='2026-11-01' WHERE id=?",(eid,))
  self.sub=self.con.execute("INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,price,per_lesson,status,created_at) VALUES (?,?,'2026-11','fixed',8,8000,1000,'paid','2026-11-01')",(self.sid,eid)).lastrowid
  self.con.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES (?,?,'2026-11-16','absent')",(self.sid,self.own))
  self.clock=patch.object(sv,'today',return_value=date(2026,11,18));self.clock.start();self.addCleanup(self.clock.stop)
 def test_nearest_eligible_draft_without_booking_or_sending(self):
  result=clientattendance.makeup_offer(self.con,self.sid)
  self.assertEqual(result['counters']['available'],1);self.assertTrue(result['slots'])
  self.assertEqual(result['slots'][0]['date'],'2026-11-19')
  self.assertEqual(result['slots'],sorted(result['slots'],key=lambda x:(x['date'],x['time'],x['count'],x['group'])))
  self.assertIn('\n\n',result['message']);self.assertIn('19.11 в 12:00',result['message'])
  self.assertEqual(self.con.execute('SELECT count(*) FROM makeups').fetchone()[0],0)
  self.assertEqual(self.con.execute('SELECT count(*) FROM messages').fetchone()[0],0)
 def test_no_absence_no_draft(self):
  self.con.execute('DELETE FROM attendance')
  result=clientattendance.makeup_offer(self.con,self.sid)
  self.assertEqual(result['counters']['available'],0);self.assertEqual(result['message'],'')
 def test_unpaid_target_month_no_offer(self):
  self.con.execute("UPDATE subscriptions SET status='invoiced' WHERE id=?",(self.sub,))
  result=clientattendance.makeup_offer(self.con,self.sid)
  self.assertEqual(result['message'],'');self.assertFalse(result['slots'])
 def test_summary_only_reads_counters(self):
  with patch.object(clientattendance,'suggestions',side_effect=AssertionError('summary must not scan slots')):
   result=server.client_makeup_offer(self.con,None,self.u,{}, {'summary':'1'},str(self.sid))
  self.assertEqual(result['counters']['available'],1)

 def test_missing_age_does_not_offer_arbitrary_groups(self):
  self.con.execute("UPDATE students SET birth='' WHERE id=?",(self.sid,))
  result=clientattendance.makeup_offer(self.con,self.sid)
  self.assertEqual(result['counters']['available'],1);self.assertEqual(result['message'],'');self.assertIn('дату рождения',result['reason'])
 def test_archive_is_not_offered(self):
  self.con.execute('UPDATE groups SET active=0 WHERE id=?',(self.target,))
  result=clientattendance.makeup_offer(self.con,self.sid)
  self.assertEqual(result['counters']['available'],1);self.assertFalse(result['slots']);self.assertEqual(result['message'],'')
 def test_full_date_counts_trial_booking(self):
  self.con.execute('UPDATE branches SET lesson_limit=1 WHERE id=(SELECT branch_id FROM groups WHERE id=?)',(self.target,))
  other=sv.create_student(self.con,{'name':'Synthetic Other','parent_name':'Parent','phone':'+79000000333','group_id':self.target,'funnel':'sales'},self.u)
  self.con.execute("UPDATE students SET trial_at='2026-11-19T12:00',trial_group_id=? WHERE id=?",(self.target,other))
  result=clientattendance.makeup_offer(self.con,self.sid)
  self.assertTrue(result['slots']);self.assertNotIn('2026-11-19',[s['date'] for s in result['slots']]);self.assertEqual(result['slots'][0]['date'],'2026-11-24')
