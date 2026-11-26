import sqlite3
import tempfile
import unittest
from pathlib import Path

from crm import db, recents, server


class RecentsLockTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "crm.sqlite3")
        self.con = db.connect(self.path)
        db.init_db(self.con)
        self.user = dict(self.con.execute("SELECT * FROM users WHERE role='admin' LIMIT 1").fetchone())
        self.con.execute("INSERT INTO families(id,name) VALUES(1,'Test family')")
        self.con.execute(
            "INSERT INTO students(id,family_id,name,stage_changed_at,created_at) "
            "VALUES(1719,1,'Test','2026-11-20T00:00','2026-11-20T00:00')"
        )

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_locked_recents_does_not_block_student_card(self):
        writer = db.connect(self.path)
        reader = db.connect(self.path)
        reader.execute("PRAGMA busy_timeout=1")
        try:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("INSERT INTO settings(key,value) VALUES('lock-test','1')")
            reader.execute("BEGIN")
            result = server.student_get(
                reader, None, self.user, {}, {}, "1719"
            )
            self.assertEqual(result["student"]["id"], 1719)
            self.assertEqual(reader.execute("SELECT COUNT(*) FROM recent_clients").fetchone()[0], 0)
            reader.execute("ROLLBACK")
        finally:
            if reader.in_transaction:
                reader.execute("ROLLBACK")
            if writer.in_transaction:
                writer.execute("ROLLBACK")
            reader.close()
            writer.close()

    def test_card_stays_readable_after_another_connection_commits(self):
        reader = db.connect(self.path)
        try:
            reader.execute("BEGIN")
            reader.execute("SELECT id FROM students WHERE id=1719").fetchone()
            self.con.execute("INSERT INTO settings(key,value) VALUES('snapshot-test','1')")
            result = server.student_get(reader, None, self.user, {}, {}, "1719")
            self.assertEqual(result["student"]["id"], 1719)
            self.assertEqual(reader.execute("SELECT COUNT(*) FROM recent_clients").fetchone()[0], 0)
            self.assertTrue(reader.in_transaction)
        finally:
            reader.close()

    def test_non_lock_operational_error_is_not_hidden(self):
        class BrokenConnection:
            def execute(self, *_args, **_kwargs):
                raise sqlite3.OperationalError("no such table: recent_clients")

        with self.assertRaisesRegex(sqlite3.OperationalError, "no such table"):
            recents.remember_if_available(BrokenConnection(), 1, 1719)


if __name__ == "__main__":
    unittest.main()
