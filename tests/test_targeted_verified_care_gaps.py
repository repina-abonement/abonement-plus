import unittest
import sqlite3

from crm import chatdaytransfer, targetedverifiedcaregaps as batch, tasks


class VerifiedCareGapsTest(unittest.TestCase):
    def test_sources_are_unique_and_incomplete_money_stays_non_financial(self):
        events = batch.events()
        self.assertEqual(35, len(events))
        self.assertEqual(len(events), len({row["source_id"] for row in events}))

        incomplete = [row for row in events if row["event_kind"] == "sale_fact"]
        self.assertEqual(19, len(incomplete))
        self.assertTrue(all(row.get("clarification_task_title") for row in incomplete))
        self.assertTrue(all("payment_method" not in row for row in incomplete))

    def test_exact_sales_balance_and_dates(self):
        exact = [row for row in batch.events() if row["event_kind"] == "subscription_sale"]
        self.assertEqual(2, len(exact))
        for row in exact:
            self.assertEqual(row["amount"], sum(part["price"] for part in row["components"]))
            self.assertEqual(
                sum(len(part["dates"]) for part in row["components"]),
                9 if row["student_name"] == "Лобченко Константин" else 11,
            )
        zhegulin = next(row for row in exact if row["student_name"] == "Жегулин Севастьян")
        self.assertEqual("cash", zhegulin["payment_method"])
        self.assertEqual(20, zhegulin["discount_percent"])

    def test_tournament_messenger_tags_are_not_payment_methods(self):
        rows = [row for row in batch.events() if "турнир" in row["source_id"]]
        self.assertEqual(3, len(rows))
        self.assertTrue(all(row["event_kind"] == "sale_fact" for row in rows))
        self.assertTrue(all("payment_method" not in row for row in rows))

    def test_do_not_contact_request_is_not_a_future_trial_result(self):
        row = next(row for row in batch.events() if "печенкин" in row["source_id"])
        self.assertEqual("decision_fact", row["event_kind"])
        self.assertEqual("2027-01-10T12:00", row["task_due"])

    def test_daneykin_visits_are_idempotent(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript("""
            CREATE TABLE students(id INTEGER PRIMARY KEY,name TEXT,archived INTEGER DEFAULT 0);
            CREATE TABLE erp_history_visits(student_id INTEGER,visit_date TEXT,visit_time TEXT,status TEXT);
            CREATE TABLE erp_history_unassigned_visits(
              source_id TEXT UNIQUE,source_subscription_id TEXT,student_id INTEGER,visit_date TEXT,
              visit_time TEXT,kind TEXT,status TEXT,source_group TEXT,source_payload TEXT,
              imported_at TEXT,imported_by TEXT);
            INSERT INTO students(id,name) VALUES (1,'Виктор Данейкин');
        """)
        first = batch._apply_daneykin_visits(con)
        second = batch._apply_daneykin_visits(con)
        self.assertEqual({"added": 3, "already": 0, "error": ""}, first)
        self.assertEqual({"added": 0, "already": 3, "error": ""}, second)
        self.assertEqual(3, con.execute("SELECT count(*) FROM erp_history_unassigned_visits").fetchone()[0])

    def test_unmatched_card_keeps_batch_retryable(self):
        result = {"errors": [], "ambiguous": [], "unmatched": [{"name": "Новый ребёнок"}]}
        self.assertFalse(chatdaytransfer.batch_is_complete(result))
        self.assertFalse(chatdaytransfer.saved_batch_can_skip({"state": "complete", "unmatched": result["unmatched"]}))
        self.assertTrue(chatdaytransfer.batch_is_complete({"errors": [], "ambiguous": [], "unmatched": []}))

    def test_missing_identity_gets_one_planner_task_and_closes_after_match(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript("""
            CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE integrations(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE users(id INTEGER PRIMARY KEY,name TEXT,role TEXT,active INTEGER);
            CREATE TABLE students(id INTEGER PRIMARY KEY);
            INSERT INTO users VALUES(1,'Юлия Репина','admin',1);
        """ + tasks.SCHEMA)
        missing = {"unmatched": [{"source_id": "sz:21768:дима-лид"}]}
        first = batch._sync_identity_tasks(con, missing)
        second = batch._sync_identity_tasks(con, missing)
        self.assertEqual(1, first["created"])
        self.assertEqual(0, second["created"])
        self.assertEqual(1, con.execute("SELECT count(*) FROM tasks").fetchone()[0])
        batch._sync_identity_tasks(con, {"unmatched": [], "ambiguous": [{"source_id": "sz:21768:дима-лид"}]})
        self.assertEqual("open", con.execute("SELECT status FROM tasks").fetchone()[0])
        batch._sync_identity_tasks(con, {"unmatched": []})
        self.assertEqual("cancelled", con.execute("SELECT status FROM tasks").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
