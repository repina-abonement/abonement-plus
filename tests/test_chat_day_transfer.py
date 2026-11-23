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
                (sid,'manual','После ПУ в 10:00 уточнить у тренера, пришёл ли Михаил','Старый вопрос','2026-11-22T12:00','open','erp','2026-11-21T12:00'))
    payload={'events':[
        {'source_id':'sz:22809:trial-v2','source_chat':'Служба заботы','source_at':'2026-11-22T10:32:29','student_name':'Ворожцов Миша','event_kind':'trial_result','event_date':'2026-11-22','came':True,'note':'Пробное: пришёл и занимался'},
        {'source_id':'sz:22809:single','source_chat':'Служба заботы','source_at':'2026-11-22T10:32:29','student_name':'Ворожцов Миша','event_kind':'single_sale','event_date':'2026-11-28','lesson_time':'10:00','amount':2500,'payment_method':'card_branch','paid_at':'2026-11-22T10:32:29','note':'Оплачено разовое занятие 28.11 в 10:00, 2500 ₽ картой'},
    ]}
    first=chatdaytransfer.apply(con,payload,user)
    assert len(first['applied'])==2 and not first['errors']
    assert con.execute('SELECT trial_came FROM students WHERE id=?',(sid,)).fetchone()[0]==1
    assert con.execute("SELECT status FROM tasks WHERE student_id=? AND kind='manual'",(sid,)).fetchone()[0]=='cancelled'
    visit=con.execute("SELECT subscription_id FROM single_visits WHERE student_id=? AND date='2026-11-28'",(sid,)).fetchone()
    assert visit and con.execute('SELECT status FROM subscriptions WHERE id=?',(visit[0],)).fetchone()[0]=='paid'
    assert con.execute('SELECT COUNT(*) FROM payments WHERE student_id=?',(sid,)).fetchone()[0]==1
    second=chatdaytransfer.apply(con,payload,user)
    assert len(second['already'])==2
    assert con.execute('SELECT COUNT(*) FROM payments WHERE student_id=?',(sid,)).fetchone()[0]==1
    con.execute("INSERT INTO tasks(student_id,kind,title,due_at,status,source,created_at) VALUES (?,?,?,?,?,?,?)",
                (sid,'manual','Напомнить о разовом занятии 28.11 в 10:00','2026-11-28T23:59','open','erp','2026-11-23T12:00'))
    chatdaytransfer.init(con)
    assert con.execute("SELECT status FROM tasks WHERE student_id=? AND title LIKE 'Напомнить о разовом%'",(sid,)).fetchone()[0]=='cancelled'


def test_existing_trial_booking_removes_imported_duplicate_and_site_priority():
    con=db.connect(str(Path(tempfile.mkdtemp())/'gunin.sqlite3'));db.init_db(con)
    user={'id':1,'role':'admin','name':'Руководитель'}
    sid=services.create_student(con,{'name':'Гунин Егор','birth':'2018-06-01','parent_name':'Светлана','phone':'+7 926 321-25-80'},user)
    con.execute("UPDATE students SET trial_at='2026-11-28T11:00',priority=1 WHERE id=?",(sid,))
    event={'source_id':'sz:22823','source_chat':'Служба заботы','source_at':'2026-11-22T11:41:54','student_name':'Гунин Егор','phone':'+7 926 321-25-80','event_kind':'trial_booking','event_date':'2026-11-28','note':'Записан на пробное 28.11 в 11:00','task_title':'Подтвердить пробное 28.11 в 11:00','task_due':'2026-11-28T09:00'}
    first=chatdaytransfer.apply(con,{'events':[event]},user)
    assert len(first['applied'])==1 and not first['errors']
    assert con.execute('SELECT priority FROM students WHERE id=?',(sid,)).fetchone()[0]==0
    assert not con.execute("SELECT 1 FROM tasks WHERE student_id=? AND auto_key='chat-day:sz:22823' AND status='open'",(sid,)).fetchone()


def test_privalov_known_later_decision_sets_stage_and_one_clear_task():
    con=db.connect(str(Path(tempfile.mkdtemp())/'privalov.sqlite3'));db.init_db(con)
    user={'id':1,'role':'admin','name':'Руководитель'}
    branch=schedule.save_branch(con,{'name':'ТЦ «Небо»'})
    group=schedule.save_group(con,{'name':'Небо Вс 17:45','branch_id':branch,'weekdays':[6],'time':'17:45','age':'4–5'})
    sid=services.create_student(con,{'name':'Привалов Даниил','birth':'2022-01-01','parent_name':'Мама','phone':'+7 900 222-33-44'},user)
    con.execute("UPDATE students SET group_id=?,trial_group_id=?,trial_at='2026-11-22T17:45' WHERE id=?",(group,group,sid))
    con.execute("""INSERT INTO tasks(student_id,kind,title,due_at,status,source,auto_key,created_at)
        VALUES (?,?,?,?,?,?,?,?)""",(sid,'why_not_bought','Почему не купили','2026-11-23T23:59','open','erp','erp:Почему не купили:Привалов','2026-11-22T20:40'))
    event={'source_id':'sz:22891:привалов-даня','source_chat':'Служба заботы','source_at':'2026-11-22T20:32:01','student_name':'Привалов Даня','event_kind':'trial_result','event_date':'2026-11-22','came':True,'note':'Пробное 22.11 в 17:45: мама изначально не планировала, пусть подрастёт, занятий хватает в секции в саду','task_title':'Уточнить решение семьи после пробного','task_due':'2026-12-01T12:00'}
    result=chatdaytransfer.apply(con,{'events':[event]},user)
    assert len(result['applied'])==1 and not result['errors']
    student=con.execute('SELECT stage,trial_came,refusal_reason FROM students WHERE id=?',(sid,)).fetchone()
    assert (student['stage'],student['trial_came'])==('later',1)
    assert 'занимается в саду' in student['refusal_reason']
    open_tasks=con.execute("SELECT kind,title,due_at FROM tasks WHERE student_id=? AND status='open'",(sid,)).fetchall()
    assert len(open_tasks)==1 and open_tasks[0]['kind']=='rebook'
    assert open_tasks[0]['title']=='Пригласить снова на ПУ: ребёнок подрос'
    assert open_tasks[0]['due_at'][:10]=='2027-02-21'
    closed=con.execute("SELECT status,result FROM tasks WHERE student_id=? AND title='Почему не купили'",(sid,)).fetchone()
    assert closed['status']=='cancelled' and 'Решение семьи уже известно' in closed['result']


def test_markov_egorov_and_khvostov_receive_their_real_followup_chains():
    con=db.connect(str(Path(tempfile.mkdtemp())/'followups.sqlite3'));db.init_db(con)
    user={'id':1,'role':'admin','name':'Руководитель'}
    branch=schedule.save_branch(con,{'name':'ТЦ «Небо»'})
    group=schedule.save_group(con,{'name':'Небо Вс','branch_id':branch,'weekdays':[6],'time':'16:45','age':'2–4'})
    cases=[
        ('Марков Тимофей','2023-04-01','2026-11-22T16:45',True,'Решили подождать до весны, в апреле исполнится 3','later','2027-04-01','Связаться с семьёй весной и предложить подходящую группу'),
        ('Егоров Артур','2023-10-17','2026-11-22T16:45',False,'Не смог зайти, перезаписаться и пробовать пока не готовы','later','2027-02-21','Пригласить снова на ПУ: ребёнок будет готов'),
        ('Хвостов Александр','2024-08-13','2026-11-23T10:30',False,'ПУ не состоялось, ребёнок не смог отойти от родителей','reinvite','2026-11-30','Предложить адаптационное повторное ПУ с родителем'),
    ]
    for index,(name,birth,trial,came,note,outcome,due,title) in enumerate(cases,1):
        sid=services.create_student(con,{'name':name,'birth':birth,'parent_name':'Родитель','phone':f'+7 900 000-10-0{index}'},user)
        con.execute('UPDATE students SET group_id=?,trial_group_id=?,trial_at=? WHERE id=?',(group,group,trial,sid))
        event={'source_id':f'sz:case:{index}','source_chat':'Служба заботы','source_at':trial[:10]+'T20:00:00','student_name':name,'event_kind':'trial_result','event_date':trial[:10],'came':came,'outcome':outcome,'note':note}
        if outcome=='reinvite':event|={'followup_due':'2026-11-30T11:00','followup_title':'Предложить адаптационное повторное ПУ с родителем'}
        result=chatdaytransfer.apply(con,{'events':[event]},user)
        assert len(result['applied'])==1 and not result['errors']
        student=con.execute('SELECT stage,trial_came FROM students WHERE id=?',(sid,)).fetchone()
        assert student['stage']==('reinvite_pu' if outcome=='reinvite' else 'later') and student['trial_came']==int(came)
        task=con.execute("SELECT title,due_at FROM tasks WHERE student_id=? AND status='open' AND kind='rebook'",(sid,)).fetchone()
        assert task and task['due_at'][:10]==due and task['title']==title


def test_confirmed_paid_sale_does_not_leave_manual_renewal_duplicate():
    con=db.connect(str(Path(tempfile.mkdtemp())/'sale.sqlite3'));db.init_db(con)
    user={'id':1,'role':'admin','name':'Руководитель'}
    branch=schedule.save_branch(con,{'name':'ТЦ «Небо»'})
    group=schedule.save_group(con,{'name':'Небо Ср Вс','branch_id':branch,'weekdays':[2,6],'time':'16:45','age':'4–5'})
    sid=services.create_student(con,{'name':'Микаладзе Леон','birth':'2022-01-01','parent_name':'Мама','phone':'+7 900 555-44-33'},user)
    con.execute("UPDATE students SET group_id=?,trial_group_id=?,trial_at='2026-11-22T16:45' WHERE id=?",(group,group,sid))
    for month in ('2026-11','2026-12'):
        con.execute("""INSERT INTO subscriptions(student_id,month,tariff,lessons,per_lesson,price,status,created_at,paid_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",(sid,month,'flex',6,1000,6000,'paid','2026-11-22T20:15','2026-11-22T20:15'))
    event={'source_id':'sz:22891:микаладзе-леон','source_chat':'Служба заботы','source_at':'2026-11-22T20:32:01','student_name':'Микаладзе Леон','event_kind':'trial_result','event_date':'2026-11-22','came':True,'outcome':'purchased','subscription_months':['2026-11','2026-12'],'note':'ПРОДАЖА НОЯ+ДЕК, группа ср/вс 16:45','task_title':'Проверить продление после декабря','task_due':'2026-12-20T12:00'}
    result=chatdaytransfer.apply(con,{'events':[event]},user)
    assert len(result['applied'])==1 and not result['errors']
    assert not con.execute("SELECT 1 FROM tasks WHERE student_id=? AND auto_key='chat-day:sz:22891:микаладзе-леон' AND status='open'",(sid,)).fetchone()


def test_confirmed_care_chat_sale_creates_two_subscriptions_and_one_payment():
    con=db.connect(str(Path(tempfile.mkdtemp())/'masterdeev.sqlite3'));db.init_db(con)
    user={'id':1,'role':'admin','name':'Руководитель'}
    branch=schedule.save_branch(con,{'name':'ТЦ «Небо»'})
    group=schedule.save_group(con,{'name':'Небо Ср Вс 17:45','branch_id':branch,'weekdays':[2,6],'time':'17:45','age':'4–5'})
    sid=services.create_student(con,{'name':'Мастердеев Денис','birth':'2022-03-04','parent_name':'Наталья','phone':'+7 985 952-37-65'},user)
    con.execute("UPDATE students SET group_id=?,trial_group_id=?,trial_at='2026-11-22T17:45',trial_came=1 WHERE id=?",(group,group,sid))
    event={'source_id':'sz:22890:subscription-sale','source_chat':'Служба заботы','source_at':'2026-11-22T20:30:00',
        'student_name':'Мастердеев Денис','event_kind':'subscription_sale','sale_date':'2026-11-22','paid_at':'2026-11-22T20:30:00',
        'lesson_time':'17:45','weekdays':[2,6],'tariff':'fixed','discount_percent':20,'amount':11880,'payment_method':'card_branch',
        'components':[
            {'month':'2026-11','price':2160,'dates':['2026-11-25','2026-11-29'],'dates_reason':'Подтверждено СЗ'},
            {'month':'2026-12','price':9720,'dates':['2026-12-02','2026-12-06','2026-12-09','2026-12-13','2026-12-16','2026-12-20','2026-12-23','2026-12-27','2026-12-30'],'dates_reason':'Подтверждено СЗ'}],
        'note':'Оплачен абонемент за ноябрь и декабрь, группа Ср/Вс 17:45, 11880 ₽ картой в филиале'}
    first=chatdaytransfer.apply(con,{'events':[event]},user)
    assert len(first['applied'])==1 and not first['errors']
    subs=con.execute("SELECT month,price,status,lessons FROM subscriptions WHERE student_id=? ORDER BY month",(sid,)).fetchall()
    assert [(x['month'],x['price'],x['status'],x['lessons']) for x in subs]==[
        ('2026-11',2160,'paid',2),('2026-12',9720,'paid',9)]
    payment=con.execute("SELECT amount,method FROM payments WHERE student_id=? AND cancelled=0",(sid,)).fetchall()
    assert [(x['amount'],x['method']) for x in payment]==[(11880,'card_branch')]
    assert con.execute("SELECT COUNT(*) FROM invoice_payment_targets WHERE payment_id=(SELECT id FROM payments WHERE student_id=?)",(sid,)).fetchone()[0]==2
    second=chatdaytransfer.apply(con,{'events':[event]},user)
    assert len(second['already'])==1
    assert con.execute("SELECT COUNT(*) FROM subscriptions WHERE student_id=?",(sid,)).fetchone()[0]==2
    assert con.execute("SELECT COUNT(*) FROM payments WHERE student_id=?",(sid,)).fetchone()[0]==1

