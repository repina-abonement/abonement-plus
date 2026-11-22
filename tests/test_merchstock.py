"""Пересчёт мерча: остатки, норма, заказ и задача руководителю. Запуск: python -m tests.test_merchstock"""
import os
import sys
import tempfile
from datetime import date, timedelta

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
    legacy = [con.execute("""INSERT INTO tasks(kind,title,assignee_id,due_at,source,note,created_at)
        VALUES('plan',?,?,'2026-11-20T23:59','manual',?,'2026-11-20T10:00')""",
        (f"Заказать мерч: старый снимок {n}", owner, f"Пересчёт 20.11 (Варя). Снимок {n}")).lastrowid for n in (1, 2)]
    partial = merch.stock_count(con, [{"id": a, "stock": 5, "norm": 15}, {"id": b, "stock": "", "norm": 6}], VARYA, True)
    first_task = partial["task"]
    check(first_task == legacy[-1] and con.execute("SELECT status FROM tasks WHERE id=?", (legacy[0],)).fetchone()[0] == "cancelled",
          "Старые задачи-снимки объединяются в одну актуальную")
    snapshot = merch.stock_count_values(con, date.today().isoformat())["values"]
    check(str(a) in snapshot and str(b) not in snapshot and snapshot[str(a)]["stock"] == 5,
          "Частичный пересчёт сохраняет заполненную строку, пустую оставляет непересчитанной")
    check(partial["stock_status"]["weekly_due"] and partial["stock_status"]["counted_items"] == 1,
          "Частичный пересчёт не закрывает недельную проверку всего склада")
    first = [{"id": x["id"], "stock": x["norm"], "norm": x["norm"]} for x in inventory]
    next(x for x in first if x["id"] == a).update(stock=5, norm=15)
    next(x for x in first if x["id"] == b).update(stock=8, norm=6)
    r = merch.stock_count(con, first, VARYA, True)
    check(r["order"] == [{"name": name[a], "qty": 10, "stock": 5, "norm": 15}], f"{name[a]}: есть 5 из 15, заказать 10; второго хватает")
    check(not r["stock_status"]["weekly_due"] and r["stock_status"]["shortages"][0]["id"] == a,
          "Недельный пересчёт закрыт, дефицит остаётся предупреждением руководителю")
    t = con.execute("SELECT title, assignee_id FROM tasks WHERE id=?", (r["task"],)).fetchone()
    check(t["assignee_id"] == owner and f"{name[a]} 10" in t["title"], "Задача руководителю в Планере: что заказать")
    check(r["task"] == first_task and con.execute("SELECT COUNT(*) FROM tasks WHERE auto_key=? AND status='open'", (merch.ORDER_TASK_KEY,)).fetchone()[0] == 1,
          "Повторный пересчёт обновляет одну задачу, а не создаёт дубли")
    task_state = con.execute("SELECT due_at,priority FROM tasks WHERE id=?", (first_task,)).fetchone()
    check(task_state["due_at"][:10] == date.today().isoformat() and task_state["priority"] == 1,
          "Актуальный заказ стоит в «Сегодня» и отмечен красным приоритетом")
    con.execute("UPDATE tasks SET due_at=? WHERE id=?", ((date.today()-timedelta(days=1)).isoformat()+"T23:59", first_task))
    merch.keep_order_task_today(con)
    check(con.execute("SELECT due_at FROM tasks WHERE id=?", (first_task,)).fetchone()[0][:10] == date.today().isoformat(),
          "Открытый заказ не уходит в верхнюю просрочку, а переносится в «Сегодня»")
    second = [{"id": x["id"], "stock": 15 if x["id"] == a else x["norm"], "norm": 1 if x["id"] == a else x["norm"]} for x in merch.items(con, inventory=True)]
    r = merch.stock_count(con, second, {"id": olga, "name": "Ольга", "role": "operator"}, True)
    check(not r["order"] and con.execute("SELECT norm FROM merch_items WHERE id=?", (a,)).fetchone()[0] == 1,
          "Администратор меняет норму; при достаточном остатке задача не создаётся")
    check(r["task"] is None and con.execute("SELECT status FROM tasks WHERE id=?", (first_task,)).fetchone()[0] == "cancelled",
          "Когда дефицита нет, актуальная задача заказа закрывается")
    OFFLINE = {"id": olga, "name": "Виктория", "role": "operator", "is_offline_admin": 1}
    offline_section = merch.planner_task_section(
        con, {"title": "Учёт мерча", "note": "", "recur_id": 7, "due_at": "2026-11-20T12:00"},
        {"weekdays": [4]}, OFFLINE)
    check(offline_section["can_norm"], "Офлайн-администратор видит редактируемую норму в карточке задачи")
    before = tuple(con.execute("SELECT stock,norm,stock_at FROM merch_items WHERE id=?", (a,)).fetchone())
    old_day = (date.today() - timedelta(days=34)).isoformat()
    historical = [{"id": x["id"], "stock": 2, "norm": 99} for x in merch.items(con, inventory=True)]
    r = merch.stock_count(con, historical, VARYA, True, old_day)
    check(r["historical"] and r["counted_on"] == old_day and r["task"] is None,
          "Старый пересчёт сохраняется выбранной датой и не создаёт сегодняшнюю задачу")
    check(tuple(con.execute("SELECT stock,norm,stock_at FROM merch_items WHERE id=?", (a,)).fetchone()) == before,
          "Исторический пересчёт не заменяет более свежий остаток и текущую норму")
    check(con.execute("SELECT count(*) FROM merch_stock_count_lines WHERE count_id=(SELECT max(id) FROM merch_stock_counts)").fetchone()[0] == len(inventory),
          "Полный исторический список хранится отдельным снимком")
    extra = merch.add_stock_item(con, {"name": "Пакеты", "unit": "шт."})
    check(tuple(con.execute("SELECT norm,stock_only FROM merch_items WHERE id=?", (extra,)).fetchone()) == (0, 1),
          "Администратор может добавить дополнительную складскую позицию без минимума и цены")
    pennant_before = con.execute("SELECT id,price FROM merch_items WHERE name='Вымпел'").fetchone()
    pennant = merch.add_stock_item(con, {"name": "Вымпел", "unit": "шт."})
    pennant_after = con.execute("SELECT id,price,active,stock_only FROM merch_items WHERE id=?", (pennant,)).fetchone()
    check(tuple(pennant_after) == (pennant_before["id"], 1200, 1, 1),
          "Товар из каталога подключается к складу без дубля и без потери цены")
    try:
        merch.add_stock_item(con, {"name": "  вымпел  ", "unit": "шт."})
        duplicate = False
    except merch.MerchError as exc:
        duplicate = "складском списке" in str(exc)
    check(duplicate, "Повтор с другим регистром не создаёт вторую складскую позицию")
    print("ok")


if __name__ == "__main__":
    main()
