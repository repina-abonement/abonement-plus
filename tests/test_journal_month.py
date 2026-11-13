"""Журнал группы за месяц: даты занятий и плашка «на абонементе» (Юлия, 04.11). Запуск: python -m tests.test_journal_month"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from crm import db, schedule, services as sv
from crm import smart; smart.REQUIRE = False
from tests.test_crm import check


def main():
    con = db.connect(os.path.join(tempfile.mkdtemp(), "j.sqlite3")); db.init_db(con)
    U = {"id": 1, "name": "Оля", "role": "operator"}
    b = schedule.save_branch(con, {"name": "ТЦ «Небо»"})
    g = schedule.save_group(con, {"name": "Небо Пн/Чт 17:00", "branch_id": b, "weekdays": [0, 3], "time": "17:00", "age": "4–5"})
    ids = [sv.create_student(con, {"name": f"Ребёнок {i}", "parent_name": "Мама", "phone": f"+7 900 100-00-0{i}",
                                   "funnel": "members", "tariff": "fixed", "group_id": g}, U) for i in range(3)]
    con.execute("INSERT INTO subscriptions(student_id,month,tariff,lessons,per_lesson,price,status,created_at,kind) VALUES (?,?,?,?,?,?,?,?,?)",
                (ids[0], "2026-11", "fixed", 9, 1000, 9000, "paid", "2026-11-01T10:00", "month"))
    con.execute("INSERT INTO subscriptions(student_id,month,tariff,lessons,per_lesson,price,status,created_at,kind) VALUES (?,?,?,?,?,?,?,?,?)",
                (ids[1], "2026-11", "fixed", 9, 1000, 9000, "invoiced", "2026-11-01T10:00", "month"))
    con.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES (?,?,?,?)", (ids[0], g, "2026-11-02", "present"))
    con.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES (?,?,?,?)", (ids[1], g, "2026-11-02", "absent"))
    con.commit()
    m = sv.journal_month(con, g, "2026-11")
    dates = [d["date"] for d in m["days"]]
    check(dates[:3] == ["2026-11-02", "2026-11-05", "2026-11-09"] and len(dates) == 9, "Ноябрь Пн/Чт: 9 занятий")
    d2 = m["days"][0]
    check(d2["list"] == 2 and d2["present"] == 1 and d2["absent"] == 1, "02.11: в журнале 2 (неоплативший без отметки не показан), был 1, пропуск 1")
    check(m["subs"] == {"paid": 1, "invoiced": 1, "single": 0, "none": 1} and m["kids"] == 3, "На абонементе 1, счёт 1, без оплаты 1")
    daily = sv.journal(con, g, "2026-11-02", paid_only=True, include_unpaid_marks=False)
    check([r["id"] for r in daily["rows"]] == [ids[0]], "Дневной список скрывает неоплаченного даже с отметкой")
    check({r["id"] for r in daily["hidden_unpaid"]} == {ids[1], ids[2]}, "Неоплаченные вынесены отдельно без дублей")
    check(con.execute("SELECT status FROM attendance WHERE student_id=?", (ids[1],)).fetchone()[0] == "absent", "Исходная отметка не изменена")
    historical_marks(con, U, b)
    print("ok")


def historical_marks(con, user, branch):
    """35 текущих + 8 исторических отметок; читаем без изменения данных."""
    group=schedule.save_group(con,{"name":"Небо Вт/Пт 16:45","branch_id":branch,"weekdays":[1,4],"time":"16:45","age":"3–4"})
    other=schedule.save_group(con,{"name":"Другая группа","branch_id":branch,"weekdays":[1,4],"time":"18:00"})
    def kid(i):
        return sv.create_student(con,{"name":f"История {i}","parent_name":"Анна","phone":f"+79000000{i:03d}","funnel":"members","group_id":group},user)
    expected=set()
    def mark(sid,day,status):
        con.execute("INSERT INTO attendance(student_id,group_id,date,status,note) VALUES (?,?,?,?,?)",(sid,group,day,status,'Из ERP'))
        expected.add((sid,day,status))
    days=['2026-10-02','2026-10-06','2026-10-09','2026-10-13','2026-10-16','2026-10-20','2026-10-23']
    current=[kid(i) for i in range(5)]
    for sid in current:
        for day in days:mark(sid,day,'present')
    # Повторная запись в группе не должна удваивать фактическую отметку.
    con.execute("INSERT INTO enrollments(student_id,group_id,tariff,created_at) VALUES (?,?,'fixed','2026-09-01')",(current[0],group))
    con.execute("INSERT INTO vacations(student_id,start,end,note) VALUES (?,?,?,'Отпуск')",(current[0],'2026-10-01','2026-10-10'))
    historical=[]
    for i in range(8):
        sid=kid(i+10);historical.append(sid)
        mark(sid,'2026-10-03' if i==7 else '2026-10-02','absent' if i%2 else 'present')
        if i%4==0:con.execute("UPDATE students SET stage='left' WHERE id=?",(sid,))
        elif i%4==1:con.execute("UPDATE students SET archived=1 WHERE id=?",(sid,))
        elif i%4==2:
            con.execute("UPDATE enrollments SET active=0,end_date='2026-09-30' WHERE student_id=?",(sid,))
            con.execute("UPDATE students SET group_id=? WHERE id=?",(other,sid))
        else:
            con.execute("UPDATE enrollments SET weekdays='0' WHERE student_id=?",(sid,))
    # Чужая группа и другой месяц не должны попасть в октябрьский журнал.
    con.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES (?,?,?,'absent')",(historical[0],other,'2026-10-02'))
    con.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES (?,?,?,'present')",(historical[0],group,'2026-11-03'))
    before=con.total_changes
    month=sv.journal_month(con,group,'2026-10')
    assert '2026-10-03' in [d['date'] for d in month['days']], 'Фактическая дата вне расписания потерялась'
    actual=[]
    for d in month['days']:
        for row in sv.journal(con,group,d['date'],paid_only=True)['rows']:
            if row.get('attendance_id'):
                assert row['group_id']==group and row['date']==d['date']
                assert row['note']=='Из ERP'
                actual.append((row['id'],d['date'],row['status']))
    assert {(r['student_id'],r['date'],r['status']) for r in month['attendance']}==expected
    assert len(month['attendance'])==43
    assert sum(r['historical_attendance'] for r in month['attendance'])==8
    assert len(actual)==43 and len(actual)==len(set(actual)) and set(actual)==expected,(len(actual),expected-set(actual))
    assert sum(d['present']+d['absent'] for d in month['days'])==43
    assert con.total_changes==before,'Чтение журнала изменило данные'
    # Исходный статус не заменяется отпуском и не зависит от фильтра оплаты.
    for paid_only in (True,False):
        row=next(r for r in sv.journal(con,group,days[0],paid_only)['rows'] if r['id']==current[0])
        assert row['status']=='present'
    # Пересечение записи на отработку и списка группы не удваивает отметку.
    absence=con.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES (?,?,?,'absent')",(current[0],group,'2026-09-30')).lastrowid
    mid=con.execute("INSERT INTO makeups(student_id,absence_id,group_id,date,status) VALUES (?,?,?,?,'booked')",(current[0],absence,group,days[0])).lastrowid
    rows=[r for r in sv.journal(con,group,days[0],True)['rows'] if r['id']==current[0]]
    assert len(rows)==1 and rows[0]['status']=='present' and rows[0]['makeup_id']==mid
    assert next(d for d in sv.journal_month(con,group,'2026-10')['days'] if d['date']==days[0])['makeups']==1
    # В группе отработок факт виден даже без сохранившейся записи на отработку.
    makeup_group=schedule.save_group(con,{"name":"Отработки","branch_id":branch,"weekdays":[5],"time":"15:00","is_makeup":True})
    con.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES (?,?,?,'absent')",(historical[0],makeup_group,'2026-10-04'))
    row=sv.journal(con,makeup_group,'2026-10-04',True)['rows']
    assert len(row)==1 and row[0]['status']=='absent' and row[0]['group_id']==makeup_group
    assert next(d for d in sv.journal_month(con,makeup_group,'2026-10')['days'] if d['date']=='2026-10-04')['absent']==1
    print('  ✓ 35 + 8 исторических отметок: точная группа/дата/статус, без дублей, включая дату вне расписания, отпуск и отработки')


if __name__ == "__main__":
    main()
