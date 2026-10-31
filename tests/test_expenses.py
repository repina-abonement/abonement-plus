"""Проверки платежей и рекламы в пабликах. Запуск: python -m tests.test_expenses"""
import os
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import db, expenses, services as sv  # noqa: E402
from tests.test_crm import check  # noqa: E402

ADM = {"id": 1, "name": "Юлия", "role": "admin"}


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "e.sqlite3"))
    db.init_db(con)
    con.execute("DELETE FROM bills")  # разовое напоминание об ОФД здесь не нужно
    t = date(2026, 10, 27)
    print("Мои платежи")
    rent = expenses.save_bill(con, {"title": "Аренда Небо", "category": "rent", "amount": 60000, "next_due": "2026-10-29", "remind_days": 3})
    expenses.save_bill(con, {"title": "Сервер", "category": "service", "amount": 1200, "next_due": "2026-10-25"})
    expenses.save_bill(con, {"title": "Домен", "category": "service", "amount": 300, "period": "yearly", "next_due": "2027-03-01"})
    due = {b["title"]: b["level"] for b in expenses.bills_due(con, t)}
    check(due == {"Аренда Небо": "yellow", "Сервер": "red"}, "Аренда через 2 дня жёлтая, просроченный сервер красный, домен не мешает")
    nxt = expenses.pay_bill(con, rent, ADM)
    check(nxt == "2026-11-29", "Оплатили аренду: следующая 29 ноября")
    jan = expenses.save_bill(con, {"title": "Тест 31", "amount": 1, "next_due": "2027-01-31"})
    check(expenses.pay_bill(con, jan, ADM) == "2027-02-28", "31 января + месяц = 28 февраля")
    check(expenses.pay_bill(con, jan, ADM) == "2027-03-31", "…а потом снова 31 марта, дата не съезжает")
    v = expenses.bills_view(con, t)
    check(v["per_month"]["Аренда"] == 60000 and v["per_month"]["Сервисы и подписки"] == 1200 + 25, "Итог в месяц по категориям, годовой делится на 12")

    print("Паблики")
    pid = expenses.save_place(con, {"name": "Солнцево Онлайн", "contact": "@admin", "price": 3000})
    did = expenses.add_deal(con, {"place_id": pid, "paid_at": "2026-10-20", "amount": 6000, "posts_total": 2,
                                   "valid_until": "2026-10-31", "planned": ["2026-10-25"]})
    al = expenses.ads_alerts(con, t)
    kinds = sorted(a["kind"] for a in al)
    check("post" in kinds and "expire" in kinds, "Пост 25.10 не вышел (красный) и оплата сгорает 31.10")
    post = con.execute("SELECT id FROM ad_posts WHERE deal_id=? ORDER BY planned LIMIT 1", (did,)).fetchone()[0]
    expenses.mark_post(con, post, "https://vk.com/wall1")
    src = con.execute("SELECT source_id FROM ad_places WHERE id=?", (pid,)).fetchone()[0]
    sv.create_student(con, {"name": "Миша", "parent_name": "Анна", "phone": "+7 900 000-00-01", "funnel": "sales", "source_id": src}, ADM)
    p = expenses.ads_view(con, t)["places"][0]
    check(p["leads"] == 1 and p["per_lead"] == 6000 and p["deals"][0]["posted"] == 1, "Паблик: 1 заявка, заявка стоила 6000 ₽, 1 пост вышел")
    check(t + timedelta(days=0) == t, "ok")


if __name__ == "__main__":
    main()
    print("\nВсе проверки расходов пройдены")


def _upcoming():
    import tempfile as _t
    from datetime import date as _d
    from crm import db as _db, expenses as _e
    con = _db.connect(os.path.join(_t.mkdtemp(), "u.sqlite3")); _db.init_db(con); con.execute("DELETE FROM bills")
    _e.save_bill(con, {"title": "Сервер CRM", "category": "crm", "amount": 990, "next_due": "2026-11-20", "remind_days": 5})
    _e.save_bill(con, {"title": "Домен", "category": "crm", "amount": 300, "period": "yearly", "next_due": "2027-10-01"})
    up = _e.bills_upcoming(con, _d(2026, 10, 28))
    assert [b["title"] for b in up] == ["Сервер CRM"] and up[0]["level"] == "" and up[0]["category_label"] == "Для работы CRM"
    print("✓ на столе платежи на 30 дней вперёд, дальние не мешают")


_upcoming()
