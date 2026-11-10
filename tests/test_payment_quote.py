import tempfile
from pathlib import Path
from datetime import date
from unittest.mock import patch
from crm import db,services as sv,pricing,paylinks,fiscal,smart,money


class LateDay(date):
    @classmethod
    def today(cls):return cls(2026,11,27)


def main():
    con=db.connect(str(Path(tempfile.mkdtemp())/'payment-quote.sqlite3'));db.init_db(con);smart.REQUIRE=False
    sv.today=lambda:date(2026,11,20);u={'id':1,'role':'admin','name':'Пример'}
    b=con.execute("INSERT INTO branches(name) VALUES ('Демо')").lastrowid
    g=con.execute("INSERT INTO groups(branch_id,name,weekdays) VALUES (?,'Вт/Пт','1,4')",(b,)).lastrowid
    def create(phone):
        sid=sv.create_student(con,{'name':'Иван Примеров','parent_name':'Анна','phone':phone,'funnel':'members','group_id':g},u)
        inv=sv.create_invoice(con,sid,'2026-12',u)
        return sid,inv['id']
    sid,sub=create('+79000000601');sid2,sub2=create('+79000000602')
    before=con.execute('SELECT COUNT(*) FROM history').fetchone()[0]
    with patch.object(pricing,'date',LateDay):
        q=sv.payment_quote(con,sub)
    assert q['total']==12758 and q['late_fee']==608
    assert con.execute('SELECT price FROM subscriptions WHERE id=?',(sub,)).fetchone()[0]==12150
    assert con.execute('SELECT COUNT(*) FROM history').fetchone()[0]==before
    def gateway(c,path,params=None,form=None):
        return {'token':'fake-token'} if path.endswith('token/') else {'invoice_id':'fake-invoice','invoice_url':'https://example.invalid/pay'}
    with patch.object(paylinks,'ready',return_value=True),patch.object(fiscal,'pk_call',side_effect=gateway):
        first=paylinks.create(con,sid,12150,u,subscription_id=sub,full_payment=True)
        second=paylinks.create(con,sid2,12150,u,subscription_id=sub2,full_payment=True)
    # Сверка после 25-го использует фактическую дату банковского платежа.
    with patch.object(pricing,'date',LateDay):
        paylinks.apply_payments(con,[{'status':'success','orderid':con.execute('SELECT orderid FROM pay_links WHERE id=?',(first["id"],)).fetchone()[0],'pay_amount':'12150','success_datetime':'2026-11-24 12:00'}])
        paylinks.apply_payments(con,[{'status':'success','orderid':con.execute('SELECT orderid FROM pay_links WHERE id=?',(second["id"],)).fetchone()[0],'pay_amount':'12150','success_datetime':'2026-11-27 12:00'}])
    one=con.execute('SELECT price,late_fee,status FROM subscriptions WHERE id=?',(sub,)).fetchone()
    two=con.execute('SELECT price,late_fee,status FROM subscriptions WHERE id=?',(sub2,)).fetchone()
    assert tuple(one)==(12150,0,'paid'),tuple(one)
    assert tuple(two)==(12758,608,'invoiced'),tuple(two)
    assert money.balance(con,sid2)['upcoming']==608
    print('ok: accurate payment preview without saved changes, actual paid date at gateway sync, late fee remains due on old fixed-price link')


if __name__=='__main__':main()
