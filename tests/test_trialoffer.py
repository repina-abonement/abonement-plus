"""Предложение ближайших ПУ по возрасту. Запуск: python -m tests.test_trialoffer"""
import os, sys, tempfile
from datetime import date, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, schedule, trialoffer
from crm import smart; smart.REQUIRE = False  # обязательные данные проверяет tests/test_required.py

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
# группа, куда пробные не пишем (например, Небо Пн/Чт 17:45), не предлагается и не даёт записать
con.execute("UPDATE groups SET trial_ok=0 WHERE id=?", (g45,))
r = trialoffer.offer(con, 4)
assert {x["group_id"] for x in r["slots"]} == {g34}, r["slots"]
from crm import services as sv, capacity
fid = con.execute("INSERT INTO families(name) VALUES ('Т')").lastrowid
sid = con.execute("INSERT INTO students(family_id, name, stage_changed_at, created_at) VALUES (?, 'Лид', '2026-01-01', '2026-01-01')", (fid,)).lastrowid
try:
    sv.update_student(con, sid, {"group_id": g45, "trial_at": (date.today() + timedelta(days=2)).isoformat() + "T17:45"}, {"name": "т"})
    raise AssertionError("должны были запретить")
except capacity.CapacityError:
    pass
print("✓ в группы без отметки «можно на пробное» не записываем")
b2 = schedule.save_branch(con, {"name": "СК «Западный» (Чоботовская)", "address": ""})
assert capacity.match_branch(con, "ТРЦ Небо") == b and capacity.match_branch(con, "Чоботовская ул") == b2
assert capacity.match_branch(con, "Москва") is None
print("✓ филиал из заявки с сайта")
from crm import logic, money
assert logic.apply_late_fee(1199, enabled=True, percent=5, today=date(2026, 11, 26), target_month="2026-12")[0] == 1259  # 1258,95 → 1259
assert money.apply_discount(1555, {"percent": 10})[0] == 1400  # 1399,5 → 1400
print("✓ копейки округляются вверх")
print("ok")
