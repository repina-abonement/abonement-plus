"""ERP presentation, personal chat pins and the new Nebo morning group's dated contract."""
import tempfile,json
from pathlib import Path
from datetime import date
from unittest.mock import patch
from crm import db,services as sv,comms,schedule,groupterms,historyview,smart


def main():
    con=db.connect(str(Path(tempfile.mkdtemp())/'features.sqlite3'));db.init_db(con);smart.REQUIRE=False
    raw='ERP, задача 123\n№: 456\nДата события: 03.09.2026 17:31\nДедлайн: 04.09.2026 00:00\nЗадача (END): Думают\nЗадача (NEW): Думают\nСобытие: Перенесение дедлайна\nАвтор: Варвара\nИсполнитель: Оператор\nID: 123\nГруппа: Старая группа\nКомментарий: Покупка абонемента #999'
    event=historyview.present({'text':raw,'user_name':'ERP'})
    assert event['display_text']=='Перенесён срок задачи «Думают» — 04.09.2026\nПокупка абонемента'
    assert event['technical_text']==raw and event['display_author']=='Варвара'
    assert historyview.present({'text':'Обычная заметка'})=={'text':'Обычная заметка'}
    def user(login):return {'id':con.execute("INSERT INTO users(login,name,role,pw_hash) VALUES (?,?,'operator','disabled')",(login,login)).lastrowid,'role':'operator','name':login}
    a=user('one');b=user('two')
    old=con.execute("INSERT INTO conversations(channel,external_id,title,created_at,last_at) VALUES ('telegram','old','Старый','2026-01-01','2026-01-01')").lastrowid
    new=con.execute("INSERT INTO conversations(channel,external_id,title,created_at,last_at) VALUES ('telegram','new','Новый','2026-11-16','2026-11-16')").lastrowid
    comms.pin_conversation(con,old,a,True);comms.pin_conversation(con,old,a,True)
    assert comms.inbox(con,user=a)['conversations'][0]['id']==old
    assert comms.inbox(con,user=b)['conversations'][0]['id']==new
    assert not any(c['id']==old for c in comms.inbox(con,q='Новый',user=a)['conversations'])
    comms.pin_conversation(con,old,b,False)
    assert comms.inbox(con,user=a)['conversations'][0]['pinned']
    comms.pin_conversation(con,old,a,False)
    assert comms.inbox(con,user=a)['conversations'][0]['id']==new
    branch=schedule.save_branch(con,{'name':'ТЦ «Небо»'})
    gid=schedule.save_group(con,{'name':'Пн/Ср 10:00','branch_id':branch,'weekdays':[0,2],'time':'10:00'})
    duplicate=schedule.save_group(con,{'name':'Другая','branch_id':branch,'weekdays':[0,2],'time':'10:00'})
    groupterms.provision_nebo_morning(con);assert not groupterms.terms(con,gid,'2026-11')
    con.execute('UPDATE groups SET active=0 WHERE id=?',(duplicate,));groupterms.provision_nebo_morning(con)
    g=con.execute('SELECT * FROM groups WHERE id=?',(gid,)).fetchone()
    assert schedule.month_dates(con,g,'','2026-11')==[date(2026,11,d) for d in (16,18,23,25,30)]
    assert schedule.on(con,g,'2026-11-16')['time']=='10:00'
    assert schedule.on(con,g,'2026-11-18')['time']=='10:30'
    assert not schedule.on(con,g,'2026-11-02')['weekdays']
    sid=sv.create_student(con,{'name':'Тестовый Ребёнок','parent_name':'Тест','phone':'+79000000777','funnel':'members','group_id':gid},a)
    eid=con.execute('SELECT id FROM enrollments WHERE student_id=? AND group_id=?',(sid,gid)).fetchone()[0]
    with patch('crm.services.today',return_value=date(2026,11,16)):
        q=sv.quote_for(con,sid,'2026-11',eid)
        assert q['per_lesson']==1600 and q['lessons_paid']==5 and q['base']==8000,q
        from crm import historyentry
        historical=historyentry.options(con,sid,gid,'2026-11',paid_date='2026-11-16')
        assert historical['components'][0]['rate']==1600,historical
        assert len(historical['components'][0]['dates'])==5,historical
        subid=con.execute("INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at) VALUES (?,?,'2026-11','fixed',5,1600,7777,'paid','2026-11-16')",(sid,eid)).lastrowid
        before=tuple(con.execute('SELECT * FROM subscriptions WHERE id=?',(subid,)).fetchone())
        for day in (16,18,23):con.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES (?,?,?,'absent')",(sid,gid,f'2026-11-{day}'))
        q=sv.quote_for(con,sid,'2026-12',eid,credit_requested=True)
        assert q['per_lesson']==1600 and q['lessons_paid']==9 and q['base']==14400 and q['credit_lessons']==2 and q['credit']==3200,q
        groupterms.provision_nebo_morning(con)
        assert tuple(con.execute('SELECT * FROM subscriptions WHERE id=?',(subid,)).fetchone())==before
    print('PASS human ERP summary/raw source, personal pins/filter/order/idempotence; unique group guard, Nov16 start/Nov18 time, rate1600*dates, cap2, saved invoice preserved')

if __name__=='__main__':main()
