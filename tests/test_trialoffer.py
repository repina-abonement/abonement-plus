"""Предложение ближайших ПУ по возрасту. Запуск: python -m tests.test_trialoffer"""
import os, sys, tempfile
from datetime import date, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, schedule, trialoffer

con = db.connect(os.path.join(tempfile.mkdtemp(), "t.sqlite3")); db.init_db(con)
b = schedule.save_branch(con, {"name": "ТЦ «Небо»", "address": "3 этаж"})
wd = (date.today() + timedelta(days=1)).weekday()
g34 = schedule.save_group(con, {"name": "Небо 3–4", "branch_id": b, "weekdays": [wd], "time": "16:45", "age": "3–4"})
g45 = schedule.save_group(con, {"name": "Небо 4–5", "branch_id": b, "weekdays": [(wd + 1) % 7], "time": "17:45", "age": "4–5"})
g56 = schedule.save_group(con, {"name": "Небо 5–6", "branch_id": b, "weekdays": [wd], "time": "18:45", "age": "5–6"})
ofp = schedule.save_group(con, {"name": "ОФП 3–5", "branch_id": b, "weekdays": [wd], "time": "16:00", "age": "3–5"})
r = trialoffer.offer(con, 4)
ids = {s["group_id"] for s in r["slots"]}
assert ids == {g34, g45}, r["slots"]
assert r["slots"][0]["group_id"] == g34, "сначала самая ранняя дата"
assert "📅" in r["text"] and "3 этаж" in r["text"]
print("✓ 4 года → группы 3–4 и 4–5, ближайшие даты первыми, без ОФП")
print(r["text"])
print("ok")
