"""Confirmed ERP sale dates with synthetic names and payment-operation IDs.

ERP client/sale IDs and lesson dates reproduce the supplied transfer case.
All people, local bank/payment records and operation IDs are synthetic. This
module only creates temporary databases and never opens a production card.
"""
from contextlib import redirect_stdout
import csv
from datetime import date, datetime
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crm import clientattendance, db, erpfull, erpimport, erpsingles, services


class ERPSingleRescheduleTest(unittest.TestCase):
    erp_client_id = '133513'
    source_dates = {
        '828081': '2026-11-04',
        '828083': '2026-11-11',
        '828084': '2026-11-18',
        '828085': '2026-11-25',
    }
    source_month = '2026-11'
    moved_sale = '828081'
    moved_date = '2026-12-02'
    reason = 'Перенос по сообщению Ольги'

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='erp-single-reschedule-')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/'synthetic.sqlite3'
        self.con = db.connect(str(self.path))
        self.addCleanup(lambda: self.con.close())
        with redirect_stdout(io.StringIO()):
            db.init_db(self.con)
        clock = patch.object(services, 'today', return_value=date(2026, 11, 20))
        clock.start()
        self.addCleanup(clock.stop)
        self.user = {'id': 1, 'role': 'admin', 'name': 'Синтетический руководитель'}
        self.sid = services.create_student(self.con, {
            'name': 'Контрольный Ребёнок', 'parent_name': 'Контрольный Родитель',
            'phone': '+79000000101', 'funnel': 'members'}, self.user)
        self.con.execute('UPDATE students SET erp_id=? WHERE id=?', (self.erp_client_id, self.sid))
        branch = self.con.execute("INSERT INTO branches(name) VALUES ('Небо')").lastrowid
        self.group_name = 'Небо'
        self.gid = self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time) VALUES (?,?,'2,6','18:45')",
                                    (branch, self.group_name)).lastrowid
        erpimport.save_groups(self.con, {self.group_name: self.gid, 'Небо · '+self.group_name: self.gid})
        # Already-accounted local payments have explicit *synthetic* ERP
        # operation mappings. The import must not adopt by amount/date or make
        # another four payments. These are not the real case's payment IDs.
        self.payment_ids = {}
        for sale_id in self.source_dates:
            operation_id = self.operation_id(sale_id)
            payment_id = self.con.execute("""INSERT INTO payments(student_id,amount,method,paid_at,
                comment,user_name,confirmed,created_at) VALUES (?,2500,'cash','2026-11-01T12:00',
                'Синтетическая ранее учтённая оплата','ERP',1,'2026-11-01T12:00')""", (self.sid,)).lastrowid
            signature = json.dumps([self.sid, '2026-11-01', 2500, 'cash'], ensure_ascii=False,
                                   sort_keys=True, separators=(',', ':'))
            self.con.execute('INSERT INTO erp_full_operations(operation_id,payment_id,signature) VALUES (?,?,?)',
                             (operation_id, payment_id, signature))
            self.payment_ids[operation_id] = payment_id

    @staticmethod
    def operation_id(sale_id):
        return 'synthetic-reschedule-operation-'+sale_id

    def upload_csv(self, kind, headers, rows):
        text = io.StringIO()
        writer = csv.writer(text, delimiter=';')
        writer.writerow(headers)
        writer.writerows(rows)
        return erpimport.upload(self.con, 'synthetic-'+kind+'.csv', text.getvalue().encode(), kind)['id']

    def stage(self, reverse=False, clients=False, payment_group=None, client_group=None,
              paid_date="2026-11-01", payment_method="Наличные"):
        self.con.execute('DELETE FROM erp_uploads')
        ids = []
        if clients:
            ids.append(self.upload_csv('clients',
                ['ID', 'ФИО ребенка', 'ФИО родителя', 'Телефон', 'Филиал', 'Группа', 'Статус'],
                [[self.erp_client_id, 'Контрольный Ребёнок', 'Контрольный Родитель', '+79000000101',
                  'Небо', self.group_name if client_group is None else client_group, 'Занимается']]))
        sources = list(self.source_dates.items())
        if reverse:
            sources.reverse()
        sales = [[sale_id, self.erp_client_id, 'Контрольный Ребёнок', self.source_month,
                  self.group_name, 2500, 1, 'Оплачен', 'Разовое занятие', day,
                  self.operation_id(sale_id), '2026-11-01'] for sale_id, day in sources]
        payments = [[self.operation_id(sale_id), sale_id, self.erp_client_id, 'Контрольный Ребёнок',
                     paid_date, 2500, payment_method, self.source_month,
                     'Синтетическая ранее учтённая оплата'] for sale_id, _ in sources]
        ids.append(self.upload_csv('subs',
            ['ID продажи', 'ID клиента', 'Ученик', 'Месяц', 'Группа', 'Стоимость', 'Занятий',
             'Статус', 'Тариф', 'Дата занятия', 'ID операции', 'Дата продажи'], sales))
        payment_headers = ['ID операции', 'ID продажи', 'ID клиента', 'Плательщик', 'Дата оплаты', 'Сумма',
                           'Способ оплаты', 'Месяц', 'Комментарий']
        if payment_group is not None:
            payment_headers.append('Группа')
            for payment in payments:
                payment.append(payment_group)
        ids.append(self.upload_csv('payments', payment_headers, payments))
        return ids

    def full_import(self, ids):
        plan = erpfull.plan(self.con, ids)
        return erpfull.apply(self.con, ids, plan['token'], self.user, 'synthetic-backup')

    def initial_import(self):
        result = self.full_import(self.stage())
        self.assertTrue(result['complete'], result['issues'])
        self.assertEqual(result['counts'].get('added_payments', 0), 0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0], 4)

    def financial_snapshot(self):
        return {
            'payments': [tuple(row) for row in self.con.execute('SELECT * FROM payments ORDER BY id')],
            'subscriptions': [tuple(row) for row in self.con.execute('SELECT * FROM subscriptions ORDER BY id')],
            'operations': [tuple(row) for row in self.con.execute('SELECT * FROM erp_full_operations ORDER BY operation_id')],
            'single_payments': [tuple(row) for row in self.con.execute('SELECT * FROM erp_single_payments ORDER BY operation_id')],
        }

    def assert_only_recorded_lesson_dates(self):
        expected = ['2026-11-11', '2026-11-18', '2026-11-25', '2026-12-02']
        self.assertEqual([row[0] for row in self.con.execute('SELECT date FROM single_visits WHERE status=\'booked\' ORDER BY date')], expected)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0], 4)
        self.assertEqual(self.con.execute('SELECT SUM(price) FROM subscriptions').fetchone()[0], 10000)
        self.assertEqual(self.con.execute('SELECT SUM(amount) FROM payments').fetchone()[0], 10000)
        self.assertEqual({row[0] for row in self.con.execute('SELECT month FROM subscriptions')}, {self.source_month})
        self.assertEqual(self.con.execute("SELECT lesson_date FROM erp_single_sales WHERE source_id='828081'").fetchone()[0], '2026-11-04')
        self.assertEqual({row['operation_id']: row['payment_id'] for row in self.con.execute('SELECT * FROM erp_full_operations')}, self.payment_ids)
        for row in self.con.execute('''SELECT sale.subscription_id,payment.subscription_id AS paid_subscription
                FROM erp_single_sales sale JOIN erp_single_payments op ON op.source_sale_id=sale.source_id
                JOIN payments payment ON payment.id=op.payment_id'''):
            self.assertEqual(row['subscription_id'], row['paid_subscription'])
        november = clientattendance.calendar(self.con, self.sid, '2026-11')
        december = clientattendance.calendar(self.con, self.sid, '2026-12')
        self.assertEqual([(event['date'], event['kind']) for event in november['events']],
                         [(day, 'single') for day in expected[:3]])
        self.assertEqual([(event['date'], event['kind']) for event in december['events']], [('2026-12-02', 'single')])
        self.assertTrue(all(event['time'] == '18:45' for event in november['events']+december['events']))
        moved_rows = [row for row in services.journal(self.con, self.gid, self.moved_date)['rows'] if row['id'] == self.sid]
        self.assertEqual(len(moved_rows), 1)
        self.assertEqual(moved_rows[0]['tag'], 'single')
        self.assertFalse(moved_rows[0]['unpaid'])
        for unpurchased_day in ('2026-12-06', '2026-12-09'):
            self.assertNotIn(self.sid, {row['id'] for row in services.journal(self.con, self.gid, unpurchased_day)['rows']})

    def test_audited_november_single_move_survives_full_legacy_replay_and_restart(self):
        self.initial_import()
        finances = self.financial_snapshot()
        erpsingles.reschedule_sale(self.con, self.moved_sale, '2026-11-04', self.moved_date,
                                  self.user, self.reason)
        self.assert_only_recorded_lesson_dates()
        self.assertEqual(self.financial_snapshot(), finances)
        for reverse in (False, True):
            with self.subTest(path='full', reverse=reverse):
                result = self.full_import(self.stage(reverse=reverse))
                self.assertTrue(result['complete'], result['issues'])
                self.assert_only_recorded_lesson_dates()
                self.assertEqual(self.financial_snapshot(), finances)
            with self.subTest(path='legacy_update', reverse=reverse):
                self.stage(reverse=reverse, clients=True)
                result = erpimport.update(self.con, self.user, now=datetime(2026, 11, 20, 12))
                self.assertFalse(result.get('issues'), result.get('issues'))
                self.assert_only_recorded_lesson_dates()
                self.assertEqual(self.financial_snapshot(), finances)
        rows = self.con.execute('SELECT * FROM erp_single_reschedules').fetchall()
        self.assertEqual(len(rows), 1)
        audit = json.dumps(dict(rows[0]), ensure_ascii=False)
        for value in (self.moved_sale, '2026-11-04', self.moved_date, self.reason):
            self.assertIn(value, audit)
        self.con.close()
        self.con = db.connect(str(self.path))
        with redirect_stdout(io.StringIO()):
            db.init_db(self.con)
        self.assert_only_recorded_lesson_dates()
        self.assertEqual(self.financial_snapshot(), finances)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM payments WHERE comment='Оплата до реестра платежей'").fetchone()[0], 0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM erp_single_reschedules').fetchone()[0], 1)

    def test_unrecorded_direct_date_edit_is_preserved_but_still_requires_review(self):
        self.initial_import()
        finances = self.financial_snapshot()
        self.con.execute('''UPDATE single_visits SET date=? WHERE subscription_id=
            (SELECT subscription_id FROM erp_single_sales WHERE source_id=?)''', (self.moved_date, self.moved_sale))
        result = self.full_import(self.stage(reverse=True))
        self.assertFalse(result['complete'])
        self.assertTrue(any('изменен' in issue['reason'] or 'изменён' in issue['reason'] for issue in result['issues']))
        self.assert_only_recorded_lesson_dates()
        self.assertEqual(self.financial_snapshot(), finances)

    def test_stale_expected_date_does_not_change_approved_move_or_finances(self):
        self.initial_import()
        erpsingles.reschedule_sale(self.con, self.moved_sale, '2026-11-04', self.moved_date,
                                  self.user, self.reason)
        finances = self.financial_snapshot()
        visits = [tuple(row) for row in self.con.execute('SELECT * FROM single_visits ORDER BY id')]
        audit = [tuple(row) for row in self.con.execute('SELECT * FROM erp_single_reschedules ORDER BY id')]
        with self.assertRaises(erpimport.ImportError_):
            erpsingles.reschedule_sale(self.con, self.moved_sale, '2026-11-04', '2026-12-09',
                                      self.user, 'Синтетический запрос с устаревшей датой')
        self.assertEqual([tuple(row) for row in self.con.execute('SELECT * FROM single_visits ORDER BY id')], visits)
        self.assertEqual([tuple(row) for row in self.con.execute('SELECT * FROM erp_single_reschedules ORDER BY id')], audit)
        self.assertEqual(self.financial_snapshot(), finances)
        self.assert_only_recorded_lesson_dates()

    def test_existing_target_lesson_rejects_move_without_merging_purchases(self):
        self.initial_import()
        finances = self.financial_snapshot()
        visits = [tuple(row) for row in self.con.execute('SELECT * FROM single_visits ORDER BY id')]
        with self.assertRaises(erpimport.ImportError_):
            erpsingles.reschedule_sale(self.con, self.moved_sale, '2026-11-04', '2026-11-11',
                                      self.user, 'Синтетическая проверка занятой даты')
        self.assertEqual([tuple(row) for row in self.con.execute('SELECT * FROM single_visits ORDER BY id')], visits)
        self.assertEqual(self.financial_snapshot(), finances)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM erp_single_reschedules').fetchone()[0], 0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM single_visits WHERE date=?', ('2026-11-11',)).fetchone()[0], 1)

    def test_actual_attendance_mark_blocks_move_and_preserves_all_recorded_data(self):
        self.initial_import()
        self.con.execute("""INSERT INTO attendance(student_id,group_id,date,status,note)
            VALUES (?,?,'2026-11-04','present','Синтетическая фактическая отметка')""", (self.sid, self.gid))
        finances = self.financial_snapshot()
        visits = [tuple(row) for row in self.con.execute('SELECT * FROM single_visits ORDER BY id')]
        marks = [tuple(row) for row in self.con.execute('SELECT * FROM attendance ORDER BY id')]
        with self.assertRaises(erpimport.ImportError_):
            erpsingles.reschedule_sale(self.con, self.moved_sale, '2026-11-04', self.moved_date,
                                      self.user, self.reason)
        self.assertEqual([tuple(row) for row in self.con.execute('SELECT * FROM attendance ORDER BY id')], marks)
        self.assertEqual([tuple(row) for row in self.con.execute('SELECT * FROM single_visits ORDER BY id')], visits)
        self.assertEqual(self.financial_snapshot(), finances)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM erp_single_reschedules').fetchone()[0], 0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM single_visits WHERE date=?', (self.moved_date,)).fetchone()[0], 0)

    def test_same_current_day_is_noop_and_reimport_preserves_latest_audited_move(self):
        self.initial_import()
        finances = self.financial_snapshot()
        first = erpsingles.reschedule_sale(self.con, self.moved_sale, '2026-11-04', self.moved_date,
                                          self.user, self.reason)
        self.assertTrue(first['changed'])
        same = erpsingles.reschedule_sale(self.con, self.moved_sale, self.moved_date, self.moved_date,
                                         self.user, 'Синтетическая повторная проверка')
        self.assertFalse(same['changed'])
        self.assertEqual(same['id'], first['id'])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM erp_single_reschedules').fetchone()[0], 1)
        second = erpsingles.reschedule_sale(self.con, self.moved_sale, self.moved_date, '2026-12-09',
                                           self.user, 'Синтетический повторный согласованный перенос')
        self.assertTrue(second['changed'])
        self.assertEqual(second['id'], first['id'])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM erp_single_reschedules').fetchone()[0], 2)
        self.assertEqual(self.financial_snapshot(), finances)
        result = self.full_import(self.stage(reverse=True))
        self.assertTrue(result['complete'], result['issues'])
        self.stage(clients=True)
        legacy = erpimport.update(self.con, self.user, now=datetime(2026, 11, 20, 12))
        self.assertFalse(legacy.get('issues'), legacy.get('issues'))
        self.assertEqual(self.financial_snapshot(), finances)
        self.assertEqual(self.con.execute("SELECT date FROM single_visits WHERE id=?", (first['id'],)).fetchone()[0], '2026-12-09')
        self.assertEqual(self.con.execute("SELECT lesson_date FROM erp_single_sales WHERE source_id='828081'").fetchone()[0], '2026-11-04')
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM single_visits WHERE date IN ('2026-11-04','2026-12-02')").fetchone()[0], 0)
        self.assertEqual([(event['date'], event['kind']) for event in clientattendance.calendar(self.con, self.sid, '2026-12')['events']],
                         [('2026-12-09', 'single')])
        audits = self.con.execute('SELECT from_date,to_date FROM erp_single_reschedules ORDER BY id').fetchall()
        self.assertEqual([tuple(row) for row in audits], [('2026-11-04', '2026-12-02'), ('2026-12-02', '2026-12-09')])

    def wrong_payment_group(self):
        branch=self.con.execute('SELECT branch_id FROM groups WHERE id=?',(self.gid,)).fetchone()[0]
        name='Денежная метка Небо 17:45'
        gid=self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time) VALUES (?,?,'2,6','17:45')",(branch,name)).lastrowid
        erpimport.save_groups(self.con,{name:gid})
        return name,gid

    def test_payment_group_and_card_payment_day_never_replace_sale_group_or_lesson_dates(self):
        payment_group,wrong_gid=self.wrong_payment_group()
        # Existing synthetic payments now model a card payment before the
        # November lessons. Neither its group nor payment day is entitlement.
        self.con.execute("UPDATE payments SET method='card',paid_at='2026-10-28T12:00'")
        signature=json.dumps([self.sid,'2026-10-28',2500,'card'],ensure_ascii=False,sort_keys=True,separators=(',',':'))
        self.con.execute('UPDATE erp_full_operations SET signature=?',(signature,))
        def stage(clients=False,reverse=False):
            return self.stage(clients=clients,reverse=reverse,payment_group=payment_group,
                              client_group=payment_group,paid_date='2026-10-28',payment_method='Картой')
        initial=self.full_import(stage())
        self.assertTrue(initial['complete'],initial['issues'])
        self.assertEqual(self.con.execute('SELECT count(*) FROM payments').fetchone()[0],4)
        finances=self.financial_snapshot()
        erpsingles.reschedule_sale(self.con,self.moved_sale,'2026-11-04',self.moved_date,self.user,self.reason)
        for reverse in (False,True):
            result=self.full_import(stage(reverse=reverse))
            self.assertTrue(result['complete'],result['issues'])
            stage(clients=True,reverse=reverse)
            legacy=erpimport.update(self.con,self.user,now=datetime(2026,11,20,12))
            self.assertFalse(legacy.get('issues'),legacy.get('issues'))
            self.assert_only_recorded_lesson_dates()
            self.assertEqual(self.financial_snapshot(),finances)
            self.assertEqual({row[0] for row in self.con.execute('SELECT group_id FROM erp_single_sales')},{self.gid})
            self.assertEqual(self.con.execute('SELECT count(*) FROM single_visits WHERE group_id=?',(wrong_gid,)).fetchone()[0],0)
            self.assertEqual({row[0] for row in self.con.execute('SELECT method FROM payments')},{'card'})
            self.assertEqual({row[0][:10] for row in self.con.execute('SELECT paid_at FROM payments')},{'2026-10-28'})

    def test_unknown_sale_group_is_not_replaced_by_known_payment_or_client_group(self):
        known=self.group_name
        self.group_name='Неидентифицированная группа продажи'
        result=self.full_import(self.stage(payment_group=known))
        self.assertFalse(result['complete'])
        self.stage(clients=True,payment_group=known,client_group=known)
        legacy=erpimport.update(self.con,self.user,now=datetime(2026,11,20,12))
        self.assertTrue(legacy.get('issues'))
        self.assertEqual(self.con.execute('SELECT count(*) FROM subscriptions').fetchone()[0],0)
        self.assertEqual(self.con.execute('SELECT count(*) FROM single_visits').fetchone()[0],0)
        self.assertEqual(self.con.execute('SELECT count(*) FROM payments').fetchone()[0],4)
        self.assertEqual({row['operation_id']:row['payment_id'] for row in self.con.execute('SELECT * FROM erp_full_operations')},self.payment_ids)
        self.assertEqual(clientattendance.calendar(self.con,self.sid,'2026-11')['events'],[])

    def test_ambiguous_saved_group_aliases_never_pick_the_first_payment_group(self):
        payment_group,wrong_gid=self.wrong_payment_group()
        self.group_name='Общее название исходной группы'
        erpimport.save_groups(self.con,{'Небо · '+self.group_name:wrong_gid,
                                      'Другой филиал · '+self.group_name:self.gid})
        self.assertIsNone(erpimport._group_id(self.con,'',self.group_name))
        self.stage(clients=True,payment_group=payment_group,client_group=payment_group)
        legacy=erpimport.update(self.con,self.user,now=datetime(2026,11,20,12))
        self.assertTrue(legacy.get('issues'))
        self.assertEqual(self.con.execute('SELECT count(*) FROM subscriptions').fetchone()[0],0)
        self.assertEqual(self.con.execute('SELECT count(*) FROM single_visits').fetchone()[0],0)
        self.assertEqual(self.con.execute('SELECT count(*) FROM payments').fetchone()[0],4)


    def test_unambiguous_shared_alias_and_explicit_mapping_remain_supported(self):
        name='Общая метка исходной группы'
        erpimport.save_groups(self.con,{'Небо · '+name:self.gid,'Другой филиал · '+name:self.gid})
        self.assertEqual(erpimport._group_id(self.con,'',name),self.gid)
        payment_group,wrong_gid=self.wrong_payment_group()
        erpimport.save_groups(self.con,{'Неопределённый филиал · '+name:wrong_gid,name:self.gid})
        self.assertEqual(erpimport._group_id(self.con,'',name),self.gid)

    def test_group_label_wildcard_characters_cannot_select_a_different_sale_group(self):
        payment_group,wrong_gid=self.wrong_payment_group()
        erpimport.save_groups(self.con,{'Небо · Группа AX18:45':wrong_gid})
        self.assertIsNone(erpimport._group_id(self.con,'','Группа A_18:45'))
        self.assertIsNone(erpimport._group_id(self.con,'','Группа A%18:45'))



if __name__ == '__main__':
    unittest.main()
