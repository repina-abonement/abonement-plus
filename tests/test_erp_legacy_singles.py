"""Legacy ERP run/update keeps independent single purchases and exact payment IDs.

All records and files are synthetic; this test never loads a production database.
"""
from contextlib import redirect_stdout
import csv
from datetime import datetime
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from crm import db, erpimport, money, services


class LegacyERPSingles(unittest.TestCase):
    now = datetime(2026, 11, 20, 9)
    owner = {'id': 1, 'name': 'Тестовый руководитель', 'role': 'admin'}
    child = 'synthetic-child-1'
    group_name = 'Ср/Вс 16:45'
    lesson_dates = ['2026-11-04', '2026-11-08', '2026-11-11', '2026-11-15']

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='erp-legacy-singles-test-')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.con = db.connect(str(self.folder/'isolated.sqlite3'))
        self.addCleanup(self.con.close)
        # init_db can print generated first-login passwords; never emit them.
        with redirect_stdout(io.StringIO()):
            db.init_db(self.con)
        branch = self.con.execute("INSERT INTO branches(name) VALUES ('Синтетический филиал')").lastrowid
        self.gid = self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time) VALUES (?,?, '2,6','16:45')",
            (branch,self.group_name)).lastrowid
        erpimport.save_groups(self.con,{self.group_name:self.gid,'Синтетический филиал · '+self.group_name:self.gid})

    def csv_file(self,headers,rows):
        data=io.StringIO();writer=csv.writer(data,delimiter=';');writer.writerow(headers);writer.writerows(rows)
        return data.getvalue().encode('utf-8')

    def stage(self,*,dates=True,sale_ids=True,reverse=False):
        # run retains uploaded source files; update receives a fresh daily set.
        self.con.execute('DELETE FROM erp_uploads')
        clients=self.csv_file(['ID','ФИО ребенка','ФИО родителя','Телефон','Филиал','Группа','Статус'],
            [[self.child,'Синтетический Ребёнок','Синтетический Родитель','+7 999 000-00-01',
              'Синтетический филиал',self.group_name,'Занимается']])
        erpimport.upload(self.con,'synthetic-clients.csv',clients,'clients')
        sales=[];payments=[]
        for index,day in enumerate(self.lesson_dates,1):
            sale='synthetic-sale-'+str(index) if sale_ids else ''
            operation='synthetic-operation-'+str(index)
            sales.append([sale,self.child,'Синтетический Ребёнок','Ноябрь 2026',self.group_name,
                2500,1,'Оплачен','Разовое',day if dates else '',operation,'2026-10-31'])
            # Every amount and payment date is equal. Only explicit IDs distinguish them.
            # Payment day is Monday, never a lesson day for this Wed/Sun group.
            payments.append([operation,sale,self.child,'Синтетический Ребёнок','2026-11-02',2500,
                'Наличные','Ноябрь 2026','synthetic source note'])
        if reverse:
            sales.reverse();payments.reverse()
        erpimport.upload(self.con,'synthetic-sales.csv',self.csv_file(
            ['ID продажи','ID клиента','Ученик','Месяц','Группа','Стоимость','Занятий','Статус','Тариф',
             'Дата занятия','ID операции','Дата продажи'],sales),'subs')
        erpimport.upload(self.con,'synthetic-payments.csv',self.csv_file(
            ['ID операции','ID продажи','ID клиента','Плательщик','Дата оплаты','Сумма','Способ оплаты',
             'Месяц','Комментарий'],payments),'payments')

    def first_import(self,**kwargs):
        self.stage(**kwargs)
        path=self.folder/'before-import.sqlite3'
        backup=sqlite3.connect(path)
        try:self.con.backup(backup)
        finally:backup.close()
        result=erpimport.run(self.con,self.owner,rkn_confirmed=True,backup_path=str(path),now=self.now)
        self.assertTrue(path.exists())
        self.sid=self.con.execute('SELECT id FROM students WHERE erp_id=?',(self.child,)).fetchone()[0]
        return result

    def assert_four_purchases(self,*,visits=4):
        subs=list(self.con.execute('SELECT * FROM subscriptions WHERE student_id=? ORDER BY id',(self.sid,)))
        self.assertEqual(len(subs),4)
        self.assertTrue(all(s['kind']=='single' and s['lessons']==1 and s['price']==2500 for s in subs))
        self.assertEqual(sum(s['price'] for s in subs),10000)
        self.assertEqual(self.con.execute('SELECT count(*) FROM single_visits WHERE student_id=?',(self.sid,)).fetchone()[0],visits)
        payments=list(self.con.execute('SELECT * FROM payments WHERE student_id=?',(self.sid,)))
        self.assertEqual(len(payments),4)
        self.assertEqual(len({p['subscription_id'] for p in payments}),4)
        linked_rows=list(self.con.execute('''SELECT s.source_id,s.subscription_id,s.lesson_date,p.operation_id,p.payment_id,
            native.subscription_id AS payment_subscription_id FROM erp_single_sales s
            JOIN erp_single_payments p ON p.source_sale_id=s.source_id
            JOIN payments native ON native.id=p.payment_id WHERE s.student_id=?''',(self.sid,)))
        self.assertEqual(len(linked_rows),4)
        for row in linked_rows:
            index=row['source_id'].rsplit('-',1)[1]
            self.assertEqual(row['operation_id'],'synthetic-operation-'+index)
            self.assertEqual(row['subscription_id'],row['payment_subscription_id'])
            self.assertEqual(row['lesson_date'],self.lesson_dates[int(index)-1] if visits else None)
        return {r['source_id']:r['subscription_id'] for r in self.con.execute('SELECT * FROM erp_single_sales WHERE student_id=?',(self.sid,))}

    def test_run_preserves_four_identified_single_sales_and_exact_payment_links(self):
        self.first_import()
        identities=self.assert_four_purchases()
        self.assertEqual(len(identities),4)
        self.assertEqual({r[0] for r in self.con.execute('SELECT date FROM single_visits WHERE student_id=?',(self.sid,))},set(self.lesson_dates))
        self.assertEqual(self.con.execute('SELECT count(*) FROM subscriptions WHERE student_id=? AND kind!=\'single\'',(self.sid,)).fetchone()[0],0)
        # A normal schedule occurrence outside the four source dates is not a purchase.
        self.assertNotIn(self.sid,{r['id'] for r in services.journal(self.con,self.gid,'2026-11-22')['rows']})
        # Restart initialization must not synthesize another four "paid" payments.
        money.init(self.con)
        self.assert_four_purchases()

    def test_update_and_reordered_file_keep_identities_attendance_and_manual_payment_edits(self):
        self.first_import()
        identities=self.assert_four_purchases()
        ids={r['operation_id']:r['payment_id'] for r in self.con.execute('SELECT * FROM erp_single_payments')}
        self.con.execute("INSERT INTO attendance(student_id,group_id,date,status,note) VALUES (?,?,?,'present','Тестовая ручная отметка')",
            (self.sid,self.gid,self.lesson_dates[0]))
        edited=ids['synthetic-operation-1']
        self.con.execute("UPDATE payments SET amount=2300,paid_at='2026-11-03T14:35',comment='Тестовая ручная правка' WHERE id=?",(edited,))
        for reverse in (False,True,False):
            with self.subTest(reverse=reverse):
                self.stage(reverse=reverse)
                erpimport.update(self.con,self.owner,now=self.now)
                self.assertEqual(self.assert_four_purchases(),identities)
                self.assertEqual({r['operation_id']:r['payment_id'] for r in self.con.execute('SELECT * FROM erp_single_payments')},ids)
                payment=self.con.execute('SELECT * FROM payments WHERE id=?',(edited,)).fetchone()
                self.assertEqual((payment['amount'],payment['paid_at'],payment['comment']),
                    (2300,'2026-11-03T14:35','Тестовая ручная правка'))
                attendance=self.con.execute('SELECT status,note FROM attendance WHERE student_id=? AND group_id=? AND date=?',
                    (self.sid,self.gid,self.lesson_dates[0])).fetchone()
                self.assertEqual(tuple(attendance),('present','Тестовая ручная отметка'))

    def test_missing_lesson_dates_keep_single_sales_without_inventing_bookings(self):
        self.first_import(dates=False)
        self.assert_four_purchases(visits=0)
        self.assertGreaterEqual(self.con.execute('SELECT count(*) FROM erp_single_reviews WHERE student_id=?',(self.sid,)).fetchone()[0],4)
        self.assertEqual(self.con.execute('SELECT count(*) FROM attendance WHERE student_id=?',(self.sid,)).fetchone()[0],0)
        self.assertNotIn(self.sid,{r['id'] for r in services.journal(self.con,self.gid,self.lesson_dates[0])['rows']})
        self.stage(dates=False,reverse=True)
        erpimport.update(self.con,self.owner,now=self.now)
        self.assert_four_purchases(visits=0)

    def test_missing_sale_id_stays_for_review_instead_of_monthly_aggregate(self):
        self.first_import(sale_ids=False)
        self.assertEqual(self.con.execute('SELECT count(*) FROM subscriptions WHERE student_id=?',(self.sid,)).fetchone()[0],0)
        self.assertEqual(self.con.execute('SELECT count(*) FROM single_visits WHERE student_id=?',(self.sid,)).fetchone()[0],0)
        reviews=list(self.con.execute('SELECT reason,payload FROM erp_single_reviews WHERE student_id=?',(self.sid,)))
        self.assertGreaterEqual(len(reviews),4)
        self.assertTrue(any('ID продажи' in r['reason'] for r in reviews))
        self.assertTrue(all(r['payload'] for r in reviews))
        self.assertNotIn(self.sid,{r['id'] for r in services.journal(self.con,self.gid,self.lesson_dates[0])['rows']})
        # Equal amounts and dates must not collapse distinct bank-operation IDs.
        self.assertEqual(self.con.execute('SELECT count(*) FROM payments WHERE student_id=?',(self.sid,)).fetchone()[0],4)
        self.assertFalse(self.con.execute('SELECT 1 FROM payments WHERE student_id=? AND subscription_id IS NOT NULL',(self.sid,)).fetchone())

    def add_monthly_source(self,*,first=True,operation_id='synthetic-monthly-operation',keep_singles=True):
        sales=self.con.execute("SELECT * FROM erp_uploads WHERE kind='subs'").fetchone()
        rows=json.loads(sales['rows']) if keep_singles else []
        monthly=['synthetic-monthly-sale',self.child,'Синтетический Ребёнок','Ноябрь 2026',self.group_name,
            '12400','8','Оплачен','Базовый','','','2026-10-31']
        rows.insert(0 if first else len(rows),monthly)
        self.con.execute('UPDATE erp_uploads SET rows=? WHERE id=?',(json.dumps(rows,ensure_ascii=False),sales['id']))
        payments=self.con.execute("SELECT * FROM erp_uploads WHERE kind='payments'").fetchone()
        rows=json.loads(payments['rows']) if keep_singles else []
        rows.append([operation_id,'synthetic-monthly-sale',self.child,'Синтетический Ребёнок','2026-11-02',
            '12400','Наличные','Ноябрь 2026','Оплата месячного абонемента'])
        self.con.execute('UPDATE erp_uploads SET rows=? WHERE id=?',(json.dumps(rows,ensure_ascii=False),payments['id']))

    def test_genuine_monthly_sale_id_does_not_turn_its_payment_into_a_single(self):
        for operation_id in ('','synthetic-monthly-operation'):
            fixture=type(self)();fixture.setUp()
            try:
                fixture.stage();fixture.add_monthly_source(operation_id=operation_id,keep_singles=False)
                erpimport.run(fixture.con,fixture.owner,True,backup_path=str(fixture.folder/'test-backup'),now=fixture.now)
                subs=fixture.con.execute('SELECT * FROM subscriptions').fetchall()
                payments=fixture.con.execute('SELECT * FROM payments').fetchall()
                with self.subTest(operation_id_present=bool(operation_id)):
                    self.assertEqual(len(subs),1)
                    self.assertEqual(subs[0]['kind'],'month')
                    self.assertEqual(len(payments),1)
                    self.assertEqual(payments[0]['subscription_id'],subs[0]['id'])
            finally:fixture.doCleanups()

    def test_mixed_monthly_and_single_import_is_order_independent_and_repeatable(self):
        results=[]
        for monthly_first in (True,False):
            fixture=type(self)();fixture.setUp()
            try:
                fixture.stage();fixture.add_monthly_source(first=monthly_first)
                erpimport.run(fixture.con,fixture.owner,True,backup_path=str(fixture.folder/'test-backup'),now=fixture.now)
                fixture.sid=fixture.con.execute('SELECT id FROM students WHERE erp_id=?',(fixture.child,)).fetchone()[0]
                identities={r['source_id']:r['subscription_id'] for r in fixture.con.execute('SELECT * FROM erp_single_sales')}
                for repeat in (False,True):
                    if repeat:
                        fixture.stage(reverse=True);fixture.add_monthly_source(first=not monthly_first)
                        erpimport.update(fixture.con,fixture.owner,now=fixture.now)
                    with self.subTest(monthly_first=monthly_first,repeat=repeat):
                        self.assertEqual([tuple(r) for r in fixture.con.execute('SELECT kind,count(*) FROM subscriptions GROUP BY kind ORDER BY kind')],
                            [('month',1),('single',4)])
                        self.assertEqual(fixture.con.execute('SELECT count(*) FROM payments').fetchone()[0],5)
                        self.assertEqual(fixture.con.execute('SELECT count(*) FROM single_visits').fetchone()[0],4)
                        self.assertEqual({r['source_id']:r['subscription_id'] for r in fixture.con.execute('SELECT * FROM erp_single_sales')},identities)
                        monthly=fixture.con.execute("SELECT p.subscription_id,s.kind FROM erp_full_operations o JOIN payments p ON p.id=o.payment_id LEFT JOIN subscriptions s ON s.id=p.subscription_id WHERE o.operation_id='synthetic-monthly-operation'").fetchone()
                        self.assertIsNotNone(monthly)
                        self.assertEqual(monthly['kind'],'month')
                        exact=fixture.con.execute('''SELECT count(*) FROM erp_single_sales s JOIN erp_single_payments p
                            ON p.source_sale_id=s.source_id JOIN payments native ON native.id=p.payment_id
                            WHERE native.subscription_id=s.subscription_id''').fetchone()[0]
                        self.assertEqual(exact,4)
                results.append(sorted((r['source_id'],r['lesson_date']) for r in fixture.con.execute('SELECT * FROM erp_single_sales')))
            finally:fixture.doCleanups()
        self.assertEqual(results[0],results[1])

    def test_undo_clears_stable_operation_foreign_keys_and_allows_reimport(self):
        self.first_import()
        edited=self.con.execute('SELECT id FROM payments ORDER BY id LIMIT 1').fetchone()[0]
        self.con.execute("UPDATE payments SET amount=2300,comment='Тестовая ручная правка перед повтором' WHERE id=?",(edited,))
        self.stage()
        erpimport.update(self.con,self.owner,now=self.now)
        row=self.con.execute('SELECT amount,comment FROM payments WHERE id=?',(edited,)).fetchone()
        self.assertEqual(tuple(row),(2300,'Тестовая ручная правка перед повтором'))
        erpimport.undo(self.con,self.owner)
        for table in ('payments','subscriptions','single_visits','erp_full_operations','erp_single_sales','erp_single_payments','erp_single_reviews'):
            with self.subTest(table=table):
                self.assertEqual(self.con.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
        self.assertEqual(self.con.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.first_import()
        self.assert_four_purchases()

    def test_undo_preserves_preexisting_external_payment_and_clears_removed_purchase_link(self):
        self.first_import()
        other_sid=services.create_student(self.con,{'name':'Синтетический Другой','parent_name':'Синтетический Родитель2',
            'phone':'+7 999 000-00-02'},self.owner)
        self.con.execute("UPDATE students SET erp_id='outside-child' WHERE id=?",(other_sid,))
        existing=self.con.execute("""INSERT INTO payments(student_id,amount,method,paid_at,created_at,user_name)
            VALUES (?,2500,'cash','2026-11-02T12:00','2026-11-02T12:00','ERP')""",(other_sid,)).lastrowid
        signature=json.dumps([other_sid,'2026-11-02',2500,'cash'],ensure_ascii=False,sort_keys=True,separators=(',',':'))
        self.con.execute('INSERT INTO erp_full_operations VALUES (?,?,?)',('outside-operation-1',existing,signature))
        self.stage()
        for upload in self.con.execute('SELECT * FROM erp_uploads').fetchall():
            rows=json.loads(upload['rows'])
            for row in rows:
                for index,value in enumerate(row):
                    if value==self.child:row[index]='outside-child'
                    elif value=='Синтетический Ребёнок':row[index]='Синтетический Другой'
                    elif value=='+7 999 000-00-01':row[index]='+7 999 000-00-02'
                    elif value.startswith('synthetic-operation-'):row[index]=value.replace('synthetic-operation-','outside-operation-')
                    elif value.startswith('synthetic-sale-'):row[index]=value.replace('synthetic-sale-','outside-sale-')
            self.con.execute('UPDATE erp_uploads SET rows=? WHERE id=?',(json.dumps(rows,ensure_ascii=False),upload['id']))
        erpimport.update(self.con,self.owner,now=self.now)
        self.assertIsNotNone(self.con.execute('SELECT subscription_id FROM payments WHERE id=?',(existing,)).fetchone()[0])
        erpimport.undo(self.con,self.owner)
        remaining=self.con.execute('SELECT student_id,amount,subscription_id FROM payments WHERE id=?',(existing,)).fetchone()
        self.assertIsNotNone(remaining)
        self.assertEqual((remaining['student_id'],remaining['amount']),(other_sid,2500))
        self.assertIsNone(remaining['subscription_id'])
        self.assertEqual(self.con.execute('SELECT payment_id FROM erp_full_operations WHERE operation_id=?',
            ('outside-operation-1',)).fetchone()[0],existing)

    def test_payment_row_with_missing_date_is_retained_for_review_on_update(self):
        self.first_import()
        original=[tuple(r) for r in self.con.execute('SELECT id,amount,paid_at,subscription_id FROM payments ORDER BY id')]
        self.stage()
        upload=self.con.execute("SELECT * FROM erp_uploads WHERE kind='payments'").fetchone()
        rows=json.loads(upload['rows']);headers=json.loads(upload['headers'])
        rows[-1][headers.index('Дата оплаты')]=''
        self.con.execute('UPDATE erp_uploads SET rows=? WHERE id=?',(json.dumps(rows,ensure_ascii=False),upload['id']))
        erpimport.update(self.con,self.owner,now=self.now)
        self.assertEqual([tuple(r) for r in self.con.execute('SELECT id,amount,paid_at,subscription_id FROM payments ORDER BY id')],original)
        self.assertTrue(any('synthetic-operation-4' in row['payload'] for row in self.con.execute('SELECT payload FROM erp_single_reviews')))
        self.assertEqual(self.con.execute('SELECT count(*) FROM erp_uploads').fetchone()[0],0)

    def test_existing_single_sale_id_cannot_become_a_monthly_bill_on_update(self):
        self.first_import()
        identities=self.assert_four_purchases()
        self.stage()
        upload=self.con.execute("SELECT * FROM erp_uploads WHERE kind='subs'").fetchone()
        rows=json.loads(upload['rows']);headers=json.loads(upload['headers'])
        rows[0][headers.index('Тариф')]='Базовый'
        rows[0][headers.index('Занятий')]='8'
        self.con.execute('UPDATE erp_uploads SET rows=? WHERE id=?',(json.dumps(rows,ensure_ascii=False),upload['id']))
        erpimport.update(self.con,self.owner,now=self.now)
        self.assertEqual(self.assert_four_purchases(),identities)
        self.assertEqual(self.con.execute("SELECT count(*) FROM subscriptions WHERE kind='month'").fetchone()[0],0)
        reviews=self.con.execute("SELECT reason,payload FROM erp_single_reviews WHERE source_id='synthetic-sale-1'").fetchall()
        self.assertTrue(any(row['reason'] and 'Базовый' in row['payload'] for row in reviews))
