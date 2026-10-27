"""Проверки оферт. Запуск: python -m tests.test_offers"""
import base64
import os
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import db, legal, offers, schedule  # noqa: E402
from tests.test_crm import check  # noqa: E402

ADM = {"id": 1, "name": "Юлия", "role": "admin"}
PDF = "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4 test").decode()


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "o.sqlite3"))
    db.init_db(con)
    print("Оферты")
    b = schedule.save_branch(con, {"name": "Арктика"})
    g = schedule.save_group(con, {"name": "Арктика Сборная", "branch_id": b, "weekdays": [1, 6]})
    t = date.today()
    try:
        offers.upload(con, {"program": "ПРО", "edition_date": t.isoformat(), "file_base64": base64.b64encode(b"hello").decode()}, ADM)
        check(False, "Не PDF не принимаем")
    except offers.OfferError:
        check(True, "Не PDF не принимаем")
    old = offers.upload(con, {"program": "Футболика ПРО (сборная)", "edition_date": (t - timedelta(days=60)).isoformat(),
                              "file_base64": PDF, "group_ids": [g]}, ADM)
    new = offers.upload(con, {"program": "Футболика ПРО (сборная)", "edition_date": t.isoformat(),
                              "effective_from": (t + timedelta(days=5)).isoformat(), "file_base64": PDF, "group_ids": [g]}, ADM)
    slug = con.execute("SELECT slug FROM offers WHERE id=?", (new,)).fetchone()[0]
    check(slug == "futbolika-pro-sbornaya", "Постоянная ссылка: /offers/futbolika-pro-sbornaya.pdf")
    check(offers.current_id(con, slug) == old, "Пока новая не вступила в силу, по ссылке старая")
    check(offers.current_id(con, slug, (t + timedelta(days=5)).isoformat()) == new, "С даты вступления по ссылке новая")
    states = {v["id"]: v["state"] for v in offers.listing(con)[0]["versions"]}
    check(states == {old: "current", new: "upcoming"}, "В списке: старая действует, новая вступит в силу")
    check(offers.for_group(con, g)["id"] == old, "Для группы сборной действует её оферта")
    check("Договор-оферта" in legal.page(con, "", ""), "Оферта есть в списке публичных документов")


if __name__ == "__main__":
    main()
    print("\nВсе проверки оферт пройдены")
