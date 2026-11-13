"""Регрессия: нельзя освободить весь ноябрь в середине месяца, баланс виден и объяснён."""
import json
import tempfile
from datetime import date
from pathlib import Path
from crm import db, services as sv, money, subcancel

WHO={'id':1,'name':'Тест','role':'admin'}
DATA={'approved_by_yulia':True,'approval':'2026-11-13','reason':'Будущий период','mode':'balance','effective_date':'2026-11-14'}


def main():
    subcancel.today=lambda:date(2026,11,13)
    with tempfile.TemporaryDirectory() as tmp:
        con=db.connect(str(Path(tmp)/'test.sqlite3'));db.init_db(con)
        b=con.execute("INSERT INTO branches(name) VALUES ('Тест')").lastrowid
        g=con.execute("INSERT INTO groups(branch_id,name,weekdays) VALUES (?,'Тест','1,4')",(b,)).lastrowid
        ofp=con.execute("INSERT INTO groups(branch_id,name,weekdays) VALUES (?,'ОФП','5')",(b,)).lastrowid
        def make(paid=10800,price=10800,components=None):
            count=con.execute('SELECT COUNT(*) FROM students').fetchone()[0]
            sid=sv.create_student(con,{'name':'Тест Примеров','birth':'2020-01-01','email':'test@example.invalid','parent_name':'Анна','phone':f'+7900000{count:04d}','funnel':'members','group_id':g},WHO)
            e=sv.enrollments_of(con,sid)[0]['id']
            components=components or [{'group_id':g,'rate':1,'dates':[f'2026-11-{d:02d}' for d in (3,6,10,13,17,20,24,27)]}]
            sub=con.execute("INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at,lesson_schedule) VALUES (?,?,'2026-11','fixed',8,1350,?,'invoiced','2026-10-20',?)",(sid,e,price,json.dumps(components))).lastrowid
            money.add_payment(con,sid,paid,'cash',WHO,subscription_id=sub)
            return sid,sub,e
        def rejects(fn):
            try:fn()
            except sv.ApiError:return
            raise AssertionError('Expected rejected cancellation')
        sid,sub,e=make()
        # Пропуски прошедшего периода не превращаются в возврат.
        con.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES (?,?,'2026-11-03','absent')",(sid,g))
        q=subcancel.preview(con,sub)
        assert (q['paid'],q['retained_amount'],q['future_amount'],q['releasable'])==(10800,5400,5400,5400)
        assert q['retained_lessons']==4 and q['future_lessons']==4
        rejects(lambda:subcancel.cancel(con,sub,WHO,{**DATA,'effective_date':'2026-11-01'}))
        rejects(lambda:subcancel.cancel(con,sub,WHO,{**DATA,'effective_date':'2026-11-13'}))
        rejects(lambda:subcancel.cancel(con,sub,WHO,{**DATA,'expected_releasable':10800}))
        subcancel.cancel(con,sub,WHO,{**DATA,'amount':10800}) # подмена суммы не влияет на серверный расчёт
        assert money.balance(con,sid)['deposit']==5400
        assert money.ledger(con,sid)[0]['rest']==5400
        cancellation=subcancel.cancellations(con,sid)[0]
        assert cancellation['amount']==5400 and cancellation['balance_available']==5400 and cancellation['period_verified']
        assert con.execute('SELECT COUNT(*) FROM attendance WHERE student_id=?',(sid,)).fetchone()[0]==1
        assert len(sv.student_detail(con,sid)['subscription_cancellations'])==1
        # Освобождённые средства могут сразу закрыть часть декабря — это объясняется в карточке.
        sid,sub,e=make()
        future=con.execute("INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at) VALUES (?,?,'2026-12','fixed',8,1350,10800,'invoiced','2026-11-01')",(sid,e)).lastrowid
        subcancel.cancel(con,sub,WHO,DATA)
        bal=money.balance(con,sid);c=subcancel.cancellations(con,sid)[0]
        assert bal['deposit']==0 and bal['covered'][future]==5400
        assert c['applied_to_invoices']==5400 and c['balance_available']==0
        # Новый абонемент, оплаченный с депозита, не называется просто выставленным счётом.
        sid2,sub2,e2=make()
        con.execute("UPDATE parents SET email='test@example.invalid'")
        subcancel.cancel(con,sub2,WHO,DATA)
        money.add_payment(con,sid2,20000,'cash',WHO)
        sale=sv.create_invoice(con,sid2,'2026-12',WHO)
        text=con.execute("SELECT text FROM history WHERE student_id=? AND text LIKE 'Абонемент оплачен средствами с баланса:%'",(sid2,)).fetchone()
        assert text and str(sale['quote']['total']) in text[0]
        before_restart=money.balance(con,sid2)
        money.init(con)
        assert money.balance(con,sid2)==before_restart, 'Startup must not invent another payment for balance-paid subscription'
        # Частичная оплата: сначала удерживается прошедшая часть, возврат только оплаченного остатка.
        sid,sub,e=make(paid=8000)
        subcancel.cancel(con,sub,WHO,{**DATA,'mode':'refund'})
        r=subcancel.listing(con,sid)[0]
        assert r['amount']==2600 and money.balance(con,sid)['deposit']==0
        # Цена фактического договора, со скидкой, без пересчёта по сегодняшнему прайсу.
        sid,sub,e=make(paid=9600,price=9600)
        assert subcancel.preview(con,sub)['releasable']==4800
        # Арктика и ОФП имеют разные ставки: делим стоимость по весам компонентов.
        components=[{'group_id':g,'rate':1600,'dates':['2026-11-03','2026-11-10','2026-11-17','2026-11-24']},
                    {'group_id':ofp,'rate':1200,'dates':['2026-11-07','2026-11-14','2026-11-21','2026-11-28']}]
        sid,sub,e=make(paid=10640,price=10640,components=components)
        q=subcancel.preview(con,sub)
        assert q['retained_amount']==4180 and q['releasable']==6460
        # Исторические даты, если они не проверяются, не подменяются приблизительным расчётом.
        con.execute("UPDATE subscriptions SET lesson_schedule='',lessons=9 WHERE id=?",(sub,))
        assert subcancel.preview(con,sub)['period_blocker']
        rejects(lambda:subcancel.cancel(con,sub,WHO,DATA))
        # Старые отмены отмечаются для сверки, без автоматической правки уже сохранённых сумм.
        con.execute('UPDATE subscription_cancellations SET period_verified=0 WHERE id=?',(r['cancellation_id'],))
        rejects(lambda:subcancel.confirm(con,r['id'],WHO,{'actually_returned':True,'proof':'full refund'}))
        print('ok: November future-only, today excluded, past absences retained, partial payment, December allocation, weighted OFP, stale quote rejected, legacy operations blocked')


if __name__=='__main__':main()
