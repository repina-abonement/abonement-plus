import unittest

from crm import db, erpimport, erppeople, services


class ErpPeopleProfileFieldsTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(":memory:")
        db.init_db(self.con)
        self.user = {"id": 1, "name": "Тест"}

    def upload(self, email="mama@example.test"):
        text = (
            "ID;ФИО посетителя;Статус;Дата рождения;Имя родителя;Телефон;Роль представителя;Почта;Источник;Комментарий\n"
            f"42;Иванов Александр;Постоянный посетитель;05.03.2018;Мария Иванова;+7 900 111-22-33;мама;{email};Рекомендация;Любит играть в воротах\n"
        )
        return erpimport.upload(self.con, "visitors.csv", text.encode(), "clients")["id"]

    def lead_file(self):
        return erpimport.upload(self.con, "leads.csv", (
            "ID;Имя ребенка;Имя родителя;Телефон;Дата создания лида\n"
            "99;Петров Пётр;Анна;+7 900 999-88-77;01.10.2026\n"
        ).encode(), "clients")["id"]

    def test_existing_card_is_enriched_without_duplicate_or_finance(self):
        sid = services.create_student(self.con, {
            "name": "Иванов Александр", "parent_name": "Родитель", "phone": "+7 900 111-22-33",
        }, self.user)
        self.con.execute("UPDATE students SET erp_id='42' WHERE id=?", (sid,))
        visitor, lead = self.upload(), self.lead_file()
        preview = erppeople.transfer(self.con, [visitor], [lead], self.user)
        self.assertEqual(preview["profile"]["fill"]["Дата рождения ребёнка"], 1)
        self.assertEqual(preview["profile"]["fill"]["Роль родителя"], 1)
        self.assertFalse(preview["profile"]["conflicts"])
        erppeople.transfer(self.con, [visitor], [lead], self.user, preview["token"])

        student = dict(self.con.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone())
        parent = dict(self.con.execute("SELECT * FROM parents WHERE family_id=?", (student["family_id"],)).fetchone())
        self.assertEqual((student["birth"], student["source"]), ("2018-03-05", "Рекомендация"))
        self.assertEqual((parent["name"], parent["phone_norm"], parent["relation"], parent["email"]),
                         ("Мария Иванова", "79001112233", "мама", "mama@example.test"))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM history WHERE student_id=? AND kind='note' AND text=?",
                                         (sid, "Любит играть в воротах")).fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM students WHERE erp_id='42'").fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM payments").fetchone()[0], 0)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0], 0)

        again = erppeople.transfer(self.con, [visitor], [lead], self.user)
        erppeople.transfer(self.con, [visitor], [lead], self.user, again["token"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM students WHERE erp_id='42'").fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM history WHERE student_id=? AND kind='note' AND text=?",
                                         (sid, "Любит играть в воротах")).fetchone()[0], 1)

    def test_nonempty_conflict_is_reported_and_preserved(self):
        sid = services.create_student(self.con, {
            "name": "Иванов Александр", "parent_name": "Мария Иванова", "phone": "+7 900 111-22-33",
        }, self.user)
        self.con.execute("UPDATE students SET erp_id='42', birth='2018-03-05', source='Сборная' WHERE id=?", (sid,))
        self.con.execute("UPDATE parents SET email='verified@example.test', relation='мама' WHERE family_id=(SELECT family_id FROM students WHERE id=?)", (sid,))
        visitor, lead = self.upload("other@example.test"), self.lead_file()
        preview = erppeople.transfer(self.con, [visitor], [lead], self.user)
        conflicts = {item["field"] for item in preview["profile"]["conflicts"]}
        self.assertEqual(conflicts, {"Электронная почта", "Рекламный источник"})
        erppeople.transfer(self.con, [visitor], [lead], self.user, preview["token"])
        self.assertEqual(self.con.execute("SELECT source FROM students WHERE id=?", (sid,)).fetchone()[0], "Сборная")
        self.assertEqual(self.con.execute("SELECT email FROM parents WHERE family_id=(SELECT family_id FROM students WHERE id=?)", (sid,)).fetchone()[0], "verified@example.test")

    def test_erp_id_filter_skips_every_other_full_file_row(self):
        sid = services.create_student(self.con, {
            "name": "Иванов Александр", "parent_name": "Родитель", "phone": "+7 900 111-22-33",
        }, self.user)
        self.con.execute("UPDATE students SET erp_id='42' WHERE id=?", (sid,))
        visitor, lead = self.upload(), self.lead_file()
        preview = erppeople.transfer(self.con, [visitor], [lead], self.user, erp_ids=["42"])
        self.assertEqual(preview["filter"], {"requested": ["42"], "found": ["42"], "missing": []})
        self.assertEqual(preview["counts"].get("visitors"), 1)
        self.assertEqual(preview["counts"].get("leads", 0), 0)
        self.assertEqual(preview["counts"].get("new", 0), 0)
        erppeople.transfer(self.con, [visitor], [lead], self.user, preview["token"], ["42"])
        self.assertEqual(self.con.execute("SELECT count(*) FROM students").fetchone()[0], 1)

        missing = erppeople.transfer(self.con, [visitor], [lead], self.user, erp_ids=["777"])
        self.assertEqual(missing["filter"]["missing"], ["777"])
        self.assertEqual(missing["counts"], {})


if __name__ == "__main__":
    unittest.main()
