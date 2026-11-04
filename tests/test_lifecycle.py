"""Цепочки задач по всем этапам (разбор 01.11). Запуск: python -m tests.test_lifecycle"""
import os, sys, tempfile
from datetime import datetime, timedelta, date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, lifecycle, money, schedule, services as sv, logic
from crm import smart; smart.REQUIRE = False
from tests.test_crm import check

con = db.connect(os.path.join(tempfile.mkdtemp(), "l.sqlite3")); db.init_db(con)
U = {"id": 1, "name": "Оля", "role": "operator"}
b = schedule.save_branch(con, {"name": "ТЦ «Небо»"})
g = schedule.save_group(con, {"name": "Небо", "branch_id": b, "weekdays": [0, 3], "time": "17:00", "age": "4–5"})
n = [0]
def kid(funnel="sales", stage=None, ago_days=0, **kw):
    n[0] += 1
    sid = sv.create_student(con, {"name": f"Ребёнок Номер{n[0]}", "parent_name": "Мама", "phone": f"+7 900 100-00-{n[0]:02d}",
                                  "funnel": funnel, **({"group_id": g} if funnel == "members" else {})}, U)
    if stage:
        con.execute("UPDATE students SET stage=?, stage_changed_at=? WHERE id=?",
                    (stage, (datetime.now() - timedelta(days=ago_days)).isoformat(timespec="minutes"), sid))
    for k, v in kw.items():
        con.execute(f"UPDATE students SET {k}=? WHERE id=?", (v, sid))
    return sid
open_kinds = lambda sid: [r[0] for r in con.execute("SELECT kind FROM tasks WHERE student_id=? AND status='open'", (sid,))]
done = lambda sid, kind: con.execute("UPDATE tasks SET status='done', done_at=? WHERE student_id=? AND kind=? AND status='open'",
                                     ((datetime.now() - timedelta(days=1)).isoformat(timespec="minutes"), sid, kind))
now = datetime.now()

print("1. Новый лид и недозвон")
lead = kid()
lifecycle.tick(con, now)
check(open_kinds(lead) == ["call_lead"], "Новый лид: задача «Позвонить новому лиду»")
sv.move_card(con, lead, "sales", "no_answer", U)
lifecycle.tick(con, now)
check(open_kinds(lead) == ["no_answer"], "Недозвон: звонок про лида снят, задача «позвонить ещё раз»")
gaps = []
for _ in range(4):
    t = con.execute("SELECT due_at FROM tasks WHERE student_id=? AND kind='no_answer' AND status='open'", (lead,)).fetchone()[0]
    base = con.execute("SELECT COALESCE(MAX(done_at), (SELECT stage_changed_at FROM students WHERE id=?)) FROM tasks WHERE student_id=? AND kind='no_answer' AND status='done'", (lead, lead)).fetchone()[0]
    gaps.append((datetime.fromisoformat(t[:10]) - datetime.fromisoformat(base[:10])).days)
    done(lead, "no_answer"); lifecycle.tick(con, now)
check(gaps == [1, 3, 7, 30], f"Лесенка недозвона: завтра, через 3 дня, неделю, месяц ({gaps})")
nl = kid(stage="no_answer", ago_days=35)
lifecycle.tick(con, now)
check("no_answer" in open_kinds(nl), "Недозвон месячной давности: цепочка продолжается (шаг «через месяц»)")
st = con.execute("SELECT stage FROM students WHERE id=?", (lead,)).fetchone()[0]
due = con.execute("SELECT due_at FROM tasks WHERE student_id=? AND kind='rebook' AND status='open'", (lead,)).fetchone()
check(st == "later" and due and due[0][5:7] in ("01", "08"), "Дальше «Пригласим позже» с задачей на ближайший набор (январь или август)")

print("2. «Как прошло» без решения")
hw = kid(stage="how_was", ago_days=2)
lifecycle.tick(con, now)
check(open_kinds(hw) == ["why_not_bought"], "Больше суток без решения: «Почему не купили», как в ERP")

print("3. «Купил» по оплате")
lead2 = kid()
con.execute("UPDATE students SET group_id=? WHERE id=?", (g, lead2))
sv.move_card(con, lead2, "sales", "bought", U)
lifecycle.tick(con, now)
check(con.execute("SELECT converted_at FROM students WHERE id=?", (lead2,)).fetchone()[0] is None and "first_pay" in open_kinds(lead2),
      "Перевели без оплаты: продажа не засчитана, задача «Принять оплату»")
inv = sv.create_invoice(con, lead2, logic.month_of(date.today()), U)
money.pay_subscription(con, inv["id"], "transfer", U)
lifecycle.tick(con, now)
cv = con.execute("SELECT converted_at, converted_by FROM students WHERE id=?", (lead2,)).fetchone()
check(cv[0] and cv[1] == 1 and "first_pay" not in open_kinds(lead2), "Первая оплата: продажа засчитана тому, кто продал, задача снята")

print("4. Отказ через 3 месяца")
r1 = kid(stage="refused", ago_days=95, refusal_reason="Дорого")
r2 = kid(stage="refused", ago_days=95, refusal_reason="Выбрали другую секцию")
r3 = kid(stage="refused", ago_days=95, refusal_reason="Дорого", dnd=1)
lifecycle.tick(con, now)
check(open_kinds(r1) == ["reinvite"] and not open_kinds(r2) and not open_kinds(r3), "Пригласить снова, кроме другой секции и «не беспокоить»")

print("5. Продление и долг")
m = kid("members")
lifecycle.tick(con, now.replace(day=20, hour=12))
check("renew" in open_kinds(m), "С 20 числа: «Продлить абонемент»")
lifecycle.tick(con, now.replace(day=27, hour=12))
check("debt" in open_kinds(m), "После 25 числа без оплаты текущего месяца: «Долг: связаться»")

print("6. Ушёл")
try:
    sv.move_card(con, m, "members", "left", U); ok = False
except sv.ApiError:
    ok = True
check(ok, "Без причины ухода нельзя")
sv.move_card(con, m, "members", "left", U, "Дорого")
lifecycle.tick(con, now)
check(not [k for k in open_kinds(m) if k in ("renew", "debt")], "Ушёл: задачи продления и долга сняты")
con.execute("UPDATE students SET stage_changed_at=? WHERE id=?", ((now - timedelta(days=61)).isoformat(timespec="minutes"), m))
lifecycle.tick(con, now)
check("win_back" not in open_kinds(m), "Дорого: без задачи по сроку, ждёт акцию (04.11)")
con.execute("UPDATE students SET refusal_reason='Другое: старый уход' WHERE id=?", (m,))
lifecycle.tick(con, now)
check("win_back" in open_kinds(m), "Старый уход без срока: через 2 месяца «Пригласить вернуться»")

print("7. Этап «Напоминание о пробном» убран")
check("trial_reminder" not in dict(sv.SALES_STAGES), "Лишнего этапа нет в воронке")

print("8. Отработка")
m2 = kid("members")
sv.move_card(con, m2, "skips", "offered", U)
con.execute("UPDATE history SET ts=? WHERE student_id=? AND text LIKE 'Отработки: Предложили%'", ((now - timedelta(days=3)).isoformat(timespec="minutes"), m2))
lifecycle.tick(con, now)
check("makeup_check" in open_kinds(m2), "Предложили отработку и 2 дня тишины: «Уточнить отработку»")
print("Старые клиенты из ERP задачами не заваливаем")
old_lead = kid(stage="lead", ago_days=200)
old_ref = kid(stage="refused", ago_days=400, refusal_reason="Дорого")
con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('revive_per_day', '0')")  # «оживление» (днём) проверяем ниже отдельно
lifecycle.tick(con, now)
con.execute("DELETE FROM settings WHERE key='revive_per_day'")
check(not open_kinds(old_lead) and not open_kinds(old_ref), "Лид полугодовой давности и отказ годичной: задач нет")
print("Старую базу оживляем порциями: горячие первыми")
con.execute("UPDATE tasks SET status='done' WHERE status='open'")
con.execute("UPDATE students SET funnel='members', stage='left' WHERE funnel='sales'")  # убираем лидов из прошлых проверок
olds = []
for i in range(15):
    sid = kid(stage="lead", ago_days=100 + i)
    con.execute("UPDATE students SET created_at=? WHERE id=?", ((now - timedelta(days=100 + i)).isoformat(timespec="minutes"), sid))
    olds.append(sid)
hot = kid(stage="missed_trial", ago_days=40, trial_came=1, source="Заявка с сайта")
lifecycle.revive(con, now.replace(hour=12))
got = [r[0] for r in con.execute("SELECT student_id FROM tasks WHERE kind='revive' AND status='open'")]
check(len(got) == lifecycle.REVIVE_PER_DAY, f"За день не больше {lifecycle.REVIVE_PER_DAY} «оживлений»: без завала")
check(hot in got, "Был на пробном и сам оставлял заявку: в первой порции")
lifecycle.revive(con, now.replace(hour=13))
check(con.execute("SELECT COUNT(*) FROM tasks WHERE kind='revive'").fetchone()[0] == lifecycle.REVIVE_PER_DAY, "Второй раз в тот же день не добавляет")
print("ok")
