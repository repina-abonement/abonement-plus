"""Проверки: перенос расписания, помощники, отсутствия и замены, приход тренеров, кто вёл тренировку.
Запуск: python -m tests.test_staff"""
import os
import sys
import tempfile
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import coaches, db, schedule, staff  # noqa: E402
from tests.test_crm import check  # noqa: E402

ADM = {"id": 1, "name": "Юлия", "role": "admin"}

PLAN = {"archive_others": True, "branches": [
    {"name": "ТЦ «Небо»", "address": "ул. Авиаторов, 3А", "groups": [
        {"name": "Небо Ср/Вс 16:45 3–4", "weekdays": [2, 6], "time": "16:45", "age": "3–4", "coach": "Никита Евсюнин", "assistant": "Артём Шатый"},
        {"name": "Небо Сб/Вс 10:00 3–4", "weekdays": [5, 6], "time": "10:00", "age": "3–4", "coach": "Никита Евсюнин", "assistant": "Артём Шатый"},
        {"name": "Отработки Сб 14:00 3–5", "weekdays": [5], "time": "14:00", "age": "3–5", "is_makeup": True, "coach": "Никита Евсюнин"}]},
    {"name": "Чоботовская", "groups": [
        {"name": "Чоботовская Сб/Вс 3–5", "weekdays": [5, 6], "time": "11:30", "day_times": {"6": "10:00"}, "age": "3–5"}]},
]}


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "st.sqlite3"))
    db.init_db(con)
    print("Перенос расписания")
    old = schedule.save_branch(con, {"name": "ТЦ «Небо»"})
    demo = schedule.save_group(con, {"name": "Тестовая", "branch_id": old, "weekdays": [0], "time": "17:00"})
    r = schedule.import_schedule(con, PLAN)
    check(r["groups_new"] == 4 and len(r["coaches_created"]) == 2, "Создано 4 группы и 2 тренера")
    check(con.execute("SELECT active FROM groups WHERE id=?", (demo,)).fetchone()[0] == 0, "Тестовая группа ушла в архив")
    r = schedule.import_schedule(con, PLAN)
    check(r["groups_new"] == 0 and r["groups_updated"] == 4 and not r["coaches_created"], "Повторный перенос ничего не дублирует")
    sun = schedule.day(con, date(2026, 11, 8))  # воскресенье
    ch = [l for b in sun["branches"] for l in b["lessons"] if l["name"].startswith("Чоботовская")][0]
    sat = schedule.day(con, date(2026, 11, 7))
    ch6 = [l for b in sat["branches"] for l in b["lessons"] if l["name"].startswith("Чоботовская")][0]
    check(ch["time"] == "10:00" and ch6["time"] == "11:30", "Чоботовская: в субботу 11:30, в воскресенье 10:00")
    nik = con.execute("SELECT id FROM users WHERE name='Никита Евсюнин'").fetchone()[0]
    art = con.execute("SELECT id FROM users WHERE name='Артём Шатый'").fetchone()[0]
    ivan = con.execute("INSERT INTO users(login,name,role,pw_hash) VALUES ('iv','Иван Ивакин','coach','x$y')").lastrowid

    print("Отсутствие и замены")
    schedule.save_absence(con, {"user_id": art, "date_from": "2026-10-31", "date_to": "2026-11-08", "note": "отпуск"}, ADM)
    need = schedule.needs_subs(con, date(2026, 10, 27))
    check(len(need) == 7 and all(x["role"] == "assistant" for x in need),
          "Артём Ш. отсутствует 31.10–8.11: 7 тренировок без помощника на столе")
    check(need[0]["date"] == "2026-10-31", "Первая — суббота 31 октября")
    schedule.save_sub(con, {"group_id": need[0]["group_id"], "date": need[0]["date"], "role": "assistant", "coach_id": ivan}, ADM)
    check(len(schedule.needs_subs(con, date(2026, 10, 27))) == 6, "Поставили Ивана — осталось 6")
    lsn = [l for b in schedule.day(con, date(2026, 10, 31))["branches"] for l in b["lessons"] if l["id"] == need[0]["group_id"]][0]
    check(lsn["assistant"] == "Иван Ивакин" and lsn["coach"] == "Никита Евсюнин", "В расписании на 31.10: Никита и Иван")
    ct = coaches.coach_today(con, art, date(2026, 10, 31))
    check(ct["away"] and not ct["today"], "У Артёма на столе: ты отдыхаешь, тренировок нет")
    ct = coaches.coach_today(con, ivan, date(2026, 10, 31))
    check(ct["today"] and ct["today"][0]["sub_for"]["role"] == "assistant", "Иван видит замену помощником")

    print("Приход на филиал")
    exp = staff.expected(con, date(2026, 10, 31))
    nik_sh = [x for x in exp if x["user_id"] == nik][0]
    check(nik_sh["first"] == "10:00" and nik_sh["due"] == "09:30", "Никита в субботу должен быть в Небе к 9:30")
    check(not [x for x in exp if x["user_id"] == art], "Артёма (в отпуске) на приход не ждём")
    try:
        staff.checkin(con, {"id": nik, "role": "coach"}, nik_sh["branch_id"], now=datetime(2026, 10, 31, 5, 0))
        check(False, "Слишком рано отметиться нельзя")
    except staff.StaffError:
        check(True, "Слишком рано отметиться нельзя")
    r = staff.checkin(con, {"id": nik, "role": "coach"}, nik_sh["branch_id"], 55.6, 37.4, 20, now=datetime(2026, 10, 31, 9, 42))
    check(r["late_min"] == 12 and r["fine"] == 500, "Пришёл в 9:42 — опоздание 12 мин, штраф 500 ₽")
    rep = staff.day_report(con, date(2026, 10, 31), now=datetime(2026, 10, 31, 10, 5))
    states = {x["name"]: x["state"] for x in rep["rows"]}
    check(states["Никита Евсюнин"] == "late" and states["Иван Ивакин"] == "absent", "Варвара видит: Никита опоздал, Иван не отметился")
    cid = con.execute("SELECT id FROM checkins").fetchone()[0]
    staff.review(con, cid, "cancel", ADM, "пробки")
    check(not staff.fines(con, "2026-10"), "Штраф сняли — в месяц не попадает")

    print("Кто вёл тренировку")
    g = need[0]["group_id"]
    staff._now = lambda: datetime(2026, 10, 31, 12, 0)
    st = staff.lesson_staff(con, g, "2026-10-31")
    check(not st["set"] and st["main_id"] == nik and st["assistant_id"] == ivan, "По плану: Никита главный, Иван помощник")
    staff.set_lesson_staff(con, g, "2026-10-31", nik, None, {"id": nik, "role": "coach", "name": "Никита"})
    st = staff.lesson_staff(con, g, "2026-10-31")
    check(st["set"] and st["solo"], "Никита отметил, что был один")
    check(staff.pay_share(con, main=True, solo=True) == 20 and staff.pay_share(con, main=True, solo=False) == 15.5
          and staff.pay_share(con, main=False, solo=False) == 10, "Проценты: один 20%, главный 15,5%, помощник 10%")
    mk = con.execute("SELECT id FROM groups WHERE is_makeup=1 AND active=1").fetchone()[0]
    check(staff.makeup_pay(con, mk, "2026-10-31") == 0, "Отработки: никто не пришёл — 0 ₽")


if __name__ == "__main__":
    main()
    print("\nВсе проверки тренеров пройдены")
