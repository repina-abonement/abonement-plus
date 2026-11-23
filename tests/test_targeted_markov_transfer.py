import tempfile
from pathlib import Path

from crm import db, targetedmarkovtransfer


def test_verified_markov_transfer_repairs_once_without_duplicate():
    con = db.connect(str(Path(tempfile.mkdtemp()) / "markov.sqlite3"))
    db.init_db(con)
    admin = con.execute("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()[0]
    con.execute("UPDATE users SET name='Юлия Репина' WHERE id=?", (admin,))
    first = targetedmarkovtransfer.apply(con)
    assert first["state"] == "complete"
    sid = first["student_id"]
    assert con.execute("SELECT COUNT(*) FROM students WHERE id=?", (sid,)).fetchone()[0] == 1
    second = targetedmarkovtransfer.apply(con)
    assert second == first
    assert con.execute("SELECT COUNT(*) FROM students WHERE name IN ('Марков Тимофей','Тимофей Марков')").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM chat_day_transfer_events WHERE source_id=?", (targetedmarkovtransfer.SOURCE_ID,)).fetchone()[0] == 1


def test_markov_transfer_does_not_run_in_other_databases():
    con = db.connect(str(Path(tempfile.mkdtemp()) / "other.sqlite3"))
    db.init_db(con)
    assert targetedmarkovtransfer.apply(con)["state"] == "not_target_database"
    assert con.execute("SELECT COUNT(*) FROM students WHERE name='Марков Тимофей'").fetchone()[0] == 0
