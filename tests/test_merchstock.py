"""Пересчёт мерча: остатки, норма, заказ и задача руководителю. Запуск: python -m tests.test_merchstock"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import db, merch  # noqa: E402
from tests.test_crm import check  # noqa: E402


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "m.sqlite3"))
    db.init_db(con)
    owner = con.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
    olga = con.execute("INSERT INTO users(login,name,role,pw_hash) VALUES ('o','Ольга','operator','x')").lastrowid
    inventory = merch.items(con, inventory=True)
    assert len(inventory) == 19
    minimums = {x["name"]: x["norm"] for x in inventory}
    assert minimums["Дневник"] == 50 and minimums["Наклейки круглые"] == 10 and minimums["Форма (размер XL)"] == 5
    a, b = [r["id"] for r in inventory[:2]]
    name = dict(con.execute("SELECT id, name FROM merch_items").fetchall())
    VARYA = {"id": owner, "name": "Варя", "role": "operator", "is_senior": 1}
    section = merch.planner_task_section(
        con, {"title": "Учёт мерча", "note": "", "recur_id": 7, "due_at": "2026-11-20T12:00"},
        {"weekdays": [4]}, VARYA)
    check(section["schedule"] == "пятница" and len(section["stock_items"]) == 19,
          "Пересчёт встроен в существующую пятничную задачу по мерчу")
    check(merch.planner_task_section(con, {"title": "Другая задача", "note": "", "recur_id": 8},
                                     {"weekdays": [4]}, VARYA) is None,
          "В прочих карточках Планера раздел мерча не появляется")
    first = [{"id": x["id"], "stock": x["norm"], "norm": x["norm"]} for x in inventory]
    next(x for x in first if x["id"] == a).update(stock=5, norm=15)
    next(x for x in first if x["id"] == b).update(stock=8, norm=6)
    r = merch.stock_count(con, first, VARYA, True)
    check(r["order"] == [{"name": name[a], "qty": 10, "stock": 5, "norm": 15}], f"{name[a]}: есть 5 из 15, заказать 10; второго хватает")
    check(not r["stock_status"]["weekly_due"] and r["stock_status"]["shortages"][0]["id"] == a,
          "Недельный пересчёт закрыт, дефицит остаётся предупреждением руководителю")
    t = con.execute("SELECT title, assignee_id FROM tasks WHERE id=?", (r["task"],)).fetchone()
    check(t["assignee_id"] == owner and f"{name[a]} 10" in t["title"], "Задача руководителю в Планере: что заказать")
    second = [{"id": x["id"], "stock": 15 if x["id"] == a else x["norm"], "norm": 1 if x["id"] == a else x["norm"]} for x in merch.items(con, inventory=True)]
    r = merch.stock_count(con, second, {"id": olga, "name": "Ольга", "role": "operator"}, False)
    check(not r["order"] and con.execute("SELECT norm FROM merch_items WHERE id=?", (a,)).fetchone()[0] == 15,
          "Администратор вписывает остатки, но норму не меняет; всего хватает - задачи нет")
    extra = merch.add_stock_item(con, {"name": "Пакеты", "unit": "шт."})
    check(tuple(con.execute("SELECT norm,stock_only FROM merch_items WHERE id=?", (extra,)).fetchone()) == (0, 1),
          "Администратор может добавить дополнительную складскую позицию без минимума и цены")
    print("ok")


if __name__ == "__main__":
    main()
