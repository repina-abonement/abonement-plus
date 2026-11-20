"""The recovered card presents effective dates without changing its ledger.

Known ERP IDs mirror the reviewed case; names, local records and all financial
rows are synthetic. Tests seed only isolated DBs, never import/apply/rollback.
"""
from contextlib import redirect_stdout
from datetime import date
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crm import db, money, services as sv, subscriptionpresentation as presentation


class SubscriptionPresentation(unittest.TestCase):
    SALES=[('828081','729454','2026-11-04','2026-12-02'),
           ('828083','729456','2026-11-11','2026-11-11'),
           ('828084','729457','2026-11-18','2026-11-18'),
           ('828085','729458','2026-11-25','2026-11-25')]

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.con=db.connect(str(Path(self.temp.name)/'synthetic.sqlite3'));self.addCleanup(self.con.close)
        with redirect_stdout(io.StringIO()):db.init_db(self.con)
        class FixedDate(date):
            @classmethod
            def today(cls):return cls(2026,11,20)
        for clock in (patch.object(sv,'today',return_value=date(2026,11,20)),patch.object(money,'date',FixedDate)):
            clock.start();self.addCleanup(clock.stop)
        self.user={'id':1,'name':'Синтетический руководитель','role':'admin'}
        self.branch=self.con.execute("INSERT INTO branches(name) VALUES ('Небо')").lastrowid
        self.gid=self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time) VALUES (?,'Подтверждённая группа','2','18:45')",(self.branch,)).lastrowid
        self.sid=sv.create_student(self.con,{'name':'Синтетический Ребёнок','parent_name':'Синтетический Родитель',
            'phone':'+79000000671','group_id':self.gid,'funnel':'members'},self.user)
        self.con.execute("UPDATE students SET erp_id='133513' WHERE id=?",(self.sid,))
        self.eid=sv.enrollments_of(self.con,self.sid)[0]['id']
        self.old_id=7115
        self.con.execute("""INSERT INTO subscriptions(id,student_id,enrollment_id,month,tariff,lessons,price,per_lesson,
            status,created_at,paid_at,kind,erp_full_managed,ledger_managed)
            VALUES (?,?,?,'2026-11','fixed',4,10000,2500,'cancelled','2026-10-28T12:00','2026-10-28T12:00','month',1,1)""",(self.old_id,self.sid,self.eid))
        self.ids={}
        for index,(sale,invoice,original,effective) in enumerate(self.SALES,1):
            sub=self.con.execute("""INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,price,per_lesson,
                status,created_at,kind,erp_full_managed,ledger_managed)
                VALUES (?,NULL,'2026-11','single',1,2500,2500,'paid','2026-10-28T12:00','single',1,1)""",(self.sid,)).lastrowid
            self.ids[sale]=sub
            self.con.execute("INSERT INTO single_visits(student_id,group_id,date,status,subscription_id,created_at) VALUES (?,?,?,'booked',?,'2026-11-20')",(self.sid,self.gid,effective,sub))
            payload=json.dumps({'invoice_id':invoice,'recovery_aggregate_id':self.old_id})
            self.con.execute('''INSERT INTO erp_single_sales(source_id,student_id,group_id,subscription_id,source_signature,
                source_payload,lesson_date,payment_operation_id,native_signature) VALUES (?,?,?,?,?,?,?,?,?)''',
                (sale,self.sid,self.gid,sub,'synthetic',payload,original,'synthetic-operation-'+str(index),'synthetic'))
            self.con.execute("""INSERT INTO payments(student_id,amount,method,paid_at,subscription_id,created_at,confirmed,user_name)
                VALUES (?,2500,'card','2026-10-28T12:00',?,'2026-10-28T12:00',1,'ERP')""",(self.sid,sub))

    def detail(self):return sv.student_detail(self.con,self.sid)

    def invoice(self,amount=9000,group=None,active=True,month='2026-11',status='invoiced'):
        gid=group or self.gid
        eid=self.con.execute("INSERT INTO enrollments(student_id,group_id,tariff,active,created_at) VALUES (?,?,'fixed',?,'2026-11-01')",(self.sid,gid,int(active))).lastrowid
        return self.con.execute("""INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,price,per_lesson,status,created_at,ledger_managed)
            VALUES (?,?,?,'fixed',1,?,?,?,'2026-11-01',1)""",(self.sid,eid,month,amount,amount,status)).lastrowid

    def test_effective_visit_month_and_original_accounting_month_are_separate(self):
        d=self.detail();live=[r for r in d['subscriptions'] if r['include_in_totals']]
        november=[r for r in live if r['display_month']=='2026-11']
        december=[r for r in live if r['display_month']=='2026-12']
        self.assertEqual((len(november),sum(r['price'] for r in november)),(3,7500))
        self.assertEqual((len(december),sum(r['price'] for r in december)),(1,2500))
        moved=december[0]
        self.assertEqual(moved['source_sale_id'],'828081')
        self.assertEqual(moved['source_invoice_id'],'729454')
        self.assertEqual(moved['source_lesson_date'],'2026-11-04')
        self.assertEqual(moved['single_date'],'2026-12-02')
        self.assertEqual(moved['single_time'],'18:45')
        self.assertEqual(moved['group_id'],self.gid)
        self.assertEqual(moved['accounting_month'],'2026-11')
        self.assertEqual(moved['month'],'2026-11')
        self.assertEqual(moved['paid_amount'],2500)
        self.assertEqual(d['unpaid_cur'],[])
        history=[r for r in d['subscriptions'] if r['id']==self.old_id]
        self.assertEqual(len(history),1)
        self.assertTrue(history[0]['history_only'])
        self.assertFalse(history[0]['include_in_totals'])
        self.assertEqual(history[0]['status'],'cancelled')
        self.assertEqual(sum(r['price'] for r in live),10000)
        self.assertEqual(self.con.execute('SELECT count(*) FROM payments').fetchone()[0],4)
        self.assertEqual({r[0] for r in self.con.execute('SELECT paid_at FROM payments')},{'2026-10-28T12:00'})

    def test_reopening_card_is_readonly_and_does_not_reallocate_payments(self):
        before=self.con.serialize();changes=self.con.total_changes
        original=money.balance(self.con,self.sid)
        first=self.detail();second=self.detail()
        self.assertEqual(first['subscriptions'],second['subscriptions'])
        self.assertEqual(first['balance']['covered'],{str(k):v for k,v in original['covered'].items()})
        self.assertEqual(first['balance']['debt'],original['debt'])
        self.assertEqual(first['balance']['deposit'],original['deposit'])
        self.assertEqual(self.con.serialize(),before)
        self.assertEqual(self.con.total_changes,changes)

    def test_unknown_or_ambiguous_single_date_does_not_duplicate_purchase_or_guess(self):
        moved=self.ids['828081'];other=self.ids['828083']
        self.con.execute('DELETE FROM single_visits WHERE subscription_id=?',(moved,))
        self.con.execute("INSERT INTO single_visits(student_id,group_id,date,status,subscription_id,created_at) VALUES (?,?,'2026-11-24','booked',?,'2026-11-20')",(self.sid,self.gid,other))
        rows=self.detail()['subscriptions']
        self.assertEqual(len(rows),5)
        self.assertEqual(len({r['id'] for r in rows}),5)
        for sid,state in ((moved,'not_booked'),(other,'ambiguous')):
            row=next(r for r in rows if r['id']==sid)
            self.assertIsNone(row['single_date'])
            self.assertEqual(row['single_time'],'')
            self.assertEqual(row['display_month'],'2026-11')
            self.assertEqual(row['single_date_status'],state)
            self.assertTrue(row['date_needs_review'])
        self.assertEqual(sum(r['price'] for r in rows if r['include_in_totals']),10000)

    def test_cancelled_booking_is_not_selected_over_the_unique_effective_booking(self):
        self.con.execute("INSERT INTO single_visits(student_id,group_id,date,status,subscription_id,created_at) VALUES (?,?,'2026-11-04','cancelled',?,'2026-11-20')",(self.sid,self.gid,self.ids['828081']))
        rows=self.detail()['subscriptions']
        row=next(r for r in rows if r['id']==self.ids['828081'])
        self.assertEqual(row['single_date'],'2026-12-02')
        self.assertEqual(row['display_month'],'2026-12')
        self.assertEqual(len(rows),5)

    def test_single_group_time_uses_effective_date_version(self):
        self.con.execute("INSERT INTO group_versions(group_id,valid_from,weekdays,time,day_times,created_at) VALUES (?,'2026-12-01','2','19:15','','2026-11-20')",(self.gid,))
        row=next(r for r in self.detail()['subscriptions'] if r['id']==self.ids['828081'])
        self.assertEqual(row['single_time'],'19:15')
        self.assertEqual(row['single_date'],'2026-12-02')

    def test_paid_single_in_inactive_enrollment_covers_its_group_not_an_enrollment_id(self):
        inactive=self.con.execute("INSERT INTO enrollments(student_id,group_id,tariff,active,created_at) VALUES (?,?,'fixed',0,'2026-11-01')",(self.sid,self.gid)).lastrowid
        self.con.execute("UPDATE subscriptions SET enrollment_id=? WHERE id=?",(inactive,self.ids['828081']))
        self.assertEqual(sv.unpaid_groups(self.con,self.sid,'2026-11'),[])
        self.assertEqual(sv.unpaid_groups(self.con,self.sid,'2026-12'),[])
        self.assertEqual(self.detail()['unpaid_cur'],[])

    def test_real_same_group_invoice_debt_is_not_hidden_by_paid_single_or_stale_paid_status(self):
        invoice=self.invoice(status='paid')
        before=money.balance(self.con,self.sid)
        d=self.detail();row=next(r for r in d['subscriptions'] if r['id']==invoice)
        self.assertEqual(row['status'],'paid')
        self.assertEqual(row['paid_amount'],0)
        self.assertEqual(d['balance']['debt'],9000)
        self.assertEqual(d['balance']['debt'],before['debt'])
        self.assertTrue(d['unpaid_cur'])

    def test_partial_real_invoice_uses_fifo_coverage_without_status_override(self):
        invoice=self.invoice()
        self.con.execute("INSERT INTO payments(student_id,amount,method,paid_at,created_at,subscription_id) VALUES (?,2000,'cash','2026-11-19','2026-11-19',?)",(self.sid,invoice))
        before=money.balance(self.con,self.sid)
        d=self.detail();row=next(r for r in d['subscriptions'] if r['id']==invoice)
        self.assertEqual(row['paid_amount'],2000)
        self.assertEqual(row['price']-row['paid_amount'],7000)
        self.assertEqual(d['balance']['debt'],7000)
        self.assertEqual(d['balance']['debt'],before['debt'])
        self.assertTrue(d['unpaid_cur'])

    def test_inactive_other_group_invoice_stays_visible_while_paid_group_warning_is_removed(self):
        other=self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time) VALUES (?,'Отдельный реальный счёт','2','17:45')",(self.branch,)).lastrowid
        self.invoice(group=other,active=False)
        before=money.balance(self.con,self.sid)
        d=self.detail()
        self.assertEqual(d['unpaid_cur'],['Отдельный реальный счёт'])
        self.assertEqual(d['balance']['debt'],9000)
        self.assertEqual(d['balance']['debt'],before['debt'])

    def test_old_debt_can_reduce_covered_amount_even_when_single_status_is_paid(self):
        self.invoice(amount=8000,month='2026-10')
        before=money.balance(self.con,self.sid)
        d=self.detail();moved=next(r for r in d['subscriptions'] if r['id']==self.ids['828081'])
        self.assertEqual(moved['status'],'paid')
        self.assertEqual(moved['paid_amount'],2000)
        self.assertEqual(moved['display_month'],'2026-12')
        self.assertEqual(d['balance']['debt'],8000)
        self.assertEqual(d['balance']['debt'],before['debt'])
        self.assertEqual(d['balance']['covered'],{str(k):v for k,v in before['covered'].items()})
