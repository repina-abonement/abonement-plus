"""CRM запускается, когда у ребёнка в одном месяце абонемент и разовое занятие (01.11.2026 из-за этого не запускалась).
Запуск: python -m tests.test_startup_single"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, money, schedule, services as sv, logic
from crm import smart; smart.REQUIRE = False
from tests.test_crm import check
from datetime import date

p = os.path.join(tempfile.mkdtemp(), "s.sqlite3")
con = db.connect(p); db.init_db(con)
U = {"id": 1, "name": "Оля", "role": "admin"}
b = schedule.save_branch(con, {"name": "ТЦ «Небо»"})
g = schedule.save_group(con, {"name": "Небо", "branch_id": b, "weekdays": [0, 3], "time": "17:00", "age": "4–5"})
sid = sv.create_student(con, {"name": "Миша Иванов", "parent_name": "Мама", "phone": "+7 900 000-00-01", "funnel": "members", "group_id": g}, U)
ym = logic.month_of(date.today())
sv.create_invoice(con, sid, ym, U)
con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('price_single', '1500')")
money.sell_single(con, sid, U)
con.close()
for i in range(2):
    con = db.connect(p); db.init_db(con)
    check(True, f"Запуск {i + 1}: CRM стартует с абонементом и разовым занятием в одном месяце")
    con.close()
con = db.connect(p)
check(con.execute("SELECT COUNT(*) FROM subscriptions WHERE kind='single' AND enrollment_id IS NULL").fetchone()[0] == 1,
      "Разовое занятие осталось без группы")
print("ok")
