"""Planner-only roles: real HTTP authorization checks on fictional accounts."""
import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
os.environ['CRM_DB'] = os.path.join(tempfile.mkdtemp(), 'trainee.sqlite3')
os.environ['CRM_ADMIN_PASSWORD'] = 'admin-test-123'
os.environ['CRM_QUIET'] = '1'
from crm import db, perms, server

def main():
    con = db.connect(); db.init_db(con); con.close()
    srv = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{srv.server_port}'
    def session(login, pw):
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        def call(method, path, body=None):
            req = urllib.request.Request(base+path, method=method, data=json.dumps(body).encode() if body is not None else None, headers={'Content-Type':'application/json'})
            try:
                with op.open(req) as r: return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e: return e.code, json.loads(e.read())
        assert call('POST','/api/login',{'login':login,'password':pw})[0] == 200
        return call
    owner = session('admin','admin-test-123')
    for login, post in [('test-senior','senior'),('test-trainee-one','trainee'),('test-trainee-two','trainee')]:
        assert owner('POST','/api/users',{'login':login,'name':login,'password':'test-pass-123','post':post})[0] == 200
    users={u['login']:u for u in owner('GET','/api/users')[1]}
    one=users['test-trainee-one']['id']; two=users['test-trainee-two']['id']
    trainee=session('test-trainee-one','test-pass-123'); senior=session('test-senior','test-pass-123')
    code, meta=trainee('GET','/api/meta'); assert code==200
    assert meta['user']['perms']=={'planner':'own'} and meta['user']['title']=='Стажёр'
    assert meta['settings']=={} and meta['groups']==[] and meta['branches']==[]
    assert [u['id'] for u in meta['staff']]==[one]
    assert perms.view_of({'role':'trainee','is_senior':1,'is_promoter':1,'is_journal':1})=={'planner':'own'}
    for path in ['/api/users','/api/students','/api/payments','/api/inbox','/api/team','/api/settings','/api/staff-docs','/api/views',f'/api/users/{two}/profile']:
        assert trainee('GET',path)[0] in (403,404), path
    code, plan=trainee('GET','/api/planner'); assert code==200 and not plan['can_manage']
    today=meta['today']
    assert trainee('GET',f'/api/planner/week?start={today}&user={two}')[1]['user_id']==one
    assert trainee('POST','/api/planner/tasks',{'title':'Not my task','date':today,'assignee_id':two})[0]!=200
    for uid in [one,two]:
        assert senior('GET',f'/api/planner/week?start={today}&user={uid}')[0]==200
        code, task=senior('POST','/api/planner/tasks',{'title':'Assignment','date':today,'assignee_id':uid,'note':'Details'})
        assert code==200
        if uid==one:
            assert trainee('POST',f'/api/tasks/{task["id"]}/done',{'result':'Done'})[0]==200
        else:
            assert trainee('POST',f'/api/tasks/{task["id"]}/done',{})[0]==403
    assert trainee('PUT','/api/planner/settings',{})[0]==403
    assert trainee('PATCH',f'/api/users/{one}',{'post':'owner'})[0]==403
    srv.shutdown()
    print('Trainee restrictions and senior access: OK')
if __name__=='__main__': main()
