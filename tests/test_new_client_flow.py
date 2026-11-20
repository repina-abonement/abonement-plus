import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from crm import db, services as sv, comms, lifecycle


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

    def test_parent_email_on_creation_and_for_an_existing_family(self):
        sid = sv.create_new_lead(self.con, {**self.data, 'email': '  parent@example.org '}, self.user)
        family = sv.get_student_row(self.con, sid)['family_id']
        contact = self.con.execute('SELECT id,email FROM parents WHERE family_id=?', (family,)).fetchone()
        self.assertEqual(contact['email'], 'parent@example.org')
        sibling = {**self.data, 'name': 'Sibling', 'family_id': family, 'email': 'other@example.org'}
        sv.create_student(self.con, sibling, self.user)
        def saved():
            return self.con.execute('SELECT email FROM parents WHERE id=?', (contact['id'],)).fetchone()[0]
        self.assertEqual(saved(), 'parent@example.org')
        self.con.execute("UPDATE parents SET email='' WHERE id=?", (contact['id'],))
        sv.create_student(self.con, {**sibling, 'phone': '+79009999999'}, self.user)
        self.assertEqual(saved(), '')
        sv.create_student(self.con, sibling, self.user)
        self.assertEqual(saved(), 'other@example.org')

    def test_explicit_messenger_is_preserved(self):
        sid = sv.create_student(self.con, {**self.data, 'messenger': 'Telegram'}, self.user)
        self.assertEqual(self.con.execute('SELECT messenger FROM parents WHERE family_id=?', (sv.get_student_row(self.con, sid)['family_id'],)).fetchone()[0], 'Telegram')

    def test_invalid_role_rejected_before_creating_family(self):
        with self.assertRaises(sv.ApiError):
            sv.create_student(self.con, {**self.data, 'parent_relation': 'invalid'}, self.user)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM families').fetchone()[0], 0)

    def test_manual_card_is_lead_with_immediate_shared_task(self):
        sid=sv.create_new_lead(self.con,self.data,self.user)
        student=sv.get_student_row(self.con,sid)
        self.assertEqual((student['funnel'],student['stage']),('sales','lead'))
        today=datetime.now(ZoneInfo('Europe/Moscow')).date().isoformat()
        self.assertEqual(student['created_at'][:10],today)
        task=self.con.execute('SELECT * FROM tasks WHERE student_id=?',(sid,)).fetchone()
        self.assertEqual((task['title'],task['due_at'],task['priority']),('Новый лид',today+'T23:59',1))
        self.assertIsNone(task['assignee_id'])
        lifecycle.tick(self.con)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM tasks WHERE auto_key=?",(f'life:call_lead:{sid}',)).fetchone()[0],1)
        self.assertIn(sid,[x['id'] for x in sv.new_cards_today(self.con)['cards']])

    def test_today_list_uses_creation_not_stage_change_and_moscow(self):
        sid=sv.create_student(self.con,self.data,self.user)
        ref=datetime.fromisoformat('2026-11-18T00:30:00+03:00')
        self.con.execute("UPDATE students SET created_at='2026-11-17T22:00:00+00:00' WHERE id=?",(sid,))
        self.assertEqual([x['id'] for x in sv.new_cards_today(self.con,ref)['cards']],[sid])
        self.con.execute("UPDATE students SET created_at='2026-11-17T20:00:00+00:00',stage_changed_at='2026-11-18T12:00' WHERE id=?",(sid,))
        self.assertEqual(sv.new_cards_today(self.con,ref)['cards'],[])

    def test_existing_max_phone_thread_links_on_creation(self):
        cid=comms.upsert_conversation(self.con,'wazzup_max','phone:79000000101',title='Synthetic')
        comms.add_message(self.con,cid,'in','Existing message')
        other=comms.upsert_conversation(self.con,'wazzup_max','79000000101',title='Numeric ID is not a phone')
        sid=sv.create_new_lead(self.con,self.data,self.user)
        fid=sv.get_student_row(self.con,sid)['family_id']
        data=comms.family_comms(self.con,fid,mark_read=False)
        self.assertIn(cid,[x['id'] for x in data['conversations']])
        self.assertNotIn(other,[x['id'] for x in data['conversations']])
        self.assertEqual(data['items'][0]['text'],'Existing message')
