"""Пока родитель присылает данные, место на пробном держим 10 минут. Запуск: python -m tests.test_trial_hold"""
import os, sys, tempfile
from datetime import date, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, schedule, capacity, services as sv
from crm import smart; smart.REQUIRE = False
from crm.services import ApiError
from tests.test_crm import check

con = db.connect(os.path.join(tempfile.mkdtemp(), "h.sqlite3")); db.init_db(con)
U = {"id": 1, "name": "Оля", "role": "operator"}
b = schedule.save_branch(con, {"name": "ТЦ «Небо»"})
con.execute("UPDATE branches SET lesson_limit=2 WHERE id=?", (b,))
wd = (date.today() + timedelta(days=2)).weekday()
g = schedule.save_group(con, {"name": "Небо 17:45 4–5", "branch_id": b, "weekdays": [wd], "time": "17:45", "age": "4–5"})
day = (date.today() + timedelta(days=2)).isoformat()
sv.create_student(con, {"name": "Ученик Один", "parent_name": "Мама", "phone": "+7 900 000-00-01", "funnel": "members", "group_id": g}, U)
a = sv.create_student(con, {"name": "Пробный А", "parent_name": "Папа", "phone": "+7 900 000-11-11", "funnel": "sales"}, U)
bb = sv.create_student(con, {"name": "Пробный Б", "parent_name": "Папа", "phone": "+7 900 000-11-12", "funnel": "sales"}, U)

h = capacity.hold(con, a, g, day, "17:45", U)
check(h["minutes"] == 10, "Место держим 10 минут")
check(capacity.occupancy(con, g, day)["free"] == 0, "Удержанное место занято для остальных")
con.execute("SAVEPOINT t")
try:
    sv.update_student(con, bb, {"group_id": g, "trial_at": day + "T17:45"}, U); ok = False
except ApiError:
    ok = True
con.execute("ROLLBACK TO t"); con.execute("RELEASE t")  # на сервере неудачная запись откатывается так же
check(ok, "Другого ребёнка на удержанное место не записать")
sv.update_student(con, a, {"group_id": g, "trial_at": day + "T17:45"}, U)
check(not capacity.active_holds(con, student_id=a), "Записали того, для кого держали: удержание снято")
check(capacity.occupancy(con, g, day)["count"] == 2, "Записанный считается один раз")
# прошло 10 минут: место свободно
c2 = sv.create_student(con, {"name": "Пробный В", "parent_name": "Папа", "phone": "+7 900 000-11-13", "funnel": "sales"}, U)
con.execute("UPDATE students SET trial_at=NULL WHERE id=?", (a,))
capacity.hold(con, c2, g, day, "17:45", U)
con.execute("UPDATE trial_holds SET until='2000-01-01T00:00:00'")
check(capacity.occupancy(con, g, day)["free"] == 1, "Через 10 минут место снова свободно")
print("ok")
