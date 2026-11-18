"""Two named offline boards become one without losing tasks or expanding viewer writes."""
import tempfile
import unittest
from pathlib import Path
from datetime import date, timedelta
from crm import db, planner, offlineplanner as op, services as sv, tasks


class UnifiedBoardTest(unittest.TestCase):
    def setUp(self):
        self.con=db.connect(str(Path(tempfile.mkdtemp())/'boards.sqlite3'));db.init_db(self.con)
        self.addCleanup(self.con.close)
        self.owner=dict(self.con.execute("SELECT * FROM users WHERE active=1 AND role='admin'").fetchone())
        self.v=self.user('Виктория Сафронова');self.t=self.user('Татьяна Данейкина');self.w=self.user('Варвара Батарева');self.other=self.user('Посторонний')
    def user(self,name):
        uid=self.con.execute("INSERT INTO users(login,name,role,pw_hash) VALUES (?,?,'operator','disabled')",(name,name)).lastrowid
        return dict(self.con.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())
    def test_merge_existing_tasks_all_authors_statuses_and_recurring(self):
        v=planner.add(self.con,self.v,{'title':'Викина','note':'Описание','priority':1})
        t=planner.add(self.con,self.t,{'title':'Танина'})
        delegated=planner.add(self.con,self.owner,{'title':'От руководителя','assignee_id':self.t['id']})
        done=planner.add(self.con,self.t,{'title':'Выполнено'});tasks.complete(self.con,done,self.t,'Отчёт')
        recurring=planner.add(self.con,self.t,{'title':'Повтор','repeat':{'rule':'daily'}})
        rid=recurring
        unrelated=planner.add(self.con,self.other,{'title':'Чужое'})
        before=[dict(r) for r in self.con.execute('SELECT * FROM tasks ORDER BY id')]
        op.unify_requested_boards(self.con);pool=op.pool_id(self.con)
        after=[dict(r) for r in self.con.execute('SELECT * FROM tasks ORDER BY id')]
        self.assertEqual(len(before),len(after))
        for old,new in zip(before,after):
            expected=pool if old['assignee_id'] in (self.v['id'],self.t['id']) else old['assignee_id']
            self.assertEqual(new['assignee_id'],expected)
            self.assertEqual({k:v for k,v in old.items() if k!='assignee_id'},{k:v for k,v in new.items() if k!='assignee_id'})
        self.assertEqual(self.con.execute('SELECT assignee_id FROM recurring WHERE id=?',(rid,)).fetchone()[0],pool)
        for actor in (self.owner,self.v,self.t,self.w):
            boards=planner.visible_staff(self.con,actor)
            self.assertIn(pool,[r['id'] for r in boards])
            self.assertNotIn(self.v['id'],[r['id'] for r in boards]);self.assertNotIn(self.t['id'],[r['id'] for r in boards])
            week=planner.week(self.con,actor,date.today().isoformat(),pool)
            self.assertEqual(week['can_edit'],actor['id']!=self.w['id'])
        people=planner.view(self.con,self.owner)['people']
        self.assertTrue({self.v['id'],self.t['id']}.issubset({p['id'] for p in people}))
        for uid in (self.v['id'],self.t['id']):
            self.assertEqual(planner.week(self.con,self.owner,date.today().isoformat(),uid)['user_id'],pool)
            new=planner.add(self.con,self.owner,{'title':'Старая ссылка назначения','assignee_id':uid})
            self.assertEqual(self.con.execute('SELECT assignee_id FROM tasks WHERE id=?',(new,)).fetchone()[0],pool)
        with self.assertRaises(planner.PlanError):planner.week(self.con,self.other,date.today().isoformat(),self.v['id'])
        with self.assertRaises(planner.PlanError):planner.add(self.con,self.w,{'title':'Нельзя','assignee_id':pool})
        current=[tuple(r) for r in self.con.execute('SELECT * FROM tasks ORDER BY id')]
        op.unify_requested_boards(self.con)
        self.assertEqual(current,[tuple(r) for r in self.con.execute('SELECT * FROM tasks ORDER BY id')])
        planner.generate(self.con,date.today()+timedelta(days=2))
        self.assertEqual(self.con.execute('SELECT count(*) FROM tasks WHERE recur_id=? AND assignee_id!=?',(rid,pool)).fetchone()[0],0)
    def test_ambiguous_name_defers_without_hiding_or_moving(self):
        self.user('Сафронова Виктория')
        tid=planner.add(self.con,self.t,{'title':'До объединения'})
        op.unify_requested_boards(self.con)
        self.assertEqual(op.board_id(self.con,self.t['id']),self.t['id'])
        self.assertEqual(self.con.execute('SELECT assignee_id FROM tasks WHERE id=?',(tid,)).fetchone()[0],self.t['id'])
    def test_client_tasks_and_unrelated_boards_are_unchanged(self):
        sid=sv.create_student(self.con,{'name':'Synthetic Child','parent_name':'Parent','phone':'+79000000222'},self.owner)
        tid=planner.add(self.con,self.v,{'title':'Связано с клиентом'})
        self.con.execute('UPDATE tasks SET student_id=? WHERE id=?',(sid,tid))
        before=tuple(self.con.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone())
        op.unify_requested_boards(self.con)
        self.assertEqual(tuple(self.con.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()),before)
        with self.assertRaises(planner.PlanError):planner.task_detail(self.con,tid,self.w)

    def test_startup_applies_once_and_editing_old_assignment_stays_shared(self):
        tid=planner.add(self.con,self.t,{'title':'До перезапуска'})
        db.init_db(self.con)
        pool=op.pool_id(self.con)
        self.assertEqual(self.con.execute('SELECT assignee_id FROM tasks WHERE id=?',(tid,)).fetchone()[0],pool)
        planner.edit_task(self.con,tid,self.owner,{'title':'Изменено','assignee_id':self.v['id']})
        self.assertEqual(self.con.execute('SELECT assignee_id FROM tasks WHERE id=?',(tid,)).fetchone()[0],pool)
