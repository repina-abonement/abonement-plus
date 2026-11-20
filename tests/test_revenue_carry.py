"""Зачёт пропуска переносит управленческую выручку, но не создаёт деньги."""
import os
import tempfile

from crm import analytics, db, erphistory, money, revenuecarry, salary, schedule, services, subscriptionpresentation

USER={'id':1,'name':'Тест','role':'admin'}


def main():
    con=db.connect(os.path.join(tempfile.mkdtemp(),'crm.sqlite3'));db.init_db(con)
    branch=con.execute("INSERT INTO branches(name) VALUES ('Тестовый филиал')").lastrowid
    group=schedule.save_group(con,{'name':'Тестовая группа','branch_id':branch,'weekdays':[1,4],'time':'18:00','capacity':12})
    family=con.execute("INSERT INTO families(name) VALUES ('Тестовая семья')").lastrowid
    student=con.execute("INSERT INTO students(family_id,name,birth,erp_id,group_id,tariff,stage_changed_at,created_at) VALUES (?,?,?,?,?,?,'2026-01-01','2026-01-01')",
                        (family,'Иван Тестов','2015-01-01','SYNTHETIC-132389',group,'flex')).lastrowid
    con.execute("INSERT INTO parents(family_id,name,phone,phone_norm,is_primary) VALUES (?,?,?,?,1)",
                (family,'Тестовый Родитель','+7 999 000-00-01','79990000001'))
    enrollment=con.execute("INSERT INTO enrollments(student_id,group_id,weekdays,tariff,created_at) VALUES (?,?,?,'flex','2026-01-01')",
                           (student,group,'1,4')).lastrowid
    november=con.execute("""INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at)
      VALUES (?,?,?,'flex',9,1550,13950,'paid','2026-10-20')""",(student,enrollment,'2026-11')).lastrowid
    money.add_payment(con,student,13950,'cash',USER,subscription_id=november,paid_at='2026-10-20')
    absences=[]
    for day in ('2026-11-03','2026-11-06'):
        absences.append(con.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES (?,?,?,'absent')",
                                    (student,group,day)).lastrowid)
    assert con.execute("""SELECT x.id FROM subscriptions x LEFT JOIN enrollments e ON e.id=x.enrollment_id
      WHERE x.student_id=? AND x.month=? AND x.status='paid' AND COALESCE(e.group_id,(SELECT group_id FROM students WHERE id=x.student_id))=?""",
      (student,'2026-11',group)).fetchone()
    assert revenuecarry._source(con,con.execute("SELECT * FROM attendance WHERE id=?",(absences[0],)).fetchone(),'2026-11')[2]==1
    quote=services.quote_for(con,student,'2026-12',enrollment_id=enrollment,credit_requested=True)
    assert quote['lessons_paid']==9 and quote['credit_lessons']==2 and quote['credit']==3100 and quote['total']==10850,quote
    created=services._create_invoice(con,student,'2026-12',USER,enrollment_id=enrollment,quote=quote)
    december=created['id']
    assert len(con.execute("SELECT * FROM revenue_carries").fetchall())==2
    # Reusing the same source rows is idempotent.
    revenuecarry.record_invoice(con,december,absences,1550,USER)
    assert len(con.execute("SELECT * FROM revenue_carries").fetchall())==2
    money.pay_subscription(con,december,'cash',USER,10850,paid_at='2026-11-20')
    assert con.execute("SELECT status FROM subscriptions WHERE id=?",(december,)).fetchone()[0]=='paid',dict(con.execute("SELECT * FROM subscriptions WHERE id=?",(december,)).fetchone())
    carries=[dict(x) for x in con.execute("SELECT * FROM revenue_carries")]
    assert con.execute("SELECT COUNT(*) FROM revenue_carries WHERE source_loaded=1 AND active=1").fetchone()[0]==2,carries
    assert revenuecarry.adjustments(con,'2026-11')[0]['amount']==-3100
    assert revenuecarry.adjustments(con,'2026-12')[0]['amount']==3100
    assert analytics._holders(con,'2026-11')[student]['revenue']==10850
    assert analytics._holders(con,'2026-12')[student]['revenue']==13950
    assert services.dashboard(con,'2026-11')['kpi']['revenue']['value']==10850
    assert services.dashboard(con,'2026-12')['kpi']['revenue']['value']==13950
    assert analytics.groups_report(con,'2026-12')['groups'][0]['revenue']==13950
    assert salary._revenue(con,'2026-11')[group]==10850
    assert salary._revenue(con,'2026-12')[group]==13950
    card={x['id']:x for x in subscriptionpresentation.rows_for_card(con,student)}
    assert card[november]['revenue_carry_out']==3100 and card[december]['revenue_carry_in']==3100
    before=con.execute("SELECT COUNT(*),COALESCE(SUM(amount),0) FROM payments WHERE student_id=?",(student,)).fetchone()
    assert tuple(before)==(2,24800)
    services.cancel_subscription(con,december,USER)
    assert revenuecarry.adjustments(con,'2026-11')==[] and revenuecarry.adjustments(con,'2026-12')==[]
    assert con.execute("SELECT COUNT(*) FROM attendance WHERE id IN (?,?) AND resolved IS NULL",absences).fetchone()[0]==2
    after=con.execute("SELECT COUNT(*),COALESCE(SUM(amount),0) FROM payments WHERE student_id=?",(student,)).fetchone()
    assert tuple(after)==tuple(before)
    assert con.execute("SELECT COUNT(*) FROM revenue_carries WHERE active=1").fetchone()[0]==0
    assert con.execute("SELECT price FROM subscriptions WHERE id=?",(november,)).fetchone()[0]==13950
    # Legacy evidence can be saved before the exact source sale is loaded.
    legacy_absence=con.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES (?,?,?,'absent')",
        (student,group,'2026-11-10')).lastrowid
    legacy_target=con.execute("""INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,credit_lessons,price,status,created_at)
      VALUES (?,?,?,'flex',9,1550,1,12400,'paid','2026-11-20')""",(student,enrollment,'2026-12')).lastrowid
    context=revenuecarry.historical_context(con,'SYNTHETIC-132389')
    assert context['student']['id']==student and any(x['id']==legacy_absence for x in context['absences'])
    checked=revenuecarry.historical_preview(con,student,legacy_target,[legacy_absence],'ERP-SOURCE-1','Подтверждено в СЗ')
    assert checked['token'] and not checked['source_loaded']
    revenuecarry.historical_apply(con,student,legacy_target,[legacy_absence],'ERP-SOURCE-1','Подтверждено в СЗ',checked['token'],USER)
    assert revenuecarry.pending_source(con,student)[0]['amount']==1550
    erphistory.init(con)
    con.execute("""INSERT INTO erp_history_subscriptions(source_id,student_id,group_id,month,valid_from,valid_until,lessons,revenue_amount,source_payload,imported_at,imported_by)
      VALUES ('ERP-SOURCE-1',?,?,?,'2026-11-01','2026-11-30',9,13950,'{}','2026-11-20','Тест')""",(student,group,'2026-10'))
    # Wrong month cannot activate the correction.
    assert revenuecarry.refresh_historical_sources(con)==0
    con.execute("UPDATE erp_history_subscriptions SET month='2026-11' WHERE source_id='ERP-SOURCE-1'")
    assert revenuecarry.refresh_historical_sources(con)==1 and revenuecarry.pending_source(con,student)==[]
    print('ok')


if __name__=='__main__':main()
