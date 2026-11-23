import tempfile
from pathlib import Path

from crm import db, money, schedule, services


def test_paid_unlinked_month_subscription_is_not_false_debt_for_one_group():
    con=db.connect(str(Path(tempfile.mkdtemp())/'members.sqlite3'));db.init_db(con)
    user={'id':1,'role':'admin','name':'Юлия'}
    branch=schedule.save_branch(con,{'name':'ТЦ «Небо»'})
    group=schedule.save_group(con,{'name':'Пн/Чт 19:45','branch_id':branch,'weekdays':[0,3],'time':'19:45','age':'6–8'})
    sid=services.create_student(con,{'name':'Тест Мамедов','parent_name':'Мама','phone':'+7 900 000-00-01',
        'funnel':'members','group_id':group},user)
    sub=con.execute("""INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at)
        VALUES (?,NULL,'2026-11','fixed',8,1350,10800,'invoiced','2026-11-01')""",(sid,)).lastrowid
    money.add_payment(con,sid,10800,'card_branch',user,subscription_id=sub,paid_at='2026-11-01T12:00')
    assert services.unpaid_groups(con,sid,'2026-11')==[]
    board=services.board(con,'members')
    status=next(card['status'] for column in board['columns'] for card in column['cards'] if card['id']==sid)
    assert status!='unpaid_cur'

