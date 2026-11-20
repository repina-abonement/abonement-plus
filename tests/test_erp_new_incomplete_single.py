"""An unresolved new ERP sale preserves classification without inventing dates."""
from contextlib import redirect_stdout
import csv
from datetime import datetime
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest

from crm import clientattendance, db, erpimport


class NewIncompleteSingleTest(unittest.TestCase):
    now = datetime(2026, 11, 20, 12)
    owner = {'id': 1, 'role': 'admin', 'name': 'Синтетический руководитель'}

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='erp-new-incomplete-single-')
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        self.con = db.connect(str(self.path/'synthetic.sqlite3'))
        self.addCleanup(self.con.close)
        with redirect_stdout(io.StringIO()):
            db.init_db(self.con)
        branch = self.con.execute("INSERT INTO branches(name) VALUES ('Синтетический филиал')").lastrowid
        self.gid = self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time) VALUES (?,'Подтверждённая группа','2','18:45')", (branch,)).lastrowid
        erpimport.save_groups(self.con, {'Подтверждённая группа': self.gid})

    def upload(self, kind, headers, rows):
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=';')
        writer.writerow(headers)
        writer.writerows(rows)
        return erpimport.upload(self.con, 'synthetic-'+kind+'.csv', buffer.getvalue().encode(), kind)['id']

    def stage(self, monthly=False):
        self.upload('clients', ['ID', 'ФИО ребенка', 'ФИО родителя', 'Телефон', 'Статус', 'Группа'],
                    [['new-unresolved-child', 'Синтетический Новый', 'Синтетический Родитель', '+79000000552',
                      'Ушедший', 'Подтверждённая группа']])
        rows = [['', 'new-unresolved-child', 'Синтетический Новый', '2026-12',
                 'Подтверждённая группа', 'Разовое', 2500, 1, 'Оплачен', '']]
        if monthly:
            rows.append(['synthetic-monthly-sale', 'new-unresolved-child', 'Синтетический Новый', '2026-11',
                         'Подтверждённая группа', 'Базовый', 10000, 4, 'Оплачен', ''])
        self.upload('subs', ['ID продажи', 'ID клиента', 'Ученик', 'Месяц', 'Группа', 'Тариф',
                            'Стоимость', 'Занятий', 'Статус', 'Дата занятия'], rows)

    def perform(self, initial):
        if initial:
            backup = sqlite3.connect(str(self.path/'before-run.sqlite3'))
            try:
                self.con.backup(backup)
            finally:
                backup.close()
            erpimport.run(self.con, self.owner, True, backup_path=str(self.path/'before-run.sqlite3'), now=self.now)
        else:
            erpimport.update(self.con, self.owner, now=self.now)
        return self.con.execute("SELECT * FROM students WHERE erp_id='new-unresolved-child'").fetchone()

    def check_unresolved_only(self, initial):
        self.stage()
        preview = erpimport.plan(self.con, self.now.date())
        self.assertEqual(preview['stats']['members_active'], 1)
        student = self.perform(initial)
        self.assertEqual((student['funnel'], student['stage']), ('members', 'active'))
        self.assertIsNone(student['group_id'])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM enrollments WHERE student_id=?', (student['id'],)).fetchone()[0], 0)
        for table in ('subscriptions', 'single_visits', 'payments'):
            self.assertEqual(self.con.execute('SELECT COUNT(*) FROM '+table+' WHERE student_id=?', (student['id'],)).fetchone()[0], 0)
        reviews = self.con.execute('SELECT group_id,payload FROM erp_single_reviews WHERE student_id=?', (student['id'],)).fetchall()
        self.assertTrue(reviews)
        self.assertEqual({row['group_id'] for row in reviews}, {self.gid})
        december = clientattendance.calendar(self.con, student['id'], '2026-12')
        self.assertEqual(december['events'], [])
        self.assertTrue(any(warning['kind'] == 'single_source_review' for warning in december['date_warnings']))

    def check_valid_monthly_is_kept(self, initial):
        self.stage(monthly=True)
        student = self.perform(initial)
        self.assertEqual((student['funnel'], student['stage'], student['group_id']), ('members', 'active', self.gid))
        self.assertEqual([row[0] for row in self.con.execute('SELECT group_id FROM enrollments WHERE student_id=? AND active=1', (student['id'],))], [self.gid])
        subs = self.con.execute('SELECT kind,month,price FROM subscriptions WHERE student_id=?', (student['id'],)).fetchall()
        self.assertEqual([tuple(row) for row in subs], [('month', '2026-11', 10000)])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM single_visits WHERE student_id=?', (student['id'],)).fetchone()[0], 0)
        self.assertEqual(len(clientattendance.calendar(self.con, student['id'], '2026-11')['events']), 4)

    def test_initial_new_incomplete_single_is_active_without_groups_or_calendar_dates(self):
        self.check_unresolved_only(initial=True)

    def test_daily_new_incomplete_single_is_active_without_groups_or_calendar_dates(self):
        self.check_unresolved_only(initial=False)

    def test_initial_valid_monthly_group_survives_an_incomplete_future_single(self):
        self.check_valid_monthly_is_kept(initial=True)

    def test_daily_valid_monthly_group_survives_an_incomplete_future_single(self):
        self.check_valid_monthly_is_kept(initial=False)


if __name__ == '__main__':
    unittest.main()
