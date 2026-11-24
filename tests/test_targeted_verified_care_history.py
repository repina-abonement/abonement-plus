import unittest

from crm import targetedverifiedcarehistory as batch


class VerifiedCareHistoryTest(unittest.TestCase):
    def test_sources_are_unique_and_facts_have_required_fields(self):
        events = batch.events()
        self.assertEqual(56, len(events))
        self.assertEqual(len(events), len({row["source_id"] for row in events}))
        for row in events:
            self.assertTrue(row["student_name"].strip())
            self.assertTrue(row["source_at"])
            self.assertTrue(row.get("event_date") or row.get("sale_date"))
            self.assertTrue(row["note"].strip())

    def test_kononenko_makeup_is_preserved(self):
        row = next(row for row in batch.events() if row["source_id"] == "sz:21928:кононенко-отработка")
        self.assertEqual(("makeup_booking", "2026-11-19"), (row["event_kind"], row["event_date"]))
        self.assertIn("19:45", row["note"])

    def test_only_fully_specified_sale_creates_financial_record(self):
        sales = [row for row in batch.events() if row["event_kind"] == "subscription_sale"]
        self.assertEqual(3, len(sales))
        for sale in sales:
            self.assertEqual(sale["amount"], sum(part["price"] for part in sale["components"]))
            self.assertTrue(sale["payment_method"])
            self.assertTrue(all(part["dates"] for part in sale["components"]))
        unresolved = [row for row in batch.events() if row["event_kind"] == "sale_fact"]
        self.assertTrue(all(row.get("clarification_task_title") for row in unresolved))

    def test_all_cross_message_source_facts_are_explicit_and_unique(self):
        sources = batch.source_events()
        self.assertEqual(17, len(sources))
        self.assertEqual(len(sources), len({row["source_id"] for row in sources}))
        self.assertTrue(all(row["source"].strip() for row in sources))
        self.assertTrue(all(row["event_kind"] == "source_fact" for row in sources))


if __name__ == "__main__":
    unittest.main()
