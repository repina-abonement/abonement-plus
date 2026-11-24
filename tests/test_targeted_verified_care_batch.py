import unittest

from crm import targetedverifiedcarebatch as batch


class VerifiedCareBatchTest(unittest.TestCase):
    def test_sources_are_unique_and_sales_are_fully_specified(self):
        events = batch.events()
        self.assertEqual(7, len(events))
        self.assertEqual(len(events), len({event["source_id"] for event in events}))
        for event in events:
            self.assertEqual("subscription_sale", event["event_kind"])
            self.assertGreater(event["amount"], 0)
            self.assertTrue(event["payment_method"])
            self.assertTrue(event["components"])
            self.assertEqual(event["amount"], sum(row["price"] for row in event["components"]))
            self.assertTrue(all(row["dates"] for row in event["components"]))


if __name__ == "__main__":
    unittest.main()
