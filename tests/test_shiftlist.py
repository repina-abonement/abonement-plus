"""Списки смены и отметка «журнал» у администратора (Юлия, 04.11). Запуск: python -m tests.test_shiftlist"""
import os, sys, tempfile
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, perms, schedule, services as sv, shiftlist
from crm import smart; smart.REQUIRE = False
from tests.test_crm import check


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "s.sqlite3")); db.init_db(con)
    U = {"id": 1, "name": "Оля", "role": "operator"}
    b = schedule.save_branch(con, {"name": "ТЦ «Небо»"})
    g = schedule.save_group(con, {"name": "Небо Пн/Чт 17:00", "branch_id": b, "weekdays": [0, 3], "time": "17:00", "age": "4–5"})
    ids = [sv.create_student(con, {"name": f"Ребёнок {i}", "parent_name": "Мама", "phone": f"+7 900 100-00-0{i}", "birth": "2021-03-01",
                                   "funnel": "members", "tariff": "fixed", "group_id": g}, U) for i in range(3)]
    con.execute("INSERT INTO subscriptions(student_id,month,tariff,lessons,per_lesson,price,status,created_at,kind) VALUES (?,?,?,?,?,?,?,?,?)",
                (ids[2], "2026-11", "fixed", 9, 1000, 9000, "paid", "2026-11-01T10:00", "month"))
    t = sv.create_student(con, {"name": "Новичок Пробный", "parent_name": "Анна", "phone": "+7 900 100-00-09", "birth": "2020-01-10",
                                "funnel": "sales", "group_id": g}, U)
    con.execute("UPDATE students SET trial_at='2026-11-02T17:00', group_id=? WHERE id=?", (g, t))
    con.execute("INSERT INTO attendance(student_id,group_id,date,status,warned,note) VALUES (?,?,?,?,?,?)", (ids[1], g, "2026-11-02", "absent", 1, "заболел"))
    con.commit()
    sh = shiftlist.shift(con, b, "2026-11-02")
    kids = sh["groups"][0]["kids"]
    check([k["label"] for k in kids] == ["абонемент", "вводник", "не придёт"], "Порядок: абонемент, вводник, не придёт; без оплаты в списке нет")
    j = sv.journal(con, g, "2026-11-02", paid_only=True)
    check([x["name"] for x in j["hidden_unpaid"]] == ["Ребёнок 0"], "Журнал: неоплативший спрятан и показан строкой «Не оплатили»")
    check(len(sv.journal(con, g, "2026-11-02")["rows"]) == 4, "Для подсчёта мест CRM по-прежнему видит всех")
    check(kids[0]["age"] == "5 лет", "Возраст на дату тренировки")
    check(sh["trials"] == [{"name": "Новичок Пробный", "age": "6 лет", "group": "Небо Пн/Чт 17:00", "time": "17:00", "parent": "Анна"}],
          "Блок вводников: имя, возраст, группа, родитель")
    check(shiftlist.shift(con, b, "2026-11-03")["groups"] == [], "Во вторник у группы занятий нет")

    from crm import lifecycle, tasks
    from datetime import datetime
    sv.today = lambda: date(2026, 11, 30)
    sv.mark_attendance(con, ids[0], g, "2026-11-02", "present", U)
    t = con.execute("SELECT kind, note FROM tasks WHERE student_id=? AND status='open' AND kind='unpaid_visit'", (ids[0],)).fetchone()
    check(t and "02.11" in t["note"], "Был на занятии без оплаты: красная задача администраторам")
    check(tasks._erp_col({"kind": "unpaid_visit", "student": {}})[0] == "unpaid_visit", "…в своей колонке на рабочем столе")
    con.execute("INSERT INTO subscriptions(student_id,month,tariff,lessons,per_lesson,price,status,created_at,kind) VALUES (?,'2026-11','fixed',9,1000,9000,'paid','2026-11-03','month')", (ids[0],))
    lifecycle.cleanup(con, datetime(2026, 11, 3, 12, 0))
    check(not con.execute("SELECT 1 FROM tasks WHERE student_id=? AND status='open' AND kind='unpaid_visit'", (ids[0],)).fetchone(), "Оплатили: задача снялась сама")
    sv.mark_attendance(con, ids[2], g, "2026-11-02", "present", U)
    check(not con.execute("SELECT 1 FROM tasks WHERE student_id=? AND kind='unpaid_visit'", (ids[2],)).fetchone(), "У оплатившего задачи нет")

    adm = {"id": 5, "role": "operator"}
    check(perms.level(adm, "journal") == perms.LEVEL["view"], "Администратор: журнал только смотреть")
    check(perms.level(adm | {"is_journal": 1}, "journal") == perms.LEVEL["full"], "С отметкой «журнал» отмечает детей и тренеров")
    check(perms.level({"id": 6, "role": "coach"}, "journal") == perms.LEVEL["own"], "Тренер по-прежнему отмечает свои группы")
    print("ok")


if __name__ == "__main__":
    main()
