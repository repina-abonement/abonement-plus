import unittest

from crm import targetedverifiedcarehistory as batch


class VerifiedCareHistoryTest(unittest.TestCase):
    def test_sources_are_unique_and_facts_have_required_fields(self):
        events = batch.events()
        self.assertEqual(38, len(events))
        self.assertEqual(len(events), len({row["source_id"] for row in events}))
        for row in events:
            self.assertTrue(row["student_name"].strip())
            self.assertTrue(row["source_at"])
            self.assertTrue(row.get("event_date") or row.get("sale_date"))
            self.assertTrue(row["note"].strip())

    def test_only_fully_specified_sale_creates_financial_record(self):
        sales = [row for row in batch.events() if row["event_kind"] == "subscription_sale"]
        self.assertEqual(3, len(sales))
        for sale in sales:
            self.assertEqual(sale["amount"], sum(part["price"] for part in sale["components"]))
            self.assertTrue(sale["payment_method"])
            self.assertTrue(all(part["dates"] for part in sale["components"]))
        unresolved = [row for row in batch.events() if row["event_kind"] == "sale_fact"]
        self.assertTrue(all(row.get("clarification_task_title") for row in unresolved))


if __name__ == "__main__":
    unittest.main()
