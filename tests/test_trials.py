"""Пробные: возраст, отчёт конверсии, задача по итогу от тренера. Запуск: python -m tests.test_trials"""
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import analytics, coachreport, db, logic, services as sv  # noqa: E402
from tests.test_crm import check  # noqa: E402

OP = {"id": 2, "name": "Ольга", "role": "operator"}


def main():
    on = date(2026, 10, 29)
    print("Возраст")
    check(logic.age_warning("2024-02-10", "3-5", "Небо", on).startswith("не по возрасту: 2 года 8 мес."), "2 года 8 месяцев в группу 3-5: предупреждение")
    check(not logic.age_warning("2017-03-01", "6-8", "Чоботовская", on), "Чоботовская 6-8: девятилетнему можно")
    check(logic.age_warning("2017-03-01", "6-8", "ТЦ «Небо»", on), "В Небе 6-8 девятилетнему нельзя")
    check(not logic.age_warning("", "3-5", "Небо", on), "Без даты рождения не ругаемся, а просим её указать")

    con = db.connect(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
    db.init_db(con)
    br = con.execute("INSERT INTO branches(name) VALUES ('Небо')").lastrowid
    coach = con.execute("INSERT INTO users(login,name,role,pw_hash) VALUES ('c','Никита','coach','x')").lastrowid
    g = con.execute("INSERT INTO groups(branch_id,name,weekdays,coach_id) VALUES (?, 'Небо Пн', '0', ?)", (br, coach)).lastrowid
    ids = []
    for i, (came, conv) in enumerate([(1, "2026-10-05T19:00"), (1, "2026-10-09T12:00"), (1, None), (0, None)]):
        sid = sv.create_student(con, {"name": f"Лид {i}", "parent_name": "Мама", "phone": f"+7 900 200-00-0{i}", "group_id": g}, OP)
        con.execute("UPDATE students SET trial_at='2026-10-05T18:00', trial_came=?, converted_at=? WHERE id=?", (came, conv, sid))
        ids.append(sid)
    con.execute("UPDATE students SET refusal_reason='Дорого' WHERE id=?", (ids[2],))
    print("Отчёт конверсии")
    r = analytics.trials_report(con, "2026-10")
    t = r["total"]
    check((t["booked"], t["came"], t["bought_same_day"], t["bought_later"]) == (4, 3, 1, 1), "Записано 4, пришли 3, купили в день 1, позже 1")
    check(t["bought_pct"] == 67 and r["coaches"][0]["name"] == "Никита" and r["reasons"][0]["reason"] == "Дорого",
          "Конверсия 67% из пришедших, разрез по тренеру и причины отказа")

    print("Итог от тренера -> задача")
    v = {"main": "Никита", "date": "2026-10-05", "groups": [{"id": g, "name": "Небо Пн"}],
         "trials": [{"id": ids[2], "came": 1, "group": "Небо Пн"}, {"id": ids[3], "came": 0, "group": "Небо Пн"}],
         "data": {"trials": {str(ids[2]): "think"}}}
    check(coachreport.trial_tasks(con, v) == 1, "«Думают» от тренера: одна задача ответственному")
    task = con.execute("SELECT title, note FROM tasks WHERE student_id=?", (ids[2],)).fetchone()
    check("Думают" in task["title"] and "Никита" in task["note"], "В задаче слова тренера")
    check(coachreport.trial_tasks(con, v) == 0, "Повторно не дублируется")
    print("ok")


if __name__ == "__main__":
    main()
