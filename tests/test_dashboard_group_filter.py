import tempfile
import unittest
from pathlib import Path
from crm import db,services as sv

class DashboardGroupFilterTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.con=db.connect(str(Path(self.tmp.name)/'dash.sqlite3'));self.addCleanup(self.con.close);db.init_db(self.con)
        self.user={'id':1,'role':'admin','name':'Synthetic'}
        self.bid=self.con.execute("INSERT INTO branches(name) VALUES ('Synthetic')").lastrowid
        self.a=self.con.execute("INSERT INTO groups(branch_id,name,weekdays,capacity) VALUES (?,'A','0',10)",(self.bid,)).lastrowid
        self.b=self.con.execute("INSERT INTO groups(branch_id,name,weekdays,capacity) VALUES (?,'B','2',20)",(self.bid,)).lastrowid
        self.sid=sv.create_student(self.con,{'name':'Synthetic Child','parent_name':'Synthetic Parent','phone':'+79000000101','group_id':self.a,'funnel':'members'},self.user)
        self.ea=self.con.execute('SELECT id FROM enrollments WHERE student_id=?',(self.sid,)).fetchone()[0]
        self.eb=self.con.execute("INSERT INTO enrollments(student_id,group_id,tariff,created_at) VALUES (?,?,'fixed','2026-11-01')",(self.sid,self.b)).lastrowid
        self.con.execute("UPDATE students SET trial_at='2026-11-16T10:00',trial_group_id=? WHERE id=?",(self.b,self.sid))
        for month,eid,price in [('2026-11',self.ea,100),('2026-11',self.eb,200),('2026-10',self.ea,80)]:
            self.con.execute("INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at) VALUES (?,?,?,'fixed',1,?,?, 'paid','2026-11-01')",(self.sid,eid,month,price,price))

    def test_revenue_uses_subscription_group_not_primary_student_group(self):
        a=sv.dashboard(self.con,'2026-11',self.bid,self.a)
        b=sv.dashboard(self.con,'2026-11',self.bid,self.b)
        all_=sv.dashboard(self.con,'2026-11',self.bid)
        self.assertEqual([r['kpi']['revenue']['value'] for r in (a,b,all_)],[100,200,300])
        self.assertEqual([r['kpi']['seats'] for r in (a,b,all_)],[10,20,30])
        self.assertEqual(all_['kpi']['holders']['value'],1)
        self.assertEqual(a['series']['revenue']['cur'][-1],100)
        self.assertEqual(b['funnel_trials'][0][1],1)
        self.assertEqual(a['funnel_trials'][0][1],0)

    def test_group_must_belong_to_selected_branch(self):
        other=self.con.execute("INSERT INTO branches(name) VALUES ('Other')").lastrowid
        with self.assertRaises(sv.ApiError):sv.dashboard(self.con,'2026-11',other,self.a)
        with self.assertRaises(sv.ApiError):sv.dashboard(self.con,'2026-11',group_id=99999)

    def test_dashboard_filters_stay_native_selects(self):
        source=(Path(__file__).resolve().parents[1]/'crm'/'static'/'app.js').read_text()
        self.assertEqual(source.count('id="dash-branch" data-search-select="off"'),2)
        self.assertIn('id="dash-group" data-search-select="off"',source)
