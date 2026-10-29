"""Перерасчёт на «Гибком» только по медсправке. Запуск: python -m tests.test_cert"""
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import db, services as sv  # noqa: E402
from tests.test_crm import check  # noqa: E402

OP = {"id": 2, "name": "Ольга", "role": "operator"}


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "c.sqlite3"))
    db.init_db(con)
    br = con.execute("INSERT INTO branches(name) VALUES ('Небо')").lastrowid
    g = con.execute("INSERT INTO groups(branch_id,name,weekdays) VALUES (?, 'Сб', '5')", (br,)).lastrowid
    sid = sv.create_student(con, {"name": "Маша", "parent_name": "Мама", "phone": "+7 900 400-00-01", "funnel": "members",
                                  "group_id": g, "tariff": "flex"}, OP)
    for d in ("2026-10-03", "2026-10-10", "2026-11-07", "2026-11-14"):  # пропуски вносим напрямую: даты в будущем для теста
        con.execute("INSERT INTO attendance(student_id, group_id, date, status) VALUES (?,?,?, 'absent')", (sid, g, d))
    sv.today = lambda: date(2026, 10, 20)
    check(sv.quote_for(con, sid, "2026-11")["credit_lessons"] == 2, "Ноябрь (до новых правил): оба пропуска октября в перерасчёте")
    sv.today = lambda: date(2026, 11, 20)
    check(sv.quote_for(con, sid, "2026-12")["credit_lessons"] == 0, "Декабрь: пропуски без справки не пересчитываем")
    a = con.execute("SELECT id FROM attendance WHERE date='2026-11-07'").fetchone()[0]
    con.execute("UPDATE attendance SET cert=1 WHERE id=?", (a,))
    q = sv.quote_for(con, sid, "2026-12")
    check(q["credit_lessons"] == 1 and q["credit_absence_dates"] == ["2026-11-07"], "Со справкой: в перерасчёт идёт только этот пропуск")
    print("ok")


if __name__ == "__main__":
    main()
