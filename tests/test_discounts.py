"""Скидки: второй ребёнок 20%, многодетные 10%, не суммируются. Запуск: python -m tests.test_discounts"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, schedule, services as sv

con = db.connect(os.path.join(tempfile.mkdtemp(), "t.sqlite3")); db.init_db(con)
b = schedule.save_branch(con, {"name": "Небо"})
g = schedule.save_group(con, {"name": "Г", "branch_id": b, "weekdays": [1, 4], "time": "17:45", "age": "4–5"})
u = {"id": 1, "name": "Ю", "role": "admin"}
s1 = sv.create_student(con, {"name": "Миша", "parent_name": "Анна", "phone": "+7 900 111-22-33", "funnel": "members", "group_id": g}, u)
fam = con.execute("SELECT family_id FROM students WHERE id=?", (s1,)).fetchone()[0]
s2 = sv.create_student(con, {"name": "Катя", "family_id": fam, "funnel": "members", "group_id": g}, u)
assert sv.quote_for(con, s1, "2026-12")["discount"] is None, "первому ребёнку скидки нет"
assert sv.quote_for(con, s2, "2026-12")["discount"]["percent"] == 20, "второму 20%"
many = con.execute("SELECT id FROM discounts WHERE name LIKE 'Многодет%'").fetchone()[0]
assert sv.quote_for(con, s2, "2026-12", None, many)["discount"]["percent"] == 20, "не суммируются, берём большую"
assert sv.quote_for(con, s1, "2026-12", None, many)["discount"]["percent"] == 10, "многодетным 10%"
assert con.execute("SELECT value FROM settings WHERE key='price_single'").fetchone()[0] == "2500"
print("ok")
