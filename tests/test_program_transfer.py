"""Сумма оставшихся Арктики и ОФП оплачивает новую группу; деньги не создаются."""
import tempfile,json
from pathlib import Path
from datetime import date
from unittest.mock import patch
from crm import db,services as sv,program,schedule,transfer,money,smart,capacity


def main():
    c=db.connect(str(Path(tempfile.mkdtemp())/'transfer.sqlite3'));db.init_db(c);smart.REQUIRE=False
    u={'id':1,'role':'admin','name':'Демо'};sv.today=lambda:date(2026,11,20)
    a=schedule.save_branch(c,{'name':'Арктика'});n=schedule.save_branch(c,{'name':'Небо'})
    pid=program.ensure_sbornaya(c)
    arena=schedule.save_group(c,{'name':'Сборная Арктика','branch_id':a,'weekdays':[1,6],'time':'18:30'})
    ofp=schedule.save_group(c,{'name':'ОФП','branch_id':n,'weekdays':[3],'time':'20:30'})
    target=schedule.save_group(c,{'name':'Новая','branch_id':n,'weekdays':[1,4],'time':'18:00'})
    c.execute("UPDATE groups SET program_id=?,lesson_kind='arena' WHERE id=?",(pid,arena));c.execute("UPDATE groups SET program_id=?,lesson_kind='ofp' WHERE id=?",(pid,ofp))
    def kid(phone,paid=True):
        sid=sv.create_student(c,{'name':'Демо Ребёнок','parent_name':'Анна','phone':phone,'funnel':'members'},u)
        program.set_plan(c,sid,pid,'pkg',ofp,u)
        eid=next(e['id'] for e in sv.enrollments_of(c,sid) if e['group_id']==arena)
        inv=sv.create_invoice(c,sid,'2026-12',u,eid)
        if paid:money.add_payment(c,sid,inv['quote']['total'],'cash',u)
        return sid,eid,inv
    sid,eid,inv=kid('+79000000801')
    assert inv['quote']['total']==19200
    before=c.total_changes;counts=lambda:tuple(c.execute('SELECT (SELECT COUNT(*) FROM enrollments),(SELECT COUNT(*) FROM subscriptions),(SELECT COUNT(*) FROM history)').fetchone())
    original=counts();p=transfer.plan(c,eid,target,'2026-12-15')
    assert counts()==original
    m=p['months'][0];assert m['arena_credit']==8000 and m['ofp_credit']==2400,m
    assert m['credit']==10400 and m['keep_price']==8800
    assert m['new_lessons']==5 and m['new_price']==6750,m
    assert p['projected_deposit']==3650 and p['projected_due']==0,p
    # Изменение текущего плана не меняет уже проданный состав пакета.
    c.execute("UPDATE enrollments SET tariff='arena_only' WHERE id=?",(eid,))
    assert transfer.plan(c,eid,target,'2026-12-15')['months'][0]['ofp_credit']==2400
    # При ошибке проверки мест откатываются обе группы и финансовая часть.
    try:
        with patch.object(capacity,'ensure_roster',side_effect=ValueError('full')):transfer.apply(c,eid,target,'2026-12-15',u)
        raise AssertionError('expected full')
    except ValueError:pass
    assert counts()==original and c.execute('SELECT active FROM enrollments WHERE id=?',(eid,)).fetchone()[0]==1
    transfer.apply(c,eid,target,'2026-12-15',u)
    assert money.balance(c,sid)['deposit']==3650
    assert c.execute('SELECT COUNT(*) FROM enrollments WHERE student_id=? AND group_id IN (?,?) AND active=1',(sid,arena,ofp)).fetchone()[0]==0
    assert c.execute('SELECT end_date FROM enrollments WHERE id=?',(eid,)).fetchone()[0]=='2026-12-15'
    assert c.execute('SELECT COUNT(*) FROM payments WHERE student_id=?',(sid,)).fetchone()[0]==1
    # Неоплаченный пакет не создаёт депозит и фиктивные платежи.
    sid,eid,inv=kid('+79000000802',False);p=transfer.plan(c,eid,target,'2026-12-15')
    assert p['paid_credit']==0 and p['projected_due']==15550 and p['projected_deposit']==0
    transfer.apply(c,eid,target,'2026-12-15',u)
    assert money.balance(c,sid)['deposit']==0 and money.balance(c,sid)['upcoming']==15550
    # Дорогая новая группа даёт доплату, а не списывает оставшуюся стоимость.
    sid,eid,inv=kid('+79000000804');c.execute("UPDATE price_list SET fixed=3000 WHERE valid_from='2026-12'")
    p=transfer.plan(c,eid,target,'2026-12-15');assert p['projected_due']==4600 and p['projected_deposit']==0,p
    expected=transfer.confirmation(p);c.execute("UPDATE price_list SET fixed=3100 WHERE valid_from='2026-12'")
    try:transfer.apply(c,eid,target,'2026-12-15',u,expected=expected);raise AssertionError('expected stale quote')
    except sv.ApiError as err:assert 'изменился' in str(err)
    assert c.execute('SELECT active FROM enrollments WHERE id=?',(eid,)).fetchone()[0]==1
    c.execute("UPDATE price_list SET fixed=3000 WHERE valid_from='2026-12'")
    transfer.apply(c,eid,target,'2026-12-15',u,expected=expected)
    assert money.balance(c,sid)['upcoming']==4600
    # После отмеченного ОФП нельзя отрезать историю переводом.
    sid,eid,inv=kid('+79000000803');c.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES (?,?,?,'present')",(sid,ofp,'2026-12-17'))
    try:transfer.plan(c,eid,target,'2026-12-15');raise AssertionError('expected marks protection')
    except sv.ApiError as e:assert 'ОФП' in str(e)
    print('ok: both Arctic/OFP credits, sold snapshot, no preview writes, atomic rollback, actual balance, unpaid safety, OFP history')

if __name__=='__main__':main()
