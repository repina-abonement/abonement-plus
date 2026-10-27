"""Проверки оплат: реестр, долг, депозит, частичная оплата, скидки, разовое. Запуск: python -m tests.test_money"""
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import db, money, services as sv  # noqa: E402
from tests.test_crm import check  # noqa: E402

OP = {"id": 2, "name": "Оля", "role": "operator"}
ADMIN = {"id": 1, "name": "Юлия", "role": "admin"}


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "m.sqlite3"))
    db.init_db(con)
    sv.today = lambda: date(2026, 10, 20)
    br = con.execute("INSERT INTO branches(name) VALUES ('Небо')").lastrowid
    g = con.execute("INSERT INTO groups(branch_id,name,weekdays) VALUES (?, 'Пн/Чт', '0,3')", (br,)).lastrowid
    sid = sv.create_student(con, {"name": "Миша", "parent_name": "Анна", "phone": "+7 900 123-45-67", "funnel": "members",
                                  "tariff": "fixed", "group_id": g}, OP)
    print("Счёт и оплата")
    inv = sv.create_invoice(con, sid, "2026-10", OP)  # октябрь: 9 занятий × 1350
    price = inv["quote"]["total"]
    b = money.balance(con, sid, date(2026, 10, 20))
    check(b["debt"] == price and b["deposit"] == 0, f"Неоплаченный октябрь — долг {price} ₽")
    money.pay_subscription(con, inv["id"], "cash", OP, 5000)
    sub = con.execute("SELECT status FROM subscriptions WHERE id=?", (inv["id"],)).fetchone()[0]
    b = money.balance(con, sid, date(2026, 10, 20))
    check(sub == "invoiced" and b["debt"] == price - 5000, "Частичная оплата: счёт ещё ждёт, долг уменьшился")
    money.pay_subscription(con, inv["id"], "card", OP)
    check(con.execute("SELECT status FROM subscriptions WHERE id=?", (inv["id"],)).fetchone()[0] == "paid",
          "Доплатили остаток — абонемент оплачен")
    p = con.execute("SELECT confirmed FROM payments WHERE method='cash'").fetchone()[0]
    check(p == 0, "Наличные от оператора ждут подтверждения администратора")
    money.add_payment(con, sid, 3000, "transfer", OP, comment="Предоплата")
    b = money.balance(con, sid, date(2026, 10, 20))
    check(b["deposit"] == 3000 and b["debt"] == 0, "Лишние деньги лежат на депозите")
    inv2 = sv.create_invoice(con, sid, "2026-11", OP)
    b = money.balance(con, sid, date(2026, 10, 20))
    check(b["deposit"] == 0 and b["upcoming"] == inv2["quote"]["total"] - 3000, "Депозит сам ушёл в счёт на ноябрь")
    pid = con.execute("SELECT id FROM payments WHERE method='card'").fetchone()[0]
    money.cancel_payment(con, pid, ADMIN)
    check(con.execute("SELECT status FROM subscriptions WHERE id=?", (inv["id"],)).fetchone()[0] == "invoiced"
          and con.execute("SELECT cancelled FROM payments WHERE id=?", (pid,)).fetchone()[0] == 1,
          "Отмена платежа: запись осталась в реестре, абонемент снова ждёт оплаты")
    r = money.registry(con, "2026-01-01", "2030-01-01")
    check(r["totals"].get("Наличные") == 5000 and "Картой" not in r["totals"], "Реестр: итоги по способам без отменённых")

    print("Скидки")
    lead = sv.create_student(con, {"name": "Даша", "parent_name": "Ира", "phone": "+7 900 222-22-22", "group_id": g}, OP)
    con.execute("UPDATE students SET trial_at='2026-10-20T17:00', funnel='members', stage='active' WHERE id=?", (lead,))
    sv.add_enrollment(con, lead, g, None, "fixed", OP)
    money_today = date.today
    try:
        money.date = type("D", (), {"today": staticmethod(lambda: date(2026, 10, 20))})
        q = sv.quote_for(con, lead, "2026-11")
        check(q["discount"] and q["discount"]["percent"] == 20 and q["discount"]["auto"],
              "Первый абонемент в день пробного: скидка 20% сама")
        base = q["total"] + q["discount"]["amount"]
        check(q["total"] == base - round(base * 0.2), "Цена со скидкой посчитана верно")
        sv.create_invoice(con, lead, "2026-11", OP)
        q2 = sv.quote_for(con, lead, "2026-12")
        check(q2["discount"] is None, "На второй абонемент скидка уже не даётся")
    finally:
        money.date = date
    con.execute("INSERT INTO discounts(name, percent, created_at) VALUES ('Многодетным', 10, '2026-10-20')")
    did = con.execute("SELECT id FROM discounts WHERE name='Многодетным'").fetchone()[0]
    q3 = sv.quote_for(con, lead, "2026-12", None, did)
    check(q3["discount"]["amount"] == round((q3["total"] + q3["discount"]["amount"]) * 0.1), "Ручная скидка 10% применяется")

    print("Разовое занятие")
    check(con.execute("SELECT value FROM settings WHERE key='price_single'").fetchone()[0] == "2500", "Разовое по умолчанию 2500 ₽")
    con.execute("UPDATE settings SET value='' WHERE key='price_single'")
    try:
        money.sell_single(con, sid, OP)
        check(False, "Без цены разовое не продаётся")
    except ValueError:
        check(True, "Без цены разовое не продаётся, просит указать в настройках")
    con.execute("UPDATE settings SET value='1800' WHERE key='price_single'")
    s1 = money.sell_single(con, sid, OP)
    check(con.execute("SELECT price, kind FROM subscriptions WHERE id=?", (s1,)).fetchone()[:] == (1800, "single"),
          "Разовое занятие 1 800 ₽ выставлено")


if __name__ == "__main__":
    main()
    print("\nВсе проверки оплат пройдены")
