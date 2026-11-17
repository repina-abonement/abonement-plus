import tempfile,unittest
from pathlib import Path
from crm import db,schedule,groupnames

class GroupNamesTest(unittest.TestCase):
 def setUp(self):
  self.con=db.connect(str(Path(tempfile.mkdtemp())/'groups.sqlite3'));self.addCleanup(self.con.close);db.init_db(self.con)
  self.bid=schedule.save_branch(self.con,{'name':'Небо'})
 def test_save_rename_branch_and_per_day_times(self):
  data={'branch_id':self.bid,'name':'Ручное неверное название','weekdays':[0,2],'time':'10:00','age':'1,5–3'}
  gid=schedule.save_group(self.con,data)
  self.assertEqual(self.con.execute('SELECT name FROM groups WHERE id=?',(gid,)).fetchone()[0],'Небо · Пн/Ср 10:00 · 1,5–3')
  schedule.save_group(self.con,{**data,'time':'10:30','day_times':{'2':'11:00'}},gid)
  schedule.save_branch(self.con,{'name':'ТРЦ Небо'},self.bid)
  name=self.con.execute('SELECT name FROM groups WHERE id=?',(gid,)).fetchone()[0]
  self.assertEqual(name,'ТРЦ Небо · Пн 10:30/Ср 11:00 · 1,5–3')
 def test_legacy_migration_is_idempotent_and_special_kind_retained(self):
  gid=self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time,age) VALUES (?,'ОФП legacy','3','20:30','6–8')",(self.bid,)).lastrowid
  # Simulate a legacy group which predates canonical names
  self.con.execute("UPDATE groups SET source_name=name WHERE id=?",(gid,))
  groupnames.init(self.con);first=self.con.execute('SELECT name FROM groups WHERE id=?',(gid,)).fetchone()[0]
  groupnames.init(self.con);second=self.con.execute('SELECT name FROM groups WHERE id=?',(gid,)).fetchone()[0]
  self.assertEqual(first,'Небо · Чт 20:30 · 6–8 · ОФП');self.assertEqual(first,second)
 def test_missing_age_is_not_invented(self):
  gid=schedule.save_group(self.con,{'branch_id':self.bid,'weekdays':[0],'time':'10:00'})
  self.assertIn('Возраст не указан',self.con.execute('SELECT name FROM groups WHERE id=?',(gid,)).fetchone()[0])
