"""Журнал группы за месяц: даты занятий и плашка «на абонементе» (Юлия, 04.11). Запуск: python -m tests.test_journal_month"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, schedule, services as sv
from crm import smart; smart.REQUIRE = False
from tests.test_crm import check


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "j.sqlite3")); db.init_db(con)
    U = {"id": 1, "name": "Оля", "role": "operator"}
    b = schedule.save_branch(con, {"name": "ТЦ «Небо»"})
    g = schedule.save_group(con, {"name": "Небо Пн/Чт 17:00", "branch_id": b, "weekdays": [0, 3], "time": "17:00", "age": "4–5"})
    ids = [sv.create_student(con, {"name": f"Ребёнок {i}", "parent_name": "Мама", "phone": f"+7 900 100-00-0{i}",
                                   "funnel": "members", "tariff": "fixed", "group_id": g}, U) for i in range(3)]
    con.execute("INSERT INTO subscriptions(student_id,month,tariff,lessons,per_lesson,price,status,created_at,kind) VALUES (?,?,?,?,?,?,?,?,?)",
                (ids[0], "2026-11", "fixed", 9, 1000, 9000, "paid", "2026-11-01T10:00", "month"))
    con.execute("INSERT INTO subscriptions(student_id,month,tariff,lessons,per_lesson,price,status,created_at,kind) VALUES (?,?,?,?,?,?,?,?,?)",
                (ids[1], "2026-11", "fixed", 9, 1000, 9000, "invoiced", "2026-11-01T10:00", "month"))
    con.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES (?,?,?,?)", (ids[0], g, "2026-11-02", "present"))
    con.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES (?,?,?,?)", (ids[1], g, "2026-11-02", "absent"))
    con.commit()
    m = sv.journal_month(con, g, "2026-11")
    dates = [d["date"] for d in m["days"]]
    check(dates[:3] == ["2026-11-02", "2026-11-05", "2026-11-09"] and len(dates) == 9, "Ноябрь Пн/Чт: 9 занятий")
    d2 = m["days"][0]
    check(d2["list"] == 2 and d2["present"] == 1 and d2["absent"] == 1, "02.11: в журнале 2 (неоплативший без отметки не показан), был 1, пропуск 1")
    check(m["subs"] == {"paid": 1, "invoiced": 1, "single": 0, "none": 1} and m["kids"] == 3, "На абонементе 1, счёт 1, без оплаты 1")
    print("ok")


if __name__ == "__main__":
    main()
