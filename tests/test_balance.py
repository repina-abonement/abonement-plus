"""Баланс клиента: оплата за семью, перенос, возврат, отмена оплаченного, лента. Запуск: python -m tests.test_balance"""
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import db, money, services as sv  # noqa: E402
from crm.services import ApiError  # noqa: E402
from tests.test_crm import check  # noqa: E402

OP = {"id": 2, "name": "Ольга", "role": "operator"}


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "b.sqlite3"))
    db.init_db(con)
    sv.today = lambda: date(2026, 11, 20)
    br = con.execute("INSERT INTO branches(name) VALUES ('Небо')").lastrowid
    g = con.execute("INSERT INTO groups(branch_id,name,weekdays) VALUES (?, 'Сб', '5')", (br,)).lastrowid  # декабрь: 4 субботы
    a = sv.create_student(con, {"name": "Аня", "parent_name": "Мама", "phone": "+7 900 000-11-22", "funnel": "members", "group_id": g}, OP)
    fam = con.execute("SELECT family_id FROM students WHERE id=?", (a,)).fetchone()[0]
    b = sv.create_student(con, {"name": "Боря", "family_id": fam, "funnel": "members", "group_id": g}, OP)
    c = sv.create_student(con, {"name": "Вова", "family_id": fam, "funnel": "members", "group_id": g}, OP)
    con.execute("UPDATE discounts SET active=0")  # сценарий без скидок: считаем только деньги
    inv = {k: sv.create_invoice(con, k, "2026-12", OP) for k in (a, b, c)}
    total = sum(x["quote"]["total"] for x in inv.values())
    check(total == 3 * 4 * 1350, f"Три счёта на декабрь: {total} ₽")

    print("Пример Юлии: СБП 11 400 + переплата 1 350 закрывают три счёта")
    money.add_payment(con, a, 1350, "cash", OP, comment="переплата с прошлого месяца")
    r = money.pay_family(con, a, total - 1350 - 4800 + 4800 - 4800, "sbp", OP)  # сначала частично
    paid_now = sum(p["amount"] for p in r["parts"])
    check(paid_now == total - 1350 - 4800, "Части платежа в сумме дают ровно внесённую сумму")
    r2 = money.pay_family(con, a, 4800, "sbp", OP)
    st = {k: con.execute("SELECT status FROM subscriptions WHERE id=?", (inv[k]["id"],)).fetchone()[0] for k in (a, b, c)}
    check(all(v == "paid" for v in st.values()), "Все три счёта семьи оплачены")
    check(sum(money.balance(con, k)["deposit"] for k in (a, b, c)) == 0 and all(money.balance(con, k)["debt"] == 0 for k in (a, b, c)),
          "Баланс семьи 0, долгов нет")
    reg = money.registry(con, "2026-01-01", "2030-12-31")
    check(reg["total"] == total, "В реестре приход учтён один раз")

    print("Перенос и возврат")
    money.add_payment(con, b, 3000, "cash", OP)
    money.move_balance(con, b, c, 2000, OP)
    check(money.balance(con, b)["deposit"] == 1000 and money.balance(con, c)["deposit"] == 2000, "Перенесли 2 000 ₽ с баланса Бори на Вову")
    check(money.registry(con, "2026-01-01", "2030-12-31")["total"] == total + 3000, "Перенос внутри семьи не считается приходом")
    try:
        money.move_balance(con, b, c, 5000, OP)
        ok = False
    except ValueError:
        ok = True
    check(ok, "Больше переплаты не перенести")
    money.refund(con, c, 2000, OP, "Вернули наличными, переезжают")
    check(money.balance(con, c)["deposit"] == 0, "Возврат забрал переплату")

    print("Отмена оплаченного абонемента")
    sv.cancel_subscription(con, inv[a]["id"], OP)
    check(money.balance(con, a)["deposit"] == 4 * 1350, "Отменили без возврата: деньги на балансе Ани")
    try:
        sv.cancel_subscription(con, inv[b]["id"], OP, refund=True)
        ok = False
    except ApiError:
        ok = True
    check(ok, "Возврат без причины не проходит")
    sv.cancel_subscription(con, inv[b]["id"], OP, refund=True, comment="Заболел надолго, вернули на карту")
    check(money.balance(con, b)["deposit"] == 1000, "Отменили с возвратом: вернули оплату декабря, прежняя переплата осталась")
    led = money.ledger(con, b)
    check(led[0]["rest"] == 1000 and any(e["text"].startswith("Возврат") for e in led), "Лента баланса: возврат и остаток после каждой строки")
    print("ok")


if __name__ == "__main__":
    main()
