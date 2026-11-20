"""ERP replay preserves records and keeps a reviewed balance plan separate.

Only the owner rule (ERP117559: free zero as of 20 November) is confirmed case
input. The test's old gross payments/earlier charge are synthetic arithmetic to
reproduce the reported FIFO split and are not assertions about working data.
No balance baseline is activated by these read-only audit tests.
"""
from contextlib import redirect_stdout
import csv
from datetime import date
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from crm import db, erpfinancialaudit, erpfull, erpimport, money, services


MISSING = object()


class ERPBalanceSnapshotImportTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='erp-balance-snapshot-import-')
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        self.con = db.connect(str(self.path/'synthetic.sqlite3'))
        self.addCleanup(lambda: self.con.close())
        with redirect_stdout(io.StringIO()):
            db.init_db(self.con)
        self.user = {'id': 1, 'name': 'Синтетический автор', 'role': 'admin'}
        self.sid = services.create_student(self.con, {'name': 'Синтетический Сверяемый',
            'parent_name': 'Синтетический Родитель', 'phone': '+79000000773', 'funnel': 'members'}, self.user)
        self.con.execute("UPDATE students SET erp_id='117559' WHERE id=?", (self.sid,))
        branch = self.con.execute("INSERT INTO branches(name) VALUES ('Синтетический филиал')").lastrowid
        self.group_name = 'Синтетическая среда 18:45'
        self.gid = self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time) VALUES (?,?,'2','18:45')", (branch, self.group_name)).lastrowid
        erpimport.save_groups(self.con, {self.group_name: self.gid})
        self.eid = self.con.execute("INSERT INTO enrollments(student_id,group_id,tariff,active,created_at,start_date) VALUES (?,?,'fixed',1,'2026-09-01','2026-09-01')", (self.sid, self.gid)).lastrowid
        self.previous = self.subscription(self.sid, self.eid, '2026-09', 5200)
        self.december = self.subscription(self.sid, self.eid, '2026-12', 12150)
        # Gross amounts are deliberately synthetic. 7300-5200 leaves 2100
        # from August, then December consumes 10050 from September, leaving
        # 39300 under the old/unbound FIFO. These are not source gross facts.
        self.august = self.payment(self.sid, 7300, '2026-08-15T12:00', 'synthetic-old-August')
        self.september = self.payment(self.sid, 49350, '2026-09-15T12:00', 'synthetic-old-September')
        self.con.execute("INSERT INTO attendance(student_id,group_id,date,status,note) VALUES (?,?,'2026-09-02','present','Синтетическая историческая отметка')", (self.sid, self.gid))
        self.file_counter = 0
        for target in ('crm.fiscal.pk_call', 'crm.comms.http'):
            provider = patch(target, side_effect=AssertionError('External requests forbidden'))
            provider.start()
            self.addCleanup(provider.stop)

    def subscription(self, sid, eid, month, price):
        return self.con.execute("""INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,
            kind,lessons,per_lesson,price,status,created_at,paid_at)
            VALUES (?,?,?,'fixed','month',1,?,?,'paid','2026-11-20T10:00','2026-11-20T10:00')""",
            (sid, eid, month, price, price)).lastrowid

    def payment(self, sid, amount, paid_at, comment):
        return self.con.execute("""INSERT INTO payments(student_id,amount,method,paid_at,comment,
            user_name,confirmed,created_at) VALUES (?,?,'card',?,?,'ERP',1,?)""",
            (sid, amount, paid_at, comment, paid_at)).lastrowid

    def upload(self, kind, headers, rows):
        self.file_counter += 1
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=';')
        writer.writerow(headers)
        writer.writerows(rows)
        return erpimport.upload(self.con, f'synthetic-{kind}-{self.file_counter}.csv', buffer.getvalue().encode(), kind)['id']

    def client_file(self, *, erp_id='117559', name='Синтетический Сверяемый', balance=MISSING, as_of=MISSING):
        headers = ['ID', 'ФИО посетителя']
        row = [erp_id, name]
        if balance is not MISSING:
            headers.append('Баланс')
            row.append(balance)
        if as_of is not MISSING:
            headers.append('Дата баланса ERP')
            row.append(as_of)
        return self.upload('clients', headers, [row])

    def stage_full(self, reverse=False):
        ids = [self.client_file(balance='0', as_of='2026-11-20')]
        sales = [['117559', 'Синтетический Сверяемый', self.group_name, '2026-09', 5200, 1, 'Оплачен', 'Базовый'],
                 ['117559', 'Синтетический Сверяемый', self.group_name, '2026-12', 12150, 1, 'Оплачен', 'Базовый']]
        payments = [['synthetic-source-August', '117559', 'Синтетический Сверяемый', '2026-08-15', 7300, 'Карта'],
                    ['synthetic-source-September', '117559', 'Синтетический Сверяемый', '2026-09-15', 49350, 'Карта']]
        if reverse:
            sales.reverse()
            payments.reverse()
        ids.append(self.upload('subs', ['ID клиента', 'Ученик', 'Группа', 'Месяц', 'Стоимость', 'Занятий', 'Статус', 'Тариф'], sales))
        ids.append(self.upload('payments', ['ID операции', 'ID клиента', 'Плательщик', 'Дата оплаты', 'Сумма', 'Вид оплаты'], payments))
        return ids

    def apply_full(self, ids):
        before = self.con.serialize()
        plan = erpfull.plan(self.con, ids)
        self.assertEqual(self.con.serialize(), before)
        destination = self.path/f'before-{self.file_counter}.sqlite3'
        backup = sqlite3.connect(str(destination))
        try:
            self.con.backup(backup)
        finally:
            backup.close()
        return erpfull.apply(self.con, ids, plan['token'], self.user, str(destination))

    def native_rows(self):
        return {table: [tuple(row) for row in self.con.execute('SELECT * FROM '+table+' WHERE student_id=? ORDER BY id', (self.sid,))]
                for table in ('subscriptions', 'payments', 'attendance')}

    def audit_owner_rule(self):
        report = erpfinancialaudit.audit(self.con, '117559', target_month='2026-12')
        self.assertEqual(report['erp_balance_snapshot']['amount'], 0)
        self.assertEqual(report['erp_balance_snapshot']['as_of_date'], '2026-11-20')
        self.assertEqual(report['erp_balance_snapshot']['cutoff_date'], '2026-11-02')
        self.assertEqual(report['erp_balance_snapshot']['provenance'], 'owner_confirmation')
        self.assertTrue(report['erp_balance_snapshot']['confirmed'])
        self.assertFalse(report['erp_balance_snapshot']['history_complete'])
        plan = report['authoritative_balance_plan']
        self.assertEqual(plan['free_balance'], 0)
        self.assertEqual(plan['historical_funds_available_for_future'], 0)
        self.assertFalse(plan['applied'])
        self.assertFalse(plan['history_recalculation_allowed'])
        december = next(row for row in plan['protected_invoices'] if row['subscription_id'] == self.december)
        self.assertEqual(december['planned_covered'], 0)
        self.assertIsNone(december['planned_unpaid'])
        self.assertEqual(december['coverage_unverified'], 12150)
        self.assertEqual(december['debt_created'], 0)
        self.assertFalse(report['apply_allowed'])
        self.assertFalse(report['plan_complete'])
        self.assertIsNone(report['after'])
        self.assertIsNone(report['proposed_after'])
        return report

    def test_full_source_replay_and_restart_preserve_rows_and_do_not_activate_a_balance_baseline(self):
        native = self.native_rows()
        with patch.object(money, 'date') as clock:
            clock.today.return_value = date(2026, 11, 20)
            observed = money.balance(self.con, self.sid)
            self.assertEqual((observed['deposit'], observed['covered'][self.december]), (39300, 12150))
            for reverse in (False, True, False):
                result = self.apply_full(self.stage_full(reverse))
                self.assertTrue(result['complete'], result['issues'])
                self.assertEqual(self.native_rows(), native)
                report = self.audit_owner_rule()
                self.assertEqual(report['before']['balance']['deposit'], 39300)
                self.assertFalse(report['post_cutoff']['apply_as_new_income'])
                self.assertEqual(money.balance(self.con, self.sid), observed)
            fields = [dict(item['fields']) for item in erpfull.rows(self.con, self.sid)['items'] if item['kind'] == 'clients']
            self.assertTrue(any(row.get('Баланс') == '0' and row.get('Дата баланса ERP') == '2026-11-20' for row in fields))
            self.con.close()
            self.con = db.connect(str(self.path/'synthetic.sqlite3'))
            with redirect_stdout(io.StringIO()):
                db.init_db(self.con)
            self.assertEqual(self.native_rows(), native)
            self.audit_owner_rule()
            self.assertEqual(money.balance(self.con, self.sid), observed)
            self.assertEqual(self.con.execute("SELECT COUNT(*) FROM payments WHERE student_id=? AND comment='Оплата до реестра платежей'", (self.sid,)).fetchone()[0], 0)

    def unconfigured_client(self, erp_id):
        sid = services.create_student(self.con, {'name': 'Синтетический Контрольный',
            'parent_name': 'Синтетический Родитель', 'phone': '+79000000774'}, self.user)
        self.con.execute('UPDATE students SET erp_id=? WHERE id=?', (erp_id, sid))
        self.payment(sid, 1000, '2026-11-10T12:00', 'Синтетическая контрольная оплата')
        return sid

    def test_archived_zero_without_as_of_date_does_not_confirm_a_snapshot_for_another_client(self):
        sid = self.unconfigured_client('synthetic-no-date')
        self.apply_full([self.client_file(erp_id='synthetic-no-date', name='Синтетический Контрольный', balance='0')])
        report = erpfinancialaudit.audit(self.con, 'synthetic-no-date')
        self.assertFalse((report.get('erp_balance_snapshot') or {}).get('confirmed', False))
        self.assertEqual(money.balance(self.con, sid)['deposit'], 1000)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments WHERE student_id=?', (sid,)).fetchone()[0], 1)

    def test_missing_raw_balance_is_not_interpreted_as_zero(self):
        sid = self.unconfigured_client('synthetic-no-balance')
        self.apply_full([self.client_file(erp_id='synthetic-no-balance', name='Синтетический Контрольный', as_of='2026-11-20')])
        report = erpfinancialaudit.audit(self.con, 'synthetic-no-balance')
        snapshot = report.get('erp_balance_snapshot')
        self.assertTrue(snapshot is None or snapshot.get('amount') is None)
        self.assertEqual(money.balance(self.con, sid)['deposit'], 1000)

    def test_stale_or_missing_export_balance_does_not_replace_the_separate_owner_zero_rule(self):
        native = self.native_rows()
        for balance, as_of in (('0', '2026-11-20'), ('39300', '2026-11-01'), (MISSING, MISSING)):
            with self.subTest(balance=str(balance), as_of=str(as_of)):
                self.apply_full([self.client_file(balance=balance, as_of=as_of)])
                self.audit_owner_rule()
                self.assertEqual(self.native_rows(), native)


if __name__ == '__main__':
    unittest.main()
