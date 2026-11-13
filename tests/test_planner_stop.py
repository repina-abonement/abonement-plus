"""Stop a recurrence from a completed occurrence without deleting its history."""
import os
import tempfile
from datetime import date, timedelta
os.environ['CRM_DB']=os.path.join(tempfile.mkdtemp(),'planner-stop.sqlite3')
os.environ['CRM_ADMIN_PASSWORD']='test-admin-password'
os.environ['CRM_QUIET']='1'
from crm import db,planner


def main():
    con=db.connect();db.init_db(con)
    owner_id=con.execute("INSERT INTO users(login,name,role,pw_hash) VALUES ('varya-stop','Варя','operator','unused')").lastrowid
    other_id=con.execute("INSERT INTO users(login,name,role,pw_hash) VALUES ('other-stop','Другой','operator','unused')").lastrowid
    owner=dict(con.execute('SELECT * FROM users WHERE id=?',(owner_id,)).fetchone())
    other=dict(con.execute('SELECT * FROM users WHERE id=?',(other_id,)).fetchone())
    today=date.today();day=today.isoformat()
    rid=planner.add(con,owner,{'title':'Ежедневная задача','date':day,'repeat':{'rule':'daily'}})
    current=con.execute('SELECT id FROM tasks WHERE recur_id=? ORDER BY due_at LIMIT 1',(rid,)).fetchone()[0]
    con.execute("UPDATE tasks SET status='done',done_at=?,done_by='Варя',result='Выполнено' WHERE id=?",(day+'T10:00',current))
    def insert(offset,status='open'):
        due=(today+timedelta(days=offset)).isoformat()+'T15:00'
        return con.execute("INSERT INTO tasks(kind,title,assignee_id,due_at,status,created_at,recur_id,result) VALUES ('plan','Экземпляр',?,?,?,?,?,'История')",(owner_id,due,status,day,rid)).lastrowid
    overdue=insert(-1);today_open=insert(0);future_done=insert(3,'done')
    before_done=[tuple(r) for r in con.execute("SELECT * FROM tasks WHERE recur_id=? AND status='done' ORDER BY id",(rid,))]
    before_overdue=tuple(con.execute('SELECT * FROM tasks WHERE id=?',(overdue,)).fetchone())
    oneoff=planner.add(con,owner,{'title':'Отдельная задача','date':(today+timedelta(days=2)).isoformat()})
    unrelated=planner.add(con,owner,{'title':'Другая серия','repeat':{'rule':'daily'}})
    d=planner.task_detail(con,current,owner)
    assert not d['can_edit'] and d['can_stop_repeat'] and d['repeat']['id']==rid
    assert any(r['id']==rid for r in planner.view(con,owner)['recurring'])
    for action in (lambda:planner.task_detail(con,current,other),lambda:planner.stop_recurring(con,rid,other)):
        try:action();raise AssertionError('foreign access accepted')
        except planner.PlanError:pass
    planner.stop_recurring(con,rid,owner)
    assert con.execute('SELECT active FROM recurring WHERE id=?',(rid,)).fetchone()[0]==0
    assert [tuple(r) for r in con.execute("SELECT * FROM tasks WHERE recur_id=? AND status='done' ORDER BY id",(rid,))]==before_done
    assert tuple(con.execute('SELECT * FROM tasks WHERE id=?',(overdue,)).fetchone())==before_overdue
    assert con.execute('SELECT status,done_by FROM tasks WHERE id=?',(today_open,)).fetchone()[:]==('cancelled','Варя')
    assert con.execute("SELECT count(*) FROM tasks WHERE recur_id=? AND status='open' AND substr(due_at,1,10)>=?",(rid,day)).fetchone()[0]==0
    assert con.execute('SELECT status FROM tasks WHERE id=?',(oneoff,)).fetchone()[0]=='open'
    assert con.execute('SELECT active FROM recurring WHERE id=?',(unrelated,)).fetchone()[0]==1
    stopped=[tuple(r) for r in con.execute('SELECT * FROM tasks WHERE recur_id=? ORDER BY id',(rid,))]
    planner.stop_recurring(con,rid,owner)
    planner.generate(con,today+timedelta(days=10));planner.week(con,owner,day,rolling=True)
    assert [tuple(r) for r in con.execute('SELECT * FROM tasks WHERE recur_id=? ORDER BY id',(rid,))]==stopped
    assert not planner.task_detail(con,current,owner)['can_stop_repeat']
    assert not any(r['id']==rid for r in planner.view(con,owner)['recurring'])
    print('PASS completed occurrence can stop series; preserve completed/overdue, cancel today/future only; permissions, idempotence, no regeneration')

if __name__=='__main__':main()
