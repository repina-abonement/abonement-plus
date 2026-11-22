import json
import unittest
from unittest.mock import patch
from tests import test_sale_paid_date as fixture
from crm import services as sv, smart, tasks


class PartialRenewalTest(unittest.TestCase):
    setUp=fixture.SaleDateTest.setUp

    def sale(self,month,excluded=None):
        with patch.object(smart,'REQUIRE',False):
            return sv.create_invoice(self.con,self.sid,month,self.u,group_id=self.gid,paid_date='2026-11-16',excluded_dates=excluded)

    def task(self):
        return self.con.execute("SELECT * FROM tasks WHERE auto_key=?",(f'subscription-renew:{self.sid}',)).fetchone()

    def test_partial_month_last_actual_lesson_minus_three_and_note(self):
        self.sale('2026-11')
        task=self.task()
        self.assertEqual(task['due_at'],'2026-11-27T23:59')
        self.assertIn('30.11.2026',task['note'])
        self.assertIn('Неполный месяц',task['note'])
        self.assertIn('3 календарных дня',task['note'])
        self.assertIsNone(task['assignee_id'])

    def test_early_end_december_not_twentieth(self):
        q=sv.quote_for(self.con,self.sid,'2026-12',group_id=self.gid,paid_date='2026-11-16',user=self.u)
        excluded=[d for d in q['calendar_dates'] if d>'2026-12-16']
        invoice=self.sale('2026-12',excluded)
        sv.pay_subscription(self.con,invoice['id'],self.u,'card_branch',invoice['due'],'2026-11-16','partial-renewal-paid-1')
        self.assertEqual(self.task()['due_at'],'2026-12-13T23:59')
        self.assertIn('16.12.2026',self.task()['note'])

    def test_full_following_month_supersedes_partial_current(self):
        self.sale('2026-11');tid=self.task()['id']
        invoice=self.sale('2026-12')
        control=self.con.execute("SELECT * FROM tasks WHERE auto_key=?",(f'subscription-payment:{self.sid}',)).fetchone()
        self.assertEqual((control['title'],control['status']),('Проверить оплату абонемента','open'))
        sv.pay_subscription(self.con,invoice['id'],self.u,'card_branch',invoice['due'],'2026-11-16','partial-renewal-paid-2')
        self.assertEqual(self.task()['id'],tid)
        self.assertEqual(self.task()['due_at'],'2026-12-20T23:59')
        self.assertNotIn('Неполный месяц',self.task()['note'])
        tasks.schedule_renewal(self.con,self.sid)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM tasks WHERE kind='renew' AND status='open'").fetchone()[0],1)

    def test_refresh_existing_only_open_automatic_and_snapshot_not_count(self):
        result=self.sale('2026-11')
        self.con.execute("UPDATE subscriptions SET lessons=99 WHERE id=?",(result['id'],))
        self.con.execute("UPDATE tasks SET due_at='2026-11-20T23:59',note='' WHERE id=?",(self.task()['id'],))
        tasks.refresh_subscription_renewals(self.con)
        self.assertEqual(self.task()['due_at'],'2026-11-27T23:59')
        self.assertIn('включено занятий: 4',self.task()['note'])
        self.con.execute("UPDATE tasks SET status='done',note='Completed' WHERE id=?",(self.task()['id'],))
        tasks.refresh_subscription_renewals(self.con)
        self.assertEqual((self.task()['status'],self.task()['note']),('done','Completed'))

    def test_valid_until_uses_last_included_lesson(self):
        invoice=self.sale('2026-12')
        self.con.execute("UPDATE subscriptions SET valid_until='2026-12-16' WHERE student_id=?",(self.sid,))
        sv.pay_subscription(self.con,invoice['id'],self.u,'card_branch',invoice['due'],'2026-11-16','partial-renewal-paid-3')
        self.assertEqual(self.task()['due_at'],'2026-12-13T23:59')

if __name__=='__main__':unittest.main()
