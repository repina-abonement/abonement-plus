import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from crm import db, lifecycle, services as sv, tasks


class SubscriptionClientReconcileTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.con=db.connect(str(Path(self.tmp.name)/'tasks.sqlite3'));self.addCleanup(self.con.close);db.init_db(self.con)
        self.user={'id':1,'name':'Synthetic owner','role':'admin'}
        self.bid=self.con.execute("INSERT INTO branches(name) VALUES ('Synthetic')").lastrowid
        self.gid=self.con.execute("INSERT INTO groups(branch_id,name,weekdays,time,age) VALUES (?,'Synthetic','0,3','18:00','6–8')",(self.bid,)).lastrowid

    def lead(self,name,phone):
        return sv.create_new_lead(self.con,{'name':name,'parent_name':'Synthetic parent','phone':phone,'group_id':self.gid},self.user)

    def subscription(self,sid,month='2026-11',status='paid'):
        eid=self.con.execute("INSERT INTO enrollments(student_id,group_id,tariff,created_at) VALUES (?,?,'fixed','2026-11-01')",(sid,self.gid)).lastrowid
        return self.con.execute("""INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,kind,lessons,per_lesson,price,status,created_at)
            VALUES (?,? ,?,'fixed','month',8,1500,12000,?,'2026-11-01')""",(sid,eid,month,status)).lastrowid

    def test_native_sale_closes_lead_and_leaves_one_twentieth_renewal(self):
        sid=self.lead('Active child','+79000000001');self.subscription(sid)
        tasks.reconcile_subscription_clients(self.con,date(2026,11,21))
        student=sv.get_student_row(self.con,sid);self.assertEqual((student['funnel'],student['stage']),('members','active'))
        self.assertFalse(next(x for x in sv.new_cards_today(self.con)['cards'] if x['id']==sid)['needs_processing'])
        self.assertEqual(self.con.execute("SELECT status FROM tasks WHERE student_id=? AND kind='call_lead'",(sid,)).fetchone()[0],'cancelled')
        renew=self.con.execute("SELECT * FROM tasks WHERE student_id=? AND kind='renew' AND status='open'",(sid,)).fetchall()
        self.assertEqual(len(renew),1);self.assertEqual(renew[0]['due_at'],'2026-11-20T23:59')
        lifecycle.tick(self.con,datetime(2026,11,21,12));self.assertEqual(self.con.execute("SELECT COUNT(*) FROM tasks WHERE student_id=? AND kind='renew' AND status='open'",(sid,)).fetchone()[0],1)

    def test_erp_like_subscription_and_paid_renewal_repair_existing_tasks(self):
        sid=self.lead('Imported active child','+79000000002');self.subscription(sid,'2026-11')
        self.con.execute("INSERT INTO tasks(kind,student_id,title,due_at,status,auto_key,source,created_at) VALUES ('call_lead',?,'Новый лид','2026-11-21','open','legacy-lead','life','2026-11-21')",(sid,))
        self.subscription(sid,'2026-12','paid');tasks.reconcile_subscription_clients(self.con,date(2026,11,21))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM tasks WHERE student_id=? AND kind='call_lead' AND status='open'",(sid,)).fetchone()[0],0)
        renew=self.con.execute("SELECT due_at FROM tasks WHERE student_id=? AND kind='renew' AND status='open'",(sid,)).fetchall();self.assertEqual([r[0] for r in renew],['2026-12-20T23:59'])

    def test_siblings_are_reconciled_per_child_and_real_lead_stays(self):
        active=self.lead('Subscribed sibling','+79000000003');family=sv.get_student_row(self.con,active)['family_id'];self.subscription(active)
        lead=sv.create_student(self.con,{'name':'New sibling','family_id':family,'funnel':'sales'},self.user)
        tasks.add_task(self.con,'call_lead',lead,'2026-11-21T23:59',auto_key=f'life:call_lead:{lead}',source='life',title='Новый лид')
        tasks.reconcile_subscription_clients(self.con,date(2026,11,21))
        self.assertEqual((sv.get_student_row(self.con,active)['stage'],sv.get_student_row(self.con,lead)['stage']),('active','lead'))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM tasks WHERE student_id=? AND kind='call_lead' AND status='open'",(lead,)).fetchone()[0],1)

    def test_control_erp_child_waits_for_authorized_comparison(self):
        sid=self.lead('Control child','+79000000004');self.con.execute("UPDATE students SET erp_id='132389' WHERE id=?",(sid,));self.subscription(sid)
        tasks.reconcile_subscription_clients(self.con,date(2026,11,21))
        self.assertEqual((sv.get_student_row(self.con,sid)['funnel'],sv.get_student_row(self.con,sid)['stage']),('sales','lead'))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM tasks WHERE student_id=? AND kind='call_lead' AND status='open'",(sid,)).fetchone()[0],1)


if __name__=='__main__':unittest.main()
