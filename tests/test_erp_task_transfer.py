"""Only actual task registers, stable ERP IDs, all dates, repeatable reviewed changes."""
import tempfile
import unittest
from pathlib import Path
from crm import db, erpimport, tasks, services as sv, erptasktransfer as transfer


class TaskTransferTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.con=db.connect(str(Path(self.tmp.name)/'test.sqlite3'));db.init_db(self.con)
        self.user={'id':1,'name':'Тест','role':'admin'}
        self.sid=sv.create_student(self.con,{'name':'Ребёнок Примеров','parent_name':'Анна','phone':'+79000000001'},self.user)
        self.con.execute("UPDATE students SET erp_id='101' WHERE id=?",(self.sid,))
        self.headers='ID задачи;ID посетителя;Задача;Дедлайн;Состояние задачи;Комментарий;Выполнено;Исполнитель;Автор\n'
    def tearDown(self):
        self.con.close();self.tmp.cleanup()
    def upload(self,rows):
        return erpimport.upload(self.con,'tasks.csv',(self.headers+rows).encode(),'tasks')['id']
    def apply(self,uid):
        p=transfer.prepare(self.con,[uid]);return transfer.apply(self.con,[uid],p['token'],self.user,'test-backup')
    def test_all_dates_idempotence_and_exact_values(self):
        uid=self.upload('1;101;Думает;01.09.2025;открыта;Позвонить;;Оля;Юлия\n2;101;Проверить оплату;20.12.2027;открыта;Уточнить;;Оля;Юлия\n3;101;Как прошло пробное;10.11.2026;выполнено;Готово;11.11.2026;Оля;Юлия\n4;101;Старое;01.01.2024;архив;;;;\n')
        before=list(self.con.execute('SELECT * FROM payments'))
        p=transfer.prepare(self.con,[uid]);self.assertEqual(p['counts'],{'add':4});self.assertEqual(self.con.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],0)
        r=self.apply(uid);self.assertEqual(r['verified_tasks'],4)
        stored=self.con.execute('SELECT * FROM tasks ORDER BY id').fetchall()
        self.assertEqual([x['status'] for x in stored],['open','open','done','cancelled'])
        self.assertEqual(stored[0]['title'],'Думает');self.assertEqual(stored[1]['due_at'],'2027-12-20T23:59')
        self.assertEqual(stored[2]['done_at'][:10],'2026-11-11');self.assertTrue(all(x['assignee_id'] is None for x in stored))
        self.assertEqual(list(self.con.execute('SELECT * FROM payments')),before)
        again=self.apply(uid);self.assertEqual(again['counts'],{'same':4});self.assertEqual(self.con.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],4)
    def test_updates_by_id_and_stale_preview(self):
        first=self.upload('1;101;Думает;13.11.2026;открыта;Старое;;;\n');self.apply(first)
        second=self.upload('1;101;Позвонить снова;01.01.2027;выполнено;Новое;02.01.2027;;\n')
        p=transfer.prepare(self.con,[second]);self.assertEqual(p['counts'],{'update':1})
        self.con.execute("UPDATE tasks SET note='Изменила администратор'")
        with self.assertRaises(sv.ApiError):transfer.apply(self.con,[second],p['token'],self.user,'backup')
        self.apply(second);self.assertEqual(self.con.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],1)
        self.assertEqual(self.con.execute('SELECT title,note FROM tasks').fetchone()[:],('Позвонить снова','Новое'))
    def test_conflicts_and_manual_tasks_preserved(self):
        manual=tasks.add_task(self.con,'manual',self.sid,'2026-11-13T23:59',title='Моё поручение',source='manual')
        uid=self.upload('1;101;Думает;13.11.2026;открыта;;;;\n1;101;Другое;14.11.2026;открыта;;;;\n2;999;Нет клиента;13.11.2026;открыта;;;;\n3;101;Неизвестно;13.11.2026;непонятно;;;;\n')
        p=transfer.prepare(self.con,[uid]);self.assertEqual(len(p['issues']),4);self.assertEqual(p['unique_tasks'],0)
        self.apply(uid);self.assertEqual(self.con.execute('SELECT status FROM tasks WHERE id=?',(manual,)).fetchone()[0],'open')
    def test_event_log_and_financial_files_never_used(self):
        uid=erpimport.upload(self.con,'history.csv','ID;Событие;Дата события\n1;Закрыли;13.11.2026\n'.encode(),'tasks')['id']
        p=transfer.prepare(self.con,[uid]);self.assertEqual(p['unique_tasks'],0);self.assertIn('история событий',p['issues'][0]['reason'])
        financial=erpimport.upload(self.con,'payments.csv','Дата оплаты;Сумма\n13.11.2026;1000\n'.encode(),'payments')['id']
        with self.assertRaises(sv.ApiError):transfer.prepare(self.con,[financial])
        with self.assertRaises(sv.ApiError):transfer.apply(self.con,[uid],p['token'],self.user,'')
    def test_two_source_ids_cannot_merge_into_one_legacy_task(self):
        tasks.add_task(self.con,'manual',self.sid,'2026-11-13T23:59',title='Думает',source='erp')
        uid=self.upload('1;101;Думает;13.11.2026;открыта;;;;\n2;101;Думает;13.11.2026;открыта;;;;\n')
        p=transfer.prepare(self.con,[uid]);self.assertEqual(p['unique_tasks'],0);self.assertEqual(len(p['issues']),2)
    def test_auto_duplicate_requires_review(self):
        tasks.add_task(self.con,'how_was',self.sid,'2026-11-13T12:00')
        uid=self.upload('1;101;Как прошло пробное;13.11.2026;открыта;;;;\n')
        p=transfer.prepare(self.con,[uid]);self.assertEqual(p['unique_tasks'],0);self.assertIn('автоматическая задача',p['issues'][0]['reason'])


if __name__=='__main__':unittest.main()
