"""«Моя зарплата»: на сегодня, весь месяц, цель. Запуск: python -m tests.test_mypay"""
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import db, money, mypay, services as sv  # noqa: E402
from tests.test_crm import check  # noqa: E402


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "y.sqlite3"))
    db.init_db(con)
    sv.today = lambda: date(2026, 11, 20)
    add = lambda login, name, role, prom=0: con.execute("INSERT INTO users(login,name,role,pw_hash,is_promoter) VALUES (?,?,?,'x',?)",
                                                        (login, name, role, prom)).lastrowid
    coach, olga = add("ivan", "Иван", "coach", 1), add("olga", "Ольга", "operator")
    br = con.execute("INSERT INTO branches(name, lesson_limit) VALUES ('Небо', 10)").lastrowid
    g = con.execute("INSERT INTO groups(branch_id,name,weekdays,coach_id,capacity) VALUES (?, 'Вт/Пт', '1,4', ?, 10)", (br, coach)).lastrowid
    con.execute("INSERT INTO shifts(user_id, branch_id, weekdays) VALUES (?,?,'0,2')", (olga, br))  # Пн и Ср
    OP = {"id": olga, "name": "Ольга", "role": "operator"}
    for i in range(3):
        sid = sv.create_student(con, {"name": f"Реб {i}", "parent_name": "Мама", "phone": f"+7 900 000-00-1{i}", "funnel": "members",
                                      "group_id": g, "tariff": "fixed"}, OP)
        inv = sv.create_invoice(con, sid, "2026-11", OP)
        money.pay_subscription(con, inv["id"], "transfer", OP)
    lead = sv.create_student(con, {"name": "Лид", "parent_name": "Папа", "phone": "+7 900 000-00-99"}, OP)
    sv.update_student(con, lead, {"group_id": g, "trial_at": "2026-11-24T18:00"}, OP)
    sv.move_card(con, lead, "sales", "bought", OP)

    print("Тренер-промоутер")
    u = {"id": coach, "role": "coach", "is_promoter": 1}
    v = mypay.view(con, u, "2026-11", date(2026, 11, 20))
    c = v["parts"]["coach"]
    check(0 < c["now"] < c["month"] and c["lessons_done"] < c["lessons"], f"На сегодня {c['now']} ₽ из {c['month']} ₽ за месяц")
    mypay.set_contacts(con, coach, "2026-11", 10)
    mypay.set_goal(con, coach, "2026-11", v["forecast"] + 10000)
    v = mypay.view(con, u, "2026-11", date(2026, 11, 20))
    check(v["parts"]["promoter"]["now"] == 3000, "10 контактов × 300 ₽ = 3 000 ₽")
    check(v["need"]["kids"] >= 1 and v["need"]["groups"][0]["free"] == 6, "Цель: сколько ещё детей и где 6 свободных мест (4 из 10 заняты)")
    check(v["need"]["contacts"] > 0 and v["need"]["per_day"] > 0, "Цель: сколько контактов и сколько в день")

    print("Администратор")
    a = mypay.view(con, {"id": olga, "role": "operator"}, "2026-11", date(2026, 11, 20))["parts"]["admin"]
    check(a["shifts"] == 9 and a["shifts_done"] == 6, "Смены по графику (пн и ср): 6 из 9 к 20 ноября")
    from crm import logic
    a = mypay.view(con, {"id": olga, "role": "operator"}, logic.month_of(date.today()))["parts"]["admin"]  # записи сделаны сейчас
    check(a["trials"] == 1 and a["sales"] == 1, "Запись на пробное и продажа засчитаны тому, кто нажал")
    print("ok")


if __name__ == "__main__":
    main()
