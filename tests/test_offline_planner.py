"""One shared offline planner; unrelated seniors cannot view it, observer cannot edit."""
import tempfile
from pathlib import Path
from datetime import date
from crm import db,planner,offlineplanner as op,perms,tasks,comms,services as sv


def main():
    con=db.connect(str(Path(tempfile.mkdtemp())/'offline.sqlite3'));db.init_db(con)
    owner=dict(con.execute("SELECT * FROM users WHERE role='admin'").fetchone())
    def user(name,senior=0):
        uid=con.execute("INSERT INTO users(login,name,role,pw_hash,is_senior) VALUES (?,?,'operator','disabled',?)",(name,name,senior)).lastrowid
        return dict(con.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())
    one=user('Анна Данейкина');two=user('Виктория Сафронова');varya=user('Варвара',1);senior=user('Другой старший',1);other=user('Другой администратор')
    # Existing personal tasks are preserved until a separate migration decision.
    private=planner.add(con,one,{'title':'Личная до новой роли'})
    duplicate=user('Другая Сафронова')
    op.provision_requested(con)
    assert not op.member(con,one) and not op.member(con,two)
    con.execute('DELETE FROM users WHERE id=?',(duplicate['id'],))
    op.provision_requested(con)
    one=dict(con.execute('SELECT * FROM users WHERE id=?',(one['id'],)).fetchone());two=dict(con.execute('SELECT * FROM users WHERE id=?',(two['id'],)).fetchone())
    assert one['is_offline_admin'] and two['is_offline_admin'] and not one['is_senior']
    assert perms.title(one)=='Офлайн-администратор' and not perms.allowed(one,'operator') and perms.allowed(one,'planner')
    assert op.can_view(con,varya) and not op.can_edit(con,varya)
    pool=op.pool_id(con);assert not con.execute('SELECT active FROM users WHERE id=?',(pool,)).fetchone()[0]
    tid=planner.add(con,one,{'title':'Общая задача'})
    assert con.execute('SELECT assignee_id FROM tasks WHERE id=?',(tid,)).fetchone()[0]==pool
    today=date.today().isoformat()
    for actor in (one,two):
        w=planner.week(con,actor,today,rolling=True)
        assert w['user_id']==pool and w['can_edit']
        assert any(t['id']==tid for d in w['days'] for t in d['tasks'])
        assert not any(t['id']==private for d in w['days'] for t in d['tasks'])
    assert planner.task_detail(con,tid,varya)['can_edit'] is False
    assert planner.week(con,varya,today,pool)['can_edit'] is False
    def denied(fn):
        try:fn();raise AssertionError('unexpected access')
        except (planner.PlanError,sv.ApiError,comms.CommsError):pass
    for actor in (senior,other):
        assert not any(s['id']==pool for s in planner.visible_staff(con,actor))
        denied(lambda:planner.week(con,actor,today,pool))
        denied(lambda:planner.task_detail(con,tid,actor))
        assert not any(r['id']==pool for r in planner.view(con,actor)['recurring'])
        privacy,ids=tasks._planner_privacy(con,actor)
        assert not con.execute('SELECT id FROM tasks WHERE id=?'+privacy,(tid,*ids)).fetchone()
    for action in (lambda:planner.move(con,tid,varya,today),lambda:planner.reopen(con,tid,varya),lambda:planner.cancel(con,tid,varya),lambda:tasks.complete(con,tid,varya),lambda:planner.add(con,varya,{'title':'Denied','assignee_id':pool})):
        denied(action)
    tasks.complete(con,tid,two,'Готово')
    assert con.execute('SELECT status FROM tasks WHERE id=?',(tid,)).fetchone()[0]=='done'
    rid=planner.add(con,one,{'title':'Общий повтор','repeat':{'rule':'daily'}})
    assert any(r['id']==rid and not r['can_edit'] for r in planner.view(con,varya)['recurring'])
    denied(lambda:planner.stop_recurring(con,rid,varya))
    planner.stop_recurring(con,rid,two)
    # Provisioning is one-time and does not override later owner decisions.
    con.execute('UPDATE users SET is_offline_admin=0 WHERE id=?',(one['id'],));op.provision_requested(con)
    assert not con.execute('SELECT is_offline_admin FROM users WHERE id=?',(one['id'],)).fetchone()[0]
    assert con.execute('SELECT assignee_id FROM tasks WHERE id=?',(private,)).fetchone()[0]==one['id']
    print('PASS shared board, two editors/owner, explicit observer read-only, unrelated senior privacy, task/repeat mutations, existing personal history, one-time named setup')

if __name__=='__main__':main()
