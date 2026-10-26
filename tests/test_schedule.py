"""Проверки расписания, групп, смен, выгрузки, архива и дней рождения. Запуск: python -m tests.test_schedule"""
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import coaches, db, schedule, services as sv, sources  # noqa: E402
from tests.test_crm import check  # noqa: E402

OP = {"id": 1, "name": "Оля", "role": "operator"}


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "s.sqlite3"))
    db.init_db(con)
    print("Филиалы и группы")
    b = schedule.save_branch(con, {"name": "Небо", "address": "Боровское ш., 2"})
    coach = con.execute("INSERT INTO users(login,name,role,pw_hash) VALUES ('c','Никита','coach','x$y')").lastrowid
    try:
        schedule.save_group(con, {"name": "Без дней", "branch_id": b, "weekdays": []})
        check(False, "Группа без дней не создаётся")
    except schedule.SchedError:
        check(True, "Группа без дней не создаётся")
    g = schedule.save_group(con, {"name": "Небо Пн/Чт", "branch_id": b, "weekdays": [0, 3], "time": "17:45", "age": "4–5",
                                  "capacity": 14, "coach_id": coach})
    check(con.execute("SELECT weekdays, time, capacity FROM groups WHERE id=?", (g,)).fetchone()[:] == ("0,3", "17:45", 14),
          "Группа создана: Пн/Чт 17:45, 14 мест")
    admin = con.execute("INSERT INTO users(login,name,role,pw_hash) VALUES ('o','Оля','operator','x$y')").lastrowid
    schedule.save_shift(con, {"user_id": admin, "branch_id": b, "weekdays": [0, 1, 2, 3, 4], "time_from": "10:00", "time_to": "21:00"})
    d = schedule.day(con, date(2026, 11, 2))  # понедельник
    br = d["branches"][0]
    check(br["lessons"][0]["coach"] == "Никита" and br["staff"][0]["name"] == "Оля", "Понедельник: тренировка с Никитой, на смене Оля")
    check(not schedule.day(con, date(2026, 11, 7))["branches"], "Суббота: в филиале ничего нет")

    print("Ученики, выгрузка, архив")
    sid = sv.create_student(con, {"name": "Миша", "parent_name": "Анна", "phone": "+7 900 123-45-67", "funnel": "members",
                                  "group_id": g, "birth": "2021-11-02",
                                  "source_id": sources.ensure_source(con, "Промоутер", "Промоутер Аня")}, OP)
    name, rows = schedule.group_export(con, g)
    check(rows[1][0] == "Миша" and rows[1][4] == "+7 900 123-45-67" and "Промоутер" in rows[1][10],
          "Выгрузка группы: имя, телефон родителя, источник")
    try:
        schedule.set_group_active(con, g, False)
        check(False, "Группу с учениками нельзя убрать в архив")
    except schedule.SchedError:
        check(True, "Группу с учениками нельзя убрать в архив")
    print("Дни рождения")
    sv.today = lambda: date(2026, 11, 2)
    check([x["name"] for x in sv.birthdays(con, date(2026, 11, 2))] == ["Миша"], "Админ видит именинника в день рождения")
    ct = coaches.coach_today(con, coach, date(2026, 11, 2))
    check(ct["today"][0]["birthdays"][0]["age"] == 5, "Тренер видит: у Миши сегодня 5 лет")
    ct = coaches.coach_today(con, coach, date(2026, 11, 3))
    check(not ct["today"], "Во вторник у группы тренировки нет")
    sid2 = sv.create_student(con, {"name": "Катя", "parent_name": "Ира", "phone": "+7 900 222-33-44", "funnel": "members",
                                   "group_id": g, "birth": "2020-11-04"}, OP)
    ct = coaches.coach_today(con, coach, date(2026, 11, 5))
    check(ct["today"][0]["birthdays"] and ct["today"][0]["birthdays"][0]["when"] == "yesterday",
          "В четверг тренер видит, что у Кати вчера был день рождения")
    schedule.set_student_archived(con, sid2, True, OP)
    check(all(r["id"] != sid2 for r in sv.journal(con, g, "2026-11-05")["rows"]), "Ученик в архиве пропал из журнала")
    check(schedule.archive_list(con)["students"][0]["name"] == "Катя", "…и виден в архиве, его можно вернуть")

    print("Стол тренера и замены")
    coach2 = con.execute("INSERT INTO users(login,name,role,pw_hash) VALUES ('c2','Иван','coach','x$y')").lastrowid
    con.execute("""INSERT INTO subscriptions(student_id, month, tariff, lessons, per_lesson, price, status, created_at)
                   VALUES (?, '2026-11', 'fixed', 8, 1000, 8000, 'paid', '2026-11-01')""", (sid,))
    pid = sv.create_student(con, {"name": "Петя", "parent_name": "Оля", "phone": "+7 900 555-00-11", "funnel": "sales",
                                  "group_id": g}, OP)
    con.execute("UPDATE students SET trial_at='2026-11-09T17:45' WHERE id=?", (pid,))
    sv.create_student(con, {"name": "Лёва", "parent_name": "Нина", "phone": "+7 900 555-00-22", "funnel": "members",
                            "group_id": g}, OP)
    ct = coaches.coach_today(con, coach, date(2026, 11, 9))
    c = ct["today"][0]["counts"]
    check((c["paid"], c["unpaid"], c["trials"], c["total"]) == (1, 1, 1, 3),
          "Тренер видит: придут 3, по абонементу 1, без оплаты 1, пробное 1")
    try:
        schedule.save_sub(con, {"group_id": g, "date": "2026-11-10", "coach_id": coach2}, OP)
        check(False, "Замену во вторник не поставить: тренировки нет")
    except schedule.SchedError:
        check(True, "Замену во вторник не поставить: тренировки нет")
    schedule.save_sub(con, {"group_id": g, "date": "2026-11-09", "coach_id": coach2, "note": "Никита на турнире"}, OP)
    ct = coaches.coach_today(con, coach, date(2026, 11, 9))
    check(not ct["today"] and ct["replaced"][0]["by"] == "Иван", "Штатный тренер видит, что сегодня его заменяет Иван")
    ct2 = coaches.coach_today(con, coach2, date(2026, 11, 9))
    check(ct2["today"][0]["sub_for"]["instead"] == "Никита", "Иван видит у себя тренировку с пометкой «замена вместо Никиты»")
    check(schedule.day(con, date(2026, 11, 9))["branches"][0]["lessons"][0]["coach"] == "Иван", "В расписании на этот день стоит Иван")
    up = coaches.coach_today(con, coach2, date(2026, 11, 6))["upcoming"]["covering"]
    check(up and up[0]["date"] == "2026-11-09", "Иван заранее видит предстоящую замену")


if __name__ == "__main__":
    main()
    print("\nВсе проверки расписания пройдены")
