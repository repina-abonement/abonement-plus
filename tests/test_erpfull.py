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

    def test_unnamed_lead_matches_only_a_unique_unnamed_card(self):
        rec={'erp_id':'102','child_name':'','phone':'79000000001'}
        self.assertIsNone(full.Matcher(self.con).find(rec,'clients')[0])
        self.con.execute("UPDATE students SET name=?,erp_id='' WHERE id=?",(ei.NO_NAME,self.sid))
        self.assertEqual(full.Matcher(self.con).find(rec,'clients')[0],self.sid)
        family=self.con.execute('SELECT family_id FROM students WHERE id=?',(self.sid,)).fetchone()[0]
        services.create_student(self.con,{'name':ei.NO_NAME,'family_id':family},self.user)
        self.assertIsNone(full.Matcher(self.con).find(rec,'clients')[0])

    def test_journal_short_name_requires_verified_student_catalog(self):
        self.con.execute("UPDATE students SET name='Тестовый Ребёнок Иванович' WHERE id=?",(self.sid,))
        rec={'erp_id':'101','child_name':'Тестовый Ребёнок'}
        self.assertIsNone(full.Matcher(self.con).find(rec,'visits')[0])
        uid=self.upload('ID;ФИО посетителя;Телефон\n101;Тестовый Ребёнок Иванович;79000000001\n','clients')
        self.run_import([uid])
        self.assertEqual(full.Matcher(self.con).find(rec,'visits')[0],self.sid)
        rec['phone']='79000000002'
        self.assertIsNone(full.Matcher(self.con).find(rec,'visits')[0])

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

    def test_lead_and_student_same_number_are_separate(self):
        other = services.create_student(self.con, {'name':'Другой Ребёнок','parent_name':'Мама лида','phone':'79000000002'}, self.user)
        self.con.execute("UPDATE students SET erp_id='999' WHERE id=?", (other,))
        client = self.upload('ID;Имя ребенка;Телефон;Имя родителя\n101;Другой Ребёнок;79000000002;Мама лида\n','clients')
        task = self.upload('ID;Дата события;Событие;Фимилия и имя\n500;05.11.2026 12:00;Выполнена;"ID:101 Мама лида;"\n','tasks')
        f = full._files(self.con, [task])[0]
        row = f['rows'][0]
        text = 'ERP, задача 500\n'+'\n'.join(f'{h}: {v}' for h,v in zip(f['headers'],row) if v)
        hid = full._history(self.con, self.sid, '2026-11-05T12:00', text, 'erp_task_event')
        before = {t:self.con.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('tasks','payments','attendance')}
        r = self.run_import([client, task])
        self.assertEqual(r['counts']['corrected_task_links'],1)
        self.assertEqual(self.con.execute('SELECT student_id FROM history WHERE id=?',(hid,)).fetchone()[0],other)
        self.assertEqual(self.con.execute('SELECT erp_id FROM students WHERE id=?',(other,)).fetchone()[0],'999')
        self.assertEqual(full.Matcher(self.con).find({'erp_id':'101','child_name':'Тестовый Ребёнок'},'visits')[0],self.sid)
        self.assertEqual(self.run_import([client,task])['history_corrections'],[])
        self.assertIsNone(full.Matcher(self.con).find({'id_namespace':'lead','erp_id':'101','child_name':'ID:101 Мама; ID:102 Папа'},'tasks')[0])
        self.assertEqual(before,{t:self.con.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in before})

    def test_unresolved_lead_event_quarantined_and_later_recovered(self):
        task = self.upload('ID;Дата события;Событие;Фимилия и имя\n500;05.11.2026 12:00;Выполнена;"ID:101 Мама лида;"\n','tasks')
        f = full._files(self.con,[task])[0];row=f['rows'][0]
        text = 'ERP, задача 500\n'+'\n'.join(f'{h}: {v}' for h,v in zip(f['headers'],row) if v)
        hid = full._history(self.con,self.sid,'2026-11-05T12:00',text,'erp_task_event')
        r=self.run_import([task])
        self.assertEqual(r['counts']['quarantined_task_events'],1)
        self.assertEqual(r['linked_rows'],0)
        self.assertEqual(self.con.execute('SELECT text FROM history WHERE id=?',(hid,)).fetchone()[0],text)
        self.assertIsNone(self.con.execute('SELECT assigned_student_id FROM erp_full_history_corrections WHERE history_id=?',(hid,)).fetchone()[0])
        self.assertFalse(any(h['text']==text for h in services.student_detail(self.con,self.sid)['history']))
        self.assertEqual(self.run_import([task])['history_corrections'],[])
        other=services.create_student(self.con,{'name':ei.NO_NAME,'parent_name':'Мама лида','phone':'79000000002'},self.user)
        client=self.upload('ID;Имя ребенка;Телефон;Имя родителя\n101;;79000000002;Мама лида\n','clients')
        r=self.run_import([client,task])
        self.assertEqual(r['counts']['corrected_task_links'],1)
        self.assertEqual(self.con.execute('SELECT student_id FROM history WHERE id=?',(hid,)).fetchone()[0],other)
        self.assertEqual(self.con.execute('SELECT assigned_student_id FROM erp_full_history_corrections WHERE history_id=?',(hid,)).fetchone()[0],other)
        self.assertFalse(self.con.execute('SELECT erp_id FROM students WHERE id=?',(other,)).fetchone()[0])
        self.assertTrue(any(h['text']==text for h in services.student_detail(self.con,other)['history']))

    def test_typed_trial_visit_does_not_create_subscription_attendance(self):
        uid = self.upload('ID посетителя;ФИО посетителя;Группа;Дата занятия;Вид занятия;Отметка\n'
                          '101;Тестовый Ребёнок;Тестовая группа;13.10.2026 16:30;Пробное занятие;+\n', 'visits')
        report = self.run_import([uid])
        self.assertEqual(report['counts']['updated_trial_visits'], 1)
        trial = self.con.execute('SELECT visit_at,group_id,came FROM erp_trial_visits WHERE student_id=?',
                                 (self.sid,)).fetchone()
        self.assertEqual(tuple(trial), ('2026-10-13T16:30', self.gid, 1))
        self.assertEqual(self.con.execute('SELECT count(*) FROM attendance').fetchone()[0], 0)
        self.assertEqual(self.run_import([uid])['counts'].get('updated_trial_visits', 0), 0)
        second = self.upload('ID посетителя;Группа;Дата занятия;Вид занятия;Отметка\n'
                             '101;Тестовая группа;16.10.2026 16:30;Пробное занятие;не был\n', 'visits')
        self.run_import([second])
        for day, came in [('2026-10-13', 1), ('2026-10-16', 0)]:
            journal = services.journal(self.con, self.gid, day)
            self.assertEqual([(row['status'], row['trial_came']) for row in journal['rows']], [('trial', came)])

    def test_reviewed_trial_reclassifies_only_matching_unencumbered_mark(self):
        self.con.execute("INSERT INTO attendance(student_id,group_id,date,status) VALUES (?,?,'2026-10-13','present')",
                         (self.sid, self.gid))
        header = ('ID посетителя;Группа;Дата занятия;Вид занятия;Отметка;ID записи ERP;'
                  'Подтверждена замена отметки CRM\n')
        row = '101;Тестовая группа;13.10.2026 16:30;Пробное занятие;+;12345;'
        blocked = self.upload(header + row + 'нет\n', 'visits')
        self.assertEqual(self.run_import([blocked])['issue_count'], 1)
        approved = self.upload(header + row + 'да\n', 'visits')
        self.assertEqual(self.run_import([approved])['counts']['updated_trial_visits'], 1)
        self.assertEqual(self.con.execute('SELECT count(*) FROM attendance').fetchone()[0], 0)
        saved = self.con.execute('SELECT replaced_mark FROM erp_trial_visits').fetchone()[0]
        self.assertIn('"status":"present"', saved)
        self.assertEqual(self.run_import([approved])['counts']['updated_trial_visits'], 0)

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

    def test_exact_period_backfill_preserves_payment_and_subscription_id(self):
        from crm import subscriptiondates
        header = ('ID посетителя;ФИО посетителя;Тип;Группа;Дата начала;Дата окончания;'
                  'Стоимость со скидкой;Статус;Количество занятий\n')
        original = self.upload(header.replace('Дата начала;Дата окончания', 'Месяц') +
                               '101;Тестовый Ребёнок;Абонемент;Тестовая группа;2026-10;5000;Оплачен;5\n', 'subs')
        self.run_import([original])
        before = dict(self.con.execute('SELECT * FROM subscriptions').fetchone())
        exact = self.upload(header +
                            '101;Тестовый Ребёнок;Абонемент;Тестовая группа;13.10.2026;27.10.2026;5000;Оплачен;5\n', 'subs')
        report = self.run_import([exact])
        self.assertEqual(report['counts']['updated_subscription_dates'], 1)
        self.assertEqual(dict(self.con.execute('SELECT * FROM subscriptions').fetchone()),
                         before | {'valid_from': '2026-10-13', 'valid_until': '2026-10-27'})
        self.assertTrue(subscriptiondates.outside_period(self.con, self.sid, self.gid, '2026-10-12'))
        self.assertFalse(subscriptiondates.outside_period(self.con, self.sid, self.gid, '2026-10-13'))
        self.assertFalse(subscriptiondates.outside_period(self.con, self.sid, self.gid, '2026-10-27'))
        self.assertTrue(subscriptiondates.outside_period(self.con, self.sid, self.gid, '2026-10-28'))
        self.assertEqual(self.run_import([exact])['counts'].get('updated_subscription_dates', 0), 0)

    def test_existing_crm_subscription_and_stage_preserved(self):
        eid=self.con.execute("INSERT INTO enrollments(student_id,group_id,tariff,active,created_at) VALUES (?,?,'fixed',1,'2026-11-01')",(self.sid,self.gid)).lastrowid
        self.con.execute("INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at) VALUES (?,?,'2026-11','fixed',8,1000,8000,'invoiced','2026-11-01')",(self.sid,eid))
        self.con.execute("UPDATE students SET stage='refused' WHERE id=?",(self.sid,))
        uid=self.upload('ID клиента;ФИО;Месяц;Группа;Стоимость;Занятий;Статус\n101;Тестовый Ребёнок;2026-11;Тестовая группа;9000;8;Оплачен\n','subs')
        r=self.run_import([uid])
        self.assertEqual(r['issue_count'],1)
        self.assertEqual(self.con.execute('SELECT price FROM subscriptions').fetchone()[0],8000)
        self.assertEqual(self.con.execute('SELECT stage FROM students WHERE id=?',(self.sid,)).fetchone()[0],'refused')

    def test_current_task_adopts_exact_legacy_without_duplicate(self):
        from crm import tasks
        tid=tasks.import_erp_task(self.con,'Почему не купили',self.sid,'2026-11-12T10:00','Прежнее примечание')
        uid=self.upload('ID задачи;ID посетителя;ФИО посетителя;Задача;Дедлайн;Состояние задачи;Исполнитель;Комментарий\n500;101;Тестовый Ребёнок;Почему не купили;12.11.2026 00:00;Открыта;Оператор;Полный исходный комментарий\n','tasks')
        r=self.run_import([uid])
        self.assertEqual(r['counts']['matched_tasks'],1)
        self.assertEqual(r['native_rows'],1)
        t=self.con.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
        self.assertIn('Прежнее примечание',t['note'])
        self.assertIn('Полный исходный комментарий',t['note'])
        self.assertEqual(t['due_at'],'2026-11-12T10:00')
        self.run_import([uid])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],1)
        self.assertEqual(self.con.execute('SELECT note FROM tasks WHERE id=?',(tid,)).fetchone()[0],t['note'])

    def test_current_task_does_not_duplicate_changed_legacy_deadline(self):
        from crm import tasks
        tid=tasks.import_erp_task(self.con,'Почему не купили',self.sid,'2026-11-12T10:00')
        uid=self.upload('ID задачи;ID посетителя;ФИО посетителя;Задача;Дедлайн;Состояние задачи\n500;101;Тестовый Ребёнок;Почему не купили;15.11.2026;Открыта\n','tasks')
        r=self.run_import([uid])
        self.assertEqual(r['issue_count'],1)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],1)
        self.assertEqual(self.con.execute('SELECT due_at FROM tasks WHERE id=?',(tid,)).fetchone()[0],'2026-11-12T10:00')

    def test_reviewed_invoice_correction_preserves_before_and_does_not_add_payment(self):
        eid=self.con.execute("INSERT INTO enrollments(student_id,group_id,tariff,active,created_at) VALUES (?,?,'fixed',1,'2026-11-01')",(self.sid,self.gid)).lastrowid
        subid=self.con.execute("INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at) VALUES (?,?,'2026-11','fixed',9,1000,9000,'invoiced','2026-11-01')",(self.sid,eid)).lastrowid
        uid=self.upload('ID посетителя;ФИО посетителя;Тип;Группа;Дата начала;Стоимость со скидкой, руб.(выручка для ПиУ);Оплачено(руб.);Сверено по выгрузке ERP;Ожидаемая сумма CRM;Ожидаемые занятия CRM;Ожидаемый статус CRM\n101;Тестовый Ребёнок;Абонемент (стандарт /8);Тестовая группа;01.11.2026;8000;8000;да;9000;9;invoiced\n','subs')
        self.con.execute('UPDATE subscriptions SET price=9100 WHERE id=?',(subid,))
        self.assertEqual(self.run_import([uid])['issue_count'],1)
        self.con.execute('UPDATE subscriptions SET price=9000 WHERE id=?',(subid,))
        r=self.run_import([uid])
        self.assertEqual(r['counts']['updated_subscriptions'],1)
        self.assertEqual(self.con.execute('SELECT price,lessons,status,paid_at FROM subscriptions WHERE id=?',(subid,)).fetchone()[:],(8000,8,'paid',None))
        money.init(self.con)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0],0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM erp_full_subscription_corrections').fetchone()[0],1)
        self.assertEqual(self.run_import([uid])['issue_count'],0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM erp_full_subscription_corrections').fetchone()[0],1)

    def test_october_lesson_review_guards_and_idempotence(self):
        self.check_lesson_review('10')

    def test_november_lesson_review_guards_and_idempotence(self):
        self.check_lesson_review('11')

    def check_lesson_review(self, month):
        eid=self.con.execute(f"INSERT INTO enrollments(student_id,group_id,tariff,active,created_at) VALUES (?,?,'fixed',1,'2026-{month}-01')",(self.sid,self.gid)).lastrowid
        subid=self.con.execute(f"INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at,paid_at) VALUES (?,?,'2026-{month}','fixed',8,1518,12150,'paid','2026-{month}-01','2026-09-28')",(self.sid,eid)).lastrowid
        header='ID посетителя;ФИО посетителя;Тип;Группа;Дата начала;Стоимость со скидкой;Статус;Количество занятий;Сверено количество занятий ERP;Ожидаемая сумма CRM;Ожидаемые занятия CRM;Ожидаемый статус CRM\n'
        row=f'101;Тестовый Ребёнок;Абонемент;Тестовая группа;01.{month}.2026;12150;оплачен;9;да;12150;8;paid\n'
        uid=self.upload(header+row,'subs')
        before=dict(self.con.execute('SELECT * FROM subscriptions WHERE id=?',(subid,)).fetchone())
        p=full.plan(self.con,[uid])
        self.assertEqual(p['counts']['updated_subscription_lessons'],1)
        self.assertEqual(dict(self.con.execute('SELECT * FROM subscriptions WHERE id=?',(subid,)).fetchone()),before)
        self.con.execute('UPDATE subscriptions SET lessons=7 WHERE id=?',(subid,))
        with self.assertRaises(ei.ImportError_):full.apply(self.con,[uid],p['token'],self.user,'backup')
        self.assertEqual(self.run_import([uid])['issue_count'],1)
        self.con.execute('UPDATE subscriptions SET lessons=8 WHERE id=?',(subid,))
        self.assertEqual(self.run_import([uid])['counts']['updated_subscription_lessons'],1)
        after=dict(self.con.execute('SELECT * FROM subscriptions WHERE id=?',(subid,)).fetchone())
        self.assertEqual({k:v for k,v in after.items() if k not in ('lessons','per_lesson')},{k:v for k,v in before.items() if k not in ('lessons','per_lesson')})
        self.assertEqual((after['lessons'],after['per_lesson']),(9,1350))
        self.assertEqual(self.run_import([uid])['counts'].get('updated_subscription_lessons',0),0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0],0)
        for bad in (row.replace('12150;оплачен','12000;оплачен'),row.replace('оплачен;9','не оплачен;9'),row.replace(f'01.{month}.2026','01.12.2026')):
            self.assertEqual(self.run_import([self.upload(header+bad,'subs')])['issue_count'],1)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0],1)

    def test_orphan_count_review_requires_exact_owned_subscription(self):
        subid=self.con.execute("INSERT INTO subscriptions(student_id,month,tariff,lessons,per_lesson,price,status,created_at) VALUES (?,'2026-10','fixed',8,1120,8960,'paid','2026-10-01')",(self.sid,)).lastrowid
        header='ID посетителя;ФИО посетителя;Тип;Группа;Дата начала;Стоимость со скидкой;Статус;Количество занятий;Сверено количество занятий ERP;Ожидаемая сумма CRM;Ожидаемые занятия CRM;Ожидаемый статус CRM;Проверенный ID абонемента CRM\n'
        row=f'101;Тестовый Ребёнок;Абонемент;Неизвестная старая группа;01.10.2026;8960;оплачен;7;да;8960;8;paid;{subid}\n'
        uid=self.upload(header+row,'subs')
        self.assertEqual(self.run_import([uid])['counts']['updated_subscription_lessons'],1)
        self.assertEqual(self.con.execute('SELECT lessons,enrollment_id FROM subscriptions WHERE id=?',(subid,)).fetchone()[:],(7,None))
        self.assertEqual(self.run_import([uid])['counts'].get('updated_subscription_lessons',0),0)
        bad=self.upload(header+row.replace(f';{subid}\n',';999999\n'),'subs')
        self.assertEqual(self.run_import([bad])['issue_count'],1)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0],1)


if __name__ == '__main__':
    unittest.main()
