"""Coach planner denial, Nebo dashboard/privacy, and reports in other branches."""
import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from http.cookiejar import CookieJar
os.environ['CRM_DB']=os.path.join(tempfile.mkdtemp(),'coach-policy.sqlite3')
os.environ['CRM_ADMIN_PASSWORD']='admin-pass-123'
os.environ['CRM_QUIET']='1'
from crm import db, server, coachpolicy, coachreport, planner, services as sv

def main():
    con=db.connect();db.init_db(con)
    today=date.today();day=today.isoformat()
    def add(login, role):
        uid=con.execute('INSERT INTO users(login,name,role,pw_hash) VALUES (?,?,?,?)',(login,login,role,db.hash_password('employee-pass-123'))).lastrowid
        return dict(con.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())
    n=add('nebo-coach','coach'); c=add('choba-coach','coach'); a=add('arctic-coach','coach'); mixed=add('mixed-coach','coach'); senior=add('senior','operator')
    con.execute('UPDATE users SET is_senior=1 WHERE id=?',(senior['id'],));senior['is_senior']=1
    bids={name:con.execute('INSERT INTO branches(name) VALUES (?)',(name,)).lastrowid for name in ['ТЦ «Небо»','Чоботовская','Арктика']}
    gids={}
    for name,u in [('ТЦ «Небо»',n),('Чоботовская',c),('Арктика',a)]:
        gids[name]=con.execute('INSERT INTO groups(branch_id,name,weekdays,time,coach_id,assistant_id) VALUES (?,?,?,?,?,?)',(bids[name],name,str(today.weekday()),'00:05',u['id'],mixed['id'])).lastrowid
    assert coachpolicy.nebo_only(con,n['id']) and not coachpolicy.nebo_only(con,mixed['id'])
    tid=planner.add(con,senior,{'title':'Задание от старшего','date':day,'assignee_id':n['id'],'note':'Перед тренировкой'})
    report_tasks={}
    for name,u in [('Чоботовская',c),('Арктика',a)]:
        report_tasks[name]=planner.add(con,senior,{'title':'Задание к смене','date':day,'assignee_id':u['id']})
        v=coachreport.get_or_create(con,bids[name],today,u)
        assert any(t['id']==report_tasks[name] for t in v['crm_tasks'])
    try:
        coachreport.get_or_create(con,bids['ТЦ «Небо»'],today,n)
        raise AssertionError('Nebo report created')
    except coachreport.ReportError:pass
    assert all(x['branch_id']!=bids['ТЦ «Небо»'] for x in coachreport.overdue(con,datetime.combine(today,datetime.min.time())+timedelta(hours=10)))
    assert con.execute('SELECT count(*) FROM coach_reports WHERE branch_id=?',(bids['ТЦ «Небо»'],)).fetchone()[0]==0
    kid=sv.create_student(con,{'name':'Тестовый Ребёнок','parent_name':'Родитель','phone':'+79060727385','funnel':'members','group_id':gids['ТЦ «Небо»'],'tariff':'fixed'},senior)
    eid=con.execute('SELECT id FROM enrollments WHERE student_id=?',(kid,)).fetchone()[0]
    con.execute("INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at,kind) VALUES (?,?,?,'fixed',8,1000,8000,'paid',?,'month')",(kid,eid,day[:7],day))
    con.close()
    srv=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);threading.Thread(target=srv.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{srv.server_port}'
    def session(login,pw='employee-pass-123'):
        op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        def call(method,path,body=None):
            req=urllib.request.Request(base+path,method=method,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
            try:
                with op.open(req) as r:return r.status,json.loads(r.read())
            except urllib.error.HTTPError as e:return e.code,json.loads(e.read())
        assert call('POST','/api/login',{'login':login,'password':pw})[0]==200
        return call
    sessions={u['login']:session(u['login']) for u in [n,c,a,mixed]}
    for call in sessions.values():
        assert 'planner' not in call('GET','/api/meta')[1]['user']['perms']
        assert call('GET','/api/planner')[0]==403
    call=sessions[n['login']]
    assert set(call('GET','/api/meta')[1]['user']['perms'])=={'today','journal'}
    for path in ['/api/salary','/api/my-pay','/api/payments','/api/envelope','/api/coach/kids','/api/coaches/board']:
        assert call('GET',path)[0]==403,path
    code,d=call('GET','/api/coach/today'); assert code==200 and d['dashboard_only']
    assert d['stats']['kids']==1 and any(t['id']==tid for t in d['tasks'])
    assert '79060727385' not in json.dumps(d)
    code,j=call('GET',f'/api/journal?group_id={gids["ТЦ «Небо»"]}&date={day}');assert code==200
    assert '79060727385' not in json.dumps(j) and 'phone' not in json.dumps(j)
    assert call('GET','/api/coach-shifts')[1]['shifts']==[]
    assert call('GET',f'/api/coach-report?branch_id={bids["ТЦ «Небо»"]}&date={day}')[0]!=200
    assert call('POST',f'/api/tasks/{tid}/done',{'result':'Сделано'})[0]==200
    for name,u in [('Чоботовская',c),('Арктика',a)]:
        call=sessions[u['login']]
        assert any(t['id']==report_tasks[name] for t in call('GET','/api/coach/today')[1]['tasks'])
        assert call('GET',f'/api/coach-report?branch_id={bids[name]}&date={day}')[0]==200
        assert any(x['branch_id']==bids[name] for x in call('GET','/api/coach-shifts')[1]['shifts'])
    assert {s['branch_id'] for s in sessions[mixed['login']]('GET','/api/coach-shifts')[1]['shifts']}=={bids['Чоботовская'],bids['Арктика']}
    srv.shutdown();print('Coach branch policy, privacy, assignments and mixed shifts: OK')
if __name__=='__main__':main()
