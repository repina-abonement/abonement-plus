"""A single sale grants only its recorded date, never the entire group month."""
from contextlib import redirect_stdout
from datetime import date
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crm import clientattendance, db, erpsingles, makeuppolicy, schedule, services as sv, subscriptiondates


class SingleCalendarEntitlements(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        self.con=db.connect(str(Path(directory.name)/'single-calendar.sqlite3'));self.addCleanup(self.con.close)
        with redirect_stdout(io.StringIO()):db.init_db(self.con)
        self.today=patch.object(sv,'today',return_value=date(2026,11,19));self.today.start();self.addCleanup(self.today.stop)
        self.user={'id':1,'role':'admin','name':'Synthetic'}
        branch=schedule.save_branch(self.con,{'name':'Synthetic'})
        self.group=schedule.save_group(self.con,{'name':'Synthetic Wed Sun','branch_id':branch,'weekdays':[2,6],'time':'16:45','age':'4-5'})
        self.other=schedule.save_group(self.con,{'name':'Synthetic Other Wed Sun','branch_id':branch,'weekdays':[2,6],'time':'18:00','age':'4-5'})
        self.sid=sv.create_student(self.con,{'name':'Synthetic Child','parent_name':'Synthetic Parent','phone':'+79000000999','funnel':'members','group_id':self.group},self.user)
        self.eid=self.con.execute('SELECT id FROM enrollments WHERE student_id=?',(self.sid,)).fetchone()[0]
        self.con.execute("UPDATE enrollments SET start_date='2026-11-01',created_at='2026-11-01' WHERE id=?",(self.eid,))

    def sale(self,kind='single',lessons=1,eid=None):
        return self.con.execute('''INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,kind,lessons,per_lesson,price,status,created_at)
            VALUES (?,?,'2026-11',?,?,?,2500,?,'paid','2026-11-01')''',
            (self.sid,eid,'single' if kind=='single' else 'fixed',kind,lessons,lessons*2500)).lastrowid

    def booking(self,sub,day,group=None):
        self.con.execute('''INSERT INTO single_visits(student_id,group_id,date,status,created_at,subscription_id)
            VALUES (?,?,?,'booked','2026-11-01',?)''',(self.sid,group or self.group,day,sub))

    def calendar(self):return clientattendance.calendar(self.con,self.sid,'2026-11')

    def test_four_known_single_dates_do_not_expand_to_nine_group_dates(self):
        exact=['2026-11-01','2026-11-08','2026-11-15','2026-11-22']
        for day in exact:self.booking(self.sale(),day)
        result=self.calendar()
        self.assertEqual([event['date'] for event in result['events']],exact)
        self.assertTrue(all(event['kind']=='single' for event in result['events']))
        self.assertFalse(result['date_warnings'])

    def test_four_undated_single_sales_remain_undated_and_finances_unchanged(self):
        for _ in range(4):self.sale()
        before=[tuple(row) for row in self.con.execute('SELECT * FROM subscriptions ORDER BY id')]
        result=self.calendar()
        self.assertEqual(result['events'],[])
        self.assertEqual(result['date_warnings'][0]['lessons'],4)
        self.assertIn('без записанных дат',result['date_warnings'][0]['message'])
        self.assertEqual([tuple(row) for row in self.con.execute('SELECT * FROM subscriptions ORDER BY id')],before)
        self.assertEqual(self.con.execute('SELECT count(*) FROM payments').fetchone()[0],0)
        self.assertEqual(self.con.execute('SELECT count(*) FROM single_visits').fetchone()[0],0)

    def test_actual_marks_survive_without_claiming_unknown_single_dates(self):
        self.sale(eid=self.eid)
        for day,status,kind in [('2026-11-08','present',''),('2026-11-15','absent','single')]:
            self.con.execute('INSERT INTO attendance(student_id,group_id,date,status,erp_visit_kind) VALUES (?,?,?,?,?)',(self.sid,self.group,day,status,kind))
        result=self.calendar()
        self.assertEqual([(event['date'],event['kind'],event['status']) for event in result['events']],
                         [('2026-11-08','visit','present'),('2026-11-15','single','absent')])
        self.assertEqual(result['attended'],1)
        self.assertEqual(result['date_warnings'][0]['lessons'],1)
        self.assertEqual(self.con.execute('SELECT count(*) FROM attendance').fetchone()[0],2)

    def test_regular_month_without_erp_evidence_keeps_existing_nine_day_plan(self):
        self.sale('month',4,self.eid)
        self.con.execute('UPDATE subscriptions SET erp_full_managed=1')
        result=self.calendar()
        self.assertEqual(len(result['events']),9)
        self.assertTrue(all(event['kind']=='subscription' for event in result['events']))
        self.assertFalse(result['date_warnings'])

    def test_month_subscription_and_extra_single_remain_separate(self):
        self.sale('month',9,self.eid)
        self.booking(self.sale(),'2026-11-14',self.other)
        result=self.calendar()
        self.assertEqual(sum(event['kind']=='subscription' for event in result['events']),9)
        self.assertEqual(sum(event['kind']=='single' for event in result['events']),1)
        self.assertFalse(result['date_warnings'])

    def test_undated_single_other_group_does_not_erase_real_month_or_expand_its_own_group(self):
        other_eid=self.con.execute("INSERT INTO enrollments(student_id,group_id,tariff,active,created_at,start_date) VALUES (?,?,'fixed',1,'2026-11-01','2026-11-01')",(self.sid,self.other)).lastrowid
        self.sale('month',9,other_eid)
        sub=self.sale()
        # This preserves the source group despite a sale with no lesson date
        # and no native enrollment link. It is not a guessed booking date.
        erpsingles.init(self.con)
        self.con.execute('''INSERT INTO erp_single_sales(source_id,student_id,group_id,subscription_id,source_signature,lesson_date,native_signature)
            VALUES ('synthetic-source',?,?,?,'synthetic',NULL,'synthetic')''',(self.sid,self.group,sub))
        result=self.calendar()
        self.assertEqual(len(result['events']),9)
        self.assertTrue(all(event['group_id']==self.other for event in result['events']))
        self.assertEqual(result['date_warnings'][0]['lessons'],1)
        future=clientattendance.calendar(self.con,self.sid,'2026-12')
        self.assertEqual(len(future['events']),9)
        self.assertTrue(all(event['group_id']==self.other for event in future['events']))

    def legacy_source(self,subid,tariffs):
        headers=['ERP kind','month','quantity','lesson date']
        rows=[[tariff,'2026-11',str(quantity),''] for tariff,quantity in tariffs]
        mapping={'tariff':0,'month':1,'lessons':2,'lesson_date':3}
        fid=self.con.execute('''INSERT INTO erp_full_files(digest,filename,kind,headers,rows,mapping,imported_at)
            VALUES ('synthetic-legacy','synthetic.csv','subs',?,?,?,'2026-11-19')''',
            (json.dumps(headers),json.dumps(rows),json.dumps(mapping))).lastrowid
        for index in range(len(rows)):
            self.con.execute("INSERT INTO erp_full_rows(file_id,row_num,student_id,state,reason,target_table,target_id) VALUES (?,? ,?,'native','','subscriptions',?)",(fid,index+2,self.sid,subid))

    def test_legacy_known_single_only_source_is_not_a_monthly_entitlement(self):
        sub=self.sale('month',4,self.eid)
        self.legacy_source(sub,[('Разовое занятие',4)])
        result=self.calendar()
        self.assertEqual(result['events'],[])
        self.assertEqual(result['date_warnings'][0]['lessons'],4)
        self.assertEqual(makeuppolicy.paid(self.con,self.sid,'2026-11'),[])
        native=self.con.execute('SELECT kind,lessons,price FROM subscriptions WHERE id=?',(sub,)).fetchone()
        self.assertEqual(tuple(native),('month',4,10000))

    def test_mixed_source_keeps_month_component_and_flags_undated_singles(self):
        sub=self.sale('month',13,self.eid)
        self.legacy_source(sub,[('Негибкий',9),('Разовое занятие',4)])
        result=self.calendar()
        self.assertEqual(sum(event['kind']=='subscription' for event in result['events']),9)
        self.assertEqual(result['date_warnings'][0]['lessons'],4)
        self.assertEqual(len(makeuppolicy.paid(self.con,self.sid,'2026-11')),1)

    def review(self,number,group=None,**extra):
        erpsingles.init(self.con)
        payload={'tariff':'Разовое занятие','month':'2026-11','lessons':'1','price':'2500',
                 '_source_key':f'subs:synthetic:{number}',**extra}
        self.con.execute("INSERT INTO erp_single_reviews(fingerprint,student_id,group_id,source_id,payload,reason,created_at) VALUES (?,?,?,'',?,'Missing sale identity','2026-11-19')",(f'synthetic-{number}',self.sid,group,json.dumps(payload)))

    def test_unresolved_single_sales_with_source_group_do_not_generate_nine_dates(self):
        for number in range(4):self.review(number,self.group)
        result=self.calendar()
        self.assertEqual(result['events'],[])
        self.assertEqual(result['date_warnings'][0]['kind'],'single_source_review')
        self.assertEqual(result['date_warnings'][0]['lessons'],4)
        self.assertEqual(self.con.execute('SELECT count(*) FROM subscriptions').fetchone()[0],0)

    def test_payment_review_never_becomes_a_single_sale_entitlement(self):
        self.review(1,self.group,_source_key='payments:synthetic:1',payment_type='Оплата')
        result=self.calendar()
        self.assertEqual(len(result['events']),9)
        self.assertFalse(result['date_warnings'])

    def test_unknown_source_group_warns_without_assigning_it_to_primary_group(self):
        self.review(1,None)
        entitlements=subscriptiondates.calendar_entitlements(self.con,self.sid,'2026-11')
        self.assertNotIn(self.group,entitlements['single_groups'])
        self.assertEqual(entitlements['date_warnings'][0]['kind'],'single_source_review')

    def test_month_subscription_is_not_erased_by_an_unresolved_extra_single_sale(self):
        self.sale('month',9,self.eid)
        self.review(1,self.group)
        result=self.calendar()
        self.assertEqual(len(result['events']),9)
        self.assertEqual(result['date_warnings'][0]['lessons'],1)


    def test_old_review_does_not_duplicate_a_native_sale_or_its_resolved_date(self):
        sub=self.sale()
        erpsingles.init(self.con)
        self.con.execute("INSERT INTO erp_single_sales(source_id,student_id,group_id,subscription_id,source_signature,lesson_date,native_signature) VALUES ('known-source',?,?,?,'synthetic',NULL,'synthetic')",(self.sid,self.group,sub))
        self.review(1,self.group,sale_id='known-source')
        unresolved=self.calendar()
        self.assertEqual(len(unresolved['date_warnings']),1)
        self.assertEqual(unresolved['date_warnings'][0]['kind'],'single_dates_unknown')
        self.assertEqual(unresolved['date_warnings'][0]['lessons'],1)
        self.booking(sub,'2026-11-08')
        self.con.execute("UPDATE erp_single_sales SET lesson_date='2026-11-08' WHERE source_id='known-source'")
        resolved=self.calendar()
        self.assertFalse(resolved['date_warnings'])
        self.assertEqual([(event['date'],event['kind']) for event in resolved['events']],[('2026-11-08','single')])


    def test_single_only_history_does_not_create_next_month_monthly_dates(self):
        sub=self.sale()
        self.booking(sub,'2026-11-08')
        result=clientattendance.calendar(self.con,self.sid,'2026-12')
        self.assertEqual(result['events'],[])
        self.assertEqual(result['date_warnings'][0]['kind'],'single_dates_not_booked')
        self.assertEqual(self.con.execute('SELECT count(*) FROM single_visits').fetchone()[0],1)

    def test_review_only_single_group_does_not_become_monthly_next_month(self):
        self.review(1,self.group)
        result=clientattendance.calendar(self.con,self.sid,'2026-12')
        self.assertEqual(result['events'],[])
        self.assertTrue(subscriptiondates.calendar_entitlements(self.con,self.sid,'2026-12')['single_only'])

    def test_unknown_group_single_review_does_not_invent_monthly_dates(self):
        self.review(1,None)
        current=self.calendar();future=clientattendance.calendar(self.con,self.sid,'2026-12')
        self.assertEqual(current['events'],[])
        self.assertEqual(future['events'],[])
        context=subscriptiondates.calendar_entitlements(self.con,self.sid,'2026-12')
        self.assertFalse(context['single_groups'])
        self.assertTrue(context['single_only'])

    def test_old_occasional_single_does_not_disable_regular_monthly_client(self):
        self.sale('month',9,self.eid)
        self.booking(self.sale(),'2026-11-08')
        result=clientattendance.calendar(self.con,self.sid,'2026-12')
        self.assertEqual(len(result['events']),9)
        self.assertTrue(all(event['kind']=='subscription' for event in result['events']))

    def test_real_next_month_subscription_can_change_single_mode_to_regular(self):
        self.booking(self.sale(),'2026-11-08')
        self.con.execute("INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,kind,lessons,per_lesson,price,status,created_at) VALUES (?,?,'2026-12','fixed','month',9,1000,9000,'paid','2026-12-01')",(self.sid,self.eid))
        result=clientattendance.calendar(self.con,self.sid,'2026-12')
        self.assertEqual(len(result['events']),9)
        self.assertTrue(all(event['kind']=='subscription' for event in result['events']))


    def test_cross_month_reschedule_preserves_sale_month_without_unknown_date_warning(self):
        originals=['2026-11-04','2026-11-11','2026-11-18','2026-11-25']
        sales=[]
        for day in originals:
            sub=self.sale();sales.append(sub);self.booking(sub,day)
        self.con.execute("UPDATE single_visits SET date='2026-12-02' WHERE subscription_id=?",(sales[0],))
        before=[tuple(row) for row in self.con.execute('SELECT id,month,lessons,price FROM subscriptions ORDER BY id')]
        november=self.calendar()
        self.assertEqual([(event['date'],event['kind']) for event in november['events']],
                         [(day,'single') for day in originals[1:]])
        self.assertFalse(november['date_warnings'])
        december=clientattendance.calendar(self.con,self.sid,'2026-12')
        self.assertEqual([(event['date'],event['kind']) for event in december['events']],[('2026-12-02','single')])
        self.assertFalse(december['date_warnings'])
        self.assertEqual([tuple(row) for row in self.con.execute('SELECT id,month,lessons,price FROM subscriptions ORDER BY id')],before)
        self.assertEqual(sum(row[3] for row in before),10000)
        self.assertEqual(self.con.execute('SELECT count(*) FROM single_visits').fetchone()[0],4)

    def test_other_sales_future_booking_cannot_fill_an_undated_sale(self):
        unknown=self.sale()
        moved=self.sale();self.booking(moved,'2026-12-02')
        warnings=self.calendar()['date_warnings']
        self.assertEqual(len(warnings),1)
        self.assertEqual(warnings[0]['lessons'],1)
        self.assertEqual(warnings[0]['subscription_ids'],[unknown])

    def test_future_booking_does_not_reclassify_earlier_unrelated_month_plan(self):
        self.con.execute("INSERT INTO subscriptions(student_id,month,tariff,kind,lessons,per_lesson,price,status,created_at) VALUES (?,'2026-12','single','single',1,2500,2500,'paid','2026-12-01')",(self.sid,))
        sub=self.con.execute('SELECT id FROM subscriptions').fetchone()[0]
        self.booking(sub,'2026-12-02')
        context=subscriptiondates.calendar_entitlements(self.con,self.sid,'2026-11')
        self.assertFalse(context['single_only'])
        self.assertFalse(context['single_groups'])


    def recovered_with_prior_month(self,old_wrong_group=False):
        self.con.execute("UPDATE groups SET time='18:45' WHERE id=?",(self.group,))
        old_group=self.group;old_enrollment=self.eid
        if old_wrong_group:
            old_group=self.other
            self.con.execute("UPDATE groups SET time='17:45' WHERE id=?",(old_group,))
            self.con.execute('UPDATE enrollments SET active=0 WHERE id=?',(self.eid,))
            old_enrollment=self.con.execute("INSERT INTO enrollments(student_id,group_id,tariff,active,created_at,start_date) VALUES (?,?,'fixed',1,'2026-10-01','2026-10-01')",(self.sid,old_group)).lastrowid
            self.con.execute('UPDATE students SET group_id=? WHERE id=?',(old_group,self.sid))
        self.con.execute("UPDATE enrollments SET start_date='2026-10-01',created_at='2026-10-01' WHERE id=?",(old_enrollment,))
        october=self.con.execute("INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,kind,lessons,per_lesson,price,status,created_at) VALUES (?,?,'2026-10','fixed','month',8,1000,8000,'paid','2026-10-01')",(self.sid,old_enrollment)).lastrowid
        aggregate=self.con.execute("INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,kind,lessons,per_lesson,price,status,created_at) VALUES (?,?,'2026-11','fixed','month',4,2500,10000,'cancelled','2026-11-01')",(self.sid,old_enrollment)).lastrowid
        before_october=[(event['date'],event['group_id'],event['kind']) for event in clientattendance.calendar(self.con,self.sid,'2026-10')['events']]
        erpsingles.init(self.con)
        for number,day in enumerate(['2026-12-02','2026-11-11','2026-11-18','2026-11-25']):
            sub=self.sale();self.booking(sub,day)
            self.con.execute("INSERT INTO erp_single_sales(source_id,student_id,group_id,subscription_id,source_signature,source_payload,lesson_date,native_signature) VALUES (?,?,?,?,'synthetic',?,?,'synthetic')",(f'synthetic-recovered-{number}',self.sid,self.group,sub,json.dumps({'recovery_aggregate_id':aggregate}),'2026-11-04' if not number else day))
        return {'aggregate':aggregate,'october':october,'old_group':old_group,'before_october':before_october}

    def assert_recovered_calendar_overrides_past_month_only(self,case):
        before=[tuple(row) for row in self.con.execute('SELECT * FROM subscriptions ORDER BY id')]
        november=self.calendar();december=clientattendance.calendar(self.con,self.sid,'2026-12')
        self.assertEqual([(event['date'],event['kind'],event['group_id']) for event in november['events']],
                         [(day,'single',self.group) for day in ['2026-11-11','2026-11-18','2026-11-25']])
        self.assertEqual([(event['date'],event['kind'],event['group_id']) for event in december['events']],[('2026-12-02','single',self.group)])
        self.assertFalse(november['date_warnings']);self.assertFalse(december['date_warnings'])
        october=[(event['date'],event['group_id'],event['kind']) for event in clientattendance.calendar(self.con,self.sid,'2026-10')['events']]
        self.assertEqual(october,case['before_october'])
        self.assertEqual(len(october),8)
        self.assertEqual([tuple(row) for row in self.con.execute('SELECT * FROM subscriptions ORDER BY id')],before)
        self.assertTrue(self.con.execute('SELECT active FROM enrollments WHERE group_id=? AND student_id=?',(case['old_group'],self.sid)).fetchone()[0])
        self.assertEqual(self.con.execute('SELECT count(*) FROM payments').fetchone()[0],0)

    def test_audited_single_recovery_overrides_past_month_schedule_in_same_group(self):
        case=self.recovered_with_prior_month()
        self.assert_recovered_calendar_overrides_past_month_only(case)
        self.con.execute("INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,kind,lessons,per_lesson,price,status,created_at) VALUES (?,?,'2027-01','fixed','month',9,1000,9000,'paid','2027-01-01')",(self.sid,self.eid))
        january=clientattendance.calendar(self.con,self.sid,'2027-01')
        self.assertGreater(len(january['events']),1)
        self.assertTrue(all(event['kind']=='subscription' for event in january['events']))
        self.assertTrue(subscriptiondates.recurring_calendar_allowed(self.group,subscriptiondates.calendar_entitlements(self.con,self.sid,'2027-01')))

    def test_recovery_blocks_old_wrong_group_and_keeps_exact_new_group_dates(self):
        case=self.recovered_with_prior_month(old_wrong_group=True)
        self.assert_recovered_calendar_overrides_past_month_only(case)
        context=subscriptiondates.calendar_entitlements(self.con,self.sid,'2026-12')
        self.assertFalse(subscriptiondates.recurring_calendar_allowed(case['old_group'],context))
        self.assertFalse(subscriptiondates.recurring_calendar_allowed(self.group,context))


    def test_recovery_marker_must_match_cancelled_aggregate_owner_and_source_month(self):
        case=self.recovered_with_prior_month()
        other=sv.create_student(self.con,{'name':'Other Synthetic Child','parent_name':'Other Parent','phone':'+79000000888','funnel':'members'},self.user)
        for invalid in ('string','active_aggregate','different_source_month','other_student'):
            with self.subTest(invalid=invalid):
                self.con.execute("UPDATE subscriptions SET student_id=?,month='2026-11',status='cancelled' WHERE id=?",(self.sid,case['aggregate']))
                marker=case['aggregate']
                if invalid=='string':marker=str(marker)
                if invalid=='active_aggregate':self.con.execute("UPDATE subscriptions SET status='paid' WHERE id=?",(marker,))
                if invalid=='different_source_month':self.con.execute("UPDATE subscriptions SET month='2026-12' WHERE id=?",(marker,))
                if invalid=='other_student':self.con.execute('UPDATE subscriptions SET student_id=? WHERE id=?',(other,marker))
                self.con.execute('UPDATE erp_single_sales SET source_payload=?',(json.dumps({'recovery_aggregate_id':marker}),))
                context=subscriptiondates.calendar_entitlements(self.con,self.sid,'2026-12')
                self.assertTrue(subscriptiondates.recurring_calendar_allowed(self.group,context))

    def test_recovery_only_restricts_its_old_and_new_groups_for_this_student(self):
        case=self.recovered_with_prior_month(old_wrong_group=True)
        branch=self.con.execute('SELECT branch_id FROM groups WHERE id=?',(self.group,)).fetchone()[0]
        unrelated=schedule.save_group(self.con,{'name':'Unrelated Synthetic','branch_id':branch,'weekdays':[1],'time':'20:00'})
        context=subscriptiondates.calendar_entitlements(self.con,self.sid,'2026-12')
        self.assertEqual(context['single_groups'],{case['old_group'],self.group})
        self.assertTrue(subscriptiondates.recurring_calendar_allowed(unrelated,context))
        other=sv.create_student(self.con,{'name':'Other Synthetic Child','parent_name':'Other Parent','phone':'+79000000888','funnel':'members','group_id':self.group},self.user)
        self.assertTrue(subscriptiondates.recurring_calendar_allowed(self.group,subscriptiondates.calendar_entitlements(self.con,other,'2026-12')))



if __name__=='__main__':unittest.main()
