"""«Ушёл» по причинам и «Воронки без дедлайнов» (Юлия, 03.11 и 04.11). Запуск: python -m tests.test_nodeadline"""
import os, sys, tempfile
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, lifecycle, nodeadline, schedule, services as sv
from crm import smart; smart.REQUIRE = False
from tests.test_crm import check


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "n.sqlite3")); db.init_db(con)
    U = {"id": 1, "name": "Оля", "role": "operator"}
    sv.today = lambda: date(2026, 11, 4)
    b = schedule.save_branch(con, {"name": "ТЦ «Небо»"})
    g = schedule.save_group(con, {"name": "Небо Пн/Чт 17:00", "branch_id": b, "weekdays": [0, 3], "time": "17:00", "age": "4–5"})
    kid = lambda i: sv.create_student(con, {"name": f"Ребёнок {i}", "parent_name": "Мама", "phone": f"+7 900 200-00-{i:02d}",
                                            "funnel": "members", "tariff": "fixed", "group_id": g}, U)
    task = lambda sid: con.execute("SELECT due_at, note FROM tasks WHERE student_id=? AND kind='win_back' AND status='open'", (sid,)).fetchone()

    check(lifecycle.next_intake(date(2026, 11, 4)) == date(2027, 1, 5) and lifecycle.next_intake(date(2027, 3, 1)) == date(2027, 8, 1),
          "Ближайший набор: 5 января или 1 августа")
    a = kid(1); r = sv.move_card(con, a, "members", "left", U, "Неудобное расписание")
    check(r["due"] == "2026-11-18" and task(a)["due_at"].startswith("2026-11-18"), "Неудобное расписание: пригласить через 2 недели")
    a = kid(2); sv.move_card(con, a, "members", "left", U, "Далеко ездить")
    check(task(a)["due_at"].startswith("2027-01-05"), "Далеко ездить: в ближайший набор")
    a = kid(3); sv.move_card(con, a, "members", "left", U, "Перешли в другую секцию")
    check(task(a)["due_at"].startswith("2027-01-05"), "Другая секция: тоже в ближайший набор")
    a = kid(4); sv.move_card(con, a, "members", "left", U, "Другое", "уехали на дачу")
    check(task(a)["due_at"].startswith("2027-01-03"), "Другое: через 2 месяца")
    a = kid(5)
    try:
        sv.move_card(con, a, "members", "left", U, "Болезнь или травма"); check(False, "Болезнь без даты не сохраняется")
    except sv.ApiError:
        check(True, "Болезнь без даты не сохраняется: админ выбирает дату")
    sv.move_card(con, a, "members", "left", U, "Болезнь или травма", "", "2026-12-10T11:00")
    check(task(a)["due_at"].startswith("2026-12-10"), "Болезнь: задача на дату администратора")
    a = kid(6); sv.move_card(con, a, "members", "left", U, "Переезд", "в Казань")
    check(con.execute("SELECT archived FROM students WHERE id=?", (a,)).fetchone()[0] == 1 and not task(a), "Переезд: в архив, без задачи")

    print("Воронки без дедлайнов")
    a = kid(7); r = sv.move_card(con, a, "members", "left", U, "Дорого")
    check(r["plan"] == "promo" and not task(a), "Дорого: без задачи по сроку")
    bd = nodeadline.board(con)["funnels"][0]
    check(bd["title"] == "Ждут акцию" and [c["id"] for c in bd["cards"]] == [a], "…и в воронке «Ждут акцию»")
    made = nodeadline.make_tasks(con, "promo", "Предложить акцию", "2026-11-10T11:00", "−15%", U)
    check(made == 1 and con.execute("SELECT note FROM tasks WHERE student_id=? AND title='Предложить акцию'", (a,)).fetchone()[0] == "−15%",
          "Появилась акция: задача всем одной кнопкой")
    check(nodeadline.make_tasks(con, "promo", "Предложить акцию", "2026-11-10T11:00", "", U) == 0, "Повторно та же задача не ставится")
    sv.update_student(con, a, {"trial_at": "2026-11-12T17:00"}, U) if hasattr(sv, "update_student") else nodeadline.auto_exit(con, a, "записан на пробное")
    check(not nodeadline.board(con)["funnels"][0]["cards"], "Записался снова: из воронки вышел сам")
    print("ok")


if __name__ == "__main__":
    main()
