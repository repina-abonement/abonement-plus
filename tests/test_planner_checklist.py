import unittest
from datetime import date
from crm import planner,offlineplanner,tasks,comms
from tests.test_planner_shifts import PlannerShifts

class PlannerChecklist(unittest.TestCase):
    setUp=PlannerShifts.setUp
    def create(self,**kwargs):
        return planner.add(self.con,self.varya,dict(title='Synthetic task',date='2026-11-23',assignee_id=self.pool,subtasks=['Cash','Cleaning'],**kwargs))
    def test_day_has_one_date_and_week_has_seven_without_deleting_tasks(self):
        first=self.create();planner.add(self.con,self.varya,{'title':'Tomorrow','date':'2026-11-24','assignee_id':self.pool})
        day=planner.week(self.con,self.varya,'2026-11-23',self.pool,rolling=True,days=1)
        self.assertEqual([x['date'] for x in day['days']],['2026-11-23'])
        self.assertEqual(day['days'][0]['tasks'][0]['id'],first)
        self.assertEqual(len(planner.week(self.con,self.varya,'2026-11-23',self.pool,rolling=True)['days']),7)
        self.assertEqual(self.con.execute('SELECT count(*) FROM tasks').fetchone()[0],2)
    def test_week_view_is_always_monday_through_sunday(self):
        view=planner.week(self.con,self.varya,'2026-11-26',self.pool,days=7)
        self.assertEqual(view['start'],'2026-11-23')
        self.assertEqual([x['date'] for x in view['days']],
                         ['2026-11-23','2026-11-24','2026-11-25','2026-11-26',
                          '2026-11-27','2026-11-28','2026-11-29'])
    def test_independent_checklists_per_shift_and_day(self):
        rid=self.create(shift_mode='both',repeat={'rule':'daily'})
        planner._materialize(self.con,date(2026,11,23));planner._materialize(self.con,date(2026,11,24))
        rows=self.con.execute('SELECT * FROM tasks WHERE recur_id=? ORDER BY due_at,shift',(rid,)).fetchall()
        self.assertEqual(len(rows),3)
        item=planner.task_detail(self.con,rows[0]['id'],self.varya)['task']['subtasks'][0]
        result=planner.change_subtask(self.con,rows[0]['id'],self.offline,{'done':True},item['id'])
        done=result['task']['subtasks'][0];self.assertEqual(done['done_by'],self.offline['name']);self.assertTrue(done['done_at'])
        self.assertEqual(self.con.execute('SELECT sum(done) FROM planner_subtasks').fetchone()[0],1)
        planner._materialize(self.con,date(2026,11,23));self.assertEqual(self.con.execute('SELECT count(*) FROM planner_subtasks').fetchone()[0],6)
        planner.change_subtask(self.con,rows[0]['id'],self.offline,{'done':False},item['id'])
        self.assertEqual(self.con.execute('SELECT sum(done) FROM planner_subtasks').fetchone()[0],0)
    def test_preserves_done_items_when_editing_and_future_templates(self):
        tid=self.create()
        item=planner.task_detail(self.con,tid,self.varya)['task']['subtasks'][0]
        planner.change_subtask(self.con,tid,self.offline,{'done':True},item['id'])
        planner.edit_task(self.con,tid,self.varya,{'title':'Changed','subtasks':['Cash','Cleaning','Field'],'repeat':{'rule':'daily'},'scope':'future'})
        result=planner.task_detail(self.con,tid,self.varya)
        self.assertEqual(result['task']['subtasks'][0]['id'],item['id']);self.assertEqual(result['task']['subtasks'][0]['done'],1)
        planner._materialize(self.con,date(2026,11,24))
        nextrow=self.con.execute("SELECT id FROM tasks WHERE substr(due_at,1,10)='2026-11-24'").fetchone()
        items=planner.task_detail(self.con,nextrow['id'],self.varya)['task']['subtasks']
        self.assertEqual([x['title'] for x in items],['Cash','Cleaning','Field']);self.assertFalse(any(x['done'] for x in items))
    def test_scope_permissions_limits_and_closed_tasks(self):
        tid=self.create();other=self.create()
        item=planner.task_detail(self.con,tid,self.varya)['task']['subtasks'][0]
        with self.assertRaises(planner.PlanError):planner.change_subtask(self.con,other,self.varya,{'done':True},item['id'])
        with self.assertRaises(planner.PlanError):planner.change_subtask(self.con,tid,self.other,{'title':'Denied'})
        with self.assertRaises(planner.PlanError):planner.change_subtask(self.con,tid,self.varya,{'done':'false'},item['id'])
        with self.assertRaises(planner.PlanError):planner.checklist_titles(['x']*51)
        tasks.complete(self.con,tid,self.offline)
        with self.assertRaises(planner.PlanError):planner.change_subtask(self.con,tid,self.offline,{'done':True},item['id'])
        planner.init(self.con);self.assertEqual(len(planner.task_detail(self.con,tid,self.varya)['task']['subtasks']),2)

    def test_real_http_day_and_checklist_routes(self):
        import json,threading,urllib.request,urllib.error
        from unittest.mock import patch
        from datetime import datetime
        from crm import db,server
        tid=self.create()
        token='synthetic-checklist-session'
        self.con.execute('INSERT INTO sessions(token,user_id,created_at) VALUES(?,?,?)',(token,self.offline['id'],datetime.now().isoformat()))
        path=self.con.execute('PRAGMA database_list').fetchone()[2]
        with patch.object(db,'DB_PATH',path):
            srv=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
            def call(method,path,data=None):
                req=urllib.request.Request(f'http://127.0.0.1:{srv.server_port}'+path,method=method,data=json.dumps(data).encode() if data is not None else None,headers={'Cookie':'crm_session='+token,'Content-Type':'application/json'})
                with urllib.request.urlopen(req,timeout=5) as response:return json.load(response)
            try:
                result=call('GET',f'/api/planner/week?rolling=1&days=1&start=2026-11-23&user={self.pool}')
                self.assertEqual(len(result['days']),1)
                added=call('POST',f'/api/planner/tasks/{tid}/subtasks',{'title':'Field'})
                self.assertEqual(len(added['task']['subtasks']),3)
                item=added['task']['subtasks'][-1]
                done=call('PATCH',f"/api/planner/tasks/{tid}/subtasks/{item['id']}",{'done':True})
                self.assertEqual(done['task']['subtasks'][-1]['done'],1)
                again=call('GET',f'/api/planner/tasks/{tid}')
                self.assertEqual(again['task']['subtasks'][-1]['done'],1)
                removed=call('PATCH',f"/api/planner/tasks/{tid}/subtasks/{item['id']}",{'remove':True})
                self.assertEqual(len(removed['task']['subtasks']),2)
            finally:srv.shutdown();srv.server_close();thread.join(timeout=5)
