import os
import tempfile
import unittest

from crm import db, targetedpriorityclients


class PriorityClientFactsTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(os.path.join(tempfile.mkdtemp(), "priority.sqlite3"))
        db.init_db(self.con)
        self.con.execute("DELETE FROM settings WHERE key=?", (targetedpriorityclients.KEY,))
        self.con.execute("INSERT INTO users(name,login,pw_hash,role,active) VALUES ('Юлия Репина','owner-test','x','admin',1)")
        for number, name in enumerate(targetedpriorityclients.CLIENTS.values(), 1):
            family = self.con.execute("INSERT INTO families(name) VALUES (?)", (name,)).lastrowid
            self.con.execute("INSERT INTO students(family_id,name,stage,stage_changed_at,created_at) VALUES (?,?, 'active','2026-11-01','2026-11-01')", (family, name))

    def test_all_facts_attach_once_and_no_cards_are_created(self):
        before = self.con.execute("SELECT count(*) FROM students").fetchone()[0]
        first = targetedpriorityclients.apply(self.con)
        self.assertEqual(first["state"], "complete")
        self.assertEqual(before, self.con.execute("SELECT count(*) FROM students").fetchone()[0])
        self.assertEqual(len(targetedpriorityclients._events()), self.con.execute(
            "SELECT count(*) FROM chat_day_transfer_events WHERE source_id LIKE 'telegram:%'").fetchone()[0])
        second = targetedpriorityclients.apply(self.con)
        self.assertEqual(second, first)
        self.assertEqual(len(targetedpriorityclients._events()), self.con.execute(
            "SELECT count(*) FROM chat_day_transfer_events WHERE source_id LIKE 'telegram:%'").fetchone()[0])
        self.assertEqual(1, self.con.execute("SELECT count(*) FROM tasks WHERE auto_key='priority-clients:sharipov-december-sale-2026-11-23'").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
