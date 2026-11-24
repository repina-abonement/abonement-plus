import unittest
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from crm import chatdaytransfer, db, schedule, services, targetedverifiedcaregaps as batch, tasks


class VerifiedCareGapsTest(unittest.TestCase):
    def test_revised_package_key_forces_one_safe_reconciliation(self):
        self.assertTrue(batch.KEY.endswith("_v12"))

    def test_sources_are_unique_and_incomplete_money_stays_non_financial(self):
        events = batch.events()
        self.assertEqual(54, len(events))
        self.assertEqual(len(events), len({row["source_id"] for row in events}))

        incomplete = [row for row in events if row["event_kind"] == "sale_fact"]
        self.assertEqual(17, len(incomplete))
        unresolved = [row for row in incomplete if not row.get("resolved_clarification_note")]
        self.assertEqual(16, len(unresolved))
        self.assertTrue(all(row.get("clarification_task_title") for row in unresolved))
        self.assertTrue(all("payment_method" not in row for row in unresolved))

    def test_shulezhko_birthday_payment_stays_a_non_subscription_fact(self):
        row = next(row for row in batch.events() if row["source_id"] == "sz:20410:шулежко-день-рождения")
        self.assertEqual(("event_payment_fact", 6000, "tochka_qr_link"),
                         (row["event_kind"], row["amount"], row["payment_method"]))
        self.assertNotIn("components", row)
        self.assertNotIn("clarification_task_title", row)

    def test_early_client_decisions_do_not_invent_a_sale(self):
        rows = {row["source_id"]: row for row in batch.events()}
        refused = rows["sz:20253:костюченко-решение"]
        self.assertEqual("decision_fact", refused["event_kind"])
        self.assertIn("отказалась", refused["note"])
        yakunin = rows["sz:20253:якунин-второй-визит"]
        self.assertEqual("sale_lead", yakunin["event_kind"])
        self.assertIn("не подтверждены", yakunin["note"])
        self.assertNotIn("amount", yakunin)
        self.assertNotIn("payment_method", yakunin)

    def test_samoylov_siblings_use_exact_erp_identity_without_a_manual_guess(self):
        rows = [row for row in batch.events() if row["source_id"].startswith("sz:20253:самойлов")]
        self.assertEqual(2, len(rows))
        self.assertEqual({"Самойлов Рома", "Самойлова Полина"}, {row["student_name"] for row in rows})
        self.assertEqual({"2022-12-18", "2021-08-17"}, {row["birth"] for row in rows})
        self.assertTrue(all(row["phone"] == "+79639242689" for row in rows))
        self.assertTrue(all(row["create_if_missing"] for row in rows))
        self.assertTrue(all(row["event_kind"] == "decision_fact" for row in rows))

    def test_exact_sales_balance_and_dates(self):
        exact = [row for row in batch.events() if row["event_kind"] == "subscription_sale"]
        self.assertEqual(5, len(exact))
        lesson_counts={"Лобченко Константин":9,"Жегулин Севастьян":11,"Гущо Михаил":9,
                       "Рахман Альфаз":14,"Кустов Павел":9}
        for row in exact:
            self.assertEqual(row["amount"], sum(part["price"] for part in row["components"]))
            self.assertEqual(
                sum(len(part["dates"]) for part in row["components"]),
                lesson_counts[row["student_name"]],
            )
        zhegulin = next(row for row in exact if row["student_name"] == "Жегулин Севастьян")
        self.assertEqual("cash", zhegulin["payment_method"])
        self.assertEqual(20, zhegulin["discount_percent"])
        gushcho = next(row for row in exact if row["student_name"] == "Гущо Михаил")
        self.assertEqual(("2026-12", 10800, "tochka_qr_link"),
                         (gushcho["components"][0]["month"], gushcho["amount"], gushcho["payment_method"]))
        rakhman = next(row for row in exact if row["student_name"] == "Рахман Альфаз")
        self.assertEqual((15120, "card_branch", 20),
                         (rakhman["amount"], rakhman["payment_method"], rakhman["discount_percent"]))
        self.assertEqual(("Елена", True), (rakhman["parent_name"], rakhman["create_if_missing"]))
        kustov = next(row for row in exact if row["student_name"] == "Кустов Павел")
        self.assertEqual((10850, "flex", "tochka_qr_link"),
                         (kustov["amount"], kustov["tariff"], kustov["payment_method"]))

    def test_tournament_messenger_tags_are_not_payment_methods(self):
        rows = [row for row in batch.events() if row["source_id"].startswith("sz:20646:")]
        self.assertEqual(3, len(rows))
        self.assertTrue(all(row["event_kind"] == "sale_fact" for row in rows))
        by_name = {row["student_name"]: row for row in rows}
        self.assertNotIn("payment_method", by_name["Чугунов Егор"])
        self.assertNotIn("payment_method", by_name["Гмыря Артем"])
        self.assertEqual("paykeeper_manual", by_name["Шулежко Даниил"]["payment_method"])
        self.assertIn("PayKeeper", by_name["Шулежко Даниил"]["resolved_clarification_note"])

    def test_resolved_clarification_closes_previous_manual_task(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript("""
            CREATE TABLE tasks(id INTEGER PRIMARY KEY,student_id INTEGER,status TEXT,auto_key TEXT,
              done_at TEXT,done_by TEXT,result TEXT);
            INSERT INTO tasks(student_id,status,auto_key) VALUES
              (7,'open','chat-clarify:sz:20646:шулежко-турнир');
        """)
        row = next(row for row in batch.events() if row["student_name"] == "Шулежко Даниил"
                   and "турнир" in row["source_id"])
        chatdaytransfer._reconcile_clarification_task(con, 7, row)
        task = con.execute("SELECT status,done_by,result FROM tasks").fetchone()
        self.assertEqual(("cancelled", "CRM"), (task["status"], task["done_by"]))
        self.assertIn("PayKeeper", task["result"])

    def test_cross_message_trainer_groups_reduce_manual_scope_without_inventing_periods(self):
        rows = {row["student_name"]: row for row in batch.events() if row["student_name"] in {
            "Мамедов Даниил", "Замковой Илья", "Гмыря Артем", "Малышев Артем"}}
        self.assertIn("пн/чт 19:45", rows["Мамедов Даниил"]["note"])
        self.assertIn("вт/пт 17:45", rows["Замковой Илья"]["note"])
        self.assertIn("вт/пт 18:45", rows["Гмыря Артем"]["note"])
        self.assertIn("вт/пт 18:45", rows["Малышев Артем"]["note"])
        self.assertTrue(all(row["event_kind"] == "sale_fact" for row in rows.values()))
        self.assertTrue(all("payment_method" not in row for row in rows.values()))

    def test_known_sale_composition_leaves_only_payment_method_unresolved(self):
        names={"Бородин Доброслав", "Смирнов Эрик", "Серегин Глеб", "Гянджунцев Артем"}
        rows=[row for row in batch.events() if row["student_name"] in names]
        self.assertEqual(4,len(rows))
        for row in rows:
            expected = ("факт и способ оплаты" if row["student_name"] == "Серегин Глеб"
                        else "только способ оплаты")
            self.assertIn(expected, row["clarification_task_title"].lower())
        self.assertTrue(all("payment_method" not in row for row in rows))

    def test_only_confirmed_refund_is_not_left_as_a_request(self):
        refunds=[row for row in batch.events() if row["event_kind"] in ("refund_fact","refund_request")]
        confirmed=[row for row in refunds if row["event_kind"]=="refund_fact"]
        self.assertEqual([("Муртазин Тагир",6750)],[(row["student_name"],row["amount"]) for row in confirmed])
        self.assertTrue(all(row.get("clarification_task_title") for row in refunds if row["event_kind"]=="refund_request"))
        nechayev = next(row for row in refunds if row["student_name"] == "Нечхаев Андрей")
        self.assertNotIn("amount", nechayev)
        self.assertIn("одно занятие", nechayev["note"])

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

    def test_complete_care_identity_can_create_card_without_inventing_old_trial_time(self):
        con=db.connect(str(Path(tempfile.mkdtemp())/"care-identity.sqlite3"));db.init_db(con)
        event={"source_id":"sz:identity","source_chat":"Служба заботы","source_at":"2026-11-21T12:00:00",
               "student_name":"Юрков Лев","birth":"2021-05-01","parent_name":"Марк",
               "phone":"+79523231764","create_if_missing":True,"event_kind":"sale_fact",
               "event_date":"2026-11-21","note":"Подтверждённый факт",
               "clarification_task_title":"Закрепить точную группу"}
        result=chatdaytransfer.apply(con,{"events":[event]},{"id":1,"role":"admin","name":"Юлия Репина"})
        self.assertEqual(1,len(result["applied"]))
        repeated=chatdaytransfer.apply(con,{"events":[event]},{"id":1,"role":"admin","name":"Юлия Репина"})
        self.assertEqual(1,len(repeated["already"]))
        student=con.execute("SELECT name,birth,trial_at FROM students WHERE name='Юрков Лев'").fetchone()
        self.assertEqual(("Юрков Лев","2021-05-01",None),(student["name"],student["birth"],student["trial_at"]))
        self.assertEqual(1,con.execute("SELECT count(*) FROM students WHERE name='Юрков Лев'").fetchone()[0])
        self.assertEqual(1,con.execute("SELECT count(*) FROM chat_day_transfer_events WHERE source_id='sz:identity'").fetchone()[0])

    def test_old_incomplete_rakhman_fact_upgrades_to_exact_sale_without_duplicate_source(self):
        con=db.connect(str(Path(tempfile.mkdtemp())/"rakhman-upgrade.sqlite3"));db.init_db(con)
        user={"id":1,"role":"admin","name":"Юлия Репина"}
        branch=schedule.save_branch(con,{"name":"ТРЦ Небо"})
        group=schedule.save_group(con,{"name":"Небо ср/вс 18:45","branch_id":branch,
                                      "weekdays":[2,6],"time":"18:45","age":"3–4"})
        sid=services.create_student(con,{"name":"Рахман Альфаз","birth":"2022-12-16",
            "parent_name":"Елена","parent_role":"мама","phone":"+79854710937","group_id":group},user)
        con.execute("UPDATE students SET trial_at='2026-11-11T18:45',trial_came=1,trial_group_id=? WHERE id=?",
                    (group,sid))
        exact=next(row for row in batch.events() if row["student_name"]=="Рахман Альфаз")
        legacy={"source_id":exact["source_id"],"source_chat":"Служба заботы",
                "source_at":exact["source_at"],"student_name":"Рахман Альфаз",
                "event_kind":"sale_fact","event_date":"2026-11-11","note":"Старый неполный факт",
                "clarification_task_title":"Сверить старую продажу"}
        first=chatdaytransfer.apply(con,{"events":[legacy]},user)
        self.assertEqual(1,len(first["applied"]))
        upgraded=chatdaytransfer.apply(con,{"events":[exact]},user)
        self.assertEqual(1,len(upgraded["already"]))
        self.assertEqual(1,con.execute("SELECT count(*) FROM chat_day_transfer_events WHERE source_id=?",
                                      (exact["source_id"],)).fetchone()[0])
        self.assertEqual([("2026-11",5400,"paid"),("2026-12",9720,"paid")],
                         [tuple(row) for row in con.execute(
                             "SELECT month,price,status FROM subscriptions WHERE student_id=? ORDER BY month",(sid,))])
        self.assertEqual((15120,"card_branch"),tuple(con.execute(
            "SELECT amount,method FROM payments WHERE student_id=? AND cancelled=0",(sid,)).fetchone()))
        self.assertEqual(0,con.execute(
            "SELECT count(*) FROM tasks WHERE auto_key=? AND status='open'",
            ('chat-day:'+exact["source_id"],)).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
