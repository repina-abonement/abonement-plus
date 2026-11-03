"""«Задачи как в ERP» на рабочем столе (Юлия, 03.11). Запуск: python -m tests.test_erpboard"""
import os, sys, tempfile
from datetime import date, datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, schedule, services as sv, tasks
from crm import smart; smart.REQUIRE = False
from tests.test_crm import check


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "e.sqlite3")); db.init_db(con)
    U = {"id": 1, "name": "Оля", "role": "operator"}
    b = schedule.save_branch(con, {"name": "ТЦ «Небо»"})
    schedule.save_group(con, {"name": "Небо", "branch_id": b, "weekdays": [0, 3], "time": "17:00", "age": "4–5"})
    sid = sv.create_student(con, {"name": "Ребёнок Тестов", "parent_name": "Мама", "phone": "+7 900 100-00-01", "funnel": "sales"}, U)
    coach = con.execute("INSERT INTO users(login,name,role,pw_hash) VALUES ('c','Тренер','coach','x$y')").lastrowid
    today = date.today()
    at = lambda d, hm="12:00": f"{(today + timedelta(days=d)).isoformat()}T{hm}"
    for kind, title, due, src in [("trial_confirm", "", at(0), "auto"), ("how_was", "", at(0), "auto"), ("close_sale", "", at(0), "life"),
                                  ("why_not_bought", "", at(-1), "auto"), ("manual", "Позвонить маме", at(0, "23:59"), "manual"),
                                  ("renew", "Продлить абонемент", at(0), "life"), ("manual", "Странный статус", at(0), "erp"),
                                  ("trial_today", "", at(1), "auto")]:
        con.execute("INSERT INTO tasks(kind,title,student_id,due_at,source,created_at) VALUES (?,?,?,?,?,?)",
                    (kind, title or tasks.KINDS[kind][0], sid, due, src, at(-2)))
    con.execute("INSERT INTO tasks(kind,title,student_id,assignee_id,due_at,source,created_at) VALUES ('manual','Тренеру',?,?,?,?,?)",
                (sid, coach, at(0), "manual", at(-2)))
    con.commit()

    d = tasks.erp_board(con, "today")
    titles = [c["title"] for c in d["columns"]]
    check(titles == ["Напоминание о пробном занятии", "Как прошло пробное", "Думает", "Странный статус", "Ручные", "Другое"],
          "Колонки идут по статусам ERP, неизвестный статус ERP своей колонкой, потом Ручные и Другое: " + str(titles))
    check(d["counts"] == {"overdue": 1, "today": 6, "tomorrow": 1}, "Просрочено, Сегодня, Завтра считаются отдельно: " + str(d["counts"]))
    check(all(t["title"] != "Тренеру" for c in d["columns"] for t in c["cards"]), "Задачи тренеров в общий список админов не попадают")
    check([c["title"] for c in tasks.erp_board(con, "overdue")["columns"]] == ["Почему не купили"], "Просроченные отдельно")
    check([c["title"] for c in tasks.erp_board(con, "tomorrow")["columns"]] == ["Напоминание о сегодняшнем ПУ"], "Завтрашние отдельно")
    print("Все проверки задач как в ERP пройдены")


if __name__ == "__main__":
    main()
