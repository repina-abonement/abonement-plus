"""Отмена, резерв, исходный способ возврата, согласование и подтверждённый чек."""
import tempfile
from pathlib import Path
from crm import db, money, services as sv, subcancel, fiscal

ADMIN={'id':1,'name':'Тест','role':'admin'}
APPROVAL={'approved_by_yulia':True,'approval':'2026-11-13','reason':'Тестовая отмена'}


def main():
    with tempfile.TemporaryDirectory() as tmp:
        con=db.connect(str(Path(tmp)/'test.sqlite3'));db.init_db(con)
        branch=con.execute("INSERT INTO branches(name) VALUES ('Тест')").lastrowid
        gid=con.execute("INSERT INTO groups(branch_id,name,weekdays) VALUES (?,'Тест','1,4')",(branch,)).lastrowid
        def sale(method='cash',month='2026-12',paid=1000):
            sid=sv.create_student(con,{'name':'Тест Примеров','birth':'2020-01-01','parent_name':'Анна','phone':'+7900000'+str(con.execute('SELECT COUNT(*) FROM students').fetchone()[0]).zfill(4),'group_id':gid,'funnel':'members'},ADMIN)
            eid=sv.enrollments_of(con,sid)[0]['id']
            sub=con.execute("INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at) VALUES (?,?,?,'fixed',8,125,1000,'invoiced','2026-11-13')",(sid,eid,month)).lastrowid
            pid=money.add_payment(con,sid,paid,method,ADMIN,subscription_id=sub) if paid else None
            return sid,sub,pid,eid
        def denied(fn):
            try:fn()
            except (sv.ApiError,ValueError):return
            raise AssertionError('Expected rejection')
        sid,sub,pid,eid=sale()
        denied(lambda:subcancel.cancel(con,sub,ADMIN,{'mode':'balance'}))
        assert con.execute('SELECT status FROM subscriptions WHERE id=?',(sub,)).fetchone()[0]=='paid'
        subcancel.cancel(con,sub,ADMIN,{**APPROVAL,'mode':'balance'})
        assert money.balance(con,sid)['deposit']==1000
        subcancel.cancel(con,sub,ADMIN,{**APPROVAL,'mode':'refund'}) # повтор не меняет решения
        assert not subcancel.listing(con,sid)
        # Отменённая запись остаётся, новый абонемент того же месяца допускается.
        con.execute("INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at) VALUES (?,?,'2026-12','fixed',8,125,1000,'invoiced','2026-11-13')",(sid,eid))
        assert con.execute('SELECT COUNT(*) FROM subscriptions WHERE student_id=?',(sid,)).fetchone()[0]==2
        sid,sub,pid,_=sale(paid=600)
        subcancel.cancel(con,sub,ADMIN,{**APPROVAL,'mode':'refund'})
        r=subcancel.listing(con,sid)[0]
        assert r['amount']==600 and money.balance(con,sid)['deposit']==0
        assert money.registry(con,'2020-01-01','2030-01-01')['total']==1600 # резерв не расход
        denied(lambda:money.cancel_payment(con,r['reserve_id'],ADMIN))
        denied(lambda:subcancel.confirm(con,r['id'],ADMIN,{'proof':'Без подтверждения'}))
        result=subcancel.confirm(con,r['id'],ADMIN,{'actually_returned':True,'proof':'Расходный документ 1'})
        assert result['status']=='receipt_pending'
        result=subcancel.confirm(con,r['id'],ADMIN,{'receipt_fn':'123456','receipt_fd':'42','receipt_fp':'76543'})
        assert result['status']=='done'
        before=con.execute('SELECT COUNT(*) FROM payments').fetchone()[0]
        subcancel.confirm(con,r['id'],ADMIN,{})
        assert con.execute('SELECT COUNT(*) FROM payments').fetchone()[0]==before
        sid,sub,pid,_=sale('online')
        denied(lambda:subcancel.cancel(con,sub,ADMIN,{**APPROVAL,'mode':'refund'}))
        con.execute("INSERT INTO pay_links(student_id,amount,orderid,status,created_at,payment_id,pk_payment_id,subscription_id) VALUES (?,1000,'test','paid','2026-11-13',?,'pk1',?)",(sid,pid,sub))
        denied(lambda:money.cancel_payment(con,pid,ADMIN))
        denied(lambda:money.refund(con,sid,1,ADMIN,'Обход PayKeeper'))
        state={'refunds':[],'receipts':[],'calls':[]}
        def fake(con,path,params=None,form=None):
            state['calls'].append((path,params,form))
            assert form is None, 'Tests must never initiate a real external refund'
            if path=='/info/receipts/bypaymentid/':assert params=={'payment_id':'pk1'}
            return state['refunds'] if path=='/info/refunds/bypaymentid/' else {'receipts':state['receipts']}
        original=fiscal.pk_call;fiscal.pk_call=fake
        try:
            subcancel.cancel(con,sub,ADMIN,{**APPROVAL,'mode':'refund'})
            r=subcancel.listing(con,sid)[0]
            assert subcancel.confirm(con,r['id'],ADMIN,{})['status']=='pending'
            state['refunds']=[{'id':'r1','refund_number':'1','payment_id':'pk1','amount':'1000.00','status':'started'}]
            assert subcancel.confirm(con,r['id'],ADMIN,{})['status']=='pending'
            state['refunds'][0]['status']='done'
            assert subcancel.confirm(con,r['id'],ADMIN,{})['status']=='receipt_pending'
            # Чек продажи, чужого возврата или без фискальных реквизитов не подходят.
            receipt={'id':'check1','payment_id':'pk1','refund_id':'wrong','type':'refund','status':'success','sum_cashless':'1000.00','fn':'1','fnd':'2','fpd':'3'}
            state['receipts']=[receipt]
            assert subcancel.confirm(con,r['id'],ADMIN,{})['status']=='receipt_pending'
            receipt['refund_id']='1';receipt['status']='failed'
            assert subcancel.confirm(con,r['id'],ADMIN,{})['status']=='receipt_pending'
            receipt['status']='success'
            assert subcancel.confirm(con,r['id'],ADMIN,{})['status']=='done'
            assert money.balance(con,sid)['deposit']==0
            assert subcancel.listing(con,sid)[0]['receipt_id']=='check1'
        finally:fiscal.pk_call=original
        # Смешанная оплата разбивается по исходным способам без произвольной замены.
        sid,sub,pid,_=sale('cash',paid=400)
        money.add_payment(con,sid,600,'card',ADMIN,subscription_id=sub)
        subcancel.cancel(con,sub,ADMIN,{**APPROVAL,'mode':'refund'})
        parts=subcancel.listing(con,sid)
        assert sorted((r['method'],r['amount']) for r in parts)==[('card',600),('cash',400)]
        assert money.balance(con,sid)['deposit']==0
        denied(lambda:money.cancel_payment(con,pid,ADMIN))
        # Нельзя начать возврат, когда PayKeeper недоступен, даже после согласования.
        sid,sub,pid,_=sale('online')
        con.execute("INSERT INTO pay_links(student_id,amount,orderid,status,created_at,payment_id,pk_payment_id,subscription_id) VALUES (?,1000,'test-timeout','paid','2026-11-13',?,'pk-timeout',?)",(sid,pid,sub))
        def unavailable(*a,**kw):raise fiscal.FxError('Test timeout')
        fiscal.pk_call=unavailable
        try:
            denied(lambda:subcancel.cancel(con,sub,ADMIN,{**APPROVAL,'mode':'refund'}))
            assert con.execute('SELECT status FROM subscriptions WHERE id=?',(sub,)).fetchone()[0]=='paid'
            assert not subcancel.listing(con,sid)
        finally:fiscal.pk_call=original
        assert con.execute('SELECT COUNT(*) FROM subscription_cancellations').fetchone()[0]==4
        print('ok: approval, audit/history, repeat cancellation, reserve, partial payment, original payment, asynchronous refund + matching fiscal receipt, no duplicate debit')


if __name__=='__main__':main()
