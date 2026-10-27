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
assert calldigest.tick(con) == 0
# parse_json терпит обёртку ```json
assert ai.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
fakegen.remove(con)
assert con.execute("select count(*) from calls").fetchone()[0] == 0
print("ok")
