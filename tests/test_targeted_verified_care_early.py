import unittest

from crm import targetedverifiedcareearly as batch


class VerifiedCareEarlyTest(unittest.TestCase):
    def test_consolidated_report_is_split_into_unique_safe_events(self):
        events=batch.events()
        self.assertEqual(42,len(events))
        self.assertEqual(len(events),len({row['source_id'] for row in events}))
        self.assertTrue(all(row['student_name'].strip() for row in events))
        self.assertTrue(all(row['note'].strip() for row in events))

    def test_incomplete_money_facts_never_claim_a_payment_method(self):
        incomplete=[row for row in batch.events() if row['event_kind']=='sale_fact']
        self.assertEqual(14,len(incomplete))
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

    def test_kononenko_makeup_and_tournament_carry_stay_separate(self):
        rows={row['source_id']:row for row in batch.events()}
        makeup=rows['sz:20167:кононенко-отработка']
        self.assertEqual(('makeup_booking','2026-11-12'),(makeup['event_kind'],makeup['event_date']))
        carry=rows['sz:20167:кононенко-турнир']
        self.assertEqual(('carry_fact','2026-11-14'),(carry['event_kind'],carry['event_date']))
        self.assertNotIn('amount',carry)
        self.assertIn('исходного абонемента',carry['clarification_task_note'])

    def test_sergievsky_subscription_fact_uses_exact_erp_identity(self):
        row=next(row for row in batch.events() if row['source_id']=='sz:20167:сергиевский-ноябрь')
        self.assertEqual(('sale_fact','182330','16:45'),
                         (row['event_kind'],row['erp_id'],row['erp_group_time']))
        self.assertIn('ср/вс 16:45',row['note'])
        self.assertNotIn('amount',row)
        self.assertNotIn('payment_method',row)

    def test_remenyuk_family_event_is_split_between_two_exact_children(self):
        rows=[row for row in batch.events() if row['source_id'].startswith('sz:20167:ременюк-')]
        self.assertEqual(2,len(rows))
        self.assertEqual({'Ременюк Марк','Ременюк Максим'},{row['student_name'] for row in rows})
        self.assertEqual({'2021-04-21','2024-04-13'},{row['birth'] for row in rows})
        self.assertTrue(all(row['phone']=='+79057790163' for row in rows))
        self.assertTrue(all(row['create_if_missing'] for row in rows))


if __name__=='__main__':unittest.main()
