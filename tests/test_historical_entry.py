"""Past trial and factual subscription sale: dates, journal, payments and duplicate protection."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from crm import db, services as sv, historyentry, clientattendance, money, pricing


class HistoricalEntryTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.con=db.connect(str(Path(self.tmp.name)/'test.sqlite3'));db.init_db(self.con)
        self.user={'id':1,'name':'Тест','role':'admin'}
        self.clock=patch('crm.historyentry.now',return_value=datetime(2026,11,13,15));self.clock.start()
        self.sid=sv.create_student(self.con,{'name':'Даниил','parent_name':'Екатерина','phone':'+79000000001'},self.user)
        b=self.con.execute("INSERT INTO branches(name) VALUES ('Тест')").lastrowid
        self.gid=self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time,age,trial_ok) VALUES (?,'Тест Чт/Вс','3,6','16:45','3–8',1)",(b,)).lastrowid
        self.con.execute("UPDATE price_list SET fixed=1250")
        self.con.execute("UPDATE discounts SET percent=20 WHERE rule='first_on_trial_day'")
        self.data={'request_id':'test-entry-0001','record_trial':True,'sold':True,'child_name':'Даниил Примеров','birth':'2020-01-01',
            'parent_name':'Екатерина','phone':'+79000000001','email':'test@example.invalid','trial_date':'2026-11-12','trial_time':'16:45',
            'trial_group_id':self.gid,'sale_date':'2026-11-12','paid_date':'2026-11-12','month':'2026-11','group_id':self.gid,
            'tariff':'fixed','price':5000,'amount':5000,'method':'cash',
            'lessons':[{'group_id':self.gid,'date':d} for d in ('2026-11-15','2026-11-19','2026-11-22','2026-11-26','2026-11-29')]}
    def tearDown(self):
        self.clock.stop();self.con.close();self.tmp.cleanup()
    def test_yesterday_trial_and_sale_are_saved_with_factual_dates(self):
        with patch('crm.tasks.send_booked') as notify,patch('crm.comms.http') as external:
            r=historyentry.record(self.con,self.sid,self.data,self.user)
        notify.assert_not_called();external.assert_not_called()
        s=sv.get_student_row(self.con,self.sid);self.assertEqual(s['trial_at'],'2026-11-12T16:45');self.assertEqual(s['trial_came'],1)
        self.assertEqual(s['funnel'],'members');self.assertEqual(s['converted_at'],'2026-11-12');self.assertEqual(s['trial_group_id'],self.gid)
        sub=self.con.execute('SELECT * FROM subscriptions WHERE id=?',(r['subscription_id'],)).fetchone()
        self.assertEqual((sub['created_at'],sub['paid_at'],sub['price'],sub['lessons'],sub['status']),('2026-11-12','2026-11-12',5000,5,'paid'))
        p=self.con.execute('SELECT * FROM payments WHERE subscription_id=?',(sub['id'],)).fetchone();self.assertEqual(p['paid_at'],'2026-11-12')
        rows=sv.journal(self.con,self.gid,'2026-11-12')['rows'];trials=[x for x in rows if x['id']==self.sid]
        self.assertEqual(len(trials),1);self.assertEqual(trials[0]['status'],'trial');self.assertEqual(trials[0]['trial_came'],1)
        events=clientattendance.calendar(self.con,self.sid,'2026-11')['events'];pu=[e for e in events if e['date']=='2026-11-12']
        self.assertEqual(len(pu),1);self.assertEqual((pu[0]['kind'],pu[0]['group_id'],pu[0]['status']),('trial',self.gid,'present'))
        before=self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0];self.assertEqual(historyentry.record(self.con,self.sid,self.data,self.user),r)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0],before)
    def test_invalid_financial_data_rolls_back_trial_and_identity(self):
        for change in ({'trial_date':'2026-11-14'},{'price':0},{'method':'online'},{'sale_date':'2026-11-11'},
                       {'lessons':[{'group_id':self.gid,'date':'2026-11-12'}]}):
            with self.assertRaises(sv.ApiError):historyentry.record(self.con,self.sid,{**self.data,**change},self.user)
            self.assertIsNone(sv.get_student_row(self.con,self.sid)['trial_at']);self.assertEqual(sv.get_student_row(self.con,self.sid)['name'],'Даниил')
            self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0],0)
    def test_already_recorded_payment_not_duplicated(self):
        money.add_payment(self.con,self.sid,5000,'cash',self.user,paid_at='2026-11-12')
        with self.assertRaises(sv.ApiError):historyentry.record(self.con,self.sid,self.data,self.user)
        r=historyentry.record(self.con,self.sid,{**self.data,'amount':0},self.user)
        self.assertEqual(r['remaining'],0);self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments').fetchone()[0],1)
    def test_past_trial_without_sale_and_arbitrary_past_day(self):
        data={**self.data,'sold':False,'trial_date':'2026-11-11'}
        historyentry.record(self.con,self.sid,data,self.user)
        self.assertEqual(sv.get_student_row(self.con,self.sid)['funnel'],'sales')
        self.assertTrue(any(d['date']=='2026-11-11' for d in sv.journal_month(self.con,self.gid,'2026-11')['days']))
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0],0)
        from crm import tasks
        tasks.run(self.con,datetime(2026,11,13,15),send=False)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM tasks WHERE student_id=? AND kind IN ('trial_confirm','trial_today','how_was')",(self.sid,)).fetchone()[0],0)
    def test_reason_for_trial_month_and_manual_date_changes(self):
        r=historyentry.record(self.con,self.sid,self.data,self.user)
        sub=self.con.execute('SELECT calculation_reason FROM subscriptions WHERE id=?',(r['subscription_id'],)).fetchone()
        self.assertIn('ПУ внутри месяца (12.11.2026)',sub[0])
        self.con.execute("UPDATE subscriptions SET calculation_reason='' WHERE id=?",(r['subscription_id'],))
        detail=sv.student_detail(self.con,self.sid)
        self.assertIn('ПУ внутри месяца',detail['subscriptions'][0]['calculation_reason'])
        self.assertEqual(self.con.execute('SELECT calculation_reason FROM subscriptions WHERE id=?',(r['subscription_id'],)).fetchone()[0],'')
        # Другой месяц покупки без повторного внесения ПУ: ручное сокращение требует причины.
        quote=historyentry.options(self.con,self.sid,self.gid,'2026-12','fixed',paid_date='2026-11-12')
        dates=[{'group_id':self.gid,'date':d} for d in quote['components'][0]['dates'][:2]]
        price=historyentry.calculated_total(quote,{(x['group_id'],x['date']) for x in dates})
        data={**self.data,'request_id':'manual-dates-0002','record_trial':False,'month':'2026-12','lessons':dates,'price':price,'amount':0}
        with self.assertRaises(sv.ApiError):historyentry.record(self.con,self.sid,data,self.user)
        result=historyentry.record(self.con,self.sid,{**data,'dates_reason':'Согласован отпуск семьи'},self.user)
        saved=self.con.execute('SELECT calculation_reason FROM subscriptions WHERE id=?',(result['subscription_id'],)).fetchone()[0]
        self.assertIn('Согласован отпуск семьи',saved);self.assertIn('Тест',saved)
        pricing.correct(self.con,result['subscription_id'],price-100,'Согласовано с руководителем',self.user)
        self.assertIn('Согласовано с руководителем',sv.student_detail(self.con,self.sid)['subscriptions'][0]['calculation_reason'])

    def test_historical_sale_without_email_keeps_data_request(self):
        from crm import smart, tasks
        with patch.object(smart,'REQUIRE',True):
            r=historyentry.record(self.con,self.sid,{**self.data,'email':''},self.user)
            self.assertEqual(r['remaining'],0)
            self.assertIn('email',smart.required_info(self.con,self.sid,'sale')['missing'])
            with self.assertRaises(sv.ApiError):smart.require(self.con,self.sid,'sale')
        tasks.request_missing_data(self.con,historyentry.now().date())
        task=self.con.execute("SELECT * FROM tasks WHERE student_id=? AND kind='request_data' AND status='open'",(self.sid,)).fetchone()
        self.assertIsNotNone(task)
        self.assertIn('Почта',task['note'])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions WHERE student_id=?',(self.sid,)).fetchone()[0],1)

    def test_select_discount_and_keep_it_in_subscription(self):
        did=self.con.execute("INSERT INTO discounts(name,percent,rule,created_at) VALUES ('Согласованная 20%',20,'manual','2026-11-01')").lastrowid
        q=historyentry.options(self.con,self.sid,self.gid,'2026-11','fixed',paid_date='2026-11-12',trial_date='2026-11-08',discount_id=did)
        chosen={(x['group_id'],x['date']) for x in self.data['lessons']}
        self.assertEqual(historyentry.calculated_total(q,chosen),5000)
        r=historyentry.record(self.con,self.sid,{**self.data,'discount_id':did},self.user)
        sub=self.con.execute('SELECT discount_name,discount_amount FROM subscriptions WHERE id=?',(r['subscription_id'],)).fetchone()
        self.assertEqual(tuple(sub),('Согласованная 20%',1250))
        with self.assertRaises(sv.ApiError):
            historyentry.options(self.con,self.sid,self.gid,'2026-10','fixed',paid_date='2026-11-12',discount_id=did)

    def test_balance_covers_purchase_without_duplicate_payment(self):
        for deposit in (2000,7000):
            with self.subTest(deposit=deposit):
                self.con.execute('SAVEPOINT balance_case')
                money.add_payment(self.con,self.sid,deposit,'cash',self.user,paid_at='2026-11-10')
                quote=historyentry.options(self.con,self.sid)
                self.assertEqual(quote['available_balance'],deposit)
                with self.assertRaises(sv.ApiError):
                    historyentry.record(self.con,self.sid,{**self.data,'use_balance':True,'expected_balance':deposit-1},self.user)
                r=historyentry.record(self.con,self.sid,{**self.data,'use_balance':True,'expected_balance':deposit,'amount':max(0,5000-deposit)},self.user)
                self.assertEqual(r['remaining'],0)
                self.assertEqual(money.balance(self.con,self.sid)['deposit'],max(0,deposit-5000))
                self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments WHERE student_id=?',(self.sid,)).fetchone()[0],2 if deposit<5000 else 1)
                self.con.execute('ROLLBACK TO balance_case');self.con.execute('RELEASE balance_case')

    def test_calendar_calculation_and_duplicate_enrollment(self):
        quote=historyentry.options(self.con,self.sid,self.gid,'2026-11','fixed',paid_date='2026-11-12',trial_date='2026-11-12')
        chosen={(x['group_id'],x['date']) for x in self.data['lessons']}
        self.assertEqual(historyentry.calculated_total(quote,chosen),5000)
        self.assertEqual(historyentry.calculated_total(quote,set(sorted(chosen)[:2])),2000)
        with self.assertRaises(sv.ApiError):historyentry.record(self.con,self.sid,{**self.data,'price':1},self.user)
        result=historyentry.record(self.con,self.sid,self.data,self.user)
        self.con.execute("UPDATE enrollments SET active=0 WHERE student_id=?",(self.sid,))
        with self.assertRaises(sv.ApiError):historyentry.record(self.con,self.sid,{**self.data,'request_id':'another-entry-0002','record_trial':False,'amount':0,'price':None},self.user)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions WHERE student_id=?',(self.sid,)).fetchone()[0],1)

    def test_price_does_not_change_to_todays_tariff(self):
        r=historyentry.record(self.con,self.sid,{**self.data,'amount':1000},self.user)
        pricing.reprice(self.con,r['subscription_id'])
        self.assertEqual(self.con.execute('SELECT price FROM subscriptions WHERE id=?',(r['subscription_id'],)).fetchone()[0],5000)


if __name__=='__main__':unittest.main()
