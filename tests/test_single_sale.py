import tempfile
from pathlib import Path
from datetime import date
from unittest.mock import patch
from crm import db, services as sv, singles, money, smart, program, schedule


def main():
    con=db.connect(str(Path(tempfile.mkdtemp())/'single-sale.sqlite3'));db.init_db(con);smart.REQUIRE=False
    user={'id':1,'role':'admin','name':'Пример'};sv.today=lambda:date(2026,11,10)
    b=schedule.save_branch(con,{'name':'Демо'});g=schedule.save_group(con,{'name':'Сб','branch_id':b,'weekdays':[5]})
    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('price_single','1800')")
    sid=sv.create_student(con,{'name':'Иван Примеров','parent_name':'Анна','phone':'+79000000401','funnel':'members'},user)
    before=con.total_changes;q=singles.sale_quote(con,sid,g,'2026-12');assert con.total_changes==before
    assert q['dates']==['2026-12-05'] and q['total']==1800
    with patch.object(money,'sell_single',side_effect=ValueError('Откат')):
        try:singles.sell_visit(con,sid,g,'2026-12-05',user)
        except ValueError:pass
    assert not con.execute('SELECT 1 FROM single_visits').fetchone()
    r=singles.sell_visit(con,sid,g,'2026-12-05',user)
    assert singles.sell_visit(con,sid,g,'2026-12-05',user)['id']==r['id']
    sub=con.execute('SELECT * FROM subscriptions WHERE id=?',(r['id'],)).fetchone()
    assert sub['month']=='2026-12' and sub['kind']=='single' and sub['price']==1800
    assert con.execute('SELECT COUNT(*) FROM single_visits').fetchone()[0]==1
    detail=sv.student_detail(con,sid)
    assert detail['subscriptions'][0]['single_date']=='2026-12-05' and detail['subscriptions'][0]['group_name']=='Сб'
    money.add_payment(con,sid,1800,'cash',user,subscription_id=r['id'])
    assert singles.sale_quote(con,sid,g,'2026-12','2026-12-05')['total']==0
    for bad in ('2026-12-06','2026-11-01'):
        try:singles.sale_quote(con,sid,g,bad[:7],bad)
        except sv.ApiError:pass
        else:raise AssertionError('Bad date accepted')
    # Карточка без группы не зависит от удалённого блока управления группами.
    sid2=sv.create_student(con,{'name':'Пётр Примеров','parent_name':'Анна','phone':'+79000000402','funnel':'members'},user)
    q=sv.quote_for(con,sid2,'2026-12',group_id=g,tariff='fixed');assert q['lessons_paid']==4
    sv.create_invoice(con,sid2,'2026-12',user,group_id=g,tariff='fixed')
    assert sv.enrollments_of(con,sid2)[0]['group_id']==g
    print('ok: single calendar, no preview writes, atomic booking/invoice, no duplicates, date/group display, paid remainder, new monthly enrollment')


if __name__=='__main__':main()
