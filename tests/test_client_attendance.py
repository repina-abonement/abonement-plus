import tempfile
from pathlib import Path
from datetime import date
from unittest.mock import patch
from crm import db, services as sv, schedule, program, clientattendance, capacity


def main():
    con=db.connect(str(Path(tempfile.mkdtemp())/'attendance.sqlite3'));db.init_db(con)
    sv.today=lambda:date(2026,11,10)
    u={'id':1,'role':'admin','name':'Пример'}
    b=schedule.save_branch(con,{'name':'Небо Демо'})
    own=schedule.save_group(con,{'name':'Своя Вт','branch_id':b,'weekdays':[1,4],'time':'18:30','age':'5-6','capacity':10})
    quiet=schedule.save_group(con,{'name':'Тихая Сб','branch_id':b,'weekdays':[5],'time':'11:00','age':'5-6','capacity':10})
    busy=schedule.save_group(con,{'name':'Загруженная Сб','branch_id':b,'weekdays':[5],'time':'12:00','age':'5-6','capacity':10})
    full=schedule.save_group(con,{'name':'Полная Сб','branch_id':b,'weekdays':[5],'time':'13:00','age':'5-6','capacity':10})
    makeup=schedule.save_group(con,{'name':'Отработки Сб','branch_id':b,'weekdays':[5],'time':'15:00','age':'5-6','is_makeup':1,'capacity':10})
    sid=sv.create_student(con,{'name':'Иван Примеров','birth':'2021-01-01','parent_name':'Анна','phone':'+79000000501','funnel':'members','group_id':own},u)
    eid=con.execute('SELECT id FROM enrollments WHERE student_id=? AND group_id=?',(sid,own)).fetchone()[0]
    con.execute("INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,price,per_lesson,status,created_at) VALUES (?,?,'2026-11','fixed',8,8000,1000,'paid','2026-11-01')",(sid,eid))
    con.execute("UPDATE students SET trial_at='2026-11-01T10:00',trial_came=1 WHERE id=?",(sid,))
    for day,status in [('2026-11-03','present'),('2026-11-06','absent'),('2026-11-10','absent')]:con.execute('INSERT INTO attendance(student_id,group_id,date,status) VALUES (?,?,?,?)',(sid,own,day,status))
    aid=con.execute("SELECT id FROM attendance WHERE date='2026-11-06'").fetchone()[0]
    con.execute("INSERT INTO makeups(student_id,absence_id,group_id,date,status) VALUES (?,?,?,'2026-11-07','done')",(sid,aid,makeup))
    con.execute("UPDATE attendance SET resolved='makeup' WHERE id=?",(aid,))
    con.execute("INSERT INTO single_visits(student_id,group_id,date,created_at) VALUES (?,?,'2026-11-14','2026-11-10')",(sid,quiet))
    result=clientattendance.calendar(con,sid,'2026-11')
    assert {'subscription','makeup','trial','single'} <= {e['kind'] for e in result['events']}
    assert result['attended']==3 and result['missed']==2 and result['counters']['available']==1
    assert any(e['date']=='2026-11-07' and e['kind']=='makeup' and e['status']=='present' for e in result['events'])
    counts={quiet:1,busy:7,full:10,makeup:3}
    def occupancy(c,g,day):return {'count':counts.get(g,0),'limit':10,'free':10-counts.get(g,0),'has_lesson':True}
    with patch.object(capacity,'occupancy',side_effect=occupancy):
        suggestions=clientattendance.suggestions(con,sid)
    assert suggestions['slots'][0]['group_id']==quiet
    assert [s['count'] for s in suggestions['slots']]==sorted(s['count'] for s in suggestions['slots'])
    assert all(s['group_id'] not in (own,full) for s in suggestions['slots'])
    assert 'Иван может посетить ещё 1 занятие' in suggestions['message']
    # Запись резервирует ровно один пропуск; отмена возвращает доступный остаток.
    r=sv.book_makeup(con,sid,quiet,'2026-11-21',None,u)
    assert sv.absence_counters(con,sid)['available']==0 and sv.absence_counters(con,sid)['booked']==1
    sv.set_makeup_status(con,r['id'],'cancelled',u)
    assert sv.absence_counters(con,sid)['available']==1
    # Арктика: альтернативный день ОФП разрешён только пакету с ОФП.
    pid=program.ensure_sbornaya(con)
    arena=schedule.save_group(con,{'name':'Сборная Арктика','branch_id':b,'weekdays':[1,6]})
    thu=schedule.save_group(con,{'name':'ОФП Чт','branch_id':b,'weekdays':[3],'time':'20:30'})
    sat=schedule.save_group(con,{'name':'ОФП Сб','branch_id':b,'weekdays':[5],'time':'17:00'})
    con.execute("UPDATE groups SET program_id=?,lesson_kind='arena' WHERE id=?",(pid,arena))
    con.execute("UPDATE groups SET program_id=?,lesson_kind='ofp' WHERE id IN (?,?)",(pid,thu,sat))
    p=sv.create_student(con,{'name':'Пётр Примеров','parent_name':'Анна','phone':'+79000000502'},u)
    program.set_plan(con,p,pid,'pkg',sat,u)
    program.check_makeup(con,p,thu,'2026-11-12')
    program.check_makeup(con,p,quiet,'2026-11-14')
    program.set_plan(con,p,pid,'arena_only',None,u)
    try:program.check_makeup(con,p,thu,'2026-11-12')
    except sv.ApiError:pass
    else:raise AssertionError('OFP allowed without package')
    print('ok: typed calendar, actual attendance/misses, available credits, quietest-first suggestions, capacity, booking/cancel, family message, Arctic OFP policy')


if __name__=='__main__':main()
