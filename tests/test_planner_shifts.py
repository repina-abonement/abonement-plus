import unittest,tempfile
from pathlib import Path
from datetime import date
from crm import db,planner,offlineplanner,tasks

class PlannerShifts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.con=db.connect(str(Path(self.tmp.name)/'shifts.sqlite3'));self.addCleanup(self.con.close);db.init_db(self.con)
        def user(login,name,offline=0):
            uid=self.con.execute("INSERT INTO users(login,name,role,pw_hash,is_offline_admin) VALUES (?,?,'operator','disabled',?)",(login,name,offline)).lastrowid
            return dict(self.con.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())
        self.varya=user('varya','Варвара Батарева');self.offline=user('offline','Synthetic Offline',1);self.other=user('other','Synthetic Senior')
        self.pool=offlineplanner.pool_id(self.con)
    def add(self,day='2026-11-23',mode='both',repeat=None):
        return planner.add(self.con,self.varya,{'title':'Synthetic checklist','date':day,'assignee_id':self.pool,'shift_mode':mode,'repeat':repeat or {}})
    def rows(self,day):
        return self.con.execute("SELECT * FROM tasks WHERE assignee_id=? AND substr(due_at,1,10)=? AND status!='cancelled' ORDER BY shift",(self.pool,day)).fetchall()
    def test_monday_wednesday_independent_and_other_days_once(self):
        for day in ['2026-11-23','2026-11-25']:
            self.add(day)
            rows=self.rows(day);self.assertEqual({r['shift'] for r in rows},{'morning','evening'})
            morning=next(r for r in rows if r['shift']=='morning');evening=next(r for r in rows if r['shift']=='evening')
            tasks.complete(self.con,morning['id'],self.offline)
            self.assertEqual(self.con.execute('SELECT status FROM tasks WHERE id=?',(evening['id'],)).fetchone()[0],'open')
            done=self.con.execute('SELECT * FROM tasks WHERE id=?',(morning['id'],)).fetchone();self.assertEqual(done['done_by'],self.offline['name']);self.assertTrue(done['done_at'])
        self.add('2026-11-24');self.assertEqual([r['shift'] for r in self.rows('2026-11-24')],['common'])
    def test_recurrence_idempotent_and_future_edit_preserves_history(self):
        rid=self.add(repeat={'rule':'daily'})
        for day in ['2026-11-23','2026-11-24','2026-11-25']:
            planner._materialize(self.con,date.fromisoformat(day));planner._materialize(self.con,date.fromisoformat(day))
        self.assertEqual(len(self.rows('2026-11-23')),2);self.assertEqual(len(self.rows('2026-11-24')),1);self.assertEqual(len(self.rows('2026-11-25')),2)
        old=self.rows('2026-11-23')[0];tasks.complete(self.con,old['id'],self.offline)
        current=self.rows('2026-11-25')[0]
        planner.edit_task(self.con,current['id'],self.varya,{'title':'Synthetic changed','date':'2026-11-25','assignee_id':self.pool,'shift_mode':'both','scope':'future','repeat':{'rule':'daily'}})
        self.assertEqual(len(self.rows('2026-11-25')),2)
        self.assertEqual(self.con.execute('SELECT status FROM tasks WHERE id=?',(old['id'],)).fetchone()[0],'done')
        self.assertEqual(self.con.execute('SELECT title FROM tasks WHERE id=?',(old['id'],)).fetchone()[0],'Synthetic checklist')
    def test_existing_series_can_be_split_and_non_pool_rejected(self):
        rid=self.add(mode='common',repeat={'rule':'weekly','weekdays':[0,2]})
        planner._materialize(self.con,date(2026,11,23));row=self.rows('2026-11-23')[0]
        planner.edit_task(self.con,row['id'],self.varya,{'title':row['title'],'date':'2026-11-23','shift_mode':'both','scope':'future','repeat':{'rule':'weekly','weekdays':[0,2]}})
        self.assertEqual({r['shift'] for r in self.rows('2026-11-23')},{'morning','evening'})
        for mode in ['bad','both']:
            with self.assertRaises(planner.PlanError):planner.add(self.con,self.other,{'title':'Denied','shift_mode':mode})
        with self.assertRaises(planner.PlanError):planner.add(self.con,self.other,{'title':'Denied','assignee_id':self.pool,'shift_mode':'both'})
        planner.init(self.con);self.assertEqual(len(self.rows('2026-11-23')),2)
