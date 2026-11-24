import unittest

from crm import targetedverifiedcareearly as batch


class VerifiedCareEarlyTest(unittest.TestCase):
    def test_consolidated_report_is_split_into_unique_safe_events(self):
        events=batch.events()
        self.assertEqual(38,len(events))
        self.assertEqual(len(events),len({row['source_id'] for row in events}))
        self.assertTrue(all(row['student_name'].strip() for row in events))
        self.assertTrue(all(row['note'].strip() for row in events))

    def test_incomplete_money_facts_never_claim_a_payment_method(self):
        incomplete=[row for row in batch.events() if row['event_kind']=='sale_fact']
        self.assertEqual(13,len(incomplete))
        self.assertTrue(all('payment_method' not in row for row in incomplete))
        self.assertTrue(all(row.get('clarification_task_title') for row in incomplete))

    def test_unknown_trial_times_are_not_invented(self):
        incomplete=[row for row in batch.events() if row['event_kind']=='trial_fact']
        self.assertEqual(6,len(incomplete))
        self.assertTrue(all('trial_time' not in row for row in incomplete))
        self.assertTrue(all(row.get('clarification_task_title') for row in incomplete))

    def test_petushkov_refund_is_confirmed_and_karimov_keeps_missing_date_open(self):
        rows={row['source_id']:row for row in batch.events()}
        refund=rows['sz:20643:петушков-возврат']
        self.assertEqual(('refund_fact',9450,'paykeeper_manual'),
                         (refund['event_kind'],refund['amount'],refund['payment_method']))
        self.assertNotIn('clarification_task_title',refund)
        karimov=rows['sz:20167:каримов-продажа']
        self.assertIn('седьмую дату',karimov['clarification_task_title'].lower())
        self.assertIn('шесть дат',karimov['clarification_task_note'].lower())


if __name__=='__main__':unittest.main()
