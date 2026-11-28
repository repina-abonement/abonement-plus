import sqlite3
import unittest
from crm.historyview import readable_event,task_timeline

class TaskTimelineTest(unittest.TestCase):
    def test_plain_events_and_import_cleanup(self):
        row=dict(id=1,ts='2026-11-27',kind='note',user_name='Администратор',text='Написала в MAX, просили позвонить завтра')
        self.assertEqual(readable_event(row)['text'],row['text'])
        self.assertIsNone(readable_event(dict(row,text='Выгрузка ERP 123 · Счета')))
        self.assertIsNone(readable_event(dict(row,text='Импорт завершён: source_key=abc')))
        result=readable_event(dict(row,kind='stage',text='Статус: trial_booked; student_id=123'))
        self.assertIn('Записан на пробное',result['text'])
        self.assertNotIn('student_id',result['text'])
        self.assertNotIn('technical_text',result)

    def test_history_chronology_pagination_and_card_isolation(self):
        con=sqlite3.connect(':memory:');con.row_factory=sqlite3.Row;self.addCleanup(con.close)
        con.executescript('''CREATE TABLE tasks(id,student_id,assignee_id);
        INSERT INTO tasks VALUES(1,10,NULL);
        CREATE TABLE history(id INTEGER PRIMARY KEY,student_id,ts,kind,text,user_name);
        CREATE TABLE erp_full_history_corrections(history_id,assigned_student_id);''')
        con.executemany('INSERT INTO history VALUES(?,10,?,?,?,?)',[(i,'2026-11-27T10:00','note','Звонок '+str(i),'Администратор') for i in range(1,106)])
        con.execute("INSERT INTO history VALUES(106,20,'2026-11-28','note','Чужой клиент','CRM')")
        con.execute("INSERT INTO history VALUES(107,10,'2026-11-28','note','Ошибочно перенесено','CRM')")
        con.execute('INSERT INTO erp_full_history_corrections VALUES(107,NULL)')
        first=task_timeline(con,1,{},0);second=task_timeline(con,1,{},first['next_offset'])
        self.assertEqual(len(first['rows']),100);self.assertEqual(len(second['rows']),5)
        self.assertEqual(first['rows'][0]['id'],105);self.assertEqual(second['rows'][-1]['id'],1)
        self.assertIsNone(second['next_offset'])
        self.assertEqual(len({r['id'] for r in first['rows']+second['rows']}),105)
