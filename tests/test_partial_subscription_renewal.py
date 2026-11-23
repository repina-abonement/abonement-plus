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

    def test_manual_due_survives_refresh_until_next_subscription_month(self):
        self.sale('2026-11')
        tid = self.task()['id']
        tasks.edit(self.con, tid, {'due_at': '2026-11-24'}, self.u)
        tasks.refresh_subscription_renewals(self.con)
        self.assertEqual(self.task()['due_at'][:10], '2026-11-24')
        self.sale('2026-12')
        tasks.refresh_subscription_renewals(self.con)
        self.assertEqual(self.task()['due_at'][:10], '2026-12-20')
        self.assertIsNone(self.task()['renewal_manual_due_at'])

    def test_partial_month_twentieth_and_note(self):
        self.sale('2026-11')
        task=self.task()
        self.assertEqual(task['due_at'],'2026-11-20T23:59')
        self.assertIn('30.11.2026',task['renewal_reason'])
        self.assertIn('Неполный месяц',task['renewal_reason'])
        self.assertNotIn('3 календарных дня',task['renewal_reason'])
        self.assertIsNone(task['assignee_id'])

    def test_early_end_december_still_twentieth(self):
        q=sv.quote_for(self.con,self.sid,'2026-12',group_id=self.gid,paid_date='2026-11-16',user=self.u)
        excluded=[d for d in q['calendar_dates'] if d>'2026-12-16']
        invoice=self.sale('2026-12',excluded)
        sv.pay_subscription(self.con,invoice['id'],self.u,'card_branch',invoice['due'],'2026-11-16','partial-renewal-paid-1')
        self.assertEqual(self.task()['due_at'],'2026-12-20T23:59')
        self.assertIn('16.12.2026',self.task()['renewal_reason'])

    def test_full_following_month_supersedes_partial_current(self):
        self.sale('2026-11');tid=self.task()['id']
        invoice=self.sale('2026-12')
        control=self.con.execute("SELECT * FROM tasks WHERE auto_key=?",(f'subscription-payment:{self.sid}',)).fetchone()
        self.assertEqual((control['title'],control['status']),('Проверить оплату абонемента','open'))
        sv.pay_subscription(self.con,invoice['id'],self.u,'card_branch',invoice['due'],'2026-11-16','partial-renewal-paid-2')
        self.assertEqual(self.task()['id'],tid)
        self.assertEqual(self.task()['due_at'],'2026-12-20T23:59')
        self.assertNotIn('Неполный месяц',self.task()['renewal_reason'])
        tasks.schedule_renewal(self.con,self.sid)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM tasks WHERE kind='renew' AND status='open'").fetchone()[0],1)

    def test_refresh_preserves_completed_history_and_restores_open_renewal(self):
        result=self.sale('2026-11')
        self.con.execute("UPDATE subscriptions SET lessons=99 WHERE id=?",(result['id'],))
        self.con.execute("UPDATE tasks SET due_at='2026-11-27T23:59',note='' WHERE id=?",(self.task()['id'],))
        tasks.refresh_subscription_renewals(self.con)
        self.assertEqual(self.task()['due_at'],'2026-11-20T23:59')
        self.assertIn('включено занятий: 4',self.task()['renewal_reason'])
        completed_id = self.task()['id']
        self.con.execute("UPDATE tasks SET status='done',note='Completed' WHERE id=?",(completed_id,))
        tasks.refresh_subscription_renewals(self.con)
        self.assertEqual(tuple(self.con.execute('SELECT status,note FROM tasks WHERE id=?',(completed_id,)).fetchone()),('done','Completed'))
        self.assertEqual(self.task()['status'],'open')
        self.assertNotEqual(self.task()['id'],completed_id)

    def test_note_survives_refresh_and_reschedule(self):
        self.sale('2026-11')
        task=self.task()
        self.assertEqual(task['note'],'')
        self.con.execute('UPDATE tasks SET renewal_reason=NULL,note=? WHERE id=?',
                         (task['renewal_reason']+'\n\nСсылка отправлена',task['id']))
        tasks.refresh_subscription_renewals(self.con)
        self.assertEqual(self.task()['note'],'Ссылка отправлена')
        tasks.edit(self.con,task['id'],{'note':'Проверить оплату'},self.u)
        self.con.execute("UPDATE subscriptions SET valid_until='2026-11-25' WHERE student_id=?",(self.sid,))
        tasks.refresh_subscription_renewals(self.con)
        tasks.schedule_renewal(self.con,self.sid)
        self.assertEqual(self.task()['note'],'Проверить оплату')
        self.assertNotEqual(self.task()['renewal_reason'],task['renewal_reason'])
        self.assertEqual(self.task()['due_at'],task['due_at'])

    def test_paid_target_month_suppresses_erp_check_only_for_that_month(self):
        invoice = self.sale('2026-12')
        sv.pay_subscription(self.con, invoice['id'], self.u, 'card_branch', invoice['due'],
                            '2026-11-16', 'renewal-covered-december')
        self.assertIsNone(tasks.import_erp_task(self.con, 'Проверить продление', self.sid, '2026-11-27'))
        self.assertFalse(tasks.renewal_covered(self.con, self.sid, '2026-12-21', 'Проверить продление'))
        self.assertFalse(tasks.renewal_covered(self.con, self.sid, '2026-11-27', 'Проверить оплату абонемента'))
        self.con.execute("UPDATE subscriptions SET status='cancelled' WHERE id=?", (invoice['id'],))
        self.assertFalse(tasks.renewal_covered(self.con, self.sid, '2026-11-27', 'Проверить продление'))

    def test_valid_until_does_not_change_renewal_date(self):
        invoice=self.sale('2026-12')
        self.con.execute("UPDATE subscriptions SET valid_until='2026-12-16' WHERE student_id=?",(self.sid,))
        sv.pay_subscription(self.con,invoice['id'],self.u,'card_branch',invoice['due'],'2026-11-16','partial-renewal-paid-3')
        self.assertEqual(self.task()['due_at'],'2026-12-20T23:59')

if __name__=='__main__':unittest.main()
