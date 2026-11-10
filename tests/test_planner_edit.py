"""Edit task settings and split future recurrence without losing history."""
import os
import tempfile
from datetime import date, timedelta
os.environ['CRM_DB'] = os.path.join(tempfile.mkdtemp(), 'planner-edit.sqlite3')
os.environ['CRM_ADMIN_PASSWORD'] = 'test-admin-123'
os.environ['CRM_QUIET'] = '1'
from crm import db, planner

def main():
    con=db.connect(); db.init_db(con)
    admin=dict(con.execute("SELECT * FROM users WHERE login='admin'").fetchone())
    uid=con.execute("INSERT INTO users(login,name,role,pw_hash) VALUES ('edit-trainee','Test Trainee','trainee','unused')").lastrowid
    trainee=dict(con.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())
    today=date.today(); day=today.isoformat()
    tid=planner.add(con,admin,{'title':'Original','date':day,'note':'Description'})
    def row(id=tid): return dict(con.execute('SELECT * FROM tasks WHERE id=?',(id,)).fetchone())
    def payload(**overrides):
        body={'title':'Edited','note':'New description','date':day,'time':'09:15','assignee_id':admin['id'],'priority':True,'repeat':{'rule':'daily'},'scope':'future'}
        body.update(overrides); return body
    planner.edit_task(con,tid,admin,payload())
    rid=row()['recur_id']; assert rid and row()['note']=='New description' and row()['priority']==1
    planner.week(con,admin,day,rolling=True)
    assert con.execute('SELECT count(*) FROM tasks WHERE recur_id=? AND substr(due_at,1,10)=?',(rid,day)).fetchone()[0]==1
    prior=(today-timedelta(days=1)).isoformat()
    history=con.execute("INSERT INTO tasks(kind,title,assignee_id,due_at,status,created_at,recur_id,result) VALUES ('plan','Historical',?,?,'done',?,?,'Keep result')",(admin['id'],prior+'T09:15',prior,rid)).lastrowid
    tomorrow=(today+timedelta(days=1)).isoformat()
    planner.edit_task(con,tid,admin,payload(title='Weekly',date=tomorrow,assignee_id=uid,repeat={'rule':'weekly','weekdays':[date.fromisoformat(tomorrow).weekday()]}))
    newrid=row()['recur_id']; assert newrid != rid and row()['assignee_id']==uid
    assert row(history)['status']=='done' and row(history)['result']=='Keep result'
    assert con.execute("SELECT count(*) FROM tasks WHERE recur_id=? AND status='open'",(rid,)).fetchone()[0]==0
    planner.week(con,trainee,tomorrow,rolling=True)
    assert con.execute('SELECT count(*) FROM tasks WHERE recur_id=? AND substr(due_at,1,10)=?',(newrid,tomorrow)).fetchone()[0]==1
    # Stop the series: keep this task as a one-off, future instances cancelled.
    planner.edit_task(con,tid,trainee,payload(title='One-off',date=tomorrow,assignee_id=uid,repeat={}))
    assert row()['recur_id'] is None
    planner.week(con,trainee,tomorrow,rolling=True)
    assert con.execute("SELECT count(*) FROM tasks WHERE recur_id=? AND status='open' AND id!=?",(newrid,tid)).fetchone()[0]==0
    # One occurrence remains an exception; the original auto_key blocks regeneration.
    own=planner.add(con,trainee,{'title':'One','date':day})
    planner.edit_task(con,own,trainee,payload(title='Daily',assignee_id=uid))
    ownrow=row(own); ownrid=ownrow['recur_id']; key=ownrow['auto_key']
    planner.edit_task(con,own,trainee,payload(title='Exception',date=tomorrow,assignee_id=uid,scope='one'))
    assert row(own)['auto_key']==key and row(own)['recur_id']==ownrid
    assert con.execute('SELECT title FROM recurring WHERE id=?',(ownrid,)).fetchone()[0]=='Daily'
    planner._materialize(con,today)
    assert con.execute('SELECT count(*) FROM tasks WHERE auto_key=?',(key,)).fetchone()[0]==1
    for invalid in [payload(assignee_id=uid,repeat={'rule':'weekly','weekdays':[]}),payload(assignee_id=uid,time='99:70'),payload(assignee_id=uid,scope='bad')]:
        before=row(own)
        try: planner.edit_task(con,own,trainee,invalid); raise AssertionError('Invalid edit accepted')
        except planner.PlanError: assert row(own)==before
    forbidden=planner.add(con,admin,{'title':'Private','date':day})
    try: planner.task_detail(con,forbidden,trainee); raise AssertionError('Private task visible')
    except planner.PlanError: pass
    print('Task editing, future series, history, permissions and deduplication: OK')
if __name__=='__main__': main()
