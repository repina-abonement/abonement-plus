"""A single-sale enrollment must not become a paid recurring class roster."""
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from crm import db, schedule, services as sv, smart


class JournalSingleEntitlement(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.con=db.connect(str(Path(self.temp.name)/'journal.sqlite3'));db.init_db(self.con)
        self.addCleanup(self.con.close)
        self.patch=patch.object(smart,'REQUIRE',False);self.patch.start();self.addCleanup(self.patch.stop)
        clock=patch.object(sv,'today',return_value=date(2026,11,20));clock.start();self.addCleanup(clock.stop)
        self.user={'id':1,'role':'admin','name':'Пример'}
        self.branch=schedule.save_branch(self.con,{'name':'Тест'})
        self.group=schedule.save_group(self.con,{'name':'Вт/Пт','branch_id':self.branch,'weekdays':[1,4],'time':'16:00'})
        self.sid=sv.create_student(self.con,{'name':'Пример Разовых','parent_name':'Анна','phone':'+79000000601','group_id':self.group,'funnel':'members'},self.user)
        self.eid=sv.enrollments_of(self.con,self.sid)[0]['id']

    def sale(self,day=None,status='paid',kind='single',lessons=1):
        sub=self.con.execute("""INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at,kind)
            VALUES (?,NULL,'2026-11','single',?,1000,?,?,?,?)""",(self.sid,lessons,lessons*1000,status,'2026-11-01T10:00',kind)).lastrowid
        if day:self.con.execute("INSERT INTO single_visits(student_id,group_id,date,subscription_id,created_at) VALUES (?,?,?,?,?)",(self.sid,self.group,day,sub,'2026-11-01T10:00'))
        return sub

    def rows(self,day,paid_only=True):
        return [r for r in sv.journal(self.con,self.group,day,paid_only=paid_only)['rows'] if r['id']==self.sid]

    def test_four_sales_keep_only_the_four_explicit_booked_dates(self):
        booked=['2026-11-03','2026-11-06','2026-11-10','2026-11-13']
        for day in booked:self.sale(day)
        before=self.con.total_changes
        for day in booked:
            row=self.rows(day)
            self.assertEqual(len(row),1);self.assertEqual(row[0]['tag'],'single');self.assertFalse(row[0]['unpaid'])
        for day in ('2026-11-17','2026-11-20','2026-11-24','2026-11-27'):
            self.assertEqual(self.rows(day),[])
            self.assertEqual(self.rows(day,False),[])
        self.assertEqual(self.con.total_changes,before)

    def test_unknown_date_sale_never_uses_the_active_group_schedule(self):
        self.sale()
        for day in ('2026-11-03','2026-11-06','2026-11-10'):
            self.assertEqual(self.rows(day),[])
            self.assertEqual(self.rows(day,False),[])

    def test_real_paid_month_and_extra_single_still_have_month_roster(self):
        self.sale('2026-11-03')
        self.con.execute("""INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at)
            VALUES (?,?,'2026-11','fixed',8,1000,8000,'paid','2026-11-01')""",(self.sid,self.eid))
        for day in ('2026-11-03','2026-11-06','2026-11-17'):
            self.assertEqual(len(self.rows(day)),1)
            self.assertEqual(self.rows(day)[0]['tag'],'paid')

    def test_paid_single_does_not_mark_an_unpaid_month_as_fully_paid(self):
        self.sale('2026-11-03')
        self.con.execute("""INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at)
            VALUES (?,?,'2026-11','fixed',8,1000,8000,'invoiced','2026-11-01')""",(self.sid,self.eid))
        self.assertEqual(self.rows('2026-11-03')[0]['tag'],'single')
        self.assertEqual(self.rows('2026-11-06'),[])
        self.assertTrue(self.rows('2026-11-06',False)[0]['unpaid'])

    def test_unpaid_single_keeps_its_own_payment_status(self):
        self.sale('2026-11-03',status='paid')
        self.sale('2026-11-06',status='invoiced')
        self.assertFalse(self.rows('2026-11-03')[0]['unpaid'])
        self.assertTrue(self.rows('2026-11-06')[0]['unpaid'])

    def test_existing_attendance_outside_single_booking_dates_is_preserved(self):
        self.sale('2026-11-03')
        aid=self.con.execute("INSERT INTO attendance(student_id,group_id,date,status,note) VALUES (?,?,?,'present','Исторический факт')",(self.sid,self.group,'2026-11-17')).lastrowid
        before=self.con.total_changes
        row=self.rows('2026-11-17')[0]
        self.assertEqual(row['attendance_id'],aid);self.assertEqual(row['status'],'present')
        self.assertEqual(row['note'],'Исторический факт')
        self.assertEqual(self.con.total_changes,before)

    def test_month_view_reuses_entitlement_evidence_and_has_only_four_booked_dates(self):
        from crm import subscriptiondates
        booked=['2026-11-03','2026-11-06','2026-11-10','2026-11-13']
        for day in booked:self.sale(day)
        with patch.object(subscriptiondates,'calendar_entitlements',wraps=subscriptiondates.calendar_entitlements) as evidence:
            view=sv.journal_month(self.con,self.group,'2026-11')
        self.assertEqual([r['date'] for r in view['days'] if r['list']],booked)
        self.assertEqual(evidence.call_count,1)

    def test_retained_erp_single_evidence_does_not_rewrite_old_month_or_create_dates(self):
        from crm import erpsingles
        legacy=self.sale(kind='month',lessons=4)
        with patch.object(erpsingles,'subscription_evidence',return_value={legacy:{'kind':'single','single_lessons':4,'known_dates':[]}}):
            for day in ('2026-11-03','2026-11-06','2026-11-10'):
                self.assertEqual(self.rows(day),[])
                self.assertEqual(self.rows(day,False),[])
        self.assertEqual(self.con.execute('SELECT kind FROM subscriptions WHERE id=?',(legacy,)).fetchone()[0],'month')

    def test_month_in_another_group_does_not_fill_unknown_single_dates(self):
        from crm import erpsingles
        erpsingles.init(self.con)
        single=self.sale()
        self.con.execute("INSERT INTO erp_single_sales(source_id,student_id,group_id,subscription_id,source_signature,native_signature) VALUES ('source-single',?,?,?,'synthetic','synthetic')",(self.sid,self.group,single))
        other=schedule.save_group(self.con,{'name':'Другая группа','branch_id':self.branch,'weekdays':[1,4]})
        eid=sv.add_enrollment(self.con,self.sid,other,None,'fixed',self.user)
        self.con.execute("""INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at)
            VALUES (?,?,'2026-11','fixed',8,1000,8000,'paid','2026-11-01')""",(self.sid,eid))
        self.assertEqual(self.rows('2026-11-03'),[])
        self.assertEqual(self.rows('2026-11-03',False),[])
        self.assertTrue(any(r['id']==self.sid for r in sv.journal(self.con,other,'2026-11-03',True)['rows']))
