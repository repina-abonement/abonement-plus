import tempfile
from pathlib import Path
from crm import db,schedule,targetedsergievskytransfer


def test_verified_sergievsky_transfer_recovers_erp_card_and_tasks_once():
    con=db.connect(str(Path(tempfile.mkdtemp())/'sergievsky.sqlite3'));db.init_db(con)
    admin=con.execute("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()[0]
    con.execute("UPDATE users SET name='Юлия Репина' WHERE id=?",(admin,))
    branch=schedule.save_branch(con,{'name':'ТЦ «Небо»'})
    group=schedule.save_group(con,{'name':'Небо НУП ср/вс 16:45 возраст 3-4','branch_id':branch,'weekdays':[2,6],'time':'16:45','age':'3-4'})
    first=targetedsergievskytransfer.apply(con)
    assert first['state']=='complete' and first['group_id']==group
    student=con.execute("SELECT name,erp_id,group_id FROM students WHERE id=?",(first['student_id'],)).fetchone()
    assert tuple(student)==('Евгений Сергиевский','182330',group)
    tasks=[tuple(row) for row in con.execute("SELECT title,substr(due_at,1,10) FROM tasks WHERE student_id=? AND status='open' ORDER BY title",(first['student_id'],))]
    assert ('Напомнить родителю об отработке 24.11 в 17:45','2026-11-23') in tasks
    assert ('Напомнить об отработке 24.11 в 17:45','2026-11-24') in tasks
    assert any(title=='Заполнить данные семьи Сергиевского из ERP' for title,_ in tasks)
    assert con.execute("SELECT count(*) FROM tasks WHERE student_id=? AND status='open' AND auto_key='sergievsky-makeup-day-before:2026-11-24'",(first['student_id'],)).fetchone()[0]==1
    assert targetedsergievskytransfer.apply(con)==first
    assert con.execute("SELECT count(*) FROM students WHERE erp_id='182330'").fetchone()[0]==1


def test_sergievsky_transfer_does_not_run_in_other_databases():
    con=db.connect(str(Path(tempfile.mkdtemp())/'other.sqlite3'));db.init_db(con)
    assert targetedsergievskytransfer.apply(con)['state']=='not_target_database'
    assert con.execute("SELECT count(*) FROM students WHERE erp_id='182330'").fetchone()[0]==0
