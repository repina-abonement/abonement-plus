import tempfile,unittest
from pathlib import Path
from crm import db,comms

class RecipientIsolationTest(unittest.TestCase):
 def setUp(self):
  self.con=db.connect(str(Path(tempfile.mkdtemp())/'isolation.sqlite3'));db.init_db(self.con);self.addCleanup(self.con.close)
 def chat(self,external,phone='',family=None,channel='wazzup_telegram'):
  cid=self.con.execute("INSERT INTO conversations(channel,external_id,phone_norm,family_id,title,created_at,last_at) VALUES (?,?,?,?,?,'2026-11-18','2026-11-18')",(channel,external,phone,family,external)).lastrowid
  comms.add_message(self.con,cid,'in','Only '+external,external_id='message-'+external)
  return cid
 def test_unknown_phone_does_not_group_unrelated_contacts_or_mark_them_read(self):
  first=self.chat('rufina');other=self.chat('anna');third=self.chat('lena')
  before={r['id']:r['unread'] for r in self.con.execute('SELECT id,unread FROM conversations')}
  self.con.execute("INSERT INTO calls(phone,phone_norm,direction,status,started_at) VALUES ('','','in','answered','2026-11-18')")
  d=comms.conversation(self.con,first)
  self.assertEqual([t['id'] for t in d['targets']],[first])
  self.assertTrue(d['targets'][0]['same_contact'])
  self.assertEqual({m['conversation_id'] for m in d['history']},{first})
  for cid in [other,third]:self.assertEqual(self.con.execute('SELECT unread FROM conversations WHERE id=?',(cid,)).fetchone()[0],before[cid])
 def test_family_without_phones_does_not_include_unlinked_empty_contacts(self):
  family=self.con.execute("INSERT INTO families(name) VALUES ('Synthetic')").lastrowid
  own=self.chat('own',family=family);other=self.chat('unlinked')
  convs,items=comms.history(self.con,family,[],False)
  self.assertEqual([c['id'] for c in convs],[own]);self.assertNotIn(other,[i.get('conversation_id') for i in items])
  self.assertEqual(comms.history(self.con,None,[],False),([],[]))
 def test_other_parent_is_not_reply_target_or_history_in_personal_chat(self):
  family=self.con.execute("INSERT INTO families(name) VALUES ('Synthetic')").lastrowid
  first=self.chat('mom-tg','79000000111',family)
  same=self.chat('mom-max','79000000111',family,'wazzup_max')
  dad=self.chat('dad','79000000222',family)
  before=self.con.execute('SELECT unread FROM conversations WHERE id=?',(dad,)).fetchone()[0]
  d=comms.conversation(self.con,first)
  self.assertEqual({t['id'] for t in d['targets']},{first,same})
  self.assertEqual({m['conversation_id'] for m in d['history']},{first,same})
  self.assertEqual(self.con.execute('SELECT unread FROM conversations WHERE id=?',(dad,)).fetchone()[0],before)
  # The deliberately aggregated family card still has both parents.
  self.assertEqual({c['id'] for c in comms.history(self.con,family,[],False)[0]},{first,same,dad})
 def test_whitespace_phone_is_not_an_identity(self):
  self.chat('unknown',' ')
  self.assertEqual(comms.history(self.con,None,[' ',''],False),([],[]))
