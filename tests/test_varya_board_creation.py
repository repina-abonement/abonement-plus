import unittest,json,threading,urllib.request
from unittest.mock import patch
from datetime import datetime
from crm import db,server
from tests.test_planner_shifts import PlannerShifts

class VaryaBoardCreation(unittest.TestCase):
    setUp=PlannerShifts.setUp
    def test_real_session_creates_on_selected_admin_board_and_shared_pool(self):
        token='synthetic-varya-board'
        self.con.execute('INSERT INTO sessions(token,user_id,created_at) VALUES(?,?,?)',(token,self.varya['id'],datetime.now().isoformat()))
        path=self.con.execute('PRAGMA database_list').fetchone()[2]
        with patch.object(db,'DB_PATH',path):
            srv=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
            def call(method,path,data=None):
                req=urllib.request.Request(f'http://127.0.0.1:{srv.server_port}'+path,method=method,data=json.dumps(data).encode() if data is not None else None,headers={'Cookie':'crm_session='+token,'Content-Type':'application/json'})
                with urllib.request.urlopen(req,timeout=5) as r:return json.load(r)
            try:
                form=call('GET','/api/planner');self.assertTrue(form['can_manage'])
                for target in (self.other['id'],self.pool):
                    self.assertIn(target,[x['id'] for x in form['staff']])
                    board=call('GET',f'/api/planner/week?rolling=1&days=1&start=2026-11-23&user={target}')
                    self.assertEqual(board['user_id'],target);self.assertTrue(board['can_add'])
                    result=call('POST','/api/planner/tasks',{'title':'Synthetic assigned from board','date':'2026-11-23','assignee_id':target})
                    row=self.con.execute('SELECT * FROM tasks WHERE id=?',(result['id'],)).fetchone()
                    self.assertEqual(row['assignee_id'],target);self.assertEqual(row['created_by_id'],self.varya['id'])
                    refreshed=call('GET',f'/api/planner/week?rolling=1&days=1&start=2026-11-23&user={target}')
                    self.assertIn(result['id'],[x['id'] for x in refreshed['days'][0]['tasks']])
                personal=call('GET',f"/api/planner/week?rolling=1&days=1&start=2026-11-23&user={self.varya['id']}")
                self.assertFalse(any(x['title']=='Synthetic assigned from board' for x in personal['days'][0]['tasks']))
            finally:srv.shutdown();srv.server_close();thread.join(timeout=5)
