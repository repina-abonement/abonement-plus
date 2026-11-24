import os
import tempfile
import unittest
from unittest.mock import patch

from crm import chatbulk, db, erpimport, services as sv


class ChatBulkTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(os.path.join(tempfile.mkdtemp(), "chat-bulk.sqlite3"))
        db.init_db(self.con)
        branch = self.con.execute("INSERT INTO branches(name,active) VALUES ('Небо',1)").lastrowid
        self.group = self.con.execute("""INSERT INTO groups(branch_id,name,weekdays,time,active)
            VALUES (?,'Небо ПУП','0,3','16:45',1)""", (branch,)).lastrowid
        self.user = {"id": 1, "name": "Юлия", "role": "admin"}
        self.ivan = sv.create_student(self.con, {"name": "Иван Иванов", "parent_name": "Анна",
            "phone": "+79990000001", "group_id": self.group}, self.user)
        self.misha = sv.create_student(self.con, {"name": "Михаил Морозов", "parent_name": "Наталья",
            "phone": "+79990000002", "group_id": self.group}, self.user)
        family = self.con.execute("SELECT family_id FROM students WHERE id=?", (self.misha,)).fetchone()[0]
        self.ilya = sv.create_student(self.con, {"name": "Илья Морозов", "family_id": family,
            "group_id": self.group}, self.user)

    def exports(self):
        care = {"name": "Служба заботы", "id": 1, "messages": [
            {"id": 10, "date": "2026-11-02T12:00:00", "text": "Иван Иванов просит ссылку на оплату"},
            {"id": 11, "date": "2026-11-02T12:01:00", "text": "Доброе утро"},
        ]}
        trainers = {"name": "ТРЕНЕРЫ | СОЛНЦЕВО", "id": 2, "messages": [
            {"id": 20, "date": "2026-11-02T20:00:00", "text": "16:45\nИван Иванов\nМорозов"},
        ]}
        return [care, trainers, dict(care)]

    @patch("crm.chatbulk.date")
    def test_plan_deduplicates_exports_and_does_not_guess_ambiguous_surname(self, fake_date):
        from datetime import date
        fake_date.today.return_value = date(2026, 11, 24)
        report = chatbulk.plan(self.con, self.exports())
        self.assertEqual({"care": 2, "trainers": 1}, report["messages"])
        self.assertEqual(1, report["trainer"]["ready"])
        self.assertEqual(1, len(report["trainer"]["unresolved"]))
        self.assertEqual("Морозов", report["trainer"]["unresolved"][0]["name"])
        self.assertEqual(1, report["care"]["significant"])
        self.assertEqual(1, report["care"]["without_events"])

    @patch("crm.chatbulk.date")
    def test_ambiguous_surname_keeps_only_child_with_existing_exact_attendance(self, fake_date):
        from datetime import date
        fake_date.today.return_value = date(2026, 11, 24)
        self.con.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES (?,?,?,'present')",
                         (self.ilya, self.group, "2026-11-02"))
        report = chatbulk.plan(self.con, self.exports())
        row = next(x for x in report["attendance"] if x["message_id"] == "20" and x["name"] == "Морозов")
        self.assertEqual(self.ilya, row["student_id"])
        self.assertTrue(row["already"])
        self.assertFalse(any(x["message_id"] == "20" and x["name"] == "Морозов"
                             for x in report["trainer"]["unresolved"]))

    @patch("crm.chatbulk.date")
    def test_owner_approved_source_link_survives_later_homonym(self, fake_date):
        from datetime import date
        fake_date.today.return_value = date(2026, 11, 24)
        exports = self.exports()
        exports[1]["messages"].append({"id": 27148, "date": "2026-11-23T22:12:23",
            "text": "16:45\nМорозов"})
        report = chatbulk.plan(self.con, exports)
        row = next(x for x in report["attendance"] if x["message_id"] == "27148")
        self.assertEqual(self.ilya, row["student_id"])
        self.assertEqual("Илья Морозов", row["student_name"])

    @patch("crm.chatbulk.date")
    def test_apply_is_preview_guarded_and_idempotent(self, fake_date):
        from datetime import date
        fake_date.today.return_value = date(2026, 11, 24)
        exports = self.exports()
        plan = chatbulk.plan(self.con, exports)
        with self.assertRaisesRegex(chatbulk.BulkError, "Сначала снова"):
            chatbulk.apply(self.con, exports, "wrong", self.user)
        first = chatbulk.apply(self.con, exports, plan["token"], self.user)
        self.assertEqual(1, first["added"])
        self.assertTrue(first["review_task"])
        second_plan = chatbulk.plan(self.con, exports)
        second = chatbulk.apply(self.con, exports, second_plan["token"], self.user)
        self.assertEqual(0, second["added"])
        self.assertEqual(1, self.con.execute(
            "SELECT count(*) FROM erp_history_unassigned_visits WHERE source_id LIKE 'trainers:20:%'"
        ).fetchone()[0])
        self.assertEqual(1, self.con.execute(
            "SELECT count(*) FROM tasks WHERE auto_key='chat-bulk:trainers-unresolved:2026-11'"
        ).fetchone()[0])

    @patch("crm.chatbulk.date")
    def test_missing_card_is_created_from_complete_staged_erp_profile(self, fake_date):
        from datetime import date
        fake_date.today.return_value = date(2026, 11, 24)
        sibling = sv.create_student(self.con, {"name": "Анна Петрова", "parent_name": "Мария",
            "phone": "79990000003", "group_id": self.group}, self.user)
        family_id = self.con.execute("SELECT family_id FROM students WHERE id=?", (sibling,)).fetchone()[0]
        erpimport.upload(self.con, "visitors.csv", (
            "ID;ФИО посетителя;Телефон;Имя родителя;Статус;Дата рождения;Филиал;Группа\n"
            "777;Пётр Петров;79990000003;Мария;Постоянный посетитель;01.02.2020;Небо;Небо ПУП\n"
        ).encode(), "clients")
        exports = self.exports()
        exports[1]["messages"].append({"id": 21, "date": "2026-11-03T20:00:00", "text": "16:45\nПётр Петров"})
        exports[1]["messages"].append({"id": 24, "date": "2026-11-04T20:00:00", "text": "16:45\nПётр Петров"})
        plan = chatbulk.plan(self.con, exports)
        self.assertEqual(1, plan["trainer"]["create_from_erp"])
        self.assertEqual(2, plan["trainer"]["erp_attendance_rows"])
        profile = next(x["create_from_erp"] for x in plan["attendance"] if x.get("create_from_erp"))
        self.assertEqual(family_id, profile["family_id"])
        result = chatbulk.apply(self.con, exports, plan["token"], self.user)
        self.assertEqual(1, result["created_from_erp"])
        student = self.con.execute("SELECT id,birth,erp_status FROM students WHERE erp_id='777'").fetchone()
        self.assertEqual(("2020-02-01", "Постоянный посетитель"), tuple(student)[1:])
        parent = self.con.execute("""SELECT name,phone FROM parents
            WHERE family_id=(SELECT family_id FROM students WHERE id=?)""", (student["id"],)).fetchone()
        self.assertEqual(("Мария", "79990000003"), tuple(parent))
        self.assertEqual(family_id, self.con.execute("SELECT family_id FROM students WHERE id=?", (student["id"],)).fetchone()[0])
        self.assertEqual(1, self.con.execute("SELECT count(*) FROM parents WHERE family_id=?", (family_id,)).fetchone()[0])
        # A later same-name CRM card must not make the already imported ERP
        # visits appear unresolved in the repeated report.
        sv.create_student(self.con, {"name": "Пётр Петров", "parent_name": "Другой родитель",
            "phone": "+79990000999", "group_id": self.group}, self.user)
        self.con.execute("DELETE FROM erp_uploads")  # repeat must also work when staged ERP files later change
        repeated = chatbulk.plan(self.con, exports)
        imported = [x for x in repeated["attendance"] if x["message_id"] in {"21", "24"}]
        self.assertEqual(2, len(imported))
        self.assertTrue(all(x["already"] and x["student_id"] == student["id"] for x in imported))
        self.assertFalse(any(x["message_id"] in {"21", "24"} for x in repeated["trainer"]["unresolved"]))

    @patch("crm.chatbulk.date")
    def test_unique_surname_creates_a_card_from_complete_erp_profile(self, fake_date):
        from datetime import date
        fake_date.today.return_value = date(2026, 11, 24)
        erpimport.upload(self.con, "visitors.csv", (
            "ID;ФИО посетителя;Телефон;Имя родителя;Статус;Дата рождения;Филиал;Группа\n"
            "778;София Степаненко;79990000004;Ольга;Постоянный посетитель;01.02.2020;Небо;Небо 16:45\n"
        ).encode(), "clients")
        exports = self.exports()
        exports[1]["messages"].append({"id": 22, "date": "2026-11-03T20:00:00", "text": "16:45\nСтепаненко"})
        plan = chatbulk.plan(self.con, exports)
        row = next(x for x in plan["attendance"] if x["message_id"] == "22")
        self.assertEqual("София Степаненко", row["student_name"])
        self.assertEqual(1, plan["trainer"]["create_from_erp"])

    @patch("crm.chatbulk.date")
    def test_single_surname_matches_unique_existing_card(self, fake_date):
        from datetime import date
        fake_date.today.return_value = date(2026, 11, 24)
        exports = self.exports()
        exports[1]["messages"].append({"id": 23, "date": "2026-11-03T20:00:00", "text": "18:00\nИванов"})
        plan = chatbulk.plan(self.con, exports)
        row = next(x for x in plan["attendance"] if x["message_id"] == "23")
        self.assertEqual(self.ivan, row["student_id"])

    @patch("crm.chatbulk.date")
    def test_single_first_name_does_not_match_existing_or_erp_card(self, fake_date):
        from datetime import date
        fake_date.today.return_value = date(2026, 11, 24)
        erpimport.upload(self.con, "first-name.csv", (
            "ID;ФИО посетителя;Телефон;Имя родителя;Статус;Дата рождения;Филиал;Группа\n"
            "779;Замир Кадымов;79990000009;Амина;Постоянный посетитель;01.02.2020;Небо;Небо 16:45\n"
            "781;Гониев Даниил;79990000012;Ольга;Постоянный посетитель;01.03.2020;Небо;Небо 16:45\n"
            "782;Макаров Артемий;79990000013;Ирина;Постоянный посетитель;01.04.2020;Небо;Небо 16:45\n"
        ).encode(), "clients")
        exports = self.exports()
        exports[1]["messages"].append({"id": 27, "date": "2026-11-03T20:00:00", "text": "16:45\nИван\nЗамир\nДаня\nАртемий"})
        plan = chatbulk.plan(self.con, exports)
        unresolved = {x["name"] for x in plan["trainer"]["unresolved"] if x["message_id"] == "27"}
        self.assertEqual({"Иван", "Замир", "Даня", "Артемий"}, unresolved)

    @patch("crm.chatbulk.date")
    def test_single_word_incomplete_cards_are_not_treated_as_surnames(self, fake_date):
        from datetime import date
        fake_date.today.return_value = date(2026, 11, 24)
        sv.create_student(self.con, {"name": "Даня", "parent_name": "Ольга",
            "phone": "+79990000010", "group_id": self.group}, self.user)
        erpimport.upload(self.con, "incomplete-name.csv", (
            "ID;ФИО посетителя;Телефон;Имя родителя;Статус;Дата рождения;Филиал;Группа\n"
            "780;Марк;79990000011;Ирина;Постоянный посетитель;01.02.2020;Небо;Небо 16:45\n"
        ).encode(), "clients")
        exports = self.exports()
        exports[1]["messages"].append({"id": 28, "date": "2026-11-03T20:00:00", "text": "16:45\nДаня\nМарк"})
        plan = chatbulk.plan(self.con, exports)
        unresolved = {x["name"] for x in plan["trainer"]["unresolved"] if x["message_id"] == "28"}
        self.assertEqual({"Даня", "Марк"}, unresolved)

    @patch("crm.chatbulk.date")
    def test_erp_homonyms_prefer_unique_active_group_hint(self, fake_date):
        from datetime import date
        fake_date.today.return_value = date(2026, 11, 24)
        erpimport.upload(self.con, "homonyms.csv", (
            "ID;ФИО посетителя;Телефон;Имя родителя;Статус;Дата рождения;Филиал;Группа\n"
            "801;Михаил Гущо;79990000005;Ольга;Постоянный посетитель;01.02.2020;Небо;Небо 19:45\n"
            "802;Павел Гущо;79990000006;Ирина;Архив;01.03.2020;Небо;Группа не выбрана\n"
        ).encode(), "clients")
        exports = self.exports()
        exports[1]["messages"].append({"id": 25, "date": "2026-11-05T20:00:00", "text": "19:45\nГущо"})
        plan = chatbulk.plan(self.con, exports)
        row = next(x for x in plan["attendance"] if x["message_id"] == "25")
        self.assertEqual("Михаил Гущо", row["student_name"])

    @patch("crm.chatbulk.date")
    def test_erp_homonym_tie_stays_unresolved(self, fake_date):
        from datetime import date
        fake_date.today.return_value = date(2026, 11, 24)
        erpimport.upload(self.con, "ties.csv", (
            "ID;ФИО посетителя;Телефон;Имя родителя;Статус;Дата рождения;Филиал;Группа\n"
            "811;Михаил Гущо;79990000007;Ольга;Постоянный посетитель;01.02.2020;Небо;Небо 19:45\n"
            "812;Павел Гущо;79990000008;Ирина;Постоянный посетитель;01.03.2020;Небо;Небо 19:45\n"
        ).encode(), "clients")
        exports = self.exports()
        exports[1]["messages"].append({"id": 26, "date": "2026-11-05T20:00:00", "text": "19:45\nГущо"})
        plan = chatbulk.plan(self.con, exports)
        self.assertTrue(any(x["message_id"] == "26" for x in plan["trainer"]["unresolved"]))


if __name__ == "__main__":
    unittest.main()
