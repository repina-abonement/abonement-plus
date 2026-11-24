import unittest

from crm import targetedverifiedcarebatch as batch


class VerifiedCareBatchTest(unittest.TestCase):
    def test_sources_are_unique_and_sales_are_fully_specified(self):
        events = batch.sale_events()
        self.assertEqual(7, len(events))
        self.assertEqual(len(events), len({event["source_id"] for event in events}))
        for event in events:
            self.assertEqual("subscription_sale", event["event_kind"])
            self.assertGreater(event["amount"], 0)
            self.assertTrue(event["payment_method"])
            self.assertTrue(event["components"])
            self.assertEqual(event["amount"], sum(row["price"] for row in event["components"]))
            self.assertTrue(all(row["dates"] for row in event["components"]))

    def test_non_sale_facts_are_structured_and_sources_are_unique(self):
        facts = batch.fact_events()
        self.assertEqual(17, len(facts))
        self.assertEqual(len(facts), len({event["source_id"] for event in facts}))
        self.assertTrue(all(event["student_name"].strip() for event in facts))
        self.assertTrue(all(event["event_date"] for event in facts))
        self.assertEqual(24, len(batch.events()))
        self.assertEqual(len(batch.events()), len({event["source_id"] for event in batch.events()}))


if __name__ == "__main__":
    unittest.main()
