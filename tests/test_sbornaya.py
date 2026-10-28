"""Сборная ПРО: расчёт абонемента по правилам 28.10.2026. Запуск: python -m tests.test_sbornaya"""
import os, sys, tempfile
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, schedule, program, money, services as sv, closures
from crm.services import ApiError

con = db.connect(os.path.join(tempfile.mkdtemp(), "s.sqlite3")); db.init_db(con)
U = {"id": 1, "name": "Юлия", "role": "admin"}
arc = schedule.save_branch(con, {"name": "Арктика"})
neb = schedule.save_branch(con, {"name": "ТЦ «Небо»"})
pid = program.ensure_sbornaya(con)
A = schedule.save_group(con, {"name": "Сборная Арктика Вт/Вс", "branch_id": arc, "weekdays": [1, 6], "time": "18:30", "age": "8+", "day_times": {"6": "19:30"}})
T = schedule.save_group(con, {"name": "Сборная ОФП Чт 20:30", "branch_id": neb, "weekdays": [3], "time": "20:30", "age": "8+"})
S = schedule.save_group(con, {"name": "Сборная ОФП Сб 17:00", "branch_id": neb, "weekdays": [5], "time": "17:00", "age": "8+"})
con.execute("UPDATE groups SET program_id=?, lesson_kind='arena' WHERE id=?", (pid, A))
con.execute("UPDATE groups SET program_id=?, lesson_kind='ofp' WHERE id IN (?,?)", (pid, T, S))


def kid(name, phone, fam=None):
    d = {"name": name, "funnel": "members"}
    d.update({"family_id": fam} if fam else {"parent_name": "Мама", "phone": phone})
    return sv.create_student(con, d, U)


def q(sid, ym, on):
    e = next(x for x in sv.enrollments_of(con, sid) if x["group_id"] == A)
    return program.quote(con, sid, ym, e, on=on)


def paid_before(sid):  # уже занимался: есть оплаченный октябрь
    e = next(x for x in sv.enrollments_of(con, sid) if x["group_id"] == A)
    con.execute("""INSERT INTO subscriptions(student_id, enrollment_id, month, tariff, lessons, per_lesson, price, status, created_at)
                   VALUES (?,?,'2026-10','pkg',8,1500,12000,'paid','2026-09-20')""", (sid, e["id"]))


res = []
# 1. декабрь, Арктика + ОФП чт, оплата 20 ноября
k1 = kid("Тест Первый", "+7 900 100-00-01"); program.set_plan(con, k1, pid, "pkg", T, U); paid_before(k1)
r = q(k1, "2026-12", date(2026, 11, 20)); res.append(("Декабрь, Арктика+ОФП чт, оплата 20.11", r["total"], 19200, r["program"]["lines"]))
# 2. декабрь, только Арктика, оплата 27 ноября; второй ребёнок в семье — скидки всё равно нет
fam = con.execute("SELECT family_id FROM students WHERE id=?", (k1,)).fetchone()[0]
k2 = kid("Тест Второй", None, fam); program.set_plan(con, k2, pid, "arena_only", None, U); paid_before(k2)
r = q(k2, "2026-12", date(2026, 11, 27)); res.append(("Декабрь, только Арктика, оплата 27.11 (второй ребёнок, скидка недоступна)", r["total"], 17010, r["program"]["note"]))
assert r["discount"] is None
# 3. декабрь, второй ребёнок, Арктика + ОФП, оплата до 25 ноября
k3 = kid("Тест Третий", None, fam); program.set_plan(con, k3, pid, "pkg", T, U); paid_before(k3)
r = q(k3, "2026-12", date(2026, 11, 24)); res.append(("Декабрь, второй ребёнок, Арктика+ОФП, оплата 24.11", r["total"], 15360, r["discount"]))
# 4. новичок с 15 ноября, Арктика + ОФП сб
k4 = kid("Тест Новичок", "+7 900 100-00-04"); program.set_plan(con, k4, pid, "pkg", S, U)
r = q(k4, "2026-11", date(2026, 11, 15)); res.append(("Новичок с 15.11, Арктика+ОФП сб, ноябрьские цены", r["total"], 9900, r["dates"]))
# 5. январь: 2 и 3 января не в расчёте
r = q(k1, "2027-01", date(2026, 12, 20)); bad = [d for d in r["dates"] if d in ("2027-01-02", "2027-01-03")]
res.append(("Январь, Арктика+ОФП чт: 2 и 3 января не в расчёте", r["total"], 8 * 1600 + 4 * 1200, bad))
assert not bad
r = q(k4, "2027-01", date(2026, 12, 20)); assert "2027-01-02" not in r["dates"] and "2027-01-03" not in r["dates"]
for name, got, want, extra in res:
    print(("✓" if got == want else "✗"), name, "→", got, "(ожидали", str(want) + ")")
    assert got == want, (name, got, want, extra)
# смена тарифа у того, кто уже платил, — только со следующего месяца
r = program.set_plan(con, k1, pid, "arena_only", None, U); assert r["applied"] == "next"
assert program.plan_of(con, k1, pid)["tariff"] == "pkg"
# пересчёт при оплате после срока: счёт выставлен до 25, оплата позже
e = next(x for x in sv.enrollments_of(con, k3) if x["group_id"] == A)
inv = sv.create_invoice(con, k3, "2027-02", U, e["id"])
before = con.execute("SELECT price, discount_amount FROM subscriptions WHERE id=?", (inv["id"],)).fetchone()
assert before[1] > 0, "до срока скидка второму ребёнку есть"
program.reprice(con, inv["id"], on=date(2027, 1, 27))
after = con.execute("SELECT price, discount_amount, late_fee FROM subscriptions WHERE id=?", (inv["id"],)).fetchone()
assert after[1] == 0 and after[2] > 0 and after[0] > before[0], (before, tuple(after))
print("✓ счёт, оплаченный после 25 числа, пересчитан: +5% и без скидки", before[0], "→", after[0])
print("✓ правила: смена тарифа со следующего месяца, разовых нет, в четверг отработок нет")
try:
    money.sell_single(con, k1, U); raise SystemExit("разовое не должно продаваться")
except ValueError:
    pass
try:
    program.check_makeup(con, k1, T, "2026-11-05"); raise SystemExit("в четверг отработок нет")
except ApiError:
    pass
try:
    program.check_makeup(con, k2, S, "2026-11-07"); raise SystemExit("только Арктика: ОФП не отработка")
except ApiError:
    pass
print("ok")
