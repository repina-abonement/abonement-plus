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
    def test_reviewed_outgoing_move_and_repeat(self):
        self.row.update(day='2027-01-09', range_edge_correction=True)
        p=m.prepare(self.c,[self.row]);m.apply(self.c,[self.row],p['token'],{'name':'Owner'},'backup')
        self.assertEqual(m.prepare(self.c,[self.row])['updated'],0)
        self.assertEqual(self.c.execute('SELECT due_at FROM tasks').fetchone()[0],'2027-01-09T23:59')
    def test_reviewed_incoming_move(self):
        self.c.execute("UPDATE tasks SET due_at='2026-11-01T10:00'")
        self.row['expected']=dict(self.c.execute('SELECT * FROM tasks').fetchone())
        with self.assertRaises(ApiError):m.prepare(self.c,[self.row])
        self.row['range_edge_correction']=True
        self.assertEqual(m.prepare(self.c,[self.row])['updated'],1)
    def test_unrelated_outside_move_rejected(self):
        self.c.execute("UPDATE tasks SET due_at='2026-11-01T10:00'")
        self.row.update(day='2026-11-20',range_edge_correction=True)
        self.row['expected']=dict(self.c.execute('SELECT * FROM tasks').fetchone())
        with self.assertRaises(ApiError):m.prepare(self.c,[self.row])
    def test_candidates_all_states_without_writes(self):
        self.c.execute("INSERT INTO tasks SELECT 2,student_id,kind,title,'2026-11-01T10:00','done',note,source,auto_key,assignee_id,priority,done_at,done_by,result FROM tasks WHERE id=1")
        self.c.execute("INSERT INTO erp_full_task_links VALUES ('100',2)")
        before=self.c.total_changes
        rows=m.candidates(self.c,'student','42')['tasks']
        self.assertEqual([r['id'] for r in rows],[1,2])
        self.assertEqual(rows[1]['erp_task_ids'],['100'])
        self.assertEqual(self.c.total_changes,before)
    def duplicate_row(self):
        self.c.execute("UPDATE tasks SET title='Напоминание о ПУ (звонок)'")
        self.c.execute("INSERT INTO erp_full_task_links VALUES ('99',1)")
        self.c.execute("INSERT INTO tasks SELECT 2,student_id,'trial_confirm','Automatic',due_at,status,'','auto','trial_confirm:2:2026-11-11',assignee_id,priority,done_at,done_by,result FROM tasks WHERE id=1")
        return dict(erp_task_id='99',task_id=2,day='2026-11-10',cancel_auto_duplicate_of=1,
                    expected=dict(self.c.execute('SELECT * FROM tasks WHERE id=2').fetchone()),
                    expected_reference=dict(self.c.execute('SELECT * FROM tasks WHERE id=1').fetchone()))
    def test_cancel_auto_duplicate_without_changing_reference_or_key(self):
        r=self.duplicate_row();p=m.prepare(self.c,[r]);m.apply(self.c,[r],p['token'],{'name':'Owner'},'backup')
        auto=dict(self.c.execute('SELECT * FROM tasks WHERE id=2').fetchone())
        self.assertEqual(auto['status'],'cancelled');self.assertEqual(auto['auto_key'],r['expected']['auto_key'])
        self.assertEqual(dict(self.c.execute('SELECT * FROM tasks WHERE id=1').fetchone()),r['expected_reference'])
        self.assertEqual(m.prepare(self.c,[r])['updated'],0)
        self.assertEqual([tuple(x) for x in self.c.execute('SELECT * FROM erp_full_task_links')],[('99',1)])
    def test_duplicate_reference_stale_or_other_day_rejected(self):
        r=self.duplicate_row();self.c.execute("UPDATE tasks SET due_at='2026-11-11T10:00' WHERE id=2")
        with self.assertRaises(ApiError):m.prepare(self.c,[r])
        self.c.execute("UPDATE tasks SET due_at='2026-11-10T10:00' WHERE id=2")
        self.c.execute("UPDATE tasks SET note='Staff update' WHERE id=1")
        with self.assertRaises(ApiError):m.prepare(self.c,[r])
    def test_snapshot_includes_all_states_and_links_without_writes(self):
        self.c.execute("INSERT INTO erp_full_task_links VALUES ('99',1)")
        for i, state in enumerate(('done','cancelled'), 2):
            self.c.execute("INSERT INTO tasks SELECT ?,student_id,kind,title,due_at,?,note,source,auto_key,assignee_id,priority,done_at,done_by,result FROM tasks WHERE id=1", (i,state))
        self.c.execute("INSERT INTO tasks SELECT 4,student_id,kind,title,'2026-11-08T10:00',status,note,source,auto_key,assignee_id,priority,done_at,done_by,result FROM tasks WHERE id=1")
        before=self.c.total_changes
        rows=m.snapshot(self.c,'2026-11-10')['tasks']
        self.assertEqual([r['id'] for r in rows],[1,2,3])
        self.assertEqual({r['status'] for r in rows},{'open','done','cancelled'})
        self.assertEqual(rows[0]['erp_task_ids'],['99'])
        self.assertEqual(rows[0]['erp_client_ids'],[{'namespace':'student','source_id':'42'}])
        self.assertEqual(self.c.total_changes,before)
    def test_snapshot_requires_scoped_date(self):
        for day in (None,'2026-11-08','2026-11-16','2026-11-10 OR 1=1'):
            with self.assertRaises(ApiError):m.snapshot(self.c,day)
