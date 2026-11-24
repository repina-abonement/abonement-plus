import unittest

from crm import targetedverifiedcareearly as batch


class VerifiedCareEarlyTest(unittest.TestCase):
    def test_consolidated_report_is_split_into_unique_safe_events(self):
        events=batch.events()
        self.assertEqual(37,len(events))
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


if __name__=='__main__':unittest.main()
