import tempfile
from pathlib import Path

from crm import db, targetedkhvostovtransfer


def test_verified_khvostov_transfer_recovers_card_and_followup_once():
    con = db.connect(str(Path(tempfile.mkdtemp()) / "khvostov.sqlite3"))
    db.init_db(con)
    admin = con.execute("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()[0]
    con.execute("UPDATE users SET name='Юлия Репина' WHERE id=?", (admin,))
    first = targetedkhvostovtransfer.apply(con)
    assert first["state"] == "complete"
    student = con.execute("SELECT name,birth,trial_at,trial_came,stage,source FROM students WHERE id=?",
                          (first["student_id"],)).fetchone()
    assert tuple(student) == ("Хвостов Александр", "2024-08-13", "2026-11-23T10:30", 0, "reinvite_pu", "ТРЦ")
    assert first["task_title"] == "Пригласить на адаптационное ПУ 25.11 в 10:30 вместе с родителем"
    assert first["task_due"][:10] == "2026-11-24"
    assert con.execute("SELECT COUNT(*) FROM tasks WHERE student_id=? AND status='open' AND kind='rebook'",(first['student_id'],)).fetchone()[0] == 1
    assert targetedkhvostovtransfer.apply(con) == first
    assert con.execute("SELECT COUNT(*) FROM students WHERE name='Хвостов Александр'").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM chat_day_transfer_events WHERE source_id=?",
                       (targetedkhvostovtransfer.SOURCE_ID,)).fetchone()[0] == 1


def test_khvostov_transfer_does_not_run_in_other_databases():
    con = db.connect(str(Path(tempfile.mkdtemp()) / "other.sqlite3"))
    db.init_db(con)
    assert targetedkhvostovtransfer.apply(con)["state"] == "not_target_database"
    assert con.execute("SELECT COUNT(*) FROM students WHERE name='Хвостов Александр'").fetchone()[0] == 0
