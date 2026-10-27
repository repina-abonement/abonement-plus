"""Лимит 10 детей на тренировку в Небе и «предупредил, что не придёт». Запуск: python -m tests.test_capacity"""
import os, sys, tempfile
from datetime import date, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, schedule, capacity, services as sv
from crm.services import ApiError

con = db.connect(os.path.join(tempfile.mkdtemp(), "c.sqlite3")); db.init_db(con)
def fails(fn, why):
    con.execute("SAVEPOINT t")
    try:
        fn()
    except ApiError as e:
        con.execute("ROLLBACK TO t"); con.execute("RELEASE t")
        return e.message
    raise SystemExit(why)


U = {"id": 1, "name": "Оля", "role": "operator"}
b = schedule.save_branch(con, {"name": "ТЦ «Небо»"})
con.execute("UPDATE branches SET lesson_limit=10 WHERE id=?", (b,))
wd = (date.today() + timedelta(days=2)).weekday()
g = schedule.save_group(con, {"name": "Небо 17:45 4–5", "branch_id": b, "weekdays": [wd], "time": "17:45", "age": "4–5"})
g2 = schedule.save_group(con, {"name": "Небо 18:45 5–6", "branch_id": b, "weekdays": [wd], "time": "18:45", "age": "5–6"})
day = (date.today() + timedelta(days=2)).isoformat()
kids = [sv.create_student(con, {"name": f"Ребёнок {i}", "parent_name": "Мама", "phone": f"+7 900 000-00-{i:02d}", "funnel": "members", "group_id": g}, U)
        for i in range(10)]
o = capacity.occupancy(con, g, day)
assert o["count"] == 10 and o["free"] == 0, o
m = fails(lambda: sv.create_student(con, {"name": "Лишний", "parent_name": "Мама", "phone": "+7 900 000-00-99", "funnel": "members", "group_id": g}, U), "11-й ученик не должен записаться")
assert "10 из 10" in m, m
lead = sv.create_student(con, {"name": "Пробный", "parent_name": "Папа", "phone": "+7 900 000-11-11", "funnel": "sales"}, U)
fails(lambda: sv.update_student(con, lead, {"group_id": g, "trial_at": day + "T17:45"}, U), "пробное сверх лимита не должно записаться")
# ребёнок предупредил — место освободилось
r = capacity.warn(con, kids[0], day, None, "заболел", U)
assert r["occupancy"]["free"] == 1, r
fam = con.execute("SELECT family_id FROM students WHERE id=?", (kids[0],)).fetchone()[0]
fl = capacity.family_lessons(con, fam)
assert fl[0]["lessons"] and any(x["warned"] for x in fl[0]["lessons"]), fl
sv.update_student(con, lead, {"group_id": g, "trial_at": day + "T17:45"}, U)
assert capacity.occupancy(con, g, day)["free"] == 0
# отработка в обычную группу: ребёнок из другой группы с пропуском
other = sv.create_student(con, {"name": "Из другой", "parent_name": "Мама", "phone": "+7 900 000-22-22", "funnel": "members", "group_id": g2}, U)
past = date.today() - timedelta(days=1)
con.execute("INSERT INTO attendance(student_id, group_id, date, status) VALUES (?,?,?,'absent')", (other, g2, past.isoformat()))
fails(lambda: sv.book_makeup(con, other, g, day, None, U), "мест нет — отработка не должна записаться")
capacity.warn(con, kids[1], day, g, "", U)
sv.book_makeup(con, other, g, day, None, U)
rows = sv.journal(con, g, day)["rows"]
assert any(x.get("status") == "makeup" for x in rows) and sum(1 for x in rows if x.get("warned")) == 2
slots = capacity.free_slots(con, 7)
assert all(s["free"] > 0 for s in slots) and not any(s["group_id"] == g and s["date"] == day for s in slots)
# отменить предупреждение нельзя, если место уже заняли
fails(lambda: capacity.unwarn(con, kids[1], day, g, U), "нельзя отменить: место занято")
print("ok")
