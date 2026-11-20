"""A fully refunded historical bill cannot recreate implied calendar rights.

Synthetic isolated DBs only. Financial refund execution belongs to the engine
suite; these cases exercise the persisted exclusion marker and read projections.
"""
import json
import unittest

from crm import clientattendance, makeuppolicy, services, subscriptiondates
from tests import test_single_calendar_entitlements as fixture


class HistoricalRefundCalendar(unittest.TestCase):
    setUp=fixture.SingleCalendarEntitlements.setUp
    sale=fixture.SingleCalendarEntitlements.sale
    booking=fixture.SingleCalendarEntitlements.booking
    calendar=fixture.SingleCalendarEntitlements.calendar

    def cancel_historical(self,sub,group=None):
        self.con.execute('''CREATE TABLE IF NOT EXISTS historical_refund_exclusions(
            subscription_id INTEGER NOT NULL,student_id INTEGER NOT NULL,
            group_id INTEGER NOT NULL,month TEXT NOT NULL,reason TEXT NOT NULL,
            PRIMARY KEY(subscription_id,group_id,month))''')
        self.con.execute("UPDATE subscriptions SET status='cancelled',historical_sale=1 WHERE id=?",(sub,))
        self.con.execute('''INSERT INTO historical_refund_exclusions(subscription_id,student_id,group_id,month,reason)
            VALUES (?,?,?,'2026-11','Synthetic historical full refund')''',(sub,self.sid,group or self.group))

    def group_events(self,result,gid):
        return [event for event in result['events'] if event['group_id']==gid]

    def test_cancelled_historical_bill_does_not_recreate_active_enrollment_schedule(self):
        sub=self.sale('month',9,self.eid)
        original=tuple(self.con.execute('SELECT price,lessons,created_at FROM subscriptions WHERE id=?',(sub,)).fetchone())
        self.cancel_historical(sub)
        self.assertEqual(self.calendar()['events'],[])
        self.assertEqual(makeuppolicy.paid(self.con,self.sid,'2026-11'),[])
        evidence=subscriptiondates.calendar_entitlements(self.con,self.sid,'2026-11')
        self.assertFalse(subscriptiondates.recurring_calendar_allowed(self.group,evidence))
        for paid_only in (False,True):
            self.assertFalse(any(row['id']==self.sid for row in services.journal(self.con,self.group,'2026-11-22',paid_only=paid_only)['rows']))
        current=self.con.execute('SELECT price,lessons,created_at FROM subscriptions WHERE id=?',(sub,)).fetchone()
        self.assertEqual(tuple(current),original)

    def test_real_attendance_survives_as_facts_without_paid_rights(self):
        sub=self.sale('month',9,self.eid)
        self.con.execute("INSERT INTO attendance(student_id,group_id,date,status,note) VALUES (?,?,'2026-11-08','present','Synthetic factual attendance')",(self.sid,self.group))
        self.con.execute("INSERT INTO attendance(student_id,group_id,date,status,note) VALUES (?,?,'2026-11-15','absent','Synthetic factual absence')",(self.sid,self.group))
        before=[tuple(row) for row in self.con.execute('SELECT * FROM attendance ORDER BY id')]
        self.cancel_historical(sub)
        result=self.calendar()
        self.assertEqual([(event['date'],event['kind'],event['status']) for event in result['events']],
            [('2026-11-08','visit','present'),('2026-11-15','visit','absent')])
        self.assertEqual(result['attended'],1)
        rows=[row for row in services.journal(self.con,self.group,'2026-11-08',paid_only=True)['rows'] if row['id']==self.sid]
        self.assertEqual(len(rows),1)
        self.assertTrue(rows[0]['unpaid'])
        self.assertEqual(rows[0]['status'],'present')
        self.assertEqual([tuple(row) for row in self.con.execute('SELECT * FROM attendance ORDER BY id')],before)

    def test_other_paid_monthly_purchase_in_same_group_keeps_its_calendar(self):
        cancelled=self.sale('month',9,self.eid)
        self.cancel_historical(cancelled)
        replacement=self.sale('month',9,self.eid)
        result=self.calendar()
        self.assertEqual(len(result['events']),9)
        self.assertTrue(all(event['kind']=='subscription' for event in result['events']))
        self.assertEqual([row['id'] for row in makeuppolicy.paid(self.con,self.sid,'2026-11')],[replacement])
        rows=[row for row in services.journal(self.con,self.group,'2026-11-22',paid_only=True)['rows'] if row['id']==self.sid]
        self.assertEqual(len(rows),1)
        self.assertFalse(rows[0]['unpaid'])

    def test_other_group_purchase_does_not_reopen_refunded_group(self):
        cancelled=self.sale('month',9,self.eid)
        other_eid=self.con.execute("INSERT INTO enrollments(student_id,group_id,tariff,active,created_at,start_date) VALUES (?,?,'fixed',1,'2026-11-01','2026-11-01')",(self.sid,self.other)).lastrowid
        other_bill=self.sale('month',9,other_eid)
        self.cancel_historical(cancelled)
        result=self.calendar()
        self.assertEqual(len(result['events']),9)
        self.assertTrue(all(event['group_id']==self.other for event in result['events']))
        self.assertEqual([row['id'] for row in makeuppolicy.paid(self.con,self.sid,'2026-11')],[other_bill])
        self.assertFalse(any(row['id']==self.sid for row in services.journal(self.con,self.group,'2026-11-22',paid_only=True)['rows']))

    def test_exact_single_purchase_survives_without_restoring_whole_schedule(self):
        cancelled=self.sale('month',9,self.eid)
        self.booking(self.sale(),'2026-11-12')
        self.cancel_historical(cancelled)
        result=self.calendar()
        self.assertEqual([(event['date'],event['kind']) for event in result['events']],[('2026-11-12','single')])
        self.assertEqual(makeuppolicy.paid(self.con,self.sid,'2026-11'),[])

    def test_replacement_with_explicit_dates_is_not_blanket_excluded(self):
        cancelled=self.sale('month',9,self.eid)
        self.cancel_historical(cancelled)
        replacement=self.sale('month',2,self.eid)
        days=['2026-11-08','2026-11-15']
        self.con.execute('UPDATE subscriptions SET historical_sale=1,lesson_schedule=? WHERE id=?',
            (json.dumps([{'group_id':self.group,'dates':days}]),replacement))
        self.assertEqual([event['date'] for event in self.calendar()['events']],days)
        self.assertFalse(subscriptiondates.excluded(self.con,self.sid,self.group,days[0]))

    def test_missing_marker_table_preserves_legacy_database_behavior(self):
        self.sale('month',9,self.eid)
        self.con.execute('DROP TABLE IF EXISTS historical_refund_exclusions')
        self.assertEqual(len(self.calendar()['events']),9)
        evidence=subscriptiondates.calendar_entitlements(self.con,self.sid,'2026-11')
        self.assertTrue(subscriptiondates.recurring_calendar_allowed(self.group,evidence))
        self.assertEqual(evidence['historical_refund_groups'],set())

    def test_marker_only_scopes_its_confirmed_month_and_student(self):
        self.cancel_historical(self.sale('month',9,self.eid))
        december=subscriptiondates.calendar_entitlements(self.con,self.sid,'2026-12')
        self.assertEqual(december['historical_refund_groups'],set())
        other=services.create_student(self.con,{'name':'Synthetic Second','parent_name':'Synthetic Parent2',
            'phone':'+79000000998','funnel':'members','group_id':self.group},self.user)
        context=subscriptiondates.calendar_entitlements(self.con,other,'2026-11')
        self.assertTrue(subscriptiondates.recurring_calendar_allowed(self.group,context))
        self.assertEqual(context['historical_refund_groups'],set())

    def test_multi_group_refund_markers_preserve_a_replacement_in_one_group(self):
        cancelled=self.sale('month',9,self.eid)
        self.cancel_historical(cancelled)
        other_eid=self.con.execute("INSERT INTO enrollments(student_id,group_id,tariff,active,created_at,start_date) VALUES (?,?,'fixed',1,'2026-11-01','2026-11-01')",(self.sid,self.other)).lastrowid
        self.con.execute('''INSERT INTO historical_refund_exclusions(subscription_id,student_id,group_id,month,reason)
            VALUES (?,?,?,'2026-11','Synthetic second confirmed component')''',(cancelled,self.sid,self.other))
        self.assertEqual(self.calendar()['events'],[])
        self.sale('month',9,other_eid)
        result=self.calendar()
        self.assertEqual(len(result['events']),9)
        self.assertTrue(all(event['group_id']==self.other for event in result['events']))
        context=subscriptiondates.calendar_entitlements(self.con,self.sid,'2026-11')
        self.assertFalse(subscriptiondates.recurring_calendar_allowed(self.group,context))
        self.assertTrue(subscriptiondates.recurring_calendar_allowed(self.other,context))
