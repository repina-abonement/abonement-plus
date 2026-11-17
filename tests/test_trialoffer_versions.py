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
