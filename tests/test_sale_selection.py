"""Выбор новой группы/тарифа: предпросмотр без изменений, атомарное оформление."""
import json
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch
from crm import db, services as sv, program, schedule, smart
from crm.services import ApiError


def main():
    con = db.connect(str(Path(tempfile.mkdtemp())/'sale.sqlite3'))
    db.init_db(con)
    smart.REQUIRE = False
    user={'id':1,'role':'admin','name':'Пример'}
    sv.today=lambda:date(2026,11,10)
    br=schedule.save_branch(con,{'name':'Пример'})
    old=schedule.save_group(con,{'name':'Пример Вт/Пт','branch_id':br,'weekdays':[1,4]})
    new=schedule.save_group(con,{'name':'Пример Сб','branch_id':br,'weekdays':[5]})
    def kid(phone):
        sid=sv.create_student(con,{'name':'Иван Примеров','parent_name':'Анна','phone':phone,'funnel':'members','group_id':old,'tariff':'fixed'},user)
        return sid,sv.enrollments_of(con,sid)[0]['id']
    sid,eid=kid('+79000000101')
    prior=con.execute("""INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at)
        VALUES (?,?,'2026-11','fixed',8,1350,10800,'paid','2026-10-20')""",(sid,eid)).lastrowid
    before=con.total_changes
    q=sv.quote_for(con,sid,'2026-12',eid,tariff='flex',group_id=new)
    assert q['tariff']=='flex' and q['lessons_paid']==4 and q['total']==6200
    assert con.total_changes==before and len(sv.enrollments_of(con,sid))==1
    with patch.object(sv,'_create_invoice',side_effect=ApiError('Проверка отката')):
        try:sv.create_invoice(con,sid,'2026-12',user,eid,tariff='flex',group_id=new)
        except ApiError:pass
    assert len(sv.enrollments_of(con,sid))==1 and sv.enrollments_of(con,sid)[0]['id']==eid
    inv=sv.create_invoice(con,sid,'2026-12',user,eid,tariff='flex',group_id=new)
    current=sv.enrollments_of(con,sid)
    assert len(current)==1 and current[0]['group_id']==new and current[0]['start_date']=='2026-12-01'
    assert con.execute('SELECT end_date FROM enrollments WHERE id=?',(eid,)).fetchone()[0]=='2026-12-01'
    assert tuple(con.execute('SELECT enrollment_id,price FROM subscriptions WHERE id=?',(prior,)).fetchone())==(eid,10800)
    sold=con.execute('SELECT * FROM subscriptions WHERE id=?',(inv['id'],)).fetchone()
    assert sold['tariff']=='flex' and sold['lessons']==4 and sold['enrollment_id']==current[0]['id']
    sid2,eid2=kid('+79000000102')
    sv.create_invoice(con,sid2,'2026-12',user,eid2,group_id=new,group_action='add')
    assert len(sv.enrollments_of(con,sid2))==2

    pid=program.ensure_sbornaya(con)
    arena=schedule.save_group(con,{'name':'Сборная Арктика Вт/Вс','branch_id':br,'weekdays':[1,6]})
    thu=schedule.save_group(con,{'name':'ОФП Чт','branch_id':br,'weekdays':[3]})
    sat=schedule.save_group(con,{'name':'ОФП Сб','branch_id':br,'weekdays':[5]})
    con.execute("UPDATE groups SET program_id=?,lesson_kind='arena' WHERE id=?",(pid,arena))
    con.execute("UPDATE groups SET program_id=?,lesson_kind='ofp' WHERE id IN (?,?)",(pid,thu,sat))
    sid3=sv.create_student(con,{'name':'Пётр Примеров','parent_name':'Анна','phone':'+79000000103','funnel':'members'},user)
    program.set_plan(con,sid3,pid,'arena_only',None,user)
    ae=sv.enrollments_of(con,sid3)[0]['id']
    con.execute("""INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at)
        VALUES (?,?,'2026-11','arena_only',8,1700,13600,'paid','2026-10-20')""",(sid3,ae))
    p0=program.plan_of(con,sid3,pid)
    q=sv.quote_for(con,sid3,'2026-12',ae,tariff='pkg',ofp_group_id=sat)
    assert q['tariff']=='pkg' and q['program']['ofp_group_id']==sat and q['total']==19200
    assert program.plan_of(con,sid3,pid)==p0
    try:sv.quote_for(con,sid3,'2026-11',ae,tariff='pkg',ofp_group_id=sat)
    except ApiError:pass
    else:raise AssertionError('Current-month plan change allowed')
    sale=sv.create_invoice(con,sid3,'2026-12',user,ae,tariff='pkg',ofp_group_id=sat)
    assert program.plan_of(con,sid3,pid)['next']['ofp_group_id']==sat
    snapshot=json.loads(con.execute('SELECT program_selection FROM subscriptions WHERE id=?',(sale['id'],)).fetchone()[0])
    assert snapshot['tariff']=='pkg' and snapshot['ofp_group_id']==sat and snapshot['contract']['lessons']==13
    con.execute("UPDATE groups SET age='8+' WHERE id=?", (arena,))
    con.execute("UPDATE students SET birth='2021-01-01' WHERE id=?", (sid3,))
    assert not any(e['age_warn'] for e in sv.student_detail(con,sid3)['enrollments'])
    program.init(con)
    assert con.execute('SELECT age FROM groups WHERE id=?', (arena,)).fetchone()[0] == '' 
    program.set_plan(con,sid3,pid,'arena_only',None,user)
    program.reprice(con,sale['id'],on=date(2026,11,27))
    x=con.execute('SELECT price,lessons FROM subscriptions WHERE id=?',(sale['id'],)).fetchone()
    assert tuple(x)==(20160,13),tuple(x)
    print('ok: other group, no preview mutation, atomic rollback, dated transfer/add, historical invoice, Arctic tariff/OFP, sold selection snapshot')


if __name__=='__main__':main()
