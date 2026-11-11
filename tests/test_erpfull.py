"""Lossless recovery, dry-run isolation and idempotent operational writes."""
import os
import tempfile
import unittest
from unittest.mock import patch

from crm import db, erpfull as full, erpimport as ei, services, money


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        os.environ['CRM_QUIET'] = '1'
        os.environ['CRM_ADMIN_PASSWORD'] = 'synthetic-test-password'
        self.tmp = tempfile.TemporaryDirectory()
        self.con = db.connect(os.path.join(self.tmp.name, 'test.sqlite3'))
        db.init_db(self.con)
        self.user = {'id': None, 'name': 'Test', 'role': 'admin'}
        self.sid = services.create_student(self.con, {'name':'Тестовый Ребёнок','parent_name':'Мама','phone':'79000000001'}, self.user)
        self.con.execute("UPDATE students SET erp_id='101' WHERE id=?", (self.sid,))
        br = self.con.execute("INSERT INTO branches(name) VALUES ('Тест')").lastrowid
        self.gid = self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time) VALUES (?,'Тестовая группа','0','10:00')", (br,)).lastrowid

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def upload(self, text, kind):
        return ei.upload(self.con, kind+'.csv', text.encode(), kind)['id']

    def run_import(self, ids):
        p=full.plan(self.con,ids)
        return full.apply(self.con,ids,p['token'],self.user,'test-backup')

    def test_visits_source_and_conflict(self):
        uid=self.upload('ID клиента;ФИО;Дата занятия;Группа;Отметка;Дополнительное поле\n101;Тестовый Ребёнок;01.11.2026;Тестовая группа;был;сохранить точно\n101;Тестовый Ребёнок;02.11.2026;Тестовая группа;не был;конфликт\n999;Неизвестный Ребёнок;01.11.2026;Тестовая группа;был;не потерять\n','visits')
        self.con.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES (?,?,'2026-11-02','present')",(self.sid,self.gid))
        self.con.execute('BEGIN IMMEDIATE')
        p=full.plan(self.con,[uid])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM erp_full_files').fetchone()[0],0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM attendance').fetchone()[0],1)
        self.con.execute('COMMIT')
        r=full.apply(self.con,[uid],p['token'],self.user,'backup')
        self.assertEqual(r['source_rows'],3)
        self.assertEqual(r['native_rows'],1)
        self.assertEqual(r['issue_count'],2)
        self.assertEqual(dict(full.rows(self.con,self.sid)['items'][0]['fields'])['Дополнительное поле'],'сохранить точно')
        self.run_import([uid])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM attendance').fetchone()[0],2)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM erp_full_rows').fetchone()[0],3)

    def test_identical_amount_distinct_operations(self):
        uid=self.upload('ID операции;ID посетителя;Посетитель;Дата оплаты;Сумма;Вид оплаты\n501;101;Тестовый Ребёнок;01.11.2026;1000;Наличные\n502;101;Тестовый Ребёнок;01.11.2026;1000;Наличные\n','payments')
        self.con.execute("INSERT INTO payments(student_id,amount,method,paid_at,user_name,confirmed,created_at) VALUES (?,1000,'cash','2026-11-01T12:00','ERP',1,'2026-11-01')",(self.sid,))
        r=self.run_import([uid])
        self.assertEqual(r['counts']['matched_payments'],1)
        self.assertEqual(r['counts']['added_payments'],1)
        self.run_import([uid])
        self.assertEqual(self.con.execute('SELECT SUM(amount) FROM payments').fetchone()[0],2000)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM erp_full_operations').fetchone()[0],2)

    def test_conflicting_id_and_precise_money(self):
        self.assertIsNone(full._money('1000,50'))
        uid=self.upload('ID;ФИО посетителя;Телефон;Имя родителя;Баланс\n102;Тестовый Ребёнок;79000000001;Мама;1234,56\n','clients')
        r=self.run_import([uid])
        self.assertEqual(r['linked_rows'],0)
        self.assertIn('различаются ID',r['issues'][0]['reason'])
        self.assertEqual(full.rows(self.con)['items'][0]['fields'][-1][1],'1234,56')

    def test_event_id_is_not_student_id_and_no_new_open_task(self):
        uid=self.upload('ID;Дата события;Дедлайн;Задача (END);Задача (NEW);Событие;Фимилия и имя;Комментарий\n101;05.11.2026 11:00;06.11.2026;Позвонить;;Выполнена;Чужой Ребёнок;Не связывать по ID задачи\n600;05.11.2026 12:00;06.11.2026;Позвонить;;Выполнена;Тестовый Ребёнок;Сохранять историю\n','tasks')
        count=self.con.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
        r=self.run_import([uid])
        self.assertEqual(r['linked_rows'],1)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],count)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM history WHERE kind='erp_task_event'").fetchone()[0],1)
        self.run_import([uid])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM history WHERE kind='erp_task_event'").fetchone()[0],1)

    def test_stale_plan_and_backup_required(self):
        uid=self.upload('ID клиента;ФИО;Дата занятия;Группа;Отметка\n101;Тестовый Ребёнок;01.11.2026;Тестовая группа;был\n','visits')
        p=full.plan(self.con,[uid])
        with self.assertRaises(ei.ImportError_):full.apply(self.con,[uid],p['token'],self.user,'')
        self.con.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES (?,?,'2026-11-01','absent')",(self.sid,self.gid))
        with self.assertRaises(ei.ImportError_):full.apply(self.con,[uid],p['token'],self.user,'backup')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM erp_full_files').fetchone()[0],0)

    def test_export_name_separators_and_unique_visit_names(self):
        uid=self.upload('ID;Дата события;Событие;Фимилия и имя\n600;05.11.2026 12:00;Выполнена;"Тестовый Ребёнок;"\n601;05.11.2026 12:01;Выполнена;"Тестовый Ребёнок; Другой Ребёнок;"\n','tasks')
        r=self.run_import([uid])
        self.assertEqual(r['native_rows'],1)
        self.assertEqual(r['issue_count'],1)
        visits=self.upload('ФИО;Дата занятия;Группа;Отметка\nТестовый Ребёнок;01.11.2026;Тестовая группа;был\n','visits')
        self.assertEqual(self.run_import([visits])['native_rows'],1)
        services.create_student(self.con, {'name':'Тестовый Ребёнок','parent_name':'Другой родитель','phone':'79000000002'}, self.user)
        self.assertEqual(self.run_import([visits])['linked_rows'],0)

    def test_short_payment_name_requires_id_and_phone(self):
        self.con.execute("UPDATE students SET name='Тестовый Ребёнок Иванович' WHERE id=?", (self.sid,))
        matcher=full.Matcher(self.con)
        rec={'erp_id':'101','child_name':'Тестовый Ребёнок','phone':'79000000001'}
        self.assertEqual(matcher.find(rec,'payments')[0],self.sid)
        rec['phone']='79000000002'
        self.assertIsNone(matcher.find(rec,'payments')[0])
        rec['phone']='79000000001';rec['child_name']='Чужой Ребёнок'
        self.assertIsNone(matcher.find(rec,'payments')[0])

    def test_restore_confirmed_trial_without_message_or_overwrite(self):
        self.con.execute("UPDATE students SET name='Александр Иванов',stage='trial_booked',birth='2022-01-01',group_id=? WHERE id=?", (self.gid,self.sid))
        self.con.execute("UPDATE parents SET name='Ирина'")
        uid=self.upload('ID;ФИО;Телефон;Имя родителя;Группа;Дата пробного;Комментарий\n101;Александр Иванов;79000000001;Ирина;Тестовая группа;2099-01-05T10:00;Подтверждённая запись из переписки\n','clients')
        with patch('crm.tasks.send_booked') as send:
            r=self.run_import([uid])
            self.assertEqual(r['counts']['restored_trial_dates'],1)
            self.assertFalse(send.called)
            self.assertEqual(self.con.execute('SELECT trial_at FROM students WHERE id=?',(self.sid,)).fetchone()[0],'2099-01-05T10:00')
            self.run_import([uid])
            self.assertFalse(send.called)
        self.con.execute("UPDATE students SET trial_at='2099-01-12T10:00' WHERE id=?", (self.sid,))
        self.assertEqual(self.run_import([uid])['issue_count'],1)
        self.assertEqual(self.con.execute('SELECT trial_at FROM students WHERE id=?',(self.sid,)).fetchone()[0],'2099-01-12T10:00')

    def test_layout_classification(self):
        self.assertEqual(ei.guess_kind(['ID','ФИО посетителя','Имя родителя','Посещение']),'clients')
        self.assertEqual(ei.guess_kind(['ID','Имя ребенка','Имя родителя','Задача']),'clients')
        self.assertEqual(ei.guess_kind(['Тип','Стоимость со скидкой, руб.(выручка для ПиУ)','Оплачено(руб.)']),'subs')
        self.assertEqual(ei.guess_kind(['ID операции','ID посетителя','Сумма','Дата оплаты']),'payments')

    def test_subscriptions_restart_does_not_invent_payment(self):
        uid=self.upload('ID посетителя;ФИО посетителя;Тип;Группа;Дата начала;Дата продажи;Стоимость со скидкой, руб.(выручка для ПиУ);Оплачено(руб.)\n101;Тестовый Ребёнок;Абонемент (стандарт /8);Тестовая группа;01.11.2026;25.10.2026;8000;8000\n','subs')
        r=self.run_import([uid])
        self.assertEqual(r['counts']['added_subscriptions'],1)
        sub=self.con.execute('SELECT * FROM subscriptions').fetchone()
        self.assertEqual((sub['price'],sub['lessons'],sub['status'],sub['created_at'],sub['paid_at']),(8000,8,'paid','2026-10-25',None))
        money.init(self.con)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0],0)
        self.run_import([uid])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0],1)

    def test_existing_crm_subscription_and_stage_preserved(self):
        eid=self.con.execute("INSERT INTO enrollments(student_id,group_id,tariff,active,created_at) VALUES (?,?,'fixed',1,'2026-11-01')",(self.sid,self.gid)).lastrowid
        self.con.execute("INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at) VALUES (?,?,'2026-11','fixed',8,1000,8000,'invoiced','2026-11-01')",(self.sid,eid))
        self.con.execute("UPDATE students SET stage='refused' WHERE id=?",(self.sid,))
        uid=self.upload('ID клиента;ФИО;Месяц;Группа;Стоимость;Занятий;Статус\n101;Тестовый Ребёнок;2026-11;Тестовая группа;9000;8;Оплачен\n','subs')
        r=self.run_import([uid])
        self.assertEqual(r['issue_count'],1)
        self.assertEqual(self.con.execute('SELECT price FROM subscriptions').fetchone()[0],8000)
        self.assertEqual(self.con.execute('SELECT stage FROM students WHERE id=?',(self.sid,)).fetchone()[0],'refused')


if __name__ == '__main__':
    unittest.main()
