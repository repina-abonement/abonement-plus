"""Лукерья: восстановить 9 + 4 занятия без изменения финансов и фактов явки."""
import tempfile,json
from pathlib import Path
from datetime import date
from unittest.mock import patch
from crm import db,services as sv,schedule,program,money,subcomposition,clientattendance,smart


def main():
    con=db.connect(str(Path(tempfile.mkdtemp())/'test.sqlite3'));db.init_db(con);smart.REQUIRE=False
    user={'id':1,'name':'Администратор','role':'admin'}
    ar=schedule.save_branch(con,{'name':'Арктика'});nb=schedule.save_branch(con,{'name':'Небо'})
    pid=program.ensure_sbornaya(con)
    arena=schedule.save_group(con,{'name':'Сборная Арктика','branch_id':ar,'weekdays':[1,6],'time':'18:30'})
    sat=schedule.save_group(con,{'name':'ОФП Сб','branch_id':nb,'weekdays':[5],'time':'17:00'})
    thu=schedule.save_group(con,{'name':'ОФП Чт','branch_id':nb,'weekdays':[3],'time':'20:30'})
    con.execute("UPDATE groups SET program_id=?,lesson_kind='arena' WHERE id=?",(pid,arena))
    con.execute("UPDATE groups SET program_id=?,lesson_kind='ofp' WHERE id IN (?,?)",(pid,sat,thu))
    sid=sv.create_student(con,{'name':'Тестовый Ребёнок','parent_name':'Анна','phone':'+79000001234','funnel':'members','group_id':arena},user)
    eid=con.execute('SELECT id FROM enrollments WHERE student_id=? AND group_id=?',(sid,arena)).fetchone()[0]
    con.execute("INSERT INTO enrollments(student_id,group_id,tariff,active,created_at) VALUES (?,?,'included',1,'2026-10-01')",(sid,thu))
    subid=con.execute("INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,price,per_lesson,status,created_at,ledger_managed) VALUES (?,?,'2026-11','fixed',16,18300,1143,'invoiced','2026-10-25',1)",(sid,eid)).lastrowid
    money.add_payment(con,sid,18300,'cash',user,paid_at='2026-10-25')
    con.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES (?,?,'2026-11-10','present')",(sid,arena))
    payments=[tuple(r) for r in con.execute('SELECT * FROM payments')];marks=[tuple(r) for r in con.execute('SELECT * FROM attendance')]
    before=con.total_changes;q=subcomposition.preview(con,subid,sat)
    assert con.total_changes==before
    assert q['lessons']==13 and q['price']==18300
    assert [len(c['dates']) for c in q['components']]==[9,4]
    assert q['components'][1]['dates']==['2026-11-07','2026-11-14','2026-11-21','2026-11-28']
    try:subcomposition.apply(con,subid,{'ofp_group_id':sat,'token':'stale','reason':'ERP 828909, 828908'},user);raise AssertionError('stale accepted')
    except sv.ApiError:pass
    subcomposition.apply(con,subid,{'ofp_group_id':sat,'token':q['token'],'reason':'ERP 828909 и 828908, сверены даты'},user)
    row=con.execute('SELECT * FROM subscriptions WHERE id=?',(subid,)).fetchone()
    assert row['lessons']==13 and row['price']==18300 and row['status']=='paid' and row['tariff']=='pkg'
    assert [tuple(r) for r in con.execute('SELECT * FROM payments')]==payments
    assert [tuple(r) for r in con.execute('SELECT * FROM attendance')]==marks
    with patch('crm.services.today',return_value=date(2026,11,13)):
        events=clientattendance.calendar(con,sid,'2026-11')['events']
    assert len(events)==13,events
    ofp=[e for e in events if e['group_id']==sat]
    assert len(ofp)==4 and all(e['time']=='17:00' and e['status']=='planned' for e in ofp)
    assert not any(e['group_id']==thu for e in events)
    assert any(e['id']==sid for e in sv.journal(con,sat,'2026-11-07',True)['rows'])
    assert not any(e['id']==sid for e in sv.journal(con,sat,'2026-12-05',True)['rows'])
    with patch('crm.program.date',wraps=date) as clock:
        clock.today.return_value=date(2026,11,13)
        program.check_makeup(con,sid,thu,'2026-11-19')
        try:program.check_makeup(con,sid,sat,'2026-11-21');raise AssertionError('regular OFP offered as makeup')
        except sv.ApiError:pass
    review=con.execute('SELECT before_json,after_json FROM subscription_composition_reviews WHERE subscription_id=?',(subid,)).fetchone()
    assert json.loads(review[0])['lessons']==16 and json.loads(review[1])['lessons']==13
    assert not program.reprice(con,subid)
    print('PASS 16→13; 9 arena + 4 Saturday OFP 17:00; no Thursdays, invented marks or finance changes; preview/stale guard/month bounds')

if __name__=='__main__':main()
