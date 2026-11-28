import json
import tempfile
import unittest
from pathlib import Path
from datetime import date
from unittest.mock import patch
from crm import db, services as sv, transfer, money

class TransferExclusionsTest(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.con=db.connect(str(Path(tmp.name)/'test.sqlite3'));self.addCleanup(self.con.close);db.init_db(self.con)
        guard=patch('crm.smart.REQUIRE',False);guard.start();self.addCleanup(guard.stop)
        self.u={'id':1,'role':'admin','name':'Тест'}
        clock=patch.object(sv,'today',return_value=date(2026,11,20));clock.start();self.addCleanup(clock.stop)
        br=self.con.execute("INSERT INTO branches(name) VALUES ('ТЦ «Небо»')").lastrowid
        self.groups=[self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time,age) VALUES (?,?,'1,4',?,'3-6')",(br,'Группа '+str(i),time)).lastrowid for i,time in enumerate(['16:45','18:45','19:45'])]
        self.sid=sv.create_student(self.con,{'name':'Тестовый Ребёнок','birth':'2021-01-01','parent_name':'Тестовый Родитель','phone':'+79000000009','funnel':'members','tariff':'fixed','group_id':self.groups[0]},self.u)
        self.eid=sv.enrollments_of(self.con,self.sid)[0]['id']

    def sale(self,excluded):
        inv=sv.create_invoice(self.con,self.sid,'2026-12',self.u,self.eid,excluded_dates=excluded,exclusion_reason='Отпуск согласован')
        money.pay_subscription(self.con,inv['id'],'transfer',self.u)
        return inv

    def test_same_weekdays_keeps_vacation_price_and_payment(self):
        inv=self.sale(['2026-12-29'])
        self.assertEqual(inv['quote']['total'],10800)
        payments=[tuple(r) for r in self.con.execute('SELECT * FROM payments')]
        p=transfer.plan(self.con,self.eid,self.groups[1],'2026-12-01')
        self.assertEqual((p['months'][0]['new_lessons'],p['months'][0]['new_price'],p['diff']),(8,10800,0))
        self.assertNotIn('2026-12-29',p['months'][0]['new_dates'])
        out=transfer.apply(self.con,self.eid,self.groups[1],'2026-12-01',self.u,expected=transfer.confirmation(p))
        row=self.con.execute('SELECT * FROM subscriptions WHERE id=?',(inv['id'],)).fetchone()
        self.assertEqual((row['lessons'],row['price'],row['status']),(8,10800,'paid'))
        self.assertIn('2026-12-29',json.loads(row['excluded_dates']))
        from crm import clientattendance
        events=clientattendance.calendar(self.con,self.sid,'2026-12')['events']
        self.assertFalse(any(e['date']=='2026-12-29' and e['kind']=='subscription' for e in events))
        self.assertEqual(sum(e['kind']=='subscription' and e['group_id']==self.groups[1] for e in events),8)
        self.assertEqual([tuple(r) for r in self.con.execute('SELECT * FROM payments')],payments)
        balance=money.balance(self.con,self.sid)
        self.assertEqual((balance['debt'],balance['upcoming'],balance['deposit']),(0,0,0))
        self.assertEqual(transfer.plan(self.con,out['enrollment_id'],self.groups[2],'2026-12-01')['diff'],0)

    def test_midmonth_excludes_dates_on_both_sides(self):
        inv=self.sale(['2026-12-04','2026-12-29'])
        p=transfer.plan(self.con,self.eid,self.groups[1],'2026-12-15')
        m=p['months'][0]
        self.assertEqual((m['keep_lessons'],m['new_lessons'],p['diff']),(3,4,0))
        transfer.apply(self.con,self.eid,self.groups[1],'2026-12-15',self.u)
        rows=self.con.execute('SELECT lessons,price,excluded_dates FROM subscriptions WHERE student_id=?',(self.sid,)).fetchall()
        self.assertEqual(sum(r['lessons'] for r in rows),7)
        self.assertEqual(sum(r['price'] for r in rows),inv['quote']['total'])
        self.assertTrue(all('2026-12-29' in json.loads(r['excluded_dates']) for r in rows))
