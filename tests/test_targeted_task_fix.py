import os,tempfile,unittest
from crm import db,targetedtaskfix


class TargetedTaskFixTest(unittest.TestCase):
 def setUp(self):
  self.con=db.connect(os.path.join(tempfile.mkdtemp(),'fix.sqlite3'));db.init_db(self.con)
  self.con.execute('DELETE FROM settings WHERE key=?',(targetedtaskfix.KEY,))
  for fid,sid,name,task in ((6,6,'Шарипов Даниил',4644),(7,7,'Даниил Шулежко',4645)):
   self.con.execute('INSERT INTO families(id,name) VALUES (?,?)',(fid,name))
   self.con.execute("INSERT INTO students(id,family_id,name,stage,stage_changed_at,created_at) VALUES (?,?,?,'new','2026-11-01','2026-11-01')",(sid,fid,name))
   self.con.execute("INSERT INTO subscriptions(student_id,month,tariff,lessons,per_lesson,price,status,created_at) VALUES (?,'2026-11','fixed',8,1500,12000,'paid','2026-11-01')",(sid,))
   self.con.execute("INSERT INTO tasks(id,kind,title,student_id,due_at,status,source,created_at) VALUES (?,'call_lead','Новый лид',?,'2026-11-20T23:59','open','auto','2026-11-01')",(task,sid))
 def test_exact_two_cards_only_and_idempotent(self):
  report=targetedtaskfix.apply(self.con);self.assertEqual(report['state'],'complete')
  self.assertEqual([r['old_status'] for r in report['rows']],['cancelled','cancelled'])
  self.assertEqual([r['new_due'] for r in report['rows']],['2026-11-20T23:59']*2)
  self.assertEqual(targetedtaskfix.apply(self.con),report)
  for sid in (6,7):
   rows=self.con.execute("SELECT * FROM tasks WHERE student_id=? AND status='open'",(sid,)).fetchall()
   self.assertEqual(len(rows),1);self.assertEqual(rows[0]['kind'],'renew')

if __name__=='__main__':unittest.main()
