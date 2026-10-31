"""Оплата по ссылке PayKeeper: счёт из карточки, оплата сама зачисляется один раз. Запуск: python -m tests.test_paylinks"""
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import comms, db, fiscal, money, paylinks, services as sv  # noqa: E402
from tests.test_crm import check  # noqa: E402

OP = {"id": 2, "name": "Ольга", "role": "operator"}
SENT = []


def fake_pk(con, path, params=None, form=None):
    SENT.append((path, form))
    if path == "/info/settings/token/":
        return {"token": "tok"}
    return {"invoice_id": "INV1", "invoice_url": "https://footbolika.server.paykeeper.ru/bill/INV1/"}


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "p.sqlite3"))
    db.init_db(con)
    br = con.execute("INSERT INTO branches(name) VALUES ('Небо')").lastrowid
    g = con.execute("INSERT INTO groups(branch_id,name,weekdays) VALUES (?, 'Сб', '5')", (br,)).lastrowid
    a = sv.create_student(con, {"name": "Аня", "parent_name": "Мама Ани", "phone": "+7 900 000-11-22", "funnel": "members", "group_id": g}, OP)
    try:
        paylinks.create(con, a, 5400, OP)
        ok = False
    except paylinks.PayLinkError:
        ok = True
    check(ok, "Без PayKeeper ссылку не создать, CRM объясняет, где подключить")
    for k, v in (("fx_pk_server", "https://footbolika.server.paykeeper.ru"), ("fx_pk_user", "api"), ("fx_pk_password", "p")):
        comms.put(con, k, v)
    fiscal.pk_call = fake_pk
    r = paylinks.create(con, a, 5400, OP)
    form = SENT[-1][1]
    check(r["url"].endswith("/bill/INV1/") and form["pay_amount"] == "5400.00" and form["client_phone"] == "+79000001122"
          and form["token"] == "tok" and form["orderid"].startswith(f"crm{a}-"), "Счёт в PayKeeper: сумма, телефон родителя, номер заказа")
    pay = {"id": "777", "orderid": form["orderid"], "pay_amount": "5400.00", "status": "success", "success_datetime": "2026-11-02 12:30:00"}
    fiscal.fetch_pk_payments = lambda c, a1, b1: [pay, {"id": "8", "orderid": "чужой", "pay_amount": "100", "status": "success"}]
    check(paylinks.sync(con, date.today()) == 1, "Оплата по ссылке зачислена, чужие платежи не трогаем")
    check(paylinks.sync(con, date.today()) == 0, "Повторно не зачисляется")
    p = con.execute("SELECT amount, method, confirmed, comment FROM payments WHERE student_id=?", (a,)).fetchone()
    check(tuple(p)[:3] == (5400, "online", 1) and "777" in p[3], "В оплатах ученика: 5400 ₽ онлайн, подтверждено, номер платежа PayKeeper")
    check(money.balance(con, a)["deposit"] == 5400, "Деньги на балансе ребёнка")
    check(paylinks.listing(con, a)[0]["status"] == "paid", "Ссылка помечена оплаченной")
    print("Ссылка на конкретный абонемент")
    con.execute("UPDATE discounts SET active=0")
    inv = sv.create_invoice(con, a, "2026-12", OP)
    left = inv["quote"]["total"]
    money.add_payment(con, a, 1, "cash", OP)  # чтобы депозит не закрыл счёт целиком
    con.execute("DELETE FROM payments WHERE student_id=?", (a,)); money.reconcile(con, a)
    r2 = paylinks.create(con, a, left, OP, "Абонемент декабрь", inv["id"])
    pay2 = {"id": "778", "orderid": SENT[-1][1]["orderid"], "pay_amount": str(left), "status": "success"}
    fiscal.fetch_pk_payments = lambda c, a1, b1: [pay2]
    paylinks.sync(con, date.today())
    st = con.execute("SELECT status FROM subscriptions WHERE id=?", (inv["id"],)).fetchone()[0]
    sp = con.execute("SELECT subscription_id FROM payments WHERE comment LIKE '%778%'").fetchone()[0]
    check(st == "paid" and sp == inv["id"] and r2["url"], "Оплатили по ссылке: абонемент сам стал оплаченным")
    print("ok")


if __name__ == "__main__":
    main()
