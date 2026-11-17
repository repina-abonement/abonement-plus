import tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta
from unittest.mock import patch
from crm import db,schedule,trialoffer

class TrialVersionTest(unittest.TestCase):
 def test_offer_obeys_dated_schedule_and_trial_permission(self):
  con=db.connect(str(Path(tempfile.mkdtemp())/'trials.sqlite3'));self.addCleanup(con.close);db.init_db(con)
  bid=schedule.save_branch(con,{'name':'СК Западный','address':'Synthetic'})
  gid=schedule.save_group(con,{'name':'Группа 3–5','branch_id':bid,'weekdays':[0],'time':'10:00','age':'3–5','capacity':12})
  # Fixed Tuesday: the previous Monday schedule must not hide Wednesday after a change
  now=datetime(2026,11,17,12,0)
  con.execute("INSERT INTO group_versions(group_id,valid_from,weekdays,time,day_times,created_at) VALUES (?,?,?,?,?,?)",(gid,'2026-11-18','2','10:30','','2026-11-17'))
  with patch('crm.trialoffer.datetime') as clock:
   clock.now.return_value=now;clock.strptime.side_effect=datetime.strptime;clock.combine.side_effect=datetime.combine
   slots=trialoffer.slots(con,3,bid)
   self.assertTrue(slots)
   self.assertEqual((slots[0]['date'],slots[0]['time']),('2026-11-18','10:30'))
   con.execute('UPDATE groups SET trial_ok=0 WHERE id=?',(gid,))
   self.assertFalse(trialoffer.slots(con,3,bid))
 def test_legacy_name_age_and_specific_failure(self):
  con=db.connect(str(Path(tempfile.mkdtemp())/'legacy.sqlite3'));self.addCleanup(con.close);db.init_db(con)
  bid=schedule.save_branch(con,{'name':'СК Западный'})
  tomorrow=datetime.now()+timedelta(days=1)
  gid=schedule.save_group(con,{'name':'Чоботовская Сб/Вс 3–5','branch_id':bid,'weekdays':[tomorrow.weekday()],'time':'10:00','age':''})
  result=trialoffer.offer(con,3,branch_id=bid)
  self.assertTrue(result['slots']);self.assertEqual(result['slots'][0]['age'],'3–5')
  con.execute('UPDATE groups SET trial_ok=0 WHERE id=?',(gid,))
  result=trialoffer.offer(con,3,branch_id=bid)
  self.assertFalse(result['slots']);self.assertIn('выключена запись на пробное',result['error'])
  con.execute("UPDATE groups SET trial_ok=1,age='6–8' WHERE id=?",(gid,))
  result=trialoffer.offer(con,3,branch_id=bid)
  self.assertFalse(result['slots']);self.assertIn('возраста 6–8',result['error'])
 def test_six_booked_of_twelve_ignores_unpaid_roster_and_archived_groups(self):
  from crm import services as sv,capacity
  con=db.connect(str(Path(tempfile.mkdtemp())/'places.sqlite3'));self.addCleanup(con.close);db.init_db(con)
  bid=schedule.save_branch(con,{'name':'СК Западный','lesson_limit':0})
  gid=schedule.save_group(con,{'branch_id':bid,'name':'Чоботовская Сб/Вс 3–5','weekdays':[5,6],'time':'11:30','age':'3–5','capacity':12})
  fid=con.execute("INSERT INTO families(name) VALUES ('Synthetic')").lastrowid
  for i in range(12):
   sid=con.execute("INSERT INTO students(family_id,name,funnel,stage,created_at,stage_changed_at) VALUES (?,?,'members','active','2026-11-01','2026-11-01')",(fid,f'Synthetic {i}')).lastrowid
   eid=con.execute("INSERT INTO enrollments(student_id,group_id,created_at) VALUES (?,?,'2026-11-01')",(sid,gid)).lastrowid
   if i<6:con.execute("INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at) VALUES (?,?,'2026-11','fixed',8,1000,8000,'paid','2026-11-01')",(sid,eid))
  archived=schedule.save_group(con,{'branch_id':bid,'name':'Архивная','weekdays':[5],'time':'10:00','age':'3–5'})
  con.execute('UPDATE groups SET active=0 WHERE id=?',(archived,))
  now=datetime(2026,11,17,12)
  with patch('crm.trialoffer.datetime') as clock:
   clock.now.return_value=now;clock.strptime.side_effect=datetime.strptime;clock.combine.side_effect=datetime.combine
   result=trialoffer.offer(con,3,branch_id=bid)
   target=next(s for s in result['slots'] if s['date']=='2026-11-21')
   self.assertEqual(target['free'],6)
   self.assertEqual(capacity.occupancy(con,gid,'2026-11-21')['count'],6)
   # An issued invoice reserves a real dated booking even before full payment.
   con.execute("INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at) VALUES (?,?,'2026-11','fixed',8,1000,8000,'invoiced','2026-11-01')",(sid,eid))
   trial=con.execute("INSERT INTO students(family_id,name,funnel,stage,created_at,stage_changed_at,group_id,trial_at) VALUES (?,'Trial','sales','trial_booked','2026-11-01','2026-11-01',?,'2026-11-21T11:30')",(fid,gid)).lastrowid
   makeup=con.execute("INSERT INTO students(family_id,name,funnel,stage,created_at,stage_changed_at) VALUES (?,'Makeup','members','active','2026-11-01','2026-11-01')",(fid,)).lastrowid
   absence=con.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES (?,?,'2026-11-01','absent')",(makeup,gid)).lastrowid
   con.execute("INSERT INTO makeups(student_id,group_id,date,absence_id) VALUES (?,?,'2026-11-21',?)",(makeup,gid,absence))
   held=con.execute("INSERT INTO students(family_id,name,funnel,stage,created_at,stage_changed_at) VALUES (?,'Hold','sales','lead','2026-11-01','2026-11-01')",(fid,)).lastrowid
   for _ in range(2):con.execute("INSERT INTO trial_holds(student_id,group_id,date,time,until,created_at) VALUES (?,?,'2026-11-21','11:30',?,'2026-11-17')",(held,gid,(datetime.now()+timedelta(minutes=10)).isoformat()))
   self.assertEqual(capacity.occupancy(con,gid,'2026-11-21')['count'],10)
   # A trial child also listed as a makeup still occupies one place.
   con.execute("INSERT INTO makeups(student_id,group_id,date,absence_id) VALUES (?,?,'2026-11-21',?)",(trial,gid,absence))
   self.assertEqual(capacity.occupancy(con,gid,'2026-11-21')['count'],10)
   con.execute('UPDATE groups SET trial_ok=0 WHERE id=?',(gid,))
   result=trialoffer.offer(con,3,branch_id=bid)
   self.assertNotIn('Архивная',result['error'])
