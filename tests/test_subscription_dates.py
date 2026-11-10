"""Календарь продажи и перерасчёт гибкого тарифа только по запросу."""
import json
import tempfile
from datetime import date
from pathlib import Path
from crm import db, services as sv, pricing
from crm.services import ApiError


def main():
    con = db.connect(str(Path(tempfile.mkdtemp()) / 'calendar.sqlite3'))
    db.init_db(con)
    sv.today = lambda: date(2026, 11, 20)
    who = {'id': 1, 'name': 'Проверка', 'role': 'admin'}
    bid = con.execute("INSERT INTO branches(name) VALUES ('Тестовая')").lastrowid
    gid = con.execute("INSERT INTO groups(branch_id,name,weekdays) VALUES (?, 'Тест Вт/Пт', '1,4')", (bid,)).lastrowid
    sid = sv.create_student(con, {'name': 'Иван Примеров', 'birth': '2020-01-01', 'parent_name': 'Анна', 'email': 'example@example.invalid', 'phone': '+79000000001', 'funnel': 'members', 'group_id': gid, 'tariff': 'flex'}, who)
    con.execute("UPDATE parents SET email='example@example.invalid'")
    eid = sv.enrollments_of(con, sid)[0]['id']
    previous = con.execute("""INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at)
        VALUES (?,?,'2026-11','flex',8,1550,12400,'paid','2026-10-20')""", (sid, eid)).lastrowid
    for day in ('2026-11-03', '2026-11-06', '2026-11-10', '2026-11-13'):
        con.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES (?,?,?,'absent')", (sid,gid,day))
    normal = sv.quote_for(con, sid, '2026-12')
    assert normal['credit_lessons'] == 0 and normal['credit_available'] == 3
    requested = sv.quote_for(con, sid, '2026-12', credit_requested=True)
    assert requested['credit_lessons'] == 3 and requested['total'] == normal['total'] - 4650
    assert sv.quote_for(con,sid,'2026-12',tariff='fixed',credit_requested=True)['credit_lessons'] == 0
    con.execute("UPDATE subscriptions SET tariff='fixed' WHERE id=?", (previous,))
    assert sv.quote_for(con,sid,'2026-12',credit_requested=True)['credit_lessons'] == 0
    con.execute("UPDATE subscriptions SET tariff='flex' WHERE id=?", (previous,))
    q = sv.quote_for(con,sid,'2026-12',excluded_dates=['2026-12-01'])
    assert len(q['calendar_dates']) == 9 and len(q['dates']) == 8 and q['total'] == normal['total'] - 1550
    for invalid in (['2026-12-02'], ['2026-11-03'], normal['dates']):
        try:
            sv.quote_for(con,sid,'2026-12',excluded_dates=invalid)
        except ApiError:
            pass
        else:
            raise AssertionError('Invalid exclusion accepted')
    inv = sv.create_invoice(con,sid,'2026-12',who,excluded_dates=['2026-12-01'],credit_requested=True)
    sub = con.execute('SELECT * FROM subscriptions WHERE id=?',(inv['id'],)).fetchone()
    assert json.loads(sub['excluded_dates']) == ['2026-12-01'] and sub['lessons'] == 8 and sub['credit_lessons'] == 3
    assert con.execute("SELECT COUNT(*) FROM attendance WHERE resolved='credit'").fetchone()[0] == 3
    pricing.reprice(con, inv['id'], date(2026,11,27))
    assert con.execute('SELECT price FROM subscriptions WHERE id=?',(inv['id'],)).fetchone()[0] == 8138  # (8-3)*1550 +5%
    sv.today = lambda: date(2026,12,2)
    try:
        sv.mark_attendance(con,sid,gid,'2026-12-01','absent',who)
    except ApiError:
        pass
    else:
        raise AssertionError('Excluded day became a new absence')
    print('ok: opt-in credit, previous flexible tariff, calendar exclusions, saved dates, late payment, no duplicate credit')


if __name__ == '__main__':
    main()
