import sqlite3
import unittest
from crm import erptaskreview as m
from crm.services import ApiError

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.c=sqlite3.connect(':memory:'); self.c.row_factory=sqlite3.Row
        self.c.execute('CREATE TABLE tasks(id INTEGER PRIMARY KEY,student_id INTEGER,kind TEXT,title TEXT,due_at TEXT,status TEXT,note TEXT,source TEXT,auto_key TEXT,assignee_id INTEGER,priority INTEGER,done_at TEXT,done_by TEXT,result TEXT)')
        self.c.execute('CREATE TABLE erp_full_task_links(source_id TEXT PRIMARY KEY,task_id INTEGER UNIQUE)')
        self.c.execute('CREATE TABLE erp_full_identities(namespace TEXT,source_id TEXT,student_id INTEGER)')
        self.c.execute("INSERT INTO erp_full_identities VALUES ('student','42',2)")
        self.c.execute("INSERT INTO tasks VALUES (1,2,'manual','Think','2026-11-10T10:00','open','Staff note','erp','legacy',13,1,NULL,'','')")
        self.row={'erp_task_id':'99','task_id':1,'student_id':2,'namespace':'student','client_erp_id':'42','day':'2026-11-11','title':'Think','status':'open','erp_note':'Current ERP note','expected':dict(self.c.execute('SELECT * FROM tasks').fetchone())}
    def test_update_and_idempotence(self):
        p=m.prepare(self.c,[self.row]);m.apply(self.c,[self.row],p['token'],{'name':'Owner'},'backup')
        r=dict(self.c.execute('SELECT * FROM tasks').fetchone())
        self.assertIn('Staff note',r['note']);self.assertEqual(r['priority'],1);self.assertIsNone(r['assignee_id'])
        self.assertEqual(m.prepare(self.c,[self.row])['updated'],0)
        self.assertEqual(self.c.execute('SELECT count(*) FROM tasks').fetchone()[0],1)
    def test_stale_blocked(self):
        p=m.prepare(self.c,[self.row]);self.c.execute("UPDATE tasks SET note='New staff note'")
        with self.assertRaises(ApiError):m.apply(self.c,[self.row],p['token'],{'name':'Owner'},'backup')
    def test_manual_preserved(self):
        self.c.execute("UPDATE tasks SET source='manual'")
        with self.assertRaises(ApiError):m.prepare(self.c,[self.row])
    def test_namespace_conflict(self):
        self.row['namespace']='lead'
        with self.assertRaises(ApiError):m.prepare(self.c,[self.row])
    def test_link_conflict(self):
        self.c.execute("INSERT INTO erp_full_task_links VALUES ('98',1)")
        with self.assertRaises(ApiError):m.prepare(self.c,[self.row])
    def test_outside_range(self):
        self.row['day']='2026-11-16'
        with self.assertRaises(ApiError):m.prepare(self.c,[self.row])
