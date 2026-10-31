"""Этап «Не дошёл до ПУ» и контроль отметок. Запуск: python -m tests.test_pu"""
import os, sys, tempfile
from datetime import date, datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, schedule, pu, tasks, services as sv, planner
from crm import smart; smart.REQUIRE = False  # обязательные данные проверяет tests/test_required.py
from crm.services import ApiError

con = db.connect(os.path.join(tempfile.mkdtemp(), "p.sqlite3")); db.init_db(con)
U = {"id": 1, "name": "Ольга", "role": "operator"}
b = schedule.save_branch(con, {"name": "ТЦ «Небо»"})
SAT = date(2026, 10, 31)  # суббота
g = schedule.save_group(con, {"name": "Небо Сб 10:00 5–6", "branch_id": b, "weekdays": [5], "time": "10:00", "age": "5–6"})
g2 = schedule.save_group(con, {"name": "Небо Сб 11:00 6–8", "branch_id": b, "weekdays": [5], "time": "11:00", "age": "6–8"})
con.execute("UPDATE settings SET value='60' WHERE key='trial_minutes'")


def lead(name, phone=None, fam=None):
    d = {"name": name, "funnel": "sales"}
    d.update({"family_id": fam} if fam else {"parent_name": "Мама", "phone": phone})
    sid = sv.create_student(con, d, U)
    con.execute("UPDATE students SET group_id=?, trial_at=?, stage='trial_booked' WHERE id=?", (g, f"{SAT}T10:00", sid))
    return sid


def stage(sid):
    return con.execute("SELECT stage FROM students WHERE id=?", (sid,)).fetchone()[0]


def task_of(sid):
    fam = con.execute("SELECT family_id FROM students WHERE id=?", (sid,)).fetchone()[0]
    return pu.open_task(con, fam)


ok = lambda m: print("✓", m)
# 1. тренер отметил «не пришёл»
a = lead("Миша Тест", "+7 900 200-00-01")
sv.mark_trial(con, a, False, U)
t = task_of(a)
assert stage(a) == "missed_trial" and t and t["kind"] == "rebook", (stage(a), t)
ok(f"Сб 10:00, «не пришёл» → этап «Не дошёл до ПУ», задача «{t['title']}» до {t['due_at']}")
# 2. отменил заранее в четверг
c = lead("Катя Тест", "+7 900 200-00-02")
pu.cancel_trial(con, c, "Заболела", U, now=datetime(2026, 10, 29, 15, 0))
t = task_of(c)
assert stage(c) == "missed_trial" and t["due_at"] == "2026-10-29T21:00", t["due_at"]
ok(f"Отменил в четверг → этап и задача в тот же день до {t['due_at'][11:]}")
# 3. тренер не поставил отметку 2 часа
d = lead("Петя Тест", "+7 900 200-00-03")
r = pu.tick(con, datetime(2026, 10, 31, 12, 50))
assert stage(d) == "trial_booked", "в 12:50 ещё рано (конец 11:00 + 2 часа = 13:00)"
flags = pu.missing_marks(con, datetime(2026, 10, 31, 11, 30))
assert any(m["group_id"] == g for m in flags)
ok("В 11:30 нет отметок → красный флаг: " + ", ".join(f"{m['group']} без отметки {m['missing']}" for m in flags))
r = pu.tick(con, datetime(2026, 10, 31, 13, 0))
assert stage(d) == "missed_trial" and r["moved"] >= 1 and r["alerts"] >= 1
last = con.execute("SELECT kind FROM trial_misses WHERE student_id=? ORDER BY id DESC", (d,)).fetchone()[0]
assert last == "no_mark"
ok("В 13:00 отметок нет → ребёнок на этапе с пометкой «нет отметки тренера», уведомление руководителю")
# тренер отметил позже «пришёл» → вернулся
sv.mark_trial(con, d, True, U)
assert stage(d) == "how_was"
ok("Тренер отметил «пришёл» позже → вернулся в «Как прошло пробное»")
# 4. «Думают» и перенос на 2 месяца нельзя
t = task_of(a)
for bad in (lambda: pu.complete(con, t["id"], "thinking", {}, U),
            lambda: pu.complete(con, t["id"], "later", {"date": (date.today() + timedelta(days=60)).isoformat()}, U),
            lambda: planner.move(con, t["id"], {"id": 1, "role": "admin"}, (date.today() + timedelta(days=60)).isoformat()),
            lambda: tasks.complete(con, t["id"], U, "Думают")):
    try:
        bad(); raise SystemExit("должно быть запрещено")
    except (ApiError, planner.PlanError, Exception) as e:
        if isinstance(e, SystemExit):
            raise
ok("«Думают» и перенос на 2 месяца → система не даёт")
# 5. недозвоны подряд
now = datetime(2026, 11, 2, 12, 0)
labels = []
for i in range(4):
    t = task_of(a)
    res = pu.complete(con, t["id"], "no_answer", {}, U, now=now)
    labels.append(res["label"])
assert labels[:3] == ["через 2 часа: позвонить и написать ещё раз", "завтра", "через 7 дней"], labels
ok("Недозвоны: " + " → ".join(labels))
# 6. брат и сестра не пришли вместе
fam = con.execute("SELECT family_id FROM students WHERE id=?", (c,)).fetchone()[0]
s2 = lead("Ваня Тест", fam=fam)
sv.mark_trial(con, s2, False, U)
n = con.execute("SELECT COUNT(*) FROM tasks WHERE kind='rebook' AND family_id=? AND status='open'", (fam,)).fetchone()[0]
assert n == 1
ok("Брат и сестра не пришли → одна задача на семью")
# перезаписан
t = task_of(c)
nxt = (SAT + timedelta(days=7)).isoformat()
pu.complete(con, t["id"], "rebooked", {"kids": [{"student_id": c, "group_id": g, "trial_at": nxt + "T10:00"},
                                                {"student_id": s2, "group_id": g2, "trial_at": nxt + "T11:00"}]}, U)
assert stage(c) == "trial_booked" and stage(s2) == "trial_booked" and not task_of(c)
ok("Перезаписан → «Записан на пробное», задача закрыта")
# отказ мягкий и жёсткий
e = lead("Лёва Тест", "+7 900 200-00-05"); sv.mark_trial(con, e, False, U)
r = pu.complete(con, task_of(e)["id"], "refused", {"reason": "Дорого"}, U)
assert stage(e) == "later" and task_of(e)
ok(f"Отказ «пока не может» → «Пригласим позже», задача пригласить снова {r['again'][:10]}")
f = lead("Глеб Тест", "+7 900 200-00-06"); sv.mark_trial(con, f, False, U)
pu.complete(con, task_of(f)["id"], "refused", {"reason": "Попросили не звонить"}, U)
row = con.execute("SELECT stage, archived, dnd FROM students WHERE id=?", (f,)).fetchone()
assert tuple(row) == ("refused", 1, 1)
ok("Отказ «попросили не звонить» → архив, больше не звоним")
# защита: нет задачи → создаётся сама
con.execute("UPDATE tasks SET status='done' WHERE kind='rebook' AND family_id=(SELECT family_id FROM students WHERE id=?)", (a,))
r = pu.tick(con, datetime(2026, 11, 3, 12, 0))
assert task_of(a) and r["created"] >= 1 and any(x["kind"] == "lost" for x in pu.open_alerts(con))
ok("Задача пропала → CRM создала её сама и подняла красный флаг")
print("ok")
