import tempfile
from pathlib import Path

from crm import coachreport, comms, db, people, services, smart
from crm.keyboardlayout import variants


def test_wrong_keyboard_layout_is_read_only_and_works_across_client_searches():
    con = db.connect(str(Path(tempfile.mkdtemp()) / "layout.sqlite3"))
    db.init_db(con)
    smart.REQUIRE = False
    user = {"id": 1, "role": "admin", "name": "Тест"}
    sid = services.create_student(con, {"name": "Иванов Александр", "parent_name": "Анна", "phone": "+7 900 123-45-67"}, user)
    family_id = con.execute("SELECT family_id FROM students WHERE id=?", (sid,)).fetchone()[0]
    conversation_id = con.execute(
        "INSERT INTO conversations(channel,external_id,title,family_id,created_at) VALUES ('telegram','layout','Иванов Александр',?,'2026-11-20')",
        (family_id,),
    ).lastrowid
    before = con.serialize()

    assert variants("bdfyjd") == ["bdfyjd", "иванов"]
    assert [row["id"] for row in services.search_students(con, "bdfyjd")] == [sid]
    assert [row["id"] for row in people.listing(con, q="bdfyjd")["rows"]] == [sid]
    assert [row["id"] for row in coachreport.search_clients(con, "bdfyjd")] == [sid]
    assert [row["id"] for row in comms.inbox(con, q="bdfyjd")["conversations"]] == [conversation_id]
    assert con.serialize() == before


def test_english_names_and_numbers_are_not_lost():
    assert "Smith" in variants("Smith")
    assert variants("79001234567") == ["79001234567"]
