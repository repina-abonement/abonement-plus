"""Почему не купили после пробного. Запуск: python -m tests.test_refusal"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, services as sv

ADM = {"id": 1, "name": "Юлия", "role": "admin"}
con = db.connect(os.path.join(tempfile.mkdtemp(), "r.sqlite3")); db.init_db(con)
mk = lambda n, p: sv.create_student(con, {"name": n, "parent_name": "Мама", "phone": p, "funnel": "sales"}, ADM)
a, b, c, d = mk("А", "+7 900 000-00-01"), mk("Б", "+7 900 000-00-02"), mk("В", "+7 900 000-00-03"), mk("Г", "+7 900 000-00-04")
try:
    sv.move_card(con, a, "sales", "refused", ADM)
    raise AssertionError("без причины нельзя")
except sv.ApiError:
    pass
try:
    sv.move_card(con, a, "sales", "refused", ADM, "Другое", "")
    raise AssertionError("«Другое» без комментария нельзя")
except sv.ApiError:
    pass
r = sv.move_card(con, a, "sales", "refused", ADM, "Дорого", "")
assert r["stage"] == "refused" and sv.get_student_row(con, a)["refusal_reason"] == "Дорого"
r = sv.move_card(con, b, "sales", "refused", ADM, "Думают, посоветуются", "с папой")
assert r["stage"] == "how_was"
assert con.execute("SELECT COUNT(*) FROM tasks WHERE student_id=? AND done_at IS NULL", (b,)).fetchone()[0] == 1
r = sv.move_card(con, c, "sales", "refused", ADM, "Заболели или уезжают", "")
assert r["stage"] == "later" and sv.get_student_row(con, c)["stage"] == "later"
print("✓ отказ только с причиной; «думают» - задача через 2 дня; «заболели» - пригласим позже")
print("ok")
