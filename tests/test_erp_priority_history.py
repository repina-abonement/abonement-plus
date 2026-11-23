import unittest

from crm import db, erpimport, erppriorityhistory, services


class ErpPriorityHistoryTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(":memory:"); db.init_db(self.con)
        self.user = {"id": 1, "name": "Тест"}
        sid = services.create_student(self.con, {"name": "Иванов Иван", "parent_name": "Мария", "phone": "79000000000"}, self.user)
        self.con.execute("UPDATE students SET erp_id='42' WHERE id=?", (sid,)); self.sid = sid
        text = ("ID продажи;ID клиента;ФИО посетителя;Дата продажи;Дата начала;Дата окончания;Группа;Количество занятий;Тип;Оплачено;Задолженность\n"
                "700;42;Иванов Иван;03.11.2026;01.11.2026;29.11.2026;Арктика;9;Абонемент;13500;0\n"
                "701;99;Другой Ребёнок;04.11.2026;01.11.2026;29.11.2026;Небо;8;Абонемент;9000;0")
        self.file_id = erpimport.upload(self.con, "subs.csv", text.encode(), "subs")["id"]

    def test_only_requested_history_without_finance(self):
        preview = erppriorityhistory.plan(self.con, self.file_id, ["42", "77"])
        self.assertEqual(preview["found"], ["42"]); self.assertEqual(preview["missing"], ["77"])
        self.assertEqual(preview["new"], 1)
        done = erppriorityhistory.apply(self.con, self.file_id, ["42", "77"], preview["token"], self.user)
        self.assertEqual(done["applied"], 1)
        text = self.con.execute("SELECT text FROM history WHERE student_id=? AND kind LIKE 'erp-subscription:%'", (self.sid,)).fetchone()[0]
        self.assertIn("Арктика", text); self.assertIn("занятий: 9", text)
        self.assertNotIn("13500", text); self.assertNotIn("Задолженность", text)
        again = erppriorityhistory.plan(self.con, self.file_id, ["42", "77"])
        self.assertEqual((again["new"], again["already"]), (0, 1))

    def test_export_without_native_sale_id_uses_stable_nonfinancial_identity(self):
        text = ("ID посетителя;ФИО посетителя;Тип;Дата продажи;Дата начала;Дата окончания;Группа;Количество занятий;Оплачено(руб.);Долг за конкретную продажу\n"
                "42;Иванов Иван;Абонемент;03.11.2026;01.11.2026;29.11.2026;Арктика;9;13500;0")
        file_id = erpimport.upload(self.con, "real-shape.csv", text.encode(), "subs")["id"]
        preview = erppriorityhistory.plan(self.con, file_id, ["42"])
        self.assertEqual((preview["new"], preview["issues"]), (1, []))
        done = erppriorityhistory.apply(self.con, file_id, ["42"], preview["token"], self.user)
        self.assertEqual(done["applied"], 1)


if __name__ == "__main__": unittest.main()
