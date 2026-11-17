from unittest.mock import patch
import unittest
from tests import test_sale_paid_date as sale_fixture
from crm import services as sv,historyentry,smart

class MorningFixedTest(unittest.TestCase):
 setUp=sale_fixture.SaleDateTest.setUp
 quote=sale_fixture.SaleDateTest.quote
 def test_fixed_only_metadata_and_flex_rejected_without_writing(self):
  group=next(g for g in sv.meta(self.con)['groups'] if g['id']==self.gid)
  self.assertTrue(group['fixed_only'])
  self.assertEqual(self.quote()['tariff'],'fixed')
  with self.assertRaises(sv.ApiError):self.quote(tariff='flex')
  with patch.object(smart,'REQUIRE',False):
   with self.assertRaises(sv.ApiError):sv.create_invoice(self.con,self.sid,'2026-11',self.u,group_id=self.gid,tariff='flex')
  self.assertEqual(self.con.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0],0)
  with self.assertRaises(sv.ApiError):historyentry.options(self.con,self.sid,self.gid,'2026-11','flex')
 def test_old_enrollment_default_is_not_new_sale_tariff(self):
  self.con.execute("UPDATE enrollments SET tariff='flex' WHERE student_id=?",(self.sid,))
  q=self.quote();self.assertEqual(q['tariff'],'fixed')
 def test_other_group_keeps_flexible_tariff(self):
  from crm import schedule
  bid=self.con.execute('SELECT branch_id FROM groups WHERE id=?',(self.gid,)).fetchone()[0]
  other=schedule.save_group(self.con,{'branch_id':bid,'weekdays':[1,4],'time':'17:00','age':'4–5'})
  self.assertFalse(next(g for g in sv.meta(self.con)['groups'] if g['id']==other)['fixed_only'])
  self.assertEqual(sv.quote_for(self.con,self.sid,'2026-11',group_id=other,tariff='flex')['tariff'],'flex')
 def test_legacy_subscription_is_not_rewritten(self):
  with patch.object(smart,'REQUIRE',False):invoice=sv.create_invoice(self.con,self.sid,'2026-11',self.u,group_id=self.gid)
  self.con.execute("UPDATE subscriptions SET tariff='flex' WHERE id=?",(invoice['id'],))
  before=tuple(self.con.execute('SELECT * FROM subscriptions WHERE id=?',(invoice['id'],)).fetchone())
  sv.meta(self.con);self.quote()
  self.assertEqual(before,tuple(self.con.execute('SELECT * FROM subscriptions WHERE id=?',(invoice['id'],)).fetchone()))
