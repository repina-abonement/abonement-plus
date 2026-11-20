"""Disposable financial projections; synthetic aggregate amounts, no real rows."""
from copy import deepcopy
import sqlite3
import unittest
from unittest.mock import patch

from crm import money,erpbalancerecovery as recovery


class ERPBalanceRecoveryPlanTest(unittest.TestCase):
    def setUp(self):
        self.con=sqlite3.connect(':memory:',isolation_level=None);self.con.row_factory=sqlite3.Row
        self.addCleanup(self.con.close)
        self.con.executescript('''CREATE TABLE students(id INTEGER PRIMARY KEY,name TEXT,erp_id TEXT);
            INSERT INTO students VALUES(1,'Synthetic financial copy','synthetic-copy');
            CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE subscriptions(id INTEGER PRIMARY KEY,student_id INTEGER,enrollment_id INTEGER,
              month TEXT,kind TEXT,tariff TEXT,price INTEGER,lessons INTEGER,status TEXT,
              created_at TEXT,paid_at TEXT,ledger_managed INTEGER DEFAULT 1);
            CREATE TABLE attendance(id INTEGER PRIMARY KEY,student_id INTEGER,date TEXT,status TEXT,note TEXT);
            INSERT INTO attendance VALUES(1,1,'2026-11-04','present','Keep actual visit'),
              (2,1,'2026-11-11','absent','Keep original mark');
            CREATE TABLE enrollments(id INTEGER PRIMARY KEY,student_id INTEGER,active INTEGER);
            INSERT INTO enrollments VALUES(1,1,1);''')
        money.init(self.con)
        for subid,month,price in ((1,'2026-11',159780),(2,'2026-12',12150)):
            self.con.execute('''INSERT INTO subscriptions(id,student_id,enrollment_id,month,kind,tariff,
                price,lessons,status,created_at,paid_at) VALUES(?,1,1,?,'month','fixed',?,8,'paid',?,?)''',
                (subid,month,price,month+'-01T10:00',month+'-01T10:00'))
        # Aggregates reproduce the error without storing a family's row history.
        for amount,name,method in ((159780,'Synthetic ERP','cash'),(51450,'Система','unknown')):
            self.con.execute('''INSERT INTO payments(student_id,amount,method,paid_at,comment,user_name,
                confirmed,created_at) VALUES(1,?,?,'2026-10-22','Synthetic aggregate',?,1,'2026-11-20')''',
                (amount,method,name))
        self.case={'erp_id':'synthetic-copy','subscription_id':2,'as_of_date':'2026-11-20',
            'cutoff_date':'2026-11-02','base_amount':0,'existing_credit':2700,
            'credit_label':'На следующий турнир','credit_ref':'synthetic-event-once',
            'evidence_id':'synthetic-reviewed-opening',
            'reviewed_fingerprint':recovery.financial_fingerprint(self.con,1,'synthetic-copy')}

    def test_both_scenarios_run_only_on_copies_and_preserve_original_rows(self):
        before=self.con.serialize()
        result=recovery.preview(self.con,'synthetic-copy',case=self.case)
        self.assertTrue(result['plan_ready'],result['issues'])
        self.assertEqual(self.con.serialize(),before)
        self.assertEqual(result['before']['balance']['deposit'],39300)
        off,on=result['without_balance'],result['with_balance']
        self.assertEqual((off['invoice']['price'],off['invoice']['payable'],off['balance']['deposit']),
            (12150,12150,2700))
        self.assertEqual((on['invoice']['price'],on['invoice']['payable'],on['balance']['deposit']),
            (12150,9450,0))
        self.assertEqual(off['funds'][0]['label'],'На следующий турнир')
        self.assertEqual(len(off['funds']),1);self.assertTrue(on['repeat_unchanged'])
        self.assertEqual(on['reopened']['already_applied'],2700)
        self.assertEqual(off['attendance_count'],2);self.assertEqual(on['attendance_count'],2)
        self.assertTrue(result['payments_preserved']);self.assertTrue(result['other_purchases_preserved'])
        self.assertTrue(result['attendance_preserved']);self.assertTrue(result['repeat_carry_unchanged'])
        self.assertFalse(result['production_apply_available']);self.assertFalse(result['can_apply'])
        self.assertEqual(result['selected_for_current_invoice'],'without_balance')
        again=recovery.preview(self.con,'synthetic-copy',case=self.case)
        self.assertEqual(again['without_balance']['balance'],off['balance'])
        self.assertEqual(self.con.serialize(),before)

    def test_changed_payment_or_purchase_requires_new_review_without_any_writes(self):
        for sql in ('UPDATE payments SET amount=amount+1 WHERE id=1',
                    'UPDATE subscriptions SET price=price+1 WHERE id=2'):
            with self.subTest(sql=sql):
                self.con.execute(sql);before=self.con.serialize()
                result=recovery.preview(self.con,'synthetic-copy',case=self.case)
                self.assertFalse(result['plan_ready']);self.assertIsNone(result['without_balance'])
                self.assertTrue(any('изменились' in issue for issue in result['issues']))
                self.assertEqual(self.con.serialize(),before)

    def test_projection_failure_and_unknown_case_do_not_leave_accounting_rows(self):
        before=self.con.serialize()
        with patch.object(recovery.balancecredit,'add_existing_fund',side_effect=ValueError('Synthetic conflict')):
            result=recovery.preview(self.con,'synthetic-copy',case=self.case)
        self.assertFalse(result['plan_ready']);self.assertEqual(result['issues'],['Synthetic conflict'])
        self.assertEqual(self.con.serialize(),before)
        self.assertFalse(recovery.preview(self.con,'unreviewed-client')['plan_ready'])
        self.assertEqual(self.con.serialize(),before)


if __name__=='__main__':unittest.main()
