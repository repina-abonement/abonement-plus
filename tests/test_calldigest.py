import os, sys, tempfile, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, fakegen, calldigest, ai, schedule

con = db.connect(os.path.join(tempfile.mkdtemp(), "t.sqlite3")); db.init_db(con)
u = {"id": 1, "name": "Юлия", "role": "admin"}
con.execute("INSERT INTO users(login,name,role,pw_hash) VALUES ('op1','Оператор Тест','operator','x$y')")
b = schedule.save_branch(con, {"name": "Небо"})
schedule.save_group(con, {"name": "Небо 17:45 4–5", "branch_id": b, "weekdays": [1, 4], "time": "17:45", "age": "4–5"})
fakegen.generate(con, 20, u, seed=3)
r = fakegen.fake_calls(con, u, seed=1)
assert r["calls"] == 14, r
lst = calldigest.listing(con)
assert len(lst) == 1 and lst[0]["calls"] == 10 and lst[0]["name"] == "Оператор Тест", lst
d = calldigest.get(con, lst[0]["day"], lst[0]["operator"])
assert len(d["map"]) == 10 and d["focus_today"] and d["booked"] >= 1
f = calldigest.focus_for(con, lst[0]["operator"])
assert f and len(f["focus"]) == 3
# tick: сводка за вчера уже есть с тем же числом звонков, повторно не строится
from datetime import datetime as _dt
assert calldigest.tick(con, _dt.now().replace(hour=12)) == 0
# parse_json терпит обёртку ```json
assert ai.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
fakegen.remove(con)
assert con.execute("select count(*) from calls").fetchone()[0] == 0
print("ok")

# школа с историей
from crm import services as sv
con2 = db.connect(os.path.join(tempfile.mkdtemp(), "r.sqlite3")); db.init_db(con2)
b = schedule.save_branch(con2, {"name": "Небо"})
for wd, t, age in (([0, 3], "16:45", "3–4"), ([1, 4], "17:45", "4–5"), ([5, 6], "10:00", "5–6"), ([2, 6], "18:00", "6–8")):
    schedule.save_group(con2, {"name": f"Небо {t} {age}", "branch_id": b, "weekdays": wd, "time": t, "age": age})
r = fakegen.realistic(con2, u, start_members=30, months_back=13)
assert r["members_now"] > 10 and r["leads"] > 300 and r["left"] > 0, r
dd = sv.dashboard(con2)
assert dd["kpi"]["holders"]["value"] > 10 and dd["funnel_leads"][0][1] >= dd["funnel_leads"][3][1]
assert con2.execute("SELECT COUNT(*) FROM subscriptions s LEFT JOIN payments p ON p.subscription_id=s.id WHERE s.status='paid' AND p.id IS NULL").fetchone()[0] == 0
fakegen.remove(con2)
assert con2.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 0
print("ok realistic")
