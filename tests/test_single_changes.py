from datetime import date, datetime
from unittest import TestCase, main
from unittest.mock import patch
from crm import db, erpworkflow, money, paymentallocation, schedule, services as sv, singlechanges, singles, smart


class SingleChanges(TestCase):
    def setUp(self):
        for mod, attr, value in [(sv,'today',lambda:date(2026,11,22)),(singles,'_now',lambda:datetime(2026,11,22,12)),(smart,'REQUIRE',False)]:
            p=patch.object(mod,attr,value);p.start();self.addCleanup(p.stop)
        self.db=db.connect(':memory:');db.init_db(self.db);self.addCleanup(self.db.close)
        self.u={'id':1,'role':'admin','name':'Тест'}
        branch=schedule.save_branch(self.db,{'name':'Тестовая площадка'})
        def group(n,t):return schedule.save_group(self.db,{'name':n,'branch_id':branch,'weekdays':[5,6],'time':t})
        self.a=group('Первая','10:00');self.b=group('Вторая','14:30')
        self.db.execute("INSERT OR REPLACE INTO settings VALUES ('price_single','2500')")
        self.sid=sv.create_student(self.db,{'name':'Тестовый клиент','parent_name':'Тест','phone':'+79000000011','funnel':'sales'},self.u)
        self.sub=singles.sell_visit(self.db,self.sid,self.a,'2026-11-28',self.u)['id']
        money.add_payment(self.db,self.sid,2500,'card',self.u,subscription_id=self.sub)
        self.vid=self.db.execute('SELECT id FROM single_visits').fetchone()[0]
        self.old=('2026-11-28',self.a)
    def opened(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM tasks WHERE student_id=? AND status='open' ORDER BY due_at",(self.sid,))]
    def count_cash(self):
        return tuple(self.db.execute("SELECT COUNT(*),SUM(amount) FROM payments WHERE student_id=?",(self.sid,)).fetchone())
    def cancel(self,**kwargs):return singlechanges.cancel(self.db,self.vid,self.u,self.old,**kwargs)
    def move(self,d,g):return singlechanges.move(self.db,self.vid,self.u,self.old,g,d)
    def test_cancel_balance(self):
        self.db.execute("""INSERT INTO subscriptions(student_id,month,tariff,lessons,per_lesson,price,status,created_at,kind)
        VALUES (?,'2026-12','fixed',1,2500,2500,'invoiced','2026-11-28','month')""",(self.sid,))
        self.assertEqual(self.cancel()['balance_credit'],2500)
        self.assertTrue(self.cancel()['already_cancelled'])
        self.assertEqual(money.balance(self.db,self.sid)['deposit'],2500)
        self.assertEqual(self.count_cash(),(1,2500))
        self.assertEqual([(t['kind'],t['due_at']) for t in self.opened() if t['kind']!='payment_check'],[('single_next','2026-12-04T10:00')])
        self.assertNotIn(self.sid,[r['id'] for r in sv.journal(self.db,self.a,self.old[0])['rows']])
        singles.book(self.db,self.sid,self.b,'2026-12-06',self.u)
        self.assertFalse(any(t['kind']=='single_next' for t in self.opened()))
    def test_bound_receipt(self):
        pid=self.db.execute('SELECT id FROM payments WHERE student_id=?',(self.sid,)).fetchone()[0]
        paymentallocation.bind_payment(self.db,pid,self.sub,2500,source_namespace='demo',source_invoice_id='demo-i',source_operation_id='demo-p',evidence_id='demo-e')
        self.assertEqual(self.cancel()['balance_credit'],2500)
        b=money.balance(self.db,self.sid)
        self.assertEqual((b['deposit'],b['restricted_deposit'],self.count_cash()),(2500,0,(1,2500)))
    def test_failure_rolls_back(self):
        with self.assertRaises(sv.ApiError):self.cancel(mode='date',contact_date='invalid')
        self.assertEqual(money.balance(self.db,self.sid)['deposit'],0)
        self.assertEqual(self.db.execute('SELECT status FROM subscriptions WHERE id=?',(self.sub,)).fetchone()[0],'paid')
    def test_custom(self):
        self.cancel(mode='date',contact_date='2026-12-01')
        self.assertEqual(self.opened()[0]['due_at'],'2026-12-01T10:00')
    def test_lead(self):
        self.cancel(mode='lead')
        task=self.opened()[0]
        self.assertEqual(task['title'],'Новый лид')
        self.assertIn('Новый лид (ндз)',erpworkflow.options(task))
        self.assertEqual(task['student_id'],self.sid)
    def test_move(self):
        self.move('2026-11-29',self.b)
        self.assertTrue(self.move('2026-11-29',self.b)['already_moved'])
        self.assertEqual([(r['kind'],r['due_at']) for r in self.opened()],
            [('single_paid_remind','2026-11-28T10:00'),('single_paid_remind','2026-11-29T11:30'),('single_paid_next','2026-11-30T10:00')])
        self.assertEqual(self.count_cash(),(1,2500))
        self.assertEqual(money.balance(self.db,self.sid)['deposit'],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM subscriptions').fetchone()[0],1)
        self.assertIn(self.sid,[r['id'] for r in sv.journal(self.db,self.b,'2026-11-29',paid_only=True)['rows']])
        with self.assertRaises(sv.ApiError):self.cancel()
    def test_same_day_group(self):
        self.move(self.old[0],self.b)
        self.assertEqual(self.opened()[1]['due_at'],'2026-11-28T11:30')
    def test_invalid_move(self):
        with self.assertRaises(sv.ApiError):self.move('2026-11-30',self.b)
        self.assertEqual(len(self.opened()),3)
    def test_other_booking(self):
        singles.book(self.db,self.sid,self.b,'2026-12-05',self.u)
        self.cancel(mode='lead')
        self.assertFalse(any(t['kind'] in ('call_lead','single_next') for t in self.opened()))


if __name__=='__main__':main()
