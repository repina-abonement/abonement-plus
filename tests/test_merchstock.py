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
    a, b = [r[0] for r in con.execute("SELECT id FROM merch_items ORDER BY sort LIMIT 2")]
    name = dict(con.execute("SELECT id, name FROM merch_items").fetchall())
    VARYA = {"id": owner, "name": "Варя", "role": "operator", "is_senior": 1}
    r = merch.stock_count(con, [{"id": a, "stock": 5, "norm": 15}, {"id": b, "stock": 8, "norm": 6}], VARYA, True)
    check(r["order"] == [{"name": name[a], "qty": 10, "stock": 5, "norm": 15}], f"{name[a]}: есть 5 из 15, заказать 10; второго хватает")
    t = con.execute("SELECT title, assignee_id FROM tasks WHERE id=?", (r["task"],)).fetchone()
    check(t["assignee_id"] == owner and f"{name[a]} 10" in t["title"], "Задача руководителю в Планере: что заказать")
    r = merch.stock_count(con, [{"id": a, "stock": 15, "norm": 1}], {"id": olga, "name": "Ольга", "role": "operator"}, False)
    check(not r["order"] and con.execute("SELECT norm FROM merch_items WHERE id=?", (a,)).fetchone()[0] == 15,
          "Администратор вписывает остатки, но норму не меняет; всего хватает - задачи нет")
    print("ok")


if __name__ == "__main__":
    main()
