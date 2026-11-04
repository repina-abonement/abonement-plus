"""Разовые занятия (Юлия, 03.11). Запуск: python -m tests.test_singles"""
import os, sys, tempfile
from datetime import date, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, lifecycle, schedule, services as sv, singles, tasks
from crm import smart; smart.REQUIRE = False
from tests.test_crm import check


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "s.sqlite3")); db.init_db(con)
    U = {"id": 1, "name": "Оля", "role": "operator"}
    sv.today = lambda: date(2026, 11, 4)
    b = schedule.save_branch(con, {"name": "ТЦ «Небо»"})
    g = schedule.save_group(con, {"name": "Небо Пн/Чт 17:00", "branch_id": b, "weekdays": [0, 3], "time": "17:00", "age": "4–5"})
    sid = sv.create_student(con, {"name": "Саша Разовый", "parent_name": "Мама", "phone": "+7 900 300-00-01", "funnel": "members",
                                  "tariff": "fixed", "group_id": g}, U)
    try:
        singles.book(con, sid, g, "2026-11-10", U); check(False, "Во вторник у группы нет занятия")
    except sv.ApiError:
        check(True, "Во вторник у группы нет занятия: не записываем")
    vid = singles.book(con, sid, g, "2026-11-12", U)
    t = con.execute("SELECT * FROM tasks WHERE auto_key=?", (f"single:{vid}:2026-11-12",)).fetchone()
    check(t["due_at"] == "2026-11-11T10:00" and "12.11" in t["title"], "Задача накануне в 10:00")
    check("17:00" in t["note"] and "Саша" in t["note"] and "ссылк" in t["note"].lower(), "CRM предлагает текст: время, имя, ссылка на оплату")
    check(tasks._erp_col({"kind": "single_remind", "student": {}})[0] == "single", "На рабочем столе колонка «Разовые занятия»")
    tasks.tick(con, datetime(2026, 11, 4, 12, 0)) if hasattr(tasks, "tick") else None
    check(con.execute("SELECT status FROM tasks WHERE auto_key=?", (f"single:{vid}:2026-11-12",)).fetchone()[0] == "open", "Уборка задач по пробным разовые не трогает")
    check(singles.is_single(con, sid, "2026-11"), "Статус «Разовые занятия»")
    bd = sv.board(con, "members") if hasattr(sv, "board") else None
    if bd:
        col = next(c for c in bd["columns"] if c["key"] == "single")
        check([k["id"] for k in col["cards"]] == [sid], "В «Действующих клиентах» в колонке «Разовые», а не в должниках")
    j = sv.journal(con, g, "2026-11-12", paid_only=True)
    r = [x for x in j["rows"] if x["id"] == sid]
    check(len(r) == 1 and r[0]["tag"] == "single", "В журнале 12.11 с пометкой «разовое»")
    check(not [x for x in sv.journal(con, g, "2026-11-09", paid_only=True)["rows"] if x["id"] == sid], "В другие дни в журнале нет")
    lifecycle.tick(con, datetime(2026, 11, 26, 12, 0))
    check(not con.execute("SELECT 1 FROM tasks WHERE student_id=? AND kind IN ('renew','debt') AND status='open'", (sid,)).fetchone(),
          "Разовик не должник: нет задач «Продлить» и «Долг»")
    r = singles.next_step(con, sid, "unknown", U)
    check(r["due"] == "2026-11-11" and con.execute("SELECT 1 FROM tasks WHERE student_id=? AND kind='single_next' AND status='open'", (sid,)).fetchone(),
          "«Пока не знаем»: спросить через 7 дней")
    singles.next_step(con, sid, "date", U, g, "2026-11-19")
    check(not con.execute("SELECT 1 FROM tasks WHERE student_id=? AND kind='single_next' AND status='open'", (sid,)).fetchone(),
          "Записали на дату: «договориться» снялась")
    r = singles.next_step(con, sid, "intake", U)
    check(r["due"] == "2027-01-05", "Ближайший набор: 5 января")
    singles.cancel(con, vid, U)
    check(con.execute("SELECT status FROM tasks WHERE auto_key=?", (f"single:{vid}:2026-11-12",)).fetchone()[0] == "cancelled", "Отменили разовое: задача снялась")
    print("ok")


if __name__ == "__main__":
    main()
