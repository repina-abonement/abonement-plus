"""Проверки бизнес-правил. Запуск: python -m tests.test_crm (из папки проекта)."""
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crm import db, logic, services as sv  # noqa: E402

PRICES = {"fixed": 1350, "flex": 1550}
USER = {"id": 1, "name": "Тест"}


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ✓", msg)


def test_logic():
    print("Расчёт цены")
    q = logic.quote(weekdays=[0, 3], ym="2026-11", tariff="fixed", prices=PRICES, vacations=[], prev_unresolved_absences=2)
    check(q["lessons_paid"] == 9 and q["total"] == 12150, "Пн/Чт, ноябрь 2026, негибкий: 9 занятий, 12 150 ₽, пропуски не учитываются")
    q = logic.quote(weekdays=[0, 3], ym="2026-11", tariff="flex", prices=PRICES, vacations=[], prev_unresolved_absences=0)
    check(q["total"] == 13950, "Гибкий: 9 × 1 550 = 13 950 ₽")
    q = logic.quote(weekdays=[0, 3], ym="2026-11", tariff="flex", prices=PRICES, vacations=[], prev_unresolved_absences=2)
    check(q["total"] == 13950 - 3100, "Гибкий, 2 пропуска: −3 100 ₽")
    q = logic.quote(weekdays=[0, 3], ym="2026-11", tariff="flex", prices=PRICES, vacations=[], prev_unresolved_absences=5)
    check(q["credit_lessons"] == 3, "Перерасчёт максимум 3 занятия")
    q = logic.quote(weekdays=[0, 3], ym="2026-11", tariff="fixed", prices=PRICES,
                    vacations=[(date(2026, 11, 9), date(2026, 11, 15))], prev_unresolved_absences=0)
    check(q["lessons_paid"] == 7 and q["total"] == 7 * 1350, "Отпуск 9–15 ноября: оплачиваются 7 занятий")
    t, fee = logic.apply_late_fee(10000, enabled=True, percent=10, today=date(2026, 10, 26), target_month="2026-11")
    check(fee == 1000, "Наценка 10% после 25 числа")
    t, fee = logic.apply_late_fee(10000, enabled=True, percent=10, today=date(2026, 10, 25), target_month="2026-11")
    check(fee == 0, "До 25 числа включительно наценки нет")
    s, e = logic.makeup_window(date(2026, 11, 10))
    check(s == date(2026, 10, 1) and e == date(2026, 11, 30), "Отработать можно пропуски текущего и прошлого месяца")


def test_flow():
    print("Пропуски, отработки, перерасчёт")
    path = os.path.join(tempfile.mkdtemp(), "t.sqlite3")
    con = db.connect(path)
    db.init_db(con)
    sv.today = lambda: date(2026, 10, 20)
    br = con.execute("INSERT INTO branches(name) VALUES ('Небо')").lastrowid
    g = con.execute("INSERT INTO groups(branch_id,name,weekdays) VALUES (?, 'Пн/Чт', '0,3')", (br,)).lastrowid
    mk = con.execute("INSERT INTO groups(branch_id,name,weekdays,is_makeup) VALUES (?, 'Отработка Сб', '5', 1)", (br,)).lastrowid
    sid = sv.create_student(con, {"name": "Миша", "parent_name": "Анна", "phone": "+7 900 123-45-67", "funnel": "members",
                                  "tariff": "flex", "group_id": g}, USER)
    for d in ["2026-10-05", "2026-10-08", "2026-10-12", "2026-10-15"]:
        sv.mark_attendance(con, sid, g, d, "absent", USER)
    c = sv.absence_counters(con, sid)
    check(c["absent_cur"] == 4 and c["available"] == 4, "4 пропуска в октябре, все можно отработать")

    sv.book_makeup(con, sid, mk, "2026-10-24", None, USER)
    c = sv.absence_counters(con, sid)
    check(c["booked"] == 1 and c["available"] == 3, "Записан на отработку: доступно 3")
    m = con.execute("SELECT id FROM makeups").fetchone()[0]
    sv.set_makeup_status(con, m, "done", USER)
    c = sv.absence_counters(con, sid)
    check(c["made_up"] == 1 and c["available"] == 3, "Отработал: 1 отработан, 3 остались")

    q = sv.quote_for(con, sid, "2026-11")
    check(q["credit_lessons"] == 3 and q["lessons_paid"] == 9, "Ноябрь: перерасчёт 3 неотработанных пропусков")
    check(q["total"] == 9 * 1550 - 3 * 1550, "Ноябрь: 13 950 − 4 650 = 9 300 ₽")
    sv.create_invoice(con, sid, "2026-11", USER)
    c = sv.absence_counters(con, sid)
    check(c["available"] == 0 and c["credited"] == 3, "После счёта пропуски обнулились и пропали из отработок")
    try:
        sv.book_makeup(con, sid, mk, "2026-10-31", None, USER)
        check(False, "Нельзя отработать пересчитанный пропуск")
    except sv.ApiError:
        check(True, "Нельзя отработать пересчитанный пропуск")
    sub = con.execute("SELECT id FROM subscriptions").fetchone()[0]
    sv.cancel_subscription(con, sub, USER)
    c = sv.absence_counters(con, sid)
    check(c["available"] == 3, "Отмена счёта возвращает пропуски")

    print("Две группы")
    g2 = con.execute("INSERT INTO groups(branch_id,name,weekdays) VALUES (?, 'Вт/Пт', '1,4')", (br,)).lastrowid
    sid2 = sv.create_student(con, {"name": "Катя", "parent_name": "Ольга", "phone": "+7 900 222-33-44",
                                   "funnel": "members", "tariff": "fixed", "group_id": g}, USER)
    e1 = sv.enrollments_of(con, sid2)[0]["id"]
    sv.update_enrollment(con, e1, {"weekdays": [0]}, USER)
    e2 = sv.add_enrollment(con, sid2, g2, [4], "fixed", USER)
    q1, q2 = sv.quote_for(con, sid2, "2026-11", e1), sv.quote_for(con, sid2, "2026-11", e2)
    check(q1["lessons_paid"] == 5 and q2["lessons_paid"] == 4, "Пн/Чт только Пн: 5 занятий; Вт/Пт только Пт: 4 занятия в ноябре")
    sv.create_invoice(con, sid2, "2026-11", USER, e1)
    sv.create_invoice(con, sid2, "2026-11", USER, e2)
    for (sub,) in con.execute("SELECT id FROM subscriptions WHERE student_id=?", (sid2,)).fetchall():
        sv.pay_subscription(con, sub, USER)
    dash = sv.dashboard(con, "2026-11")
    check(dash["kpi"]["holders"]["value"] == 1 and dash["kpi"]["fill"]["subs_count"] == 2,
          "Дашборд: 2 абонемента, но 1 уникальный держатель")
    check(dash["kpi"]["revenue"]["value"] == 9 * 1350, "Выручка = сумма обоих абонементов (5 + 4 занятия)")
    j = sv.journal(con, g, "2026-11-05")
    check(all(r["id"] != sid2 for r in j["rows"]), "В четверг Катя не в журнале Пн/Чт")
    j = sv.journal(con, g, "2026-11-02")
    check(any(r["id"] == sid2 for r in j["rows"]), "В понедельник Катя в журнале Пн/Чт")

    print("Устаревшие пропуски")
    sv.today = lambda: date(2026, 12, 2)
    c = sv.absence_counters(con, sid)
    check(c["available"] == 0, "В декабре октябрьские пропуски уже сгорели")

    print("Дубли")
    try:
        sv.create_student(con, {"name": "Катя", "parent_name": "Анна", "phone": "8 (900) 123 45 67"}, USER)
        check(False, "Предупреждение о дубле по телефону")
    except sv.ApiError as e:
        check(e.status == 409, "Предупреждение о дубле по телефону")


if __name__ == "__main__":
    test_logic()
    test_flow()
    print("\nВсе проверки пройдены")
