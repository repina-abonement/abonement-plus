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
    check(d["counts"] == {"overdue": 1, "today": 7, "tomorrow": 1}, "Просрочено, Сегодня, Завтра считаются отдельно: " + str(d["counts"]))
    check(any(t["title"] == "Тренеру" for c in d["columns"] for t in c["cards"]), "Клиентская задача видна всем администраторам независимо от старого исполнителя")
    check([c["title"] for c in tasks.erp_board(con, "overdue")["columns"]] == ["Почему не купили"], "Просроченные отдельно")
    check([c["title"] for c in tasks.erp_board(con, "tomorrow")["columns"]] == ["Напоминание о сегодняшнем ПУ"], "Завтрашние отдельно")
    # «Приглашение на ПУ повторно» (04.11): дата обязательна, задача в этот день, старые задачи по пробному закрываются
    sid2 = sv.create_student(con, {"name": "Елисей Тестов", "parent_name": "Анастасия", "phone": "+7 900 100-00-02", "funnel": "sales"}, U)
    con.execute("UPDATE students SET stage='how_was', trial_at=? WHERE id=?", (at(-60, "18:00"), sid2))
    con.execute("INSERT INTO tasks(kind,title,student_id,due_at,source,created_at,note) VALUES ('why_not_bought','Почему не купили',?,?,?,?,?)",
                (sid2, at(30, "10:00"), "erp", at(-2), "пригласить позже"))
    from crm import server
    check(len(server._old_how_was(con)) == 1, "Был на пробном давно и не купил: найден для перевода")
    server.erp_reinvite_old(con, None, {"id": 1, "name": "Юлия"}, {}, {})
    st = con.execute("SELECT stage FROM students WHERE id=?", (sid2,)).fetchone()[0]
    rt = con.execute("SELECT due_at, note FROM tasks WHERE student_id=? AND kind='reinvite_pu' AND status='open'", (sid2,)).fetchone()
    check(st == "reinvite_pu" and rt and rt[0][:10] == at(30)[:10] and rt[1] == "пригласить позже",
          "Переведён в «Приглашение на ПУ повторно», дата и комментарий взяты из его задачи ERP")
    check(not con.execute("SELECT 1 FROM tasks WHERE student_id=? AND kind='why_not_bought' AND status='open'", (sid2,)).fetchone(),
          "Старая задача «Почему не купили» закрыта")
    try:
        sv.move_card(con, sid2, "sales", "reinvite_pu", U, due="")
        check(False, "Без даты нельзя")
    except sv.ApiError:
        check(True, "Без даты перевести нельзя")
    sv.move_card(con, sid2, "sales", "reinvite_pu", U, comment="через месяц", due=at(1, "11:00"))
    check([c["title"] for c in tasks.erp_board(con, "tomorrow")["columns"]][-1] == "Приглашение на ПУ повторно",
          "Задача на выбранный день в колонке «Приглашение на ПУ повторно»")
    sid3 = sv.create_student(con, {"name": "Новенький Лидов", "parent_name": "Мама", "phone": "+7 900 100-00-03", "funnel": "sales"}, U)
    con.execute("INSERT INTO tasks(kind,title,student_id,due_at,source,created_at) VALUES ('no_answer','Недозвон: позвонить ещё раз',?,?,?,?)",
                (sid3, at(0, "15:00"), "life", at(-1)))
    con.execute("UPDATE students SET stage='no_answer' WHERE id=?", (sid3,))
    cols = {c["title"]: [t["student"]["name"] for t in c["cards"]] for c in tasks.erp_board(con, "today")["columns"]}
    check("Новенький Лидов" in cols.get("Новый лид", []) and list(cols)[0] == "Новый лид",
          "Лид без пробного: задача в колонке «Новый лид», первой слева")
    print("Все проверки задач как в ERP пройдены")


if __name__ == "__main__":
    main()
