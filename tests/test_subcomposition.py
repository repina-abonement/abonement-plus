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
    # Individual seven-date fixture is synthetic, NOT retrieved Ivan ERP dates.
    for total,arena_amount,ofp_amount,arena_days,ofp_days in (
        (14640,10800,3840,q['components'][0]['dates'],q['components'][1]['dates']),
        (12600,9000,3600,q['components'][0]['dates'][2:],['2026-11-14','2026-11-21','2026-11-28'])):
        con.execute('UPDATE subscriptions SET price=? WHERE id=?',(total,subid))
        parts=[{'group_id':arena,'dates':arena_days,'amount':arena_amount,'erp_subscription_id':'825986'},
               {'group_id':sat,'dates':ofp_days,'amount':ofp_amount,'erp_subscription_id':'825983'}]
        before=dict(con.execute('SELECT * FROM subscriptions WHERE id=?',(subid,)).fetchone())
        changes=con.total_changes
        checked=subcomposition.preview(con,subid,components=parts)
        assert con.total_changes==changes and checked['price']==total
        for bad in ([{**parts[0],'amount':arena_amount+1},parts[1]],
                    [{**parts[0],'dates':['2026-10-31']},parts[1]],
                    [{**parts[0],'dates':[arena_days[0],arena_days[0]]},parts[1]],
                    [{**parts[0],'erp_subscription_id':''},parts[1]]):
            try:subcomposition.preview(con,subid,components=bad);raise AssertionError('invalid accepted')
            except sv.ApiError:pass
        subcomposition.apply(con,subid,{'components':parts,'token':checked['token'],'reason':'Synthetic ERP reconciliation fixture'},user)
        after=dict(con.execute('SELECT * FROM subscriptions WHERE id=?',(subid,)).fetchone())
        for key in ('price','status','paid_at','discount_amount'):
            if key in before:assert before[key]==after[key],key
        assert [tuple(r) for r in con.execute('SELECT * FROM payments')]==payments
        assert [tuple(r) for r in con.execute('SELECT * FROM attendance')]==marks
        assert after['lessons']==len(arena_days)+len(ofp_days)
        stored=json.loads(after['lesson_schedule']);assert stored[0]['amount']==arena_amount
        assert stored[0]['erp_subscription_id']=='825986'
        with patch('crm.services.today',return_value=date(2026,11,13)):
            events=clientattendance.calendar(con,sid,'2026-11')['events']
        assert {e['date'] for e in events if e['group_id']==sat}==set(ofp_days)
        for day in ('2026-11-07','2026-11-14','2026-11-21','2026-11-28'):
            assert any(r['id']==sid for r in sv.journal(con,sat,day,True)['rows'])==(day in ofp_days)
        assert sum(day['list'] for day in sv.journal_month(con,sat,'2026-11')['days'])==len(ofp_days)
        if total==12600:
            try:subcomposition.unit_rate(stored[0]);raise AssertionError('invented rounded date price')
            except sv.ApiError:pass
    con.execute("INSERT INTO enrollments(student_id,group_id,tariff,active,created_at,start_date) VALUES (?,?,'included',1,'2026-11-14','2026-11-14')",(sid,sat))
    assert sum(r['id']==sid for r in sv.journal(con,sat,'2026-11-14',True)['rows'])==1
    # An explicit off-schedule date is still planned in calendar/day/month journals.
    parts[1]['dates']=['2026-11-13','2026-11-21','2026-11-28']
    checked=subcomposition.preview(con,subid,components=parts)
    subcomposition.apply(con,subid,{'components':parts,'token':checked['token'],'reason':'Confirmed off-schedule fixture'},user)
    assert any(r['id']==sid for r in sv.journal(con,sat,'2026-11-13',True)['rows'])
    assert any(day['date']=='2026-11-13' and day['list']==1 for day in sv.journal_month(con,sat,'2026-11')['days'])
    print('PASS 16→13; 9 arena + 4 Saturday OFP 17:00; no Thursdays, invented marks or finance changes; preview/stale guard/month bounds')

if __name__=='__main__':main()
