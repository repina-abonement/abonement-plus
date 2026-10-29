"""Версии расписания группы с даты. Запуск: python -m tests.test_schedversions"""
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import db, logic, money, salary, schedule, services as sv, staff  # noqa: E402
from tests.test_crm import check  # noqa: E402

OP = {"id": 2, "name": "Варя", "role": "operator"}


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "v.sqlite3"))
    db.init_db(con)
    sv.today = lambda: date(2026, 11, 20)
    br = con.execute("INSERT INTO branches(name) VALUES ('Чоботовская')").lastrowid
    coach = con.execute("INSERT INTO users(login,name,role,pw_hash) VALUES ('i','Иван','coach','x')").lastrowid
    g = con.execute("INSERT INTO groups(branch_id,name,weekdays,time,coach_id) VALUES (?, 'Чоб 6-8', '5', '11:00', ?)", (br, coach)).lastrowid
    sid = sv.create_student(con, {"name": "Петя", "parent_name": "Мама", "phone": "+7 900 300-00-01", "funnel": "members", "group_id": g}, OP)
    inv = sv.create_invoice(con, sid, "2026-12", OP)
    money.pay_subscription(con, inv["id"], "cash", OP)
    check(inv["quote"]["lessons_paid"] == 4, "Декабрь по субботам: 4 занятия, оплачено")

    print("С 1 декабря суббота и воскресенье, в 10:00")
    r = schedule.plan_change(con, g, "2026-12-01", {"weekdays": [5, 6], "time": "10:00"}, OP)
    x = con.execute("SELECT lessons, price FROM subscriptions WHERE id=?", (inv["id"],)).fetchone()
    check(r["repriced"] == 1 and x["lessons"] == 8 and x["price"] == 8 * 1350, "Оплаченный декабрь сам переехал: 8 занятий по той же цене")
    check(money.balance(con, sid, date(2026, 12, 5))["debt"] == 4 * 1350, "Разница стала долгом (доплата за новые дни)")
    check(not sv.journal(con, g, "2026-11-29")["has_lesson"], "Воскресенье 29 ноября: занятий ещё нет (старое расписание)")
    check(sv.journal(con, g, "2026-12-06")["has_lesson"], "Воскресенье 6 декабря: занятие есть (новое)")
    g_row = con.execute("SELECT * FROM groups WHERE id=?", (g,)).fetchone()
    check(len(salary._dates(con, g_row, "2026-11")) == 4 and len(salary._dates(con, g_row, "2026-12")) == 8,
          "Зарплата: ноябрь по старому расписанию, декабрь по новому")
    p = next(x for x in staff.planned(con, date(2026, 12, 6)) if x["group_id"] == g)
    check(p["time"] == "10:00", "В декабре время 10:00, тренер видит новое")
    check(sv.quote_for(con, sid, "2027-01")["lessons_paid"] == 8, "Январь по новому расписанию: 10 сб и вс минус праздники 2 и 3 января")
    check(con.execute("SELECT weekdays FROM groups WHERE id=?", (g,)).fetchone()[0] == "5", "Пока 1 декабря не наступило, в карточке группы старое")
    schedule.apply_versions(con, date(2026, 12, 1))
    check(con.execute("SELECT weekdays, time FROM groups WHERE id=?", (g,)).fetchone()[:] == ("5,6", "10:00"), "1 декабря карточка группы переключилась сама")
    print("ok")


if __name__ == "__main__":
    main()
