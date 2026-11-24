import unittest

from crm import targetedverifiedlatesales as batch


class VerifiedLateSalesTest(unittest.TestCase):
    def test_exact_and_incomplete_sales_are_separated(self):
        events=batch.events()
        self.assertEqual(21,len(events))
        self.assertEqual(len(events),len({row['source_id'] for row in events}))
        exact=[row for row in events if row['event_kind']=='subscription_sale']
        self.assertEqual(6,len(exact))
        for row in exact:
            self.assertEqual(row['amount'],sum(part['price'] for part in row['components']))
            self.assertTrue(row['payment_method'])
            self.assertTrue(all(part['dates'] for part in row['components']))
        incomplete=[row for row in events if row['event_kind']=='sale_fact']
        self.assertEqual(13,len(incomplete))
        self.assertTrue(all(row.get('clarification_task_title') for row in incomplete))

    def test_referral_credit_is_non_payment_balance_fact(self):
        credit=[row for row in batch.events() if row['event_kind']=='balance_credit']
        self.assertEqual(1,len(credit))
        self.assertEqual(3000,credit[0]['amount'])
        self.assertNotIn('payment_method',credit[0])


if __name__=='__main__':unittest.main()
