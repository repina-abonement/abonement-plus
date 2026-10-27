"""Проверки планера. Запуск: python -m tests.test_planner"""
import os
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import db, planner, tasks  # noqa: E402
from tests.test_crm import check  # noqa: E402


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "p.sqlite3"))
    db.init_db(con)
    print("Планер")
    u = lambda login, name, role: con.execute("INSERT INTO users(login,name,role,pw_hash) VALUES (?,?,?,'x$y')", (login, name, role)).lastrowid
    julia = {"id": con.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0], "name": "Юлия Репина", "role": "admin"}
    varya = {"id": u("v", "Варвара", "operator"), "name": "Варвара", "role": "operator"}
    olya = {"id": u("o", "Ольга", "operator"), "name": "Ольга", "role": "operator"}
    t = date.today().isoformat()
    planner.add(con, julia, {"title": "Переписать оферту", "priority": True})
    check(tasks.my_tasks(con, julia)["groups"]["today"][0]["title"] == "Переписать оферту", "Личная задача Юлии на её столе")
    planner.add(con, julia, {"title": "Обзвонить должников", "assignee_id": olya["id"]})
    mine = tasks.my_tasks(con, olya)["groups"]["today"]
    check(mine and mine[0]["created_by"] == "Юлия Репина", "У Ольги на столе «Задание от Юли»")
    try:
        planner.add(con, olya, {"title": "Сделай за меня", "assignee_id": varya["id"]})
        check(False, "Оператор не может ставить задачи другим")
    except planner.PlanError:
        check(True, "Оператор не может ставить задачи другим")
    planner.save_settings(con, {"planner_controller": varya["id"]})
    planner.add(con, varya, {"title": "Купить воду", "assignee_id": varya["id"], "repeat": {"rule": "daily"}})
    n = con.execute("SELECT COUNT(*) FROM tasks WHERE title='Купить воду'").fetchone()[0]
    check(n == 2, "Повторяющееся дело разложилось на сегодня и завтра")
    planner.generate(con)
    check(con.execute("SELECT COUNT(*) FROM tasks WHERE title='Купить воду'").fetchone()[0] == 2, "Повтор не дублируется")
    bday = (date.today() + timedelta(days=7)).replace(year=1995)
    planner.set_birth(con, olya["id"], bday.isoformat())
    planner.generate(con)
    bt = con.execute("SELECT * FROM tasks WHERE kind='bday'").fetchone()
    check(bt and bt["assignee_id"] == varya["id"] and "Ольга" in bt["title"], "За неделю до ДР Ольги Варваре задача создать чат")
    team = planner.team(con)
    check(any(x["name"] == "Ольга" and x["today"] for x in team), "Контролёр видит задачи Ольги")
    tid = mine[0]["id"]
    tasks.complete(con, tid, olya, "обзвонила")
    planner.return_task(con, tid, varya, "не всех")
    r = con.execute("SELECT status, returned_note FROM tasks WHERE id=?", (tid,)).fetchone()
    check(r["status"] == "open" and r["returned_note"] == "не всех", "Варвара вернула задачу с комментарием")
    v = planner.view(con, julia)
    check(v["given"] and v["recurring"] and v["can_manage"], "Страница планера: поручения и повторы видны")
    w = planner.week(con, varya, date.today().isoformat())
    water = [x for d in w["days"] for x in d["tasks"] if x["title"] == "Купить воду"]
    check(len(water) >= len([d for d in w["days"] if d["date"] >= t]), "Неделя: «Купить воду» стоит в каждом дне до конца недели")
    tid2 = water[-1]["id"]
    planner.move(con, tid2, varya, (date.today() + timedelta(days=10)).isoformat())
    check(con.execute("SELECT substr(due_at,1,10) FROM tasks WHERE id=?", (tid2,)).fetchone()[0] == (date.today() + timedelta(days=10)).isoformat(),
          "Перетащили задачу на другой день")
    try:
        planner.move(con, tid2, olya, t)
        check(False, "Чужую задачу перенести нельзя")
    except planner.PlanError:
        check(True, "Чужую задачу перенести нельзя")


if __name__ == "__main__":
    main()
    print("\nВсе проверки планера пройдены")
