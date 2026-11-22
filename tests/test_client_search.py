import tempfile
import unittest

from crm import db, people, services, smart


class ClientSearch(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(tempfile.mktemp(suffix=".sqlite3"))
        db.init_db(self.con)
        smart.REQUIRE = False
        self.sid = services.create_student(
            self.con,
            {"name": "Минибаев Дамир", "parent_name": "Анна Минибаева", "phone": "+7 900 123-45-67"},
            {"id": 1, "role": "admin", "name": "Тест"},
        )

    def tearDown(self):
        self.con.close()

    def ids(self, query):
        return [row["id"] for row in services.search_students(self.con, query)]

    def test_child_and_parent_words_work_in_both_orders(self):
        for query in ("Минибаев Дамир", "Дамир Минибаев", "Анна Минибаева", "Минибаева Анна"):
            self.assertEqual(self.ids(query), [self.sid], query)
            self.assertEqual([row["id"] for row in people.listing(self.con, q=query)["rows"]], [self.sid], query)

    def test_phone_and_wrong_keyboard_layout_are_preserved(self):
        self.assertEqual(self.ids("79001234567"), [self.sid])
        self.assertEqual(self.ids("vbyb,ftd"), [self.sid])

    def test_all_words_are_required(self):
        self.assertEqual(self.ids("Дамир Несуществующий"), [])


if __name__ == "__main__":
    unittest.main()
