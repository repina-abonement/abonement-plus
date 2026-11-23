import tempfile
from pathlib import Path

from crm import chatdaytransfer, db, schedule, services


def test_chat_day_transfer_is_idempotent_and_never_guesses_duplicate_names():
    con=db.connect(str(Path(tempfile.mkdtemp())/'chat-day.sqlite3'));db.init_db(con)
    user={'id':1,'role':'admin','name':'Руководитель'}
    sid=services.create_student(con,{'name':'Ворожцов Михаил','parent_name':'Анна','phone':'+7 900 111-22-33'},user)
    services.create_student(con,{'name':'Иванов Иван','parent_name':'Ольга','phone':'+7 900 000-00-01'},user)
    services.create_student(con,{'name':'Иванов Иван','parent_name':'Мария','phone':'+7 900 000-00-02'},user)
    payload={'events':[{'source_id':'sz:1','source_chat':'СЗ','source_at':'2026-11-22T10:32:29','student_name':'Ворожцов Миша','event_kind':'note','note':'Записан на разовое занятие 28.11 в 10:00','task_title':'Напомнить о разовом занятии','task_due':'2026-11-27T12:00'}]}
    first=chatdaytransfer.apply(con,payload,user)
    assert first['applied'][0]['student_id']==sid
    assert len(con.execute("SELECT * FROM history WHERE student_id=? AND kind='chat_transfer'",(sid,)).fetchall())==1
    assert con.execute("SELECT title FROM tasks WHERE auto_key='chat-day:sz:1'").fetchone()[0]=='Напомнить о разовом занятии'
    second=chatdaytransfer.apply(con,payload,user)
    assert len(second['already'])==1 and not second['applied']
    ambiguous=chatdaytransfer.apply(con,{'events':[{'source_id':'sz:2','source_at':'2026-11-22T12:00:00','student_name':'Иванов Иван','note':'Факт'}]},user)
    assert ambiguous['ambiguous'] and not ambiguous['applied']


def test_confirmed_trial_and_paid_single_are_linked_without_duplicates():
    con=db.connect(str(Path(tempfile.mkdtemp())/'vorozhtsov.sqlite3'));db.init_db(con)
    user={'id':1,'role':'admin','name':'Руководитель'}
    branch=schedule.save_branch(con,{'name':'ТЦ «Небо»'})
    group=schedule.save_group(con,{'name':'Небо Сб 10:00','branch_id':branch,'weekdays':[5],'time':'10:00','age':'3–4'})
    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('price_single','2500')")
    sid=services.create_student(con,{'name':'Ворожцов Михаил','birth':'2024-07-01','parent_name':'Мария','phone':'+7 991 701-69-38','trial_group_id':group,'trial_at':'2026-11-22T10:00'},user)
    con.execute('UPDATE students SET trial_group_id=?,trial_at=? WHERE id=?',(group,'2026-11-22T10:00',sid))
    con.execute("INSERT INTO tasks(student_id,kind,title,note,due_at,status,source,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (sid,'manual','После ПУ в 10:00 уточнить у тренера, пришёл ли Михаил','Старый вопрос','2026-11-22T12:00','open','manual','2026-11-21T12:00'))
    payload={'events':[
        {'source_id':'sz:22809:trial-v2','source_chat':'Служба заботы','source_at':'2026-11-22T10:32:29','student_name':'Ворожцов Миша','event_kind':'trial_result','event_date':'2026-11-22','came':True,'note':'Пробное: пришёл и занимался'},
        {'source_id':'sz:22809:single','source_chat':'Служба заботы','source_at':'2026-11-22T10:32:29','student_name':'Ворожцов Миша','event_kind':'single_sale','event_date':'2026-11-28','lesson_time':'10:00','amount':2500,'payment_method':'card_branch','paid_at':'2026-11-22T10:32:29','note':'Оплачено разовое занятие 28.11 в 10:00, 2500 ₽ картой'},
    ]}
    first=chatdaytransfer.apply(con,payload,user)
    assert len(first['applied'])==2 and not first['errors']
    assert con.execute('SELECT trial_came FROM students WHERE id=?',(sid,)).fetchone()[0]==1
    assert con.execute("SELECT status FROM tasks WHERE student_id=? AND source='manual'",(sid,)).fetchone()[0]=='cancelled'
    visit=con.execute("SELECT subscription_id FROM single_visits WHERE student_id=? AND date='2026-11-28'",(sid,)).fetchone()
    assert visit and con.execute('SELECT status FROM subscriptions WHERE id=?',(visit[0],)).fetchone()[0]=='paid'
    assert con.execute('SELECT COUNT(*) FROM payments WHERE student_id=?',(sid,)).fetchone()[0]==1
    second=chatdaytransfer.apply(con,payload,user)
    assert len(second['already'])==2
    assert con.execute('SELECT COUNT(*) FROM payments WHERE student_id=?',(sid,)).fetchone()[0]==1

