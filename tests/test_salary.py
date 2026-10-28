"""Зарплата тренеров. Запуск: python -m tests.test_salary"""
import os, sys, tempfile
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, schedule, salary, logic, staff

con = db.connect(os.path.join(tempfile.mkdtemp(), "s.sqlite3")); db.init_db(con)
ym = "2026-09"  # прошедший месяц, все тренировки уже были
b = schedule.save_branch(con, {"name": "ТЦ «Небо»"})
uid = lambda n, l: con.execute("INSERT INTO users(login, name, role, pw_hash) VALUES (?,?,'coach','x')", (l, n)).lastrowid
nik, art, ivan = uid("Никита", "n"), uid("Артём", "a"), uid("Иван", "i")
g = schedule.save_group(con, {"name": "Небо Вт/Пт", "branch_id": b, "weekdays": [1, 4], "time": "16:45", "age": "3–4",
                              "coach_id": nik, "assistant_id": art})
dates = salary._dates(con, con.execute("SELECT * FROM groups WHERE id=?", (g,)).fetchone(), ym)
n = len(dates)  # вторники и пятницы сентября 2026 = 9
fid = con.execute("INSERT INTO families(name) VALUES ('Ф')").lastrowid
for i in range(10):  # 10 детей, у каждого абонемент 1100 ₽ за занятие
    sid = con.execute("INSERT INTO students(family_id, name, group_id, funnel, stage, stage_changed_at, created_at) VALUES (?,?,?,'members','active','2026-08-01','2026-08-01')",
                      (fid, f"Ребёнок {i}", g)).lastrowid
    con.execute("INSERT INTO subscriptions(student_id, month, tariff, lessons, per_lesson, price, status, created_at) VALUES (?,?,?,?,?,?,'paid','2026-08-20')",
                (sid, ym, "fixed", n, 1100, 1100 * n))
# одну тренировку Никита вёл один
staff.set_lesson_staff(con, g, dates[0], nik, None, {"name": "Юлия", "role": "admin"})
r = salary.payroll(con, ym)
p = {x["name"]: x for x in r["coaches"]}
assert p["Никита"]["total"] == round(11000 * 0.20 + (n - 1) * 11000 * 0.155), p["Никита"]
assert p["Артём"]["total"] == round((n - 1) * 11000 * 0.10), p["Артём"]
assert p["Никита"]["planned"] == n - 1
print(f"✓ {n} тренировок по 11 000 ₽: главный 1705, помощник 1100, один 2200")
# отработки: 1500, если кто-то пришёл, иначе 0
m = schedule.save_group(con, {"name": "Отработки Сб", "branch_id": b, "weekdays": [5], "time": "14:00", "is_makeup": True, "coach_id": ivan})
sat = salary._dates(con, con.execute("SELECT * FROM groups WHERE id=?", (m,)).fetchone(), ym)
ab = con.execute("INSERT INTO attendance(student_id, group_id, date, status) VALUES (1, ?, ?, 'absent')", (g, dates[0])).lastrowid
con.execute("INSERT INTO makeups(student_id, absence_id, group_id, date, status) VALUES (1, ?, ?, ?, 'done')", (ab, m, sat[0]))
r = salary.payroll(con, ym)
iv = next(x for x in r["coaches"] if x["name"] == "Иван")
assert iv["total"] == 1500, iv
print("✓ отработки: 1500 ₽ только за субботу, где кто-то пришёл")
# штраф за опоздание вычитается после подтверждения
con.execute("INSERT INTO checkins(user_id, branch_id, date, at, due, late_min, fine, fine_status) VALUES (?,?,?,?,?,?,?,?)",
            (art, b, dates[1], "16:30", "16:15", 15, 500, "confirmed"))
r = salary.payroll(con, ym)
a = next(x for x in r["coaches"] if x["name"] == "Артём")
assert a["to_pay"] == a["total"] - 500
r = salary.payroll(con, ym, coach_id=art)
assert [x["name"] for x in r["coaches"]] == ["Артём"]
print("✓ штраф за опоздание вычитается, тренер видит только себя")
print("ok")
