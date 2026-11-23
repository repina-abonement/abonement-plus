import tempfile
from pathlib import Path
from crm import db,targetedrubaylotransfer


def test_verified_rubaylo_transfer_recovers_future_booking_once():
    con=db.connect(str(Path(tempfile.mkdtemp())/'rubaylo.sqlite3'));db.init_db(con)
    admin=con.execute("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()[0]
    con.execute("UPDATE users SET name='Юлия Репина' WHERE id=?",(admin,))
    first=targetedrubaylotransfer.apply(con)
    assert first['state']=='complete'
    student=con.execute("SELECT name,birth,trial_at,historical_trial,stage,source FROM students WHERE id=?",(first['student_id'],)).fetchone()
    assert tuple(student)==('Рубайло Михаил','2025-02-01','2026-11-30T10:30',0,'trial_booked','канал Футболики')
    assert first['task_title']=='Напоминание о ПУ (звонок)' and first['task_due'][:10]=='2026-11-29'
    assert targetedrubaylotransfer.apply(con)==first
    assert con.execute("SELECT count(*) FROM students WHERE name='Рубайло Михаил'").fetchone()[0]==1


def test_rubaylo_transfer_does_not_run_in_other_databases():
    con=db.connect(str(Path(tempfile.mkdtemp())/'other.sqlite3'));db.init_db(con)
    assert targetedrubaylotransfer.apply(con)['state']=='not_target_database'
    assert con.execute("SELECT count(*) FROM students WHERE name='Рубайло Михаил'").fetchone()[0]==0
