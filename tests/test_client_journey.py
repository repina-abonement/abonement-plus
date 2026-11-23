import json
import tempfile
from pathlib import Path

from crm import db, historyview, money, schedule, services


def test_client_journey_is_business_chronology_not_entry_log():
    con=db.connect(str(Path(tempfile.mkdtemp())/'journey.sqlite3'));db.init_db(con)
    user={'id':1,'role':'admin','name':'Юлия Репина'}
    branch=schedule.save_branch(con,{'name':'ТЦ «Небо»'})
    group=schedule.save_group(con,{'name':'Небо Вс 17:45','branch_id':branch,'weekdays':[6],'time':'17:45','age':'4–5'})
    sid=services.create_student(con,{'name':'Привалов Даниил','birth':'2022-01-01','parent_name':'Мама',
        'phone':'+7 900 222-33-44','source':'Промоутер Иван Ивакин'},user)
    con.execute("""UPDATE students SET created_at='2026-11-01T12:00',group_id=?,trial_group_id=?,
        trial_at='2026-11-22T17:45',trial_came=1 WHERE id=?""",(group,group,sid))
    event={'source_id':'sz:result','source_chat':'Служба заботы','source_at':'2026-11-23T14:00:00',
        'student_name':'Привалов Даниил','event_kind':'trial_result','event_date':'2026-11-22','came':True,
        'note':'На ПУ ребёнок занимался. Семья пока не планирует покупку'}
    con.execute('INSERT INTO chat_day_transfer_events VALUES (?,?,?,?,?,?,?,?)',
        ('sz:result',sid,'Служба заботы','2026-11-23T14:00:00','trial_result',json.dumps(event,ensure_ascii=False),'2026-11-23T14:01','Юлия'))
    con.execute("""INSERT INTO tasks(student_id,kind,title,note,due_at,status,source,created_at)
        VALUES (?,?,?,?,?,'open','auto',?)""",(sid,'rebook','Пригласить снова на ПУ: ребёнок подрос','Позвонить семье','2027-02-21T11:00','2026-11-23T14:02'))
    rows=historyview.journey(con,services.get_student_row(con,sid))
    assert [row['title'] for row in rows]==[
        'Лид от промоутера Ивана Ивакина','Записан на ПУ','Пришёл на ПУ','Результат ПУ','Следующий шаг']
    assert rows[0]['ts']=='2026-11-01T12:00'
    assert rows[1]['ts']==rows[2]['ts']==rows[3]['ts']=='2026-11-22T17:45'
    assert '21.02.2027' in rows[-1]['text']


def test_paid_subscription_in_journey_shows_confirmed_telegram_payment():
    con=db.connect(str(Path(tempfile.mkdtemp())/'journey-payment.sqlite3'));db.init_db(con)
    user={'id':1,'role':'admin','name':'Юлия Репина'}
    sid=services.create_student(con,{'name':'Покупатель Тест','parent_name':'Мама','phone':'+7 900 111-22-33'},user)
    sub=con.execute("""INSERT INTO subscriptions(student_id,month,tariff,lessons,per_lesson,price,status,created_at,paid_at)
        VALUES (?,'2026-11','fixed',2,1080,2160,'paid','2026-11-22','2026-11-22')""",(sid,)).lastrowid
    money.add_payment(con,sid,2160,'card_branch',user,subscription_id=sub,paid_at='2026-11-22T20:30',comment='Подтверждено СЗ')
    rows=historyview.journey(con,services.get_student_row(con,sid))
    sale=next(row for row in rows if row['title']=='Куплен абонемент')
    assert '2 160 ₽' in sale['text'] and 'картой в филиале' in sale['text']

