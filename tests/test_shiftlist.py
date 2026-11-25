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
    check([k["label"] for k in kids] == ["абонемент", "вводник"], "В смене только оплаченный абонемент и вводник; неоплаченный с отметкой скрыт")
    j = sv.journal(con, g, "2026-11-02", paid_only=True)
    check([x["name"] for x in j["hidden_unpaid"]] == ["Ребёнок 0"], "Журнал: неоплативший спрятан и показан строкой «Не оплатили»")
    check(len(sv.journal(con, g, "2026-11-02")["rows"]) == 4, "Для подсчёта мест CRM по-прежнему видит всех")
    check(kids[0]["age"] == "5 лет 8 мес.", "Возраст с месяцами на дату тренировки")
    check(shiftlist.shift(con, b, "2026-11-03")["groups"] == [], "Во вторник у группы занятий нет")

    from unittest.mock import patch
    from crm import clientattendance
    with patch.object(sv, "today", return_value=date(2026, 11, 30)):
        for status in ("present", "absent"):
            try:
                sv.mark_attendance(con, ids[0], g, "2026-11-02", status, U)
            except sv.ApiError as error:
                check("не оплачено" in str(error), "Неоплаченное занятие нельзя отметить")
            else:
                raise AssertionError("Неоплаченная отметка сохранена")
        check(not con.execute("SELECT 1 FROM attendance WHERE student_id=?", (ids[0],)).fetchone(),
              "Неоплаченная отметка не записана")
        sv.mark_attendance(con, ids[2], g, "2026-11-02", "present", U)
        other = schedule.save_group(con, {"name": "Вторая группа", "branch_id": b, "weekdays": [0]})
        sv.add_enrollment(con, ids[2], other, None, "fixed", U)
        con.execute("UPDATE enrollments SET start_date='2026-11-01' WHERE student_id=? AND group_id=?", (ids[2], other))
        check(sv.attendance_payment(con, ids[2], other, "2026-11-02") == "unpaid",
              "Абонемент первой группы не даёт отметить занятие второй")
        events = clientattendance.calendar(con, ids[2], "2026-11")["events"]
        check(any(e["group_id"] == g and not e["unpaid"] for e in events), "Карточка показывает оплаченное занятие")
        check(any(e["group_id"] == other and e["unpaid"] for e in events), "Карточка показывает неоплаченное занятие")

    check(sh["trials"] == [{"name": "Новичок Пробный", "age": "6 лет 9 мес.", "group": "Небо Пн/Чт 17:00", "time": "17:00", "parent": "Анна"}],
          "Блок вводников: имя, возраст, группа, родитель")
    adm = {"id": 5, "role": "operator"}
    check(perms.level(adm, "journal") == perms.LEVEL["view"], "Администратор: журнал только смотреть")
    check(perms.level(adm | {"is_journal": 1}, "journal") == perms.LEVEL["full"], "С отметкой «журнал» отмечает детей и тренеров")
    check(perms.level({"id": 6, "role": "coach"}, "journal") == perms.LEVEL["own"], "Тренер по-прежнему отмечает свои группы")
    print("ok")


if __name__ == "__main__":
    main()
