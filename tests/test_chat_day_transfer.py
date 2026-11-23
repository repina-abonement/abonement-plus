import json
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
    rebook=[task for task in open_tasks if task['kind']=='rebook']
    assert len(rebook)==1
    assert rebook[0]['title']=='Пригласить снова на ПУ: ребёнок подрос'
    assert rebook[0]['due_at'][:10]=='2027-02-21'
    closed=con.execute("SELECT status,result FROM tasks WHERE student_id=? AND title='Почему не купили'",(sid,)).fetchone()
    assert closed['status']=='cancelled' and 'Решение семьи уже известно' in closed['result']


def test_markov_egorov_and_khvostov_receive_their_real_followup_chains():
    con=db.connect(str(Path(tempfile.mkdtemp())/'followups.sqlite3'));db.init_db(con)
    user={'id':1,'role':'admin','name':'Руководитель'}
    branch=schedule.save_branch(con,{'name':'ТЦ «Небо»'})
    group=schedule.save_group(con,{'name':'Небо Вс','branch_id':branch,'weekdays':[6],'time':'16:45','age':'2–4'})
    cases=[
        ('Марков Тимофей','2024-04-23','2026-11-22T16:45',True,'Решили подождать до весны, в апреле исполнится 3','later','2027-03-23','Связаться с семьёй весной и предложить подходящую группу'),
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
        task=con.execute("SELECT id,title,due_at FROM tasks WHERE student_id=? AND status='open' AND kind='rebook'",(sid,)).fetchone()
        assert task and task['due_at'][:10]==due and task['title']==title
        if name=='Марков Тимофей':
            con.execute("UPDATE tasks SET due_at='2027-04-01T11:00' WHERE id=?",(task['id'],))
            chatdaytransfer._reconcile_trial_outcome(con,sid,event|{'followup_due':'2027-04-01T11:00'})
            repaired=con.execute('SELECT due_at FROM tasks WHERE id=?',(task['id'],)).fetchone()
            assert repaired['due_at'][:10]=='2027-03-23'


def test_markov_missing_card_is_created_from_verified_care_identity():
    con=db.connect(str(Path(tempfile.mkdtemp())/'markov-create.sqlite3'));db.init_db(con)
    user={'id':1,'role':'admin','name':'Руководитель'}
    event={'source_id':'sz:22891:марков-тимофей','source_chat':'Служба заботы','source_at':'2026-11-22T20:32:01',
        'student_name':'Марков Тимофей','birth':'2024-04-23','parent_name':'Вера','phone':'+7 926 177-54-74',
        'create_if_missing':True,'event_kind':'trial_result','event_date':'2026-11-22','trial_time':'16:45','came':True,
        'outcome':'later','note':'Решили подождать до весны: на тренировке пока не всё понимал, хотят группу с детьми постарше'}
    first=chatdaytransfer.apply(con,{'events':[event]},user)
    assert len(first['applied'])==1 and not first['errors'] and not first['unmatched']
    student=con.execute("SELECT id,birth,trial_at,trial_came,stage FROM students WHERE name='Марков Тимофей'").fetchone()
    assert student and (student['birth'],student['trial_at'],student['trial_came'],student['stage'])==('2024-04-23','2026-11-22T16:45',1,'later')
    parent=con.execute("SELECT name,phone_norm FROM parents WHERE family_id=(SELECT family_id FROM students WHERE id=?)",(student['id'],)).fetchone()
    assert parent['name']=='Вера' and parent['phone_norm'].endswith('9261775474')
    task=con.execute("SELECT title,due_at FROM tasks WHERE student_id=? AND status='open' AND kind='rebook'",(student['id'],)).fetchone()
    assert task and task['due_at'][:10]=='2027-03-23'
    assert con.execute("SELECT 1 FROM tasks WHERE student_id=? AND status='open' AND auto_key=?",(student['id'],f"ad-source-required:{student['id']}")).fetchone()
    second=chatdaytransfer.apply(con,{'events':[event]},user)
    assert len(second['already'])==1 and con.execute("SELECT COUNT(*) FROM students WHERE name='Марков Тимофей'").fetchone()[0]==1


def test_future_verified_booking_creates_one_card_but_incomplete_identity_does_not():
    con=db.connect(str(Path(tempfile.mkdtemp())/'future-create.sqlite3'));db.init_db(con)
    user={'id':1,'role':'admin','name':'Руководитель'}
    complete={'source_id':'sz:future:1','source_chat':'Служба заботы','source_at':'2026-11-23T12:00:00',
        'student_name':'Новый Ребёнок','birth':'2023-05-06','parent_name':'Анна','phone':'+7 900 123-45-67',
        'create_if_missing':True,'event_kind':'trial_booking','event_date':'2026-11-28','trial_time':'11:00',
        'note':'Записан на пробное 28.11 в 11:00'}
    incomplete=complete|{'source_id':'sz:future:2','student_name':'Другой Ребёнок','phone':'','parent_name':''}
    result=chatdaytransfer.apply(con,{'events':[complete,incomplete]},user)
    assert len(result['applied'])==1 and len(result['unmatched'])==1 and not result['errors']
    assert con.execute("SELECT COUNT(*) FROM students WHERE name='Новый Ребёнок'").fetchone()[0]==1
    assert con.execute("SELECT COUNT(*) FROM students WHERE name='Другой Ребёнок'").fetchone()[0]==0
    repeat=chatdaytransfer.apply(con,{'events':[complete]},user)
    assert len(repeat['already'])==1 and con.execute("SELECT COUNT(*) FROM students WHERE name='Новый Ребёнок'").fetchone()[0]==1


def test_create_capable_result_runs_before_other_facts_of_same_client():
    con=db.connect(str(Path(tempfile.mkdtemp())/'ordered-create.sqlite3'));db.init_db(con)
    user={'id':1,'role':'admin','name':'Руководитель'}
    note={'source_id':'sz:ordered:note','source_chat':'Служба заботы','source_at':'2026-11-22T19:00:00',
          'student_name':'Тестов Тимофей','event_kind':'note','note':'Предварительно обсудили следующий шаг'}
    result={'source_id':'sz:ordered:result','source_chat':'Служба заботы','source_at':'2026-11-22T20:00:00',
        'student_name':'Тестов Тимофей','birth':'2024-04-23','parent_name':'Вера','phone':'+7 900 111-22-33',
        'create_if_missing':True,'event_kind':'trial_result','event_date':'2026-11-22','trial_time':'16:45','came':True,
        'outcome':'later','note':'Семья решила вернуться позже'}
    applied=chatdaytransfer.apply(con,{'events':[note,result]},user)
    assert len(applied['applied'])==2 and not applied['unmatched'] and not applied['errors']
    assert con.execute("SELECT COUNT(*) FROM students WHERE name='Тестов Тимофей'").fetchone()[0]==1


def test_confirmed_paid_sale_does_not_leave_manual_renewal_duplicate():
    con=db.connect(str(Path(tempfile.mkdtemp())/'sale.sqlite3'));db.init_db(con)
    user={'id':1,'role':'admin','name':'Руководитель'}
    branch=schedule.save_branch(con,{'name':'ТЦ «Небо»'})
    group=schedule.save_group(con,{'name':'Небо Ср Вс','branch_id':branch,'weekdays':[2,6],'time':'16:45','age':'4–5'})
    sid=services.create_student(con,{'name':'Микаладзе Леон','birth':'2022-01-01','parent_name':'Анна','phone':'+7 900 555-44-33'},user)
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


def test_previously_saved_sale_is_completed_on_repeat_without_duplicates():
    con=db.connect(str(Path(tempfile.mkdtemp())/'saved-sale.sqlite3'));db.init_db(con)
    user={'id':1,'role':'admin','name':'Руководитель'}
    branch=schedule.save_branch(con,{'name':'ТЦ «Небо»'})
    group=schedule.save_group(con,{'name':'Небо Ср Вс 16:45','branch_id':branch,'weekdays':[2,6],'time':'16:45','age':'4–5'})
    sid=services.create_student(con,{'name':'Микаладзе Леон','birth':'2022-01-01','parent_name':'Анна','phone':'+7 900 555-44-33'},user)
    con.execute("UPDATE students SET group_id=?,trial_group_id=?,trial_at='2026-11-22T16:45',trial_came=1 WHERE id=?",(group,group,sid))
    event={'source_id':'sz:22889:subscription-sale','source_chat':'Служба заботы','source_at':'2026-11-22T20:15:29',
        'student_name':'Микаладзе Леон','event_kind':'subscription_sale','sale_date':'2026-11-22','paid_at':'2026-11-22T20:15:29',
        'lesson_time':'16:45','weekdays':[2,6],'tariff':'flex','discount_percent':20,'amount':9920,'payment_method':'card_branch',
        'components':[{'month':'2026-11','price':2480,'dates':['2026-11-25','2026-11-29']},
            {'month':'2026-12','price':7440,'dates':['2026-12-02','2026-12-06','2026-12-09','2026-12-13','2026-12-16','2026-12-20']}],
        'note':'Оплачен абонемент за ноябрь и декабрь, группа Ср/Вс 16:45, 9920 ₽ картой в филиале'}
    con.execute('INSERT INTO chat_day_transfer_events VALUES (?,?,?,?,?,?,?,?)',(
        event['source_id'],sid,event['source_chat'],event['source_at'],event['event_kind'],json.dumps(event,ensure_ascii=False),
        '2026-11-23T00:00','Перенос СЗ'))
    first=chatdaytransfer.apply(con,{'events':[event]},user)
    assert len(first['already'])==1 and not first['errors']
    assert [(row['month'],row['price'],row['status']) for row in con.execute(
        "SELECT month,price,status FROM subscriptions WHERE student_id=? ORDER BY month",(sid,))]==[
        ('2026-11',2480,'paid'),('2026-12',7440,'paid')]
    assert con.execute("SELECT COUNT(*) FROM payments WHERE student_id=? AND amount=9920 AND cancelled=0",(sid,)).fetchone()[0]==1
    second=chatdaytransfer.apply(con,{'events':[event]},user)
    assert len(second['already'])==1 and not second['errors']
    assert con.execute("SELECT COUNT(*) FROM subscriptions WHERE student_id=?",(sid,)).fetchone()[0]==2
    assert con.execute("SELECT COUNT(*) FROM payments WHERE student_id=?",(sid,)).fetchone()[0]==1


def test_dussek_december_cash_sale_records_net_amount_and_no_duplicate_followup():
    con=db.connect(str(Path(tempfile.mkdtemp())/'dussek.sqlite3'));db.init_db(con)
    user={'id':1,'role':'admin','name':'Руководитель'}
    branch=schedule.save_branch(con,{'name':'ТЦ «Небо»'})
    group=schedule.save_group(con,{'name':'Небо Пн Ср 10:30','branch_id':branch,'weekdays':[0,2],'time':'10:30','age':'1,5–3'})
    con.execute("INSERT INTO group_terms(group_id,valid_from,per_lesson,credit_limit) VALUES (?,'2026-11',1600,2)",(group,))
    sid=services.create_student(con,{'name':'Дюссек Михаил','birth':'2024-01-26','parent_name':'Мария','phone':'+7 910 541-64-73'},user)
    con.execute("UPDATE students SET group_id=?,trial_group_id=?,trial_at='2026-11-23T10:30' WHERE id=?",(group,group,sid))
    sale={'source_id':'sz:22930:дюссек-продажа','source_chat':'Служба заботы','source_at':'2026-11-23T11:45:01',
        'student_name':'Дюссек Михаил','event_kind':'subscription_sale','sale_date':'2026-11-23','paid_at':'2026-11-23T11:45:01',
        'lesson_time':'10:30','weekdays':[0,2],'tariff':'fixed','discount_percent':20,'amount':10240,'payment_method':'cash',
        'components':[{'month':'2026-12','price':10240,'dates':['2026-12-02','2026-12-07','2026-12-09','2026-12-14','2026-12-16','2026-12-21','2026-12-23','2026-12-28']}],
        'note':'Куплен декабрьский абонемент за 10240 ₽; получено 10500 ₽ наличными, сдача 260 ₽ возвращена клиенту'}
    outcome={'source_id':'sz:22930:дюссек-результат','source_chat':'Служба заботы','source_at':'2026-11-23T11:45:01',
        'student_name':'Дюссек Михаил','event_kind':'trial_result','event_date':'2026-11-23','came':True,'outcome':'purchased',
        'subscription_months':['2026-12'],'note':'Пришёл на ПУ и купил декабрьский абонемент'}
    result=chatdaytransfer.apply(con,{'events':[sale,outcome]},user)
    assert len(result['applied'])==2 and not result['errors']
    sub=con.execute("SELECT price,status,lessons FROM subscriptions WHERE student_id=? AND month='2026-12'",(sid,)).fetchone()
    assert (sub['price'],sub['status'],sub['lessons'])==(10240,'paid',8)
    payment=con.execute("SELECT amount,method,comment FROM payments WHERE student_id=? AND cancelled=0",(sid,)).fetchone()
    assert (payment['amount'],payment['method'])==(10240,'cash')
    assert not con.execute("SELECT 1 FROM tasks WHERE student_id=? AND status='open' AND (kind='why_not_bought' OR title='Почему не купили')",(sid,)).fetchone()
    second=chatdaytransfer.apply(con,{'events':[sale,outcome]},user)
    assert len(second['already'])==2 and not second['errors']
    assert con.execute("SELECT COUNT(*) FROM payments WHERE student_id=? AND cancelled=0",(sid,)).fetchone()[0]==1


def test_reshetnyak_two_month_sibling_sales_are_exact_and_idempotent():
    con=db.connect(str(Path(tempfile.mkdtemp())/'reshetnyak.sqlite3'));db.init_db(con)
    user={'id':1,'role':'admin','name':'Руководитель'}
    branch=schedule.save_branch(con,{'name':'ТЦ «Небо»'})
    group=schedule.save_group(con,{'name':'Небо Пн Ср 10:30','branch_id':branch,'weekdays':[0,2],'time':'10:30','age':'1,5–3'})
    con.execute("INSERT INTO group_terms(group_id,valid_from,per_lesson,credit_limit) VALUES (?,'2026-11',1600,2)",(group,))
    older=services.create_student(con,{'name':'Решетняк Арсений','birth':'2020-01-01','parent_name':'Кристина','phone':'+7 963 616-19-67','funnel':'members','group_id':group},user)
    family=con.execute('SELECT family_id FROM students WHERE id=?',(older,)).fetchone()[0]
    sid=services.create_student(con,{'name':'Решетняк Матвей','birth':'2025-03-10','family_id':family},user)
    con.execute("UPDATE students SET group_id=?,trial_group_id=?,trial_at='2026-11-16T10:00' WHERE id=?",(group,group,sid))
    common={'source_chat':'Служба заботы','student_name':'Решетняк Матвей','event_kind':'subscription_sale','weekdays':[0,2],
        'tariff':'fixed','discount_percent':20,'discount_rule':'sibling'}
    nov=common|{'source_id':'sz:21661:решетняк-ноябрь','source_at':'2026-11-16T11:07:53','sale_date':'2026-11-16','paid_at':'2026-11-16T11:07:53','amount':5120,'payment_method':'card_branch',
        'components':[{'month':'2026-11','price':5120,'dates':['2026-11-18','2026-11-23','2026-11-25','2026-11-30']}], 'note':'Ноябрь 5120 ₽, карта, скидка за второго ребёнка'}
    trial={'source_id':'sz:21661:решетняк-пу','source_chat':'Служба заботы','source_at':'2026-11-16T11:07:53','student_name':'Решетняк Матвей','event_kind':'trial_result','event_date':'2026-11-16','came':True,'outcome':'purchased','subscription_months':['2026-11'],'note':'Пришёл на ПУ и купил абонемент'}
    dec=common|{'source_id':'sz:22925:решетняк-декабрь','source_at':'2026-11-23T11:15:40','sale_date':'2026-11-23','paid_at':'2026-11-23T11:15:40','lesson_time':'10:30','amount':11520,'payment_method':'tochka_qr_link',
        'components':[{'month':'2026-12','price':11520,'dates':['2026-12-02','2026-12-07','2026-12-09','2026-12-14','2026-12-16','2026-12-21','2026-12-23','2026-12-28','2026-12-30']}], 'note':'Декабрь 11520 ₽, Точка, скидка за второго ребёнка'}
    first=chatdaytransfer.apply(con,{'events':[nov,trial,dec]},user)
    assert len(first['applied'])==3 and not first['errors']
    assert [(r['month'],r['price'],r['status'],r['lessons']) for r in con.execute("SELECT month,price,status,lessons FROM subscriptions WHERE student_id=? ORDER BY month",(sid,))]==[('2026-11',5120,'paid',4),('2026-12',11520,'paid',9)]
    assert [(r['amount'],r['method']) for r in con.execute("SELECT amount,method FROM payments WHERE student_id=? AND cancelled=0 ORDER BY paid_at",(sid,))]==[(5120,'card_branch'),(11520,'tochka_qr_link')]
    assert con.execute("SELECT trial_came FROM students WHERE id=?",(sid,)).fetchone()[0]==1
    assert con.execute("SELECT 1 FROM tasks WHERE student_id=? AND auto_key=? AND status='open'",(sid,f'ad-source-required:{sid}')).fetchone()
    second=chatdaytransfer.apply(con,{'events':[nov,trial,dec]},user)
    assert len(second['already'])==3 and not second['errors']
    assert con.execute("SELECT COUNT(*) FROM payments WHERE student_id=? AND cancelled=0",(sid,)).fetchone()[0]==2


def test_kiselev_november_sale_is_exact_and_idempotent():
    con=db.connect(str(Path(tempfile.mkdtemp())/'kiselev.sqlite3'));db.init_db(con)
    user={'id':1,'role':'admin','name':'Руководитель'}
    branch=schedule.save_branch(con,{'name':'ТЦ «Небо»'})
    group=schedule.save_group(con,{'name':'Небо Пн Ср 10:30','branch_id':branch,'weekdays':[0,2],'time':'10:30','age':'1,5–3'})
    con.execute("INSERT INTO group_terms(group_id,valid_from,per_lesson,credit_limit) VALUES (?,'2026-11',1600,2)",(group,))
    sid=services.create_student(con,{'name':'Киселёв Илья','birth':'2025-04-01','parent_name':'Мария','phone':'+7 919 107-65-40','source':'ТЦ Небо','group_id':group},user)
    con.execute("UPDATE students SET group_id=?,trial_group_id=?,trial_at='2026-11-16T10:00' WHERE id=?",(group,group,sid))
    sale={'source_id':'sz:21788:киселев-ноябрь','source_chat':'Служба заботы','source_at':'2026-11-16T18:16:23','student_name':'Киселёв Илья','event_kind':'subscription_sale','sale_date':'2026-11-16','paid_at':'2026-11-16T18:16:23','weekdays':[0,2],'tariff':'fixed','discount_percent':20,'amount':5120,'payment_method':'card','components':[{'month':'2026-11','price':5120,'dates':['2026-11-18','2026-11-23','2026-11-25','2026-11-30']}], 'note':'Ноябрь 5120 ₽, способ в сообщениях не уточнён'}
    trial={'source_id':'sz:21788:киселев-пу','source_chat':'Служба заботы','source_at':'2026-11-16T18:16:23','student_name':'Киселёв Илья','event_kind':'trial_result','event_date':'2026-11-16','came':True,'outcome':'purchased','subscription_months':['2026-11'],'note':'Пришёл на ПУ и купил абонемент'}
    first=chatdaytransfer.apply(con,{'events':[sale,trial]},user)
    assert len(first['applied'])==2 and not first['errors']
    assert [(r['month'],r['price'],r['status'],r['lessons']) for r in con.execute("SELECT month,price,status,lessons FROM subscriptions WHERE student_id=?",(sid,))]==[('2026-11',5120,'paid',4)]
    assert [(r['amount'],r['method']) for r in con.execute("SELECT amount,method FROM payments WHERE student_id=? AND cancelled=0",(sid,))]==[(5120,'card')]
    assert con.execute("SELECT trial_came FROM students WHERE id=?",(sid,)).fetchone()[0]==1
    assert not con.execute("SELECT 1 FROM tasks WHERE student_id=? AND auto_key=? AND status='open'",(sid,f'ad-source-required:{sid}')).fetchone()
    second=chatdaytransfer.apply(con,{'events':[sale,trial]},user)
    assert len(second['already'])==2 and not second['errors']
    assert con.execute("SELECT COUNT(*) FROM payments WHERE student_id=? AND cancelled=0",(sid,)).fetchone()[0]==1

