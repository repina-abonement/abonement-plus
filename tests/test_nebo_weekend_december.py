from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest

from crm import db, groupterms, schedule


class NeboWeekendDecember(unittest.TestCase):
    hours = [(19, '3–4', '10:00'), (20, '4–5', '11:00'),
             (21, '5–6', '12:00'), (22, '6–8', '13:00')]

    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.con = db.connect(str(Path(folder.name) / 'crm.db'))
        self.addCleanup(self.con.close)
        db.init_db(self.con)
        self.con.execute("INSERT INTO branches(id,name) VALUES (1,'ТЦ «Небо»')")
        self.con.executemany(
            "INSERT INTO groups(id,age,time,branch_id,name,weekdays) VALUES (?,?,?,1,'Группа','5,6')",
            self.hours + [(23, '5–6', '14:00')],
        )

    def test_only_the_four_groups_move_from_december_without_financial_writes(self):
        writes = set()

        def trace(action, table, column, db_name, trigger):
            if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
                writes.add(table)
            return sqlite3.SQLITE_OK

        self.con.set_authorizer(trace)
        groupterms.provision_nebo_weekend_december(self.con)
        self.assertEqual(writes, {'group_versions', 'integrations', 'audit'})
        count = self.con.total_changes
        groupterms.provision_nebo_weekend_december(self.con)
        self.assertEqual(self.con.total_changes, count)
        for day in (date(2026, 11, 28), date(2026, 11, 29), date(2026, 12, 5), date(2026, 12, 6)):
            lessons = schedule.day(self.con, day)['branches'][0]['lessons']
            actual = {lesson['id']: lesson['time'] for lesson in lessons}
            expected = {gid: old if day.month == 11 else old[:3] + '30' for gid, _, old in self.hours}
            self.assertEqual(actual, expected | {23: '14:00'})

    def test_conflict_keeps_all_four_original_schedules(self):
        self.con.execute("""INSERT INTO group_versions(group_id,valid_from,weekdays,time,created_at)
                            VALUES (22,'2026-12-01','5,6','14:15','2026-11-25')""")
        count = self.con.total_changes
        groupterms.provision_nebo_weekend_december(self.con)
        self.assertEqual(self.con.total_changes, count)
