import unittest
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from crm import chatdaytransfer, db, targetedverifiedcaregaps as batch, tasks


class VerifiedCareGapsTest(unittest.TestCase):
    def test_sources_are_unique_and_incomplete_money_stays_non_financial(self):
        events = batch.events()
        self.assertEqual(48, len(events))
        self.assertEqual(len(events), len({row["source_id"] for row in events}))

        incomplete = [row for row in events if row["event_kind"] == "sale_fact"]
        self.assertEqual(19, len(incomplete))
        self.assertTrue(all(row.get("clarification_task_title") for row in incomplete))
        self.assertTrue(all("payment_method" not in row for row in incomplete))

    def test_exact_sales_balance_and_dates(self):
        exact = [row for row in batch.events() if row["event_kind"] == "subscription_sale"]
        self.assertEqual(3, len(exact))
        for row in exact:
            self.assertEqual(row["amount"], sum(part["price"] for part in row["components"]))
            self.assertEqual(
                sum(len(part["dates"]) for part in row["components"]),
                11 if row["student_name"] == "Жегулин Севастьян" else 9,
            )
        zhegulin = next(row for row in exact if row["student_name"] == "Жегулин Севастьян")
        self.assertEqual("cash", zhegulin["payment_method"])
        self.assertEqual(20, zhegulin["discount_percent"])
        gushcho = next(row for row in exact if row["student_name"] == "Гущо Михаил")
        self.assertEqual(("2026-12", 10800, "tochka_qr_link"),
                         (gushcho["components"][0]["month"], gushcho["amount"], gushcho["payment_method"]))

    def test_tournament_messenger_tags_are_not_payment_methods(self):
        rows = [row for row in batch.events() if row["source_id"].startswith("sz:20646:")]
        self.assertEqual(3, len(rows))
        self.assertTrue(all(row["event_kind"] == "sale_fact" for row in rows))
        self.assertTrue(all("payment_method" not in row for row in rows))

    def test_only_confirmed_refund_is_not_left_as_a_request(self):
        refunds=[row for row in batch.events() if row["event_kind"] in ("refund_fact","refund_request")]
        confirmed=[row for row in refunds if row["event_kind"]=="refund_fact"]
        self.assertEqual([("Муртазин Тагир",6750)],[(row["student_name"],row["amount"]) for row in confirmed])
        self.assertTrue(all(row.get("clarification_task_title") for row in refunds if row["event_kind"]=="refund_request"))

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

    def test_unique_staged_erp_profile_is_used_for_full_care_name(self):
        event={"source_chat":"Служба заботы","student_name":"Иван Иванов","phone":"+79990000001"}
        profiles=[{"erp_id":"1","name":"Иванов Иван","phone":"+79990000001","status":"Постоянный посетитель"},
                  {"erp_id":"2","name":"Иван Иванов","phone":"+79990000002","status":"Нет абонемента"}]
        self.assertEqual("1",chatdaytransfer._staged_erp_profile(event,profiles)["erp_id"])
        self.assertIsNone(chatdaytransfer._staged_erp_profile(
            {"source_chat":"Служба заботы","student_name":"Иван"},profiles))

    def test_namesake_without_phone_prefers_only_active_erp_profile(self):
        event={"source_chat":"Служба заботы","student_name":"Иван Иванов"}
        profiles=[{"erp_id":"1","name":"Иванов Иван","status":"Постоянный посетитель"},
                  {"erp_id":"2","name":"Иван Иванов","status":"Архив, неактивен"}]
        self.assertEqual("1",chatdaytransfer._staged_erp_profile(event,profiles)["erp_id"])
        profile={"group":"Небо ср/вс 17:45;Небо вт/пт 18:45"}
        self.assertEqual("Небо вт/пт 18:45",chatdaytransfer._erp_group_for_event(profile,{"lesson_time":"18:45"}))

    def test_full_care_name_creates_card_from_staged_erp_profile(self):
        con=db.connect(str(Path(tempfile.mkdtemp())/"erp-auto.sqlite3"));db.init_db(con)
        profile={"erp_id":"9001","name":"Проверкин Пётр","birth":"2020-02-03","parent_name":"Анна",
                 "phone":"+79990000001","email":"","status":"Постоянный посетитель","group":"","branch":"Небо"}
        event={"source_id":"sz:erp-auto","source_chat":"Служба заботы","source_at":"2026-11-20T12:00:00",
               "student_name":"Пётр Проверкин","event_kind":"contact_fact","event_date":"2026-11-20",
               "note":"Точный факт из СЗ"}
        with patch("crm.chatbulk._erp_profiles",return_value=[profile]):
            result=chatdaytransfer.apply(con,{"events":[event]},{"id":1,"role":"admin","name":"Юлия Репина"})
        self.assertEqual(1,len(result["applied"]))
        student=con.execute("SELECT erp_id,birth FROM students WHERE name='Проверкин Пётр'").fetchone()
        self.assertEqual(("9001","2020-02-03"),(student["erp_id"],student["birth"]))


if __name__ == "__main__":
    unittest.main()
