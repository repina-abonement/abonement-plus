"""Move Victoria's open personal tasks/series to one board without copies or lost history."""
import tempfile
from pathlib import Path
from datetime import date,timedelta
from crm import db,planner,offlineplanner as op,tasks


def main():
    con=db.connect(str(Path(tempfile.mkdtemp())/'shared.sqlite3'));db.init_db(con)
    def user(name):
        uid=con.execute("INSERT INTO users(login,name,role,pw_hash) VALUES (?,?,'operator','disabled')",(name+str(con.execute('SELECT count(*) FROM users').fetchone()[0]),name)).lastrowid
        return dict(con.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())
    victoria=user('Виктория Сафронова');tatyana=user('Татьяна Данейкина');varya=user('Варвара');stranger=user('Другой')
    tid=planner.add(con,victoria,{'title':'Существующая задача','note':'Сохранить описание'})
    done=planner.add(con,victoria,{'title':'Выполненная задача'});tasks.complete(con,done,victoria,'Исторический результат')
    rid=planner.add(con,victoria,{'title':'Личный повтор','repeat':{'rule':'daily'}})
    personal_other=planner.add(con,tatyana,{'title':'Личная Татьяны'})
    other=planner.add(con,stranger,{'title':'Чужая задача'})
    before=dict(con.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone())
    history=tuple(con.execute('SELECT * FROM tasks WHERE id=?',(done,)).fetchone())
    count=con.execute('SELECT count(*) FROM tasks').fetchone()[0]
    op.provision_requested(con)
    # Ambiguous identity must not cause any migration.
    duplicate=user('Виктория Сафронова');con.execute('UPDATE users SET is_offline_admin=1 WHERE id=?',(duplicate['id'],))
    op.share_victoria_existing(con)
    assert con.execute('SELECT assignee_id FROM tasks WHERE id=?',(tid,)).fetchone()[0]==victoria['id']
    con.execute('DELETE FROM users WHERE id=?',(duplicate['id'],))
    op.share_victoria_existing(con);pool=op.pool_id(con)
    after=dict(con.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone())
    assert {k:v for k,v in after.items() if k!='assignee_id'}=={k:v for k,v in before.items() if k!='assignee_id'}
    assert after['assignee_id']==pool and con.execute('SELECT count(*) FROM tasks').fetchone()[0]==count
    assert tuple(con.execute('SELECT * FROM tasks WHERE id=?',(done,)).fetchone())==history
    assert con.execute('SELECT assignee_id FROM recurring WHERE id=?',(rid,)).fetchone()[0]==pool
    assert con.execute("SELECT count(*) FROM tasks WHERE recur_id=? AND status='open' AND assignee_id!=?",(rid,pool)).fetchone()[0]==0
    assert con.execute('SELECT assignee_id FROM tasks WHERE id=?',(personal_other,)).fetchone()[0]==tatyana['id']
    assert con.execute('SELECT assignee_id FROM tasks WHERE id=?',(other,)).fetchone()[0]==stranger['id']
    assert any(t['id']==tid for day in planner.week(con,tatyana,date.today().isoformat())['days'] for t in day['tasks'])
    assert planner.task_detail(con,tid,tatyana)['can_edit']
    try:planner.task_detail(con,tid,stranger);raise AssertionError('private task leaked')
    except planner.PlanError:pass
    # Opening the week legitimately generates the remaining recurring dates.
    # Check migration idempotence against that updated state, not the pre-week count.
    after_week=[tuple(r) for r in con.execute('SELECT * FROM tasks ORDER BY id')]
    op.share_victoria_existing(con)
    assert [tuple(r) for r in con.execute('SELECT * FROM tasks ORDER BY id')]==after_week
    planner.generate(con,date.today()+timedelta(days=3))
    assert con.execute("SELECT count(*) FROM tasks WHERE recur_id=? AND status='open' AND assignee_id!=?",(rid,pool)).fetchone()[0]==0
    print('PASS existing open tasks and repeats visible to Tatyana, unchanged IDs/content/history, no copies, future shared instances, identity guard, outsider privacy, idempotence')

if __name__=='__main__':main()
