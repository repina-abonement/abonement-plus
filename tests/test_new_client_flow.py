import tempfile
import unittest
from pathlib import Path
from crm import db, services as sv


class NewClientTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.con = db.connect(str(Path(self.tmp.name) / 'new.sqlite3'))
        self.addCleanup(self.con.close)
        db.init_db(self.con)
        self.user = {'id': 1, 'role': 'admin', 'name': 'Synthetic'}
        self.data = {'name': 'Synthetic child', 'parent_name': 'Synthetic parent', 'phone': '+79000000101', 'parent_relation': 'папа', 'messenger': 'MAX', 'funnel': 'members'}

    def test_no_group_saves_role_and_max_without_booking(self):
        sid = sv.create_student(self.con, self.data, self.user)
        student = sv.get_student_row(self.con, sid)
        parent = self.con.execute('SELECT * FROM parents WHERE family_id=?', (student['family_id'],)).fetchone()
        self.assertEqual((parent['relation'], parent['messenger']), ('папа', 'MAX'))
        self.assertIsNone(student['group_id'])
        for table in ('enrollments', 'subscriptions', 'payments', 'messages'):
            self.assertEqual(self.con.execute('SELECT COUNT(*) FROM '+table).fetchone()[0], 0)

    def test_explicit_messenger_is_preserved(self):
        sid = sv.create_student(self.con, {**self.data, 'messenger': 'Telegram'}, self.user)
        self.assertEqual(self.con.execute('SELECT messenger FROM parents WHERE family_id=?', (sv.get_student_row(self.con, sid)['family_id'],)).fetchone()[0], 'Telegram')

    def test_invalid_role_rejected_before_creating_family(self):
        with self.assertRaises(sv.ApiError):
            sv.create_student(self.con, {**self.data, 'parent_relation': 'invalid'}, self.user)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM families').fetchone()[0], 0)
