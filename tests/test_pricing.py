"""Цены обычных групп: прайс с датой, +5% и без скидок после 25 числа, корректировка, новичок. Запуск: python -m tests.test_pricing"""
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import db, money, pricing, services as sv  # noqa: E402
from crm.services import ApiError  # noqa: E402
from tests.test_crm import check  # noqa: E402

OP = {"id": 2, "name": "Ольга", "role": "operator"}


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "p.sqlite3"))
    db.init_db(con)
    br = con.execute("INSERT INTO branches(name) VALUES ('Чоботовская')").lastrowid
    g = con.execute("INSERT INTO groups(branch_id,name,weekdays) VALUES (?, 'Вт/Пт', '1,4')", (br,)).lastrowid  # в декабре 2026: 9 занятий
    fam = lambda n, ph: sv.create_student(con, {"name": n, "parent_name": "Мама", "phone": ph, "funnel": "members", "tariff": "fixed", "group_id": g}, OP)
    a = fam("Вася", "+7 900 000-00-01")
    b = sv.create_student(con, {"name": "Петя", "family_id": con.execute("SELECT family_id FROM students WHERE id=?", (a,)).fetchone()[0],
                                "funnel": "members", "tariff": "fixed", "group_id": g}, OP)  # брат: скидка второму ребёнку

    print("Прайс с датой")
    check(pricing.prices_for(con, "2026-11")["late_pct"] == 0 and pricing.prices_for(con, "2026-12")["late_pct"] == 5,
          "Ноябрь без надбавки, с декабря +5% после 25 числа")
    pricing.save_price(con, {"valid_from": "2027-01", "fixed": 1400, "flex": 1600, "late_pct": 5})
    check(pricing.prices_for(con, "2026-12")["fixed"] == 1350 and pricing.prices_for(con, "2027-01")["fixed"] == 1400,
          "Новая цена с января не трогает декабрь")

    print("Пример Юлии: Чоботовская, Базовый, 9 занятий в декабре")
    sv.today = lambda: date(2026, 11, 20)
    q = sv.quote_for(con, a, "2026-12")
    check(q["lessons_paid"] == 9 and q["total"] == 12150 and not q["late_fee"], "Оплата до 25 ноября: 12 150 ₽")
    sv.today = lambda: date(2026, 11, 27)
    q = sv.quote_for(con, a, "2026-12")
    check(q["total"] == 12758 and q["late_fee"] == 608, "Оплата 27 ноября: 12 758 ₽ (12 757,50 вверх)")
    sv.today = lambda: date(2026, 10, 27)
    check(sv.quote_for(con, a, "2026-11")["late_fee"] == 0, "Ноябрь после 25 октября по обычной цене")

    print("Скидки только до 25 числа и от обычной цены")
    sv.today = lambda: date(2026, 11, 20)
    q = sv.quote_for(con, b, "2026-12")
    check(q["discount"] and q["total"] == 12150 - 2430, "До 25-го брату скидка 20% от обычной цены")
    sv.today = lambda: date(2026, 11, 26)
    q = sv.quote_for(con, b, "2026-12", discount_id=con.execute("SELECT id FROM discounts WHERE name LIKE 'Многодет%'").fetchone()[0])
    check(q["discount"] is None and q["total"] == 12758, "После 25-го ни автоматической, ни ручной скидки, только +5%")

    print("Дата оплаты решает")
    sv.today = lambda: date(2026, 11, 20)
    inv = sv.create_invoice(con, b, "2026-12", OP)
    check(inv["quote"]["total"] == 9720, "Счёт брату выставлен 20 ноября со скидкой: 9 720 ₽")
    pricing.reprice(con, inv["id"], on=date(2026, 11, 27))
    x = con.execute("SELECT * FROM subscriptions WHERE id=?", (inv["id"],)).fetchone()
    check(x["price"] == 12758 and x["discount_amount"] == 0 and x["late_fee"] == 608,
          "Оплачивают 27-го: счёт пересчитан на 12 758 ₽ без скидки")

    print("Корректировка")
    try:
        pricing.correct(con, inv["id"], 12150, "", OP)
        ok = False
    except pricing.PriceError:
        ok = True
    check(ok, "Без причины не корректирует")
    pricing.correct(con, inv["id"], 12150, "Болели, договорились с руководителем", OP)
    x = con.execute("SELECT price, corrected FROM subscriptions WHERE id=?", (inv["id"],)).fetchone()
    check(x["price"] == 12150 and x["corrected"] == 1, "Обычная цена после 25-го только корректировкой")
    check(not pricing.reprice(con, inv["id"], on=date(2026, 11, 28)), "Скорректированный счёт при оплате не пересчитывается")
    rep = pricing.corrections(con, "2026-01-01", "2030-12-31")
    check(len(rep) == 1 and rep[0]["user_name"] == "Ольга" and rep[0]["old_price"] == 12758 and "Болели" in rep[0]["comment"],
          "В отчёте видно: кто, на сколько и почему")
    money.pay_subscription(con, inv["id"], "cash", OP)
    check(con.execute("SELECT status FROM subscriptions WHERE id=?", (inv["id"],)).fetchone()[0] == "paid", "Оплачен по скорректированной цене")

    print("Новичок с середины месяца")
    n = sv.create_student(con, {"name": "Новенький", "parent_name": "Папа", "phone": "+7 900 000-00-09", "funnel": "sales",
                                "group_id": g}, OP)
    sv.today = lambda: date(2026, 12, 16)
    con.execute("UPDATE students SET funnel='members', stage='active', converted_at='2026-12-15T18:00' WHERE id=?", (n,))
    sv.add_enrollment(con, n, g, None, "fixed", OP) if not sv.enrollments_of(con, n) else None
    q = sv.quote_for(con, n, "2026-12")
    check(q["pricing"]["newcomer"] and q["lessons_paid"] == 4 and q["late_fee"] == 0,
          "Купил 15 декабря: платит за 4 оставшихся занятия, без надбавки")
    check(sv.quote_for(con, a, "2026-12")["lessons_paid"] == 9, "Кто занимается давно, платит за весь месяц")
    print("ok")


if __name__ == "__main__":
    try:
        main()
    except ApiError as e:
        raise SystemExit(f"ApiError: {e}")
