"""Перевод в другую группу без удаления абонемента. Запуск: python -m tests.test_transfer"""
import os
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import db, logic, money, services as sv, transfer  # noqa: E402
from crm.services import ApiError  # noqa: E402
from tests.test_crm import check  # noqa: E402

OP = {"id": 2, "name": "Варя", "role": "operator"}


def raises(fn, *a, **kw) -> str:
    try:
        fn(*a, **kw)
    except ApiError as e:
        return str(e)
    return ""


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
    db.init_db(con)
    t = date.today()
    nxt = logic.shift_month(logic.month_of(t), 1)
    first = date.fromisoformat(nxt + "-01")
    sv.today = lambda: first - timedelta(days=10)  # счёт на следующий месяц выставляем заранее, без наценки
    br = con.execute("INSERT INTO branches(name) VALUES ('Чоботовская')").lastrowid
    old = con.execute("INSERT INTO groups(branch_id,name,weekdays,age) VALUES (?, 'Чоботовская 8-12', '5,6', '8-12')", (br,)).lastrowid
    new = con.execute("INSERT INTO groups(branch_id,name,weekdays,age) VALUES (?, 'Чоботовская 6-8 11:00', '5,6', '6-8')", (br,)).lastrowid
    pn = con.execute("INSERT INTO groups(branch_id,name,weekdays,age) VALUES (?, 'Только суббота', '5', '6-8')", (br,)).lastrowid
    sid = sv.create_student(con, {"name": "Петя", "parent_name": "Анна", "phone": "+7 900 111-22-33", "funnel": "members",
                                  "tariff": "fixed", "group_id": old}, OP)
    eid = sv.enrollments_of(con, sid)[0]["id"]
    inv = sv.create_invoice(con, sid, nxt, OP, eid)
    money.pay_subscription(con, inv["id"], "transfer", OP)
    price = inv["quote"]["total"]

    print("Перевод с 1 числа в группу с теми же днями")
    p = transfer.plan(con, eid, new, first.isoformat())
    check(len(p["months"]) == 1 and p["diff"] == 0, "Абонемент переезжает целиком, доплаты нет")
    transfer.apply(con, eid, new, first.isoformat(), OP)
    sub = con.execute("SELECT * FROM subscriptions WHERE id=?", (inv["id"],)).fetchone()
    ne = con.execute("SELECT * FROM enrollments WHERE student_id=? AND active=1", (sid,)).fetchone()
    check(ne["group_id"] == new and sub["enrollment_id"] == ne["id"] and sub["status"] == "paid" and sub["price"] == price,
          "Оплаченный абонемент теперь в новой группе и остался оплаченным")
    b = money.balance(con, sid)
    check(b["debt"] == 0 and b["deposit"] == 0, "Долга и переплаты нет")
    before = (first - timedelta(days=1 + (first.weekday() - 5) % 7)).isoformat()  # последняя суббота до перевода
    on = next((first + timedelta(days=i)).isoformat() for i in range(7) if (first + timedelta(days=i)).weekday() == 5)
    in_old = lambda d: any(r["id"] == sid for r in sv.journal(con, old, d)["rows"])
    in_new = lambda d: any(r["id"] == sid for r in sv.journal(con, new, d)["rows"])
    check(in_old(before) and not in_new(before), "До даты перевода ребёнок в журнале старой группы")
    check(in_new(on) and not in_old(on), "С даты перевода в журнале новой группы")
    check(con.execute("SELECT group_id FROM students WHERE id=?", (sid,)).fetchone()[0] == new, "В карточке новая группа")
    check("Переведён из группы" in con.execute("SELECT text FROM history WHERE student_id=? ORDER BY id DESC", (sid,)).fetchone()[0],
          "Перевод записан в историю ученика")

    print("Перевод с середины месяца в группу только по субботам")
    mid = date.fromisoformat(on) + timedelta(days=14)  # третья суббота месяца
    p = transfer.plan(con, ne["id"], pn, mid.isoformat())
    m = p["months"][0]
    keep = sum(1 for i in range((mid - first).days) if (first + timedelta(days=i)).weekday() in (5, 6))
    check(m["keep_lessons"] == keep and m["new_lessons"] >= 2, f"До {mid:%d.%m} остаются {keep} занятий, дальше субботы новой группы")
    transfer.apply(con, ne["id"], pn, mid.isoformat(), OP)
    subs = con.execute("SELECT * FROM subscriptions WHERE student_id=? AND month=? ORDER BY id", (sid, nxt)).fetchall()
    unit = price / sub["lessons"]
    check(len(subs) == 2 and subs[0]["lessons"] == keep and subs[1]["lessons"] == m["new_lessons"],
          "Абонемент разделился: начало месяца у прежней группы, конец у новой")
    b = money.balance(con, sid)
    check(b["deposit"] == price - subs[0]["price"] - subs[1]["price"] and b["deposit"] > 0,
          f"Меньше занятий: разница {b['deposit']} ₽ ушла на депозит")
    check(subs[1]["price"] == round(unit * m["new_lessons"]), "Цена занятия та же, что при покупке")

    print("Защита от ошибок")
    e3 = con.execute("SELECT id FROM enrollments WHERE student_id=? AND active=1", (sid,)).fetchone()[0]
    check("уже в этой группе" in raises(transfer.plan, con, e3, pn, mid.isoformat()), "В ту же группу не переводит")
    con.execute("INSERT INTO attendance(student_id, group_id, date, status) VALUES (?,?,?, 'present')",
                (sid, pn, (mid + timedelta(days=7)).isoformat()))
    check("уже есть отметки" in raises(transfer.plan, con, e3, old, mid.isoformat()),
          "Нельзя перевести задним числом поверх отметок в журнале")
    check("с начала текущего месяца" in raises(transfer.plan, con, e3, old, "2020-01-01"), "Слишком старая дата не принимается")
    print("ok")


if __name__ == "__main__":
    main()
