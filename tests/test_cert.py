"""Перерасчёт на «Гибком» без справки, до 3 занятий. Запуск: python -m tests.test_cert"""
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
    eid = sv.enrollments_of(con, sid)[0]["id"]
    for month in ("2026-10", "2026-11"):
        con.execute("""INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at)
            VALUES (?,?,?,'flex',4,1550,6200,'paid','2026-09-20')""", (sid,eid,month))
    check(sv.quote_for(con, sid, "2026-11")["credit_lessons"] == 0, "Без запроса клиента перерасчёта нет")
    sv.today = lambda: date(2026, 10, 20)
    check(sv.quote_for(con, sid, "2026-11", credit_requested=True)["credit_lessons"] == 2, "Ноябрь (до новых правил): оба пропуска октября в перерасчёте")
    sv.today = lambda: date(2026, 11, 20)
    check(sv.quote_for(con, sid, "2026-12", credit_requested=True)["credit_lessons"] == 2, "Декабрь: пропуски переносим и без справки")
    for d in ("2026-11-21", "2026-11-28"):
        con.execute("INSERT INTO attendance(student_id, group_id, date, status) VALUES (?,?,?, 'absent')", (sid, g, d))
    check(sv.quote_for(con, sid, "2026-12", credit_requested=True)["credit_lessons"] == 3, "Не больше 3 занятий в перерасчёт")
    print("ok")


if __name__ == "__main__":
    main()
