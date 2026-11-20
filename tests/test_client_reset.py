import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from crm import backup, clientreset, db


class ClientResetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / 'crm.sqlite3'
        self.con = db.connect(str(self.path)); db.init_db(self.con)
        self.owner = dict(self.con.execute("SELECT * FROM users WHERE role='admin' LIMIT 1").fetchone())
        self.con.execute("INSERT INTO integrations(key,value) VALUES('backup_yadisk_token','test-token')")
        self.con.execute("INSERT INTO integrations(key,value) VALUES('backup_key','test-encryption-key')")
        branch = self.con.execute("INSERT INTO branches(name) VALUES('Branch')").lastrowid
        self.group = self.con.execute("INSERT INTO groups(branch_id,name,weekdays) VALUES(?,?,'0,3')",(branch,'Group')).lastrowid
        family = self.con.execute("INSERT INTO families(name) VALUES('Family')").lastrowid
        self.student = self.con.execute("INSERT INTO students(family_id,name,group_id,stage_changed_at,created_at) VALUES(?,?,?,'2026-11-20','2026-11-20')",(family,'Child',self.group)).lastrowid
        self.con.execute("INSERT INTO payments(student_id,amount,paid_at,created_at) VALUES(?,1000,'2026-11-20','2026-11-20')",(self.student,))
        conversation = self.con.execute("INSERT INTO conversations(channel,external_id,family_id,created_at) VALUES('telegram','x',?,'2026-11-20')",(family,)).lastrowid
        self.con.execute("INSERT INTO messages(conversation_id,direction,ts,text) VALUES(?,'in','2026-11-20','hello')",(conversation,))
        self.client_task = self.con.execute("INSERT INTO tasks(kind,title,student_id,family_id,due_at,created_at) VALUES('manual','Client',?,?,'2026-11-21','2026-11-20')",(self.student,family)).lastrowid
        self.general_task = self.con.execute("INSERT INTO tasks(kind,title,due_at,created_at) VALUES('manual','General','2026-11-21','2026-11-20')").lastrowid

    def tearDown(self):
        self.con.close(); self.tmp.cleanup()

    def test_verified_archive_precedes_cleanup_and_structure_remains(self):
        planned = clientreset.plan(self.con,self.owner)
        self.assertTrue(planned['ready']); self.assertGreater(planned['total_rows'],0)
        with patch.object(backup,'DIR',str(self.root/'backups')), patch.object(backup,'upload',return_value='remote.enc'):
            result = clientreset.apply(self.con,self.owner,{'token':planned['token'],'confirmation':clientreset.CONFIRMATION},'test-commit')
        self.assertTrue(result['applied']); self.assertEqual(result['client_rows_remaining'],0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM students').fetchone()[0],0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0],0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM conversations').fetchone()[0],0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM groups WHERE id=?',(self.group,)).fetchone()[0],1)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM tasks WHERE id=?',(self.client_task,)).fetchone()[0],0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM tasks WHERE id=?',(self.general_task,)).fetchone()[0],1)
        row=self.con.execute('SELECT * FROM client_data_archives').fetchone()
        archive=self.root/'backups'/row['local_filename']; self.assertTrue(archive.is_file())
        saved=sqlite3.connect(archive); self.assertEqual(saved.execute('SELECT COUNT(*) FROM students').fetchone()[0],1); saved.close()
        self.assertEqual(row['remote_filename'],'remote.enc'); self.assertEqual(len(row['sha256']),64)
        checked=clientreset.status(self.con); self.assertTrue(checked['completed'])
        self.assertEqual(checked['client_rows_remaining'],0); self.assertEqual(checked['archive']['sha256'],row['sha256'])

    def test_technical_rows_after_cleanup_do_not_revoke_completion(self):
        planned=clientreset.plan(self.con,self.owner)
        with patch.object(backup,'DIR',str(self.root/'backups')), patch.object(backup,'upload',return_value='remote.enc'):
            clientreset.apply(self.con,self.owner,{'token':planned['token'],'confirmation':clientreset.CONFIRMATION},'test-commit')
        self.con.execute("INSERT INTO audit(ts,user_id,action,detail) VALUES('2026-11-20',?,'login','')",(self.owner['id'],))
        checked=clientreset.status(self.con)
        self.assertTrue(checked['completed'])
        self.assertEqual(checked['client_rows_remaining'],0)
        self.assertEqual(checked['rows_observed_after_reset'],1)

    def test_changed_data_blocks_stale_plan_before_backup(self):
        planned=clientreset.plan(self.con,self.owner)
        self.con.execute("INSERT INTO families(name) VALUES('Later')")
        with patch.object(backup,'DIR',str(self.root/'backups')), patch.object(backup,'upload') as upload:
            with self.assertRaisesRegex(Exception,'изменились'):
                clientreset.apply(self.con,self.owner,{'token':planned['token'],'confirmation':clientreset.CONFIRMATION})
        upload.assert_not_called(); self.assertEqual(self.con.execute('SELECT COUNT(*) FROM students').fetchone()[0],1)

    def test_external_archive_is_required(self):
        self.con.execute("DELETE FROM integrations WHERE key='backup_yadisk_token'")
        with self.assertRaisesRegex(Exception,'внешнюю'):
            clientreset.plan(self.con,self.owner)

    def test_background_job_finishes_after_quick_start_response(self):
        planned=clientreset.plan(self.con,self.owner)
        with patch.object(backup,'DIR',str(self.root/'backups')), patch.object(backup,'upload',return_value='remote.enc'):
            started=clientreset.start(self.con,self.owner,{'token':planned['token'],'confirmation':clientreset.CONFIRMATION},'async-commit')
            self.assertTrue(started['started'])
            for _ in range(100):
                checked=clientreset.status(self.con)
                if checked.get('job',{}).get('state') in ('completed','failed'): break
                time.sleep(.02)
        self.assertEqual(checked['job']['state'],'completed'); self.assertTrue(checked['completed'])
        self.assertEqual(checked['client_rows_remaining'],0)

    def test_status_before_cleanup_is_read_only(self):
        before=self.con.serialize(); checked=clientreset.status(self.con)
        self.assertFalse(checked['completed']); self.assertGreater(checked['client_rows_remaining'],0)
        self.assertEqual(self.con.serialize(),before)

    def test_failed_remote_archive_changes_nothing(self):
        planned=clientreset.plan(self.con,self.owner)
        before=self.con.serialize()
        with patch.object(backup,'DIR',str(self.root/'backups')), patch.object(backup,'upload',side_effect=RuntimeError('offline')):
            with self.assertRaisesRegex(Exception,'не подтверждена'):
                clientreset.apply(self.con,self.owner,{'token':planned['token'],'confirmation':clientreset.CONFIRMATION})
        self.assertEqual(self.con.serialize(),before)

    def test_start_failure_is_visible_in_read_only_status(self):
        with patch.object(clientreset,'_start',side_effect=RuntimeError('synthetic secret must stay hidden')):
            with self.assertRaises(RuntimeError): clientreset.start(self.con,self.owner,{})
        checked=clientreset.status(self.con)
        self.assertEqual(checked['last_start_error']['type'],'RuntimeError')
        self.assertEqual(checked['last_start_error']['message'],'')
        self.assertNotIn('synthetic secret',str(checked['last_start_error']))


if __name__ == '__main__': unittest.main()
