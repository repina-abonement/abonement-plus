"""Напоминание перед каждым занятием (Юлия, 04.11). Запуск: python -m tests.test_reminders"""
import os, sys, tempfile
from datetime import date, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import comms, db, reminders, schedule, services as sv, singles
from crm import smart; smart.REQUIRE = False
from tests.test_crm import check


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "r.sqlite3")); db.init_db(con)
    U = {"id": 1, "name": "Оля", "role": "operator"}
    sv.today = lambda: date(2026, 11, 2)
    b = schedule.save_branch(con, {"name": "ТЦ «Небо»"})
    g = schedule.save_group(con, {"name": "Небо Пн/Чт 18:00", "branch_id": b, "weekdays": [0, 3], "time": "18:00", "age": "4–5"})
    mk = lambda name, phone, fid=None: sv.create_student(con, {"name": name, "parent_name": "Юлия Иванова", "phone": phone, "funnel": "members",
                                                                "tariff": "fixed", "group_id": g} | ({"family_id": fid} if fid else {}), U)
    paid = mk("Миша Платный", "+7 900 400-00-01")
    unpaid = mk("Петя Неоплатил", "+7 900 400-00-02")
    warned = mk("Ваня Заболел", "+7 900 400-00-03")
    single = sv.create_student(con, {"name": "Саша Разовый", "parent_name": "Ольга", "phone": "+7 900 400-00-04", "funnel": "members",
                                     "tariff": "fixed"}, U)
    trial = sv.create_student(con, {"name": "Лёва Пробный", "parent_name": "Нина", "phone": "+7 900 400-00-05", "funnel": "sales"}, U)
    con.execute("UPDATE students SET group_id=?, trial_at='2026-11-02T18:00' WHERE id=?", (g, trial))
    for k in (paid, warned):
        con.execute("INSERT INTO subscriptions(student_id,month,tariff,lessons,per_lesson,price,status,created_at,kind) VALUES (?,'2026-11','fixed',9,1000,9000,'paid','2026-11-01','month')", (k,))
    con.execute("INSERT INTO attendance(student_id,group_id,date,status,warned,note) VALUES (?,?,'2026-11-02','absent',1,'болеет')", (warned, g))
    singles.book(con, single, g, "2026-11-02", U)
    fams = {}
    for k in (paid, unpaid, warned, single, trial):
        fid = con.execute("SELECT family_id FROM students WHERE id=?", (k,)).fetchone()[0]
        cid = comms.upsert_conversation(con, "telegram", f"t-{k}", phone=f"7900{k}")
        comms.link(con, cid, fid); fams[cid] = k
    sent = []
    comms.send = lambda con, c, text, user: sent.append((fams.get(c), text)) or {"id": 1}

    check(reminders.tick(con, datetime(2026, 11, 2, 15, 0)) == 0 and not sent, "Пока автосообщения не запущены, ничего не уходит")
    comms.put(con, "auto_live", "1")
    check(reminders.tick(con, datetime(2026, 11, 2, 14, 30)) == 0, "Раньше чем за 3 часа не пишем")
    reminders.tick(con, datetime(2026, 11, 2, 15, 0))
    who = sorted(k for k, _ in sent)
    check(who == sorted([paid, single]), "Ушло тем, кто по абонементу, и разовику; не пробному, не предупредившему, не неоплатившему")
    t = dict(sent)[paid]
    check("Здравствуйте, Юлия!" in t and "02.11.2026" in t and "18:00" in t and "Авиаторов" in t, "Текст: имя родителя, дата, время, место из настроек филиала")
    check("—" not in t and "{" not in t, "Без длинного тире и неподставленных полей")
    reminders.tick(con, datetime(2026, 11, 2, 16, 0))
    check(len(sent) == 2, "Повторно на ту же тренировку не уходит")
    check(reminders.tick(con, datetime(2026, 11, 2, 18, 5)) == 0, "После начала не пишем")
    print("ok")


if __name__ == "__main__":
    main()
