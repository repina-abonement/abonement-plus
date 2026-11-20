import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crm import backup, chatarchiverestore, clientreset, db


class ChatArchiveRestoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name); self.path=self.root/'crm.sqlite3'
        self.con=db.connect(str(self.path)); db.init_db(self.con)
        self.owner=dict(self.con.execute("SELECT * FROM users WHERE role='admin' LIMIT 1").fetchone())
        self.con.execute("INSERT INTO integrations(key,value) VALUES('backup_yadisk_token','token')")
        self.con.execute("INSERT INTO integrations(key,value) VALUES('backup_key','key')")
        family=self.con.execute("INSERT INTO families(name) VALUES('Archived family')").lastrowid
        self.cid=self.con.execute("""INSERT INTO conversations(channel,external_id,title,phone,family_id,created_at,last_at,last_text)
            VALUES('telegram','old-chat','Old title','79990000000',?,'2026-01-01','2026-01-02','second')""",(family,)).lastrowid
        self.con.execute("INSERT INTO messages(conversation_id,direction,text,external_id,ts) VALUES(?,'in','first','m1','2026-01-01')",(self.cid,))
        self.con.execute("INSERT INTO messages(conversation_id,direction,text,external_id,ts) VALUES(?,'out','second','m2','2026-01-02')",(self.cid,))
        self.con.execute("INSERT INTO conversation_pins(user_id,conversation_id,created_at) VALUES(?,?,?)",(self.owner['id'],self.cid,'2026-01-02'))

    def tearDown(self): self.con.close(); self.tmp.cleanup()

    def _reset(self):
        planned=clientreset.plan(self.con,self.owner)
        with patch.object(backup,'DIR',str(self.root/'backups')),patch.object(backup,'upload',return_value='remote.enc'):
            clientreset.apply(self.con,self.owner,{'token':planned['token'],'confirmation':clientreset.CONFIRMATION},'reset')

    def test_restores_history_merges_new_traffic_and_is_idempotent(self):
        self._reset()
        current=self.con.execute("""INSERT INTO conversations(channel,external_id,title,created_at,last_at,last_text)
            VALUES('telegram','old-chat','','2026-01-01','2026-01-03','third')""").lastrowid
        self.con.execute("INSERT INTO messages(conversation_id,direction,text,external_id,ts) VALUES(?,'in','third','m3','2026-01-03')",(current,))
        with patch.object(backup,'DIR',str(self.root/'backups')):
            planned=chatarchiverestore.plan(self.con,self.owner)
            self.assertEqual(planned['preview']['source_counts']['messages'],2)
            self.assertEqual(planned['preview']['added']['messages'],2)
            done=chatarchiverestore.apply(self.con,self.owner,{'token':planned['token'],'confirmation':chatarchiverestore.CONFIRMATION},'restore')
            self.assertTrue(done['applied'])
            planned2=chatarchiverestore.plan(self.con,self.owner)
            done2=chatarchiverestore.apply(self.con,self.owner,{'token':planned2['token'],'confirmation':chatarchiverestore.CONFIRMATION},'restore')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM conversations').fetchone()[0],1)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM messages').fetchone()[0],3)
        self.assertEqual(self.con.execute('SELECT family_id FROM conversations').fetchone()[0],None)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM conversation_pins').fetchone()[0],1)
        self.assertEqual(done2['result']['added']['messages'],0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM students').fetchone()[0],0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0],0)


if __name__=='__main__': unittest.main()
