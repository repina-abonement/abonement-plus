import json
from unittest.mock import patch
from tests.test_sale_paid_date import SaleDateTest
from crm import services as sv,smart,saleinfo

class SaleInfoTest(SaleDateTest):
 def test_attribution_and_exclusions_survive_later_changes(self):
  with patch.object(smart,'REQUIRE',False):
   invoice=sv.create_invoice(self.con,self.sid,'2026-11',self.u,group_id=self.gid,excluded_dates=['2026-11-16','2026-11-18'],exclusion_reason='Согласованный отпуск')
  row=dict(self.con.execute('SELECT * FROM subscriptions WHERE id=?',(invoice['id'],)).fetchone())
  self.assertEqual(row['issued_by'],self.u['id']);self.assertEqual(row['issued_by_name'],'Synthetic')
  self.assertEqual(json.loads(row['exclusion_reasons']),{'2026-11-16':'Пробное занятие','2026-11-18':'Согласованный отпуск'})
  self.con.execute("UPDATE users SET name='Renamed' WHERE id=?",(self.u['id'],))
  self.con.execute("UPDATE students SET trial_at='2026-11-23T10:00' WHERE id=?",(self.sid,))
  detail=sv.student_detail(self.con,self.sid)['subscriptions'][0]
  self.assertEqual(detail['issued_by_name'],'Synthetic');self.assertEqual(json.loads(detail['exclusion_reasons']),json.loads(row['exclusion_reasons']))
  saleinfo.init(self.con)
  self.assertEqual(self.con.execute('SELECT issued_by_name FROM subscriptions').fetchone()[0],'Synthetic')
 def test_legacy_unknown_not_guessed(self):
  with patch.object(smart,'REQUIRE',False):r=sv.create_invoice(self.con,self.sid,'2026-11',None,group_id=self.gid)
  self.assertEqual(self.con.execute('SELECT issued_by_name FROM subscriptions WHERE id=?',(r['id'],)).fetchone()[0],'')

 def test_same_group_month_cannot_be_sold_under_second_enrollment(self):
  with patch.object(smart,'REQUIRE',False):
   sv.create_invoice(self.con,self.sid,'2026-11',self.u,group_id=self.gid)
   second=self.con.execute("INSERT INTO enrollments(student_id,group_id,tariff,created_at) VALUES (?,?,'fixed','2026-11-17')",(self.sid,self.gid)).lastrowid
   with self.assertRaises(sv.ApiError):
    sv.create_invoice(self.con,self.sid,'2026-11',self.u,enrollment_id=second)
  self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0],1)
