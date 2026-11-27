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

    def test_card_history_is_scoped_to_sale_group_and_not_counted_twice(self):
        from crm import analytics, salary
        for key, group, price in [('same', self.a, 100), ('second', self.b, 100), ('unknown', None, 60)]:
            self.con.execute("INSERT INTO erp_revenue_history VALUES (?,?,?,'2026-11','Абонемент','','','',?)",
                             (key, self.sid, '42', price))
            if group:
                self.con.execute("INSERT INTO erp_revenue_groups VALUES (?,?)", (key, group))
        branch = sv.dashboard(self.con, '2026-11', self.bid)['kpi']
        self.assertEqual((branch['revenue']['value'], branch['holders']['value']), (400, 1))
        self.assertEqual(sv.dashboard(self.con, '2026-11')['kpi']['revenue']['value'], 460)
        report = analytics.groups_report(self.con, '2026-11', self.bid)
        self.assertEqual(report['branches'][0]['kids'], 1)
        self.assertEqual({g['id']: g['revenue'] for g in report['groups']}, {self.a: 100, self.b: 300})
        self.assertEqual(salary._revenue(self.con, '2026-11'), {self.a: 100, self.b: 300})

    def test_renamed_chobotovskaya_uses_schedule_capacity_even_in_empty_groups(self):
        from crm import analytics, sale_locations, schedule
        self.con.execute("UPDATE branches SET name='СК «Западный» (Чоботовская)' WHERE id=?", (self.bid,))
        # The two existing groups represent pre-migration groups with old capacities.
        self.con.execute("DELETE FROM settings WHERE key='group_capacities_202611'")
        db.sync_group_capacities(self.con)
        schedule.save_group(self.con, dict(branch_id=self.bid, name='Третья', weekdays='5', time='12:00'))
        self.assertEqual(sale_locations.branch_ids(self.con)['Чоботовская 4с2'], self.bid)
        k = sv.dashboard(self.con, '2026-11', self.bid)['kpi']
        self.assertEqual((k['seats'], k['fill']['subs'], k['fill']['value']), (36, 6, 3))
        self.assertEqual(analytics.groups_report(self.con, '2026-11', self.bid)['branches'][0]['capacity'], 36)
        self.con.execute("UPDATE groups SET capacity=13 WHERE id=?", (self.a,))
        db.sync_group_capacities(self.con)
        self.assertEqual(sv.dashboard(self.con, '2026-11', self.bid)['kpi']['seats'], 37)

    def test_group_must_belong_to_selected_branch(self):
        other=self.con.execute("INSERT INTO branches(name) VALUES ('Other')").lastrowid
        with self.assertRaises(sv.ApiError):sv.dashboard(self.con,'2026-11',other,self.a)
        with self.assertRaises(sv.ApiError):sv.dashboard(self.con,'2026-11',group_id=99999)

    def test_dashboard_filters_stay_native_selects(self):
        source=(Path(__file__).resolve().parents[1]/'crm'/'static'/'app.js').read_text()
        self.assertEqual(source.count('id="dash-branch" data-search-select="off"'),2)
        self.assertIn('id="dash-group" data-search-select="off"',source)
