"""Проверки аналитики. Запуск: python -m tests.test_analytics"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, schedule, fakegen, analytics

con = db.connect(os.path.join(tempfile.mkdtemp(), "a.sqlite3")); db.init_db(con)
b = schedule.save_branch(con, {"name": "Небо"})
for wd, t, age in (([0, 3], "16:45", "3–4"), ([1, 4], "17:45", "4–5"), ([5, 6], "10:00", "5–6")):
    schedule.save_group(con, {"name": f"Небо {t} {age}", "branch_id": b, "weekdays": wd, "time": t, "age": age})
fakegen.realistic(con, {"id": 1, "name": "Юлия", "role": "admin"}, start_members=25, months_back=13)
g = analytics.report(con, None, None, "groups")
assert len(g["groups"]) == 3 and g["branches"][0]["kids"] == sum(x["kids"] for x in g["groups"])
assert all(x["kids"] <= x["capacity"] for x in g["groups"])
c = analytics.report(con, None, None, "churn")
assert c["left_count"] == len(c["left"]) and len(c["series"]) == 12 and c["avg_months"]
r = c["renewal"]
assert r["paid"] + r["invoiced"] + r["none"] == g["branches"][0]["kids"]
s = analytics.report(con, None, None, "sources")
assert s["total"]["leads"] >= s["total"]["bought"] and s["total"]["spend"] > 0
assert analytics.report(con, None, b, "coaches") is not None
print("ok")
