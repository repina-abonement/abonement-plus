import unittest,tempfile
from pathlib import Path
from crm import db,planner,offlineplanner

class VaryaAssignment(unittest.TestCase):
    def test_assign_shared_admin_tasks_without_mutating_existing_tasks_or_granting_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            con=db.connect(str(Path(tmp)/'test.sqlite3'));self.addCleanup(con.close);db.init_db(con)
            def user(login,name,offline=0,senior=0):
                uid=con.execute("INSERT INTO users(login,name,role,pw_hash,is_offline_admin,is_senior) VALUES (?,?,'operator','disabled',?,?)",(login,name,offline,senior)).lastrowid
                return dict(con.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())
            varya=user('varya','Варвара Батарева',senior=1)
            admin=user('offline','Synthetic Offline',offline=1)
            other=user('other','Synthetic Senior',senior=1)
            observer=user('observer','Synthetic Viewer')
            from crm import comms
            comms.put(con,offlineplanner.VIEWER_KEY,str(observer['id']))
            pool=offlineplanner.pool_id(con)
            existing=planner.add(con,admin,{'title':'Synthetic existing task'})
            week=planner.week(con,varya,'2026-11-19',pool,rolling=True)
            self.assertTrue(week['can_add'])
            self.assertFalse(week['can_edit'])
            self.assertFalse(planner.week(con,observer,'2026-11-19',pool,rolling=True)['can_add'])
            task=planner.add(con,varya,{'title':'Synthetic assignment','assignee_id':pool,'date':'2026-11-19'})
            row=con.execute('SELECT * FROM tasks WHERE id=?',(task,)).fetchone()
            self.assertEqual(row['assignee_id'],pool);self.assertEqual(row['created_by_id'],varya['id'])
            self.assertEqual(con.execute('SELECT status FROM tasks WHERE id=?',(existing,)).fetchone()[0],'open')
            self.assertTrue(planner.view(con,varya)['can_manage'])
            self.assertIn(pool,[r['id'] for r in planner.view(con,varya)['staff']])
            for actor in (other,observer):
                with self.assertRaises(planner.PlanError):planner.add(con,actor,{'title':'Denied','assignee_id':pool})
            with self.assertRaises(planner.PlanError):planner.cancel(con,existing,varya)
            user('duplicate','Варвара Батарева')
            self.assertFalse(offlineplanner.can_assign(con,varya))
