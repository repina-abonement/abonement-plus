"""Manual unread, cancelled automatic reminders, private preferences and identified titles."""
import tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta
from crm import db,comms,chatinbox as inbox

NOW=datetime(2026,11,16,12,0)
class ChatActions(unittest.TestCase):
    def setUp(self):
        self.con=db.connect(str(Path(tempfile.mkdtemp())/'actions.sqlite3'));db.init_db(self.con)
        self.user=dict(self.con.execute("SELECT * FROM users WHERE role='admin'").fetchone())
        uid=self.con.execute("INSERT INTO users(login,name,role,pw_hash) VALUES ('synthetic-other','Synthetic','operator','disabled')").lastrowid
        self.other={'id':uid,'role':'operator'}
        self.cid=comms.upsert_conversation(self.con,'wazzup_telegram','phone:79000000111',phone='79000000111')
    def tearDown(self):self.con.close()
    def message(self,seconds,direction='in',status=''):
        return comms.add_message(self.con,self.cid,direction,'Synthetic text',status=status,ts=(NOW-timedelta(seconds=seconds)).isoformat())
    def test_manual_unread_is_idempotent_and_readonly_polling_does_not_clear_it(self):
        self.message(10);comms.conversation(self.con,self.cid)
        before=[tuple(r) for r in self.con.execute('SELECT * FROM messages')]
        first=inbox.mark_unread(self.con,self.cid,self.user);second=inbox.mark_unread(self.con,self.cid,self.user)
        self.assertEqual(first['unread'],1);self.assertEqual(second['unread'],1)
        for _ in range(3):self.assertEqual(comms.conversation(self.con,self.cid,False)['conversation']['unread'],1)
        self.assertEqual(comms.counts(self.con)['unread'],1);self.assertEqual([tuple(r) for r in self.con.execute('SELECT * FROM messages')],before)
        comms.conversation(self.con,self.cid,True);self.assertEqual(comms.counts(self.con)['unread'],0)
    def test_old_unanswered_message_stays_read_and_never_becomes_urgent(self):
        self.message(3600,'out');self.message(1800)
        comms.conversation(self.con,self.cid,True)
        for _ in range(3):
            data=comms.inbox(self.con,'unread',user=self.user)
            self.assertEqual(data['conversations'],[]);self.assertEqual(data['unread'],0)
            self.assertEqual(data['overdue'],0)
            self.assertFalse(comms.conversation(self.con,self.cid,False)['conversation']['reply_overdue'])

    def test_archive_and_mute_are_private_new_message_restores_unmuted_chat(self):
        self.message(20)
        inbox.set_preference(self.con,self.cid,self.user,{'archived':True})
        self.assertEqual(comms.inbox(self.con,user=self.user)['conversations'],[])
        self.assertEqual(len(comms.inbox(self.con,user=self.other)['conversations']),1)
        self.assertEqual(len(comms.inbox(self.con,'archived',user=self.user)['conversations']),1)
        self.assertEqual(len(comms.inbox(self.con,'unread',user=self.user)['conversations']),1)
        self.message(5);self.assertEqual(len(comms.inbox(self.con,user=self.user)['conversations']),1)
        inbox.set_preference(self.con,self.cid,self.user,{'archived':True,'muted':True});self.message(0)
        self.assertEqual(comms.inbox(self.con,user=self.user)['conversations'],[])
        self.assertEqual(comms.counts(self.con,self.user)['last_notify_in'],0)
        self.assertGreater(comms.counts(self.con,self.other)['last_notify_in'],0)
        with self.assertRaises(comms.CommsError):inbox.set_preference(self.con,self.cid,self.user,{'muted':'yes'})
    def test_read_archived_message_stays_read_without_counter_writes(self):
        self.message(301);inbox.set_preference(self.con,self.cid,self.user,{'archived':True,'muted':True});inbox.mark_read(self.con,self.cid)
        before=self.con.total_changes
        data=comms.inbox(self.con,'unread',user=self.user)
        self.assertEqual(data['unread'],0);self.assertEqual(data['conversations'],[]);self.assertEqual(self.con.total_changes,before)

    def test_title_uses_card_children_correct_parent_and_relation_without_overwriting_original(self):
        fid=self.con.execute("INSERT INTO families(name) VALUES ('Synthetic family')").lastrowid
        self.con.execute("INSERT INTO parents(family_id,name,phone,phone_norm,relation) VALUES (?,'Анна','+79000000111','79000000111','мама')",(fid,))
        self.con.execute("INSERT INTO parents(family_id,name,phone,phone_norm,relation) VALUES (?,'Кирилл','+79000000222','79000000222','папа')",(fid,))
        self.con.execute("INSERT INTO students(family_id,name,stage_changed_at,created_at) VALUES (?,'Тестовик Примеров','2026-11-16','2026-11-16')",(fid,))
        self.con.execute("UPDATE conversations SET family_id=?,title='Messenger name' WHERE id=?",(fid,self.cid))
        c=comms.inbox(self.con,user=self.user)['conversations'][0]
        self.assertEqual(c['title'],'Примеров Тестовик · Анна, мама');self.assertEqual(c['original_title'],'Messenger name')
        self.assertEqual(self.con.execute('SELECT title FROM conversations WHERE id=?',(self.cid,)).fetchone()[0],'Messenger name')
        self.assertEqual(len(comms.inbox(self.con,q='Примеров Тестовик',user=self.user)['conversations']),1)
        self.assertEqual(len(comms.inbox(self.con,q='Анна',user=self.user)['conversations']),1)
    def test_unique_full_name_can_identify_title_but_ambiguous_names_never_link_histories(self):
        fid=self.con.execute("INSERT INTO families(name) VALUES ('Synthetic family')").lastrowid
        self.con.execute("INSERT INTO students(family_id,name,stage_changed_at,created_at) VALUES (?,'Тестовик Примеров','2026-11-16','2026-11-16')",(fid,))
        self.con.execute("INSERT INTO parents(family_id,name,relation) VALUES (?,'Анна','мама')",(fid,))
        self.con.execute("UPDATE conversations SET title='Примеров Тестовик',phone_norm='',phone='' WHERE id=?",(self.cid,))
        c=comms.inbox(self.con,user=self.user)['conversations'][0]
        self.assertEqual(c['title'],'Примеров Тестовик · Анна, мама');self.assertIsNone(c['family_id'])
        other=self.con.execute("INSERT INTO families(name) VALUES ('Other synthetic family')").lastrowid
        self.con.execute("INSERT INTO students(family_id,name,stage_changed_at,created_at) VALUES (?,'Тестовик Примеров','2026-11-16','2026-11-16')",(other,))
        c=comms.inbox(self.con,user=self.user)['conversations'][0]
        self.assertEqual(c['title'],'Примеров Тестовик');self.assertIsNone(c['display_family_id'])
    def test_pinned_and_recent_chats_are_not_displaced_by_old_unanswered(self):
        self.message(700)
        recent=comms.upsert_conversation(self.con,'wazzup_telegram','newer')
        comms.add_message(self.con,recent,'in','New pending',ts=(NOW-timedelta(seconds=301)).isoformat())
        pinned=comms.upsert_conversation(self.con,'wazzup_telegram','pinned')
        comms.add_message(self.con,pinned,'out','Answered',ts=NOW.isoformat());comms.pin_conversation(self.con,pinned,self.user,True)
        items=comms.inbox(self.con,user=self.user)['conversations']
        self.assertEqual([c['id'] for c in items],[pinned,recent,self.cid])
        self.assertTrue(all(not c['reply_overdue'] for c in items))

    def test_genuine_unread_and_manual_marks_are_preserved_after_cancellation(self):
        self.message(3600)
        data=comms.inbox(self.con,'unread',user=self.user)
        self.assertEqual(data['unread'],1);self.assertFalse(data['conversations'][0]['reply_overdue'])
        inbox.mark_read(self.con,self.cid);inbox.mark_unread(self.con,self.cid,self.user)
        self.assertEqual(comms.counts(self.con)['unread'],1)


if __name__=='__main__':unittest.main()
