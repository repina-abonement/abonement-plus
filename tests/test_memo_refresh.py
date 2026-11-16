"""No static image may bypass effective price/rule/schedule/renderer version checks."""
import base64,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from crm import db,schedule,memos,logic,pricing,makeuppolicy,comms,chatscripts

class RefreshTest(unittest.TestCase):
    def setUp(self):
        self.con=db.connect(str(Path(tempfile.mkdtemp())/'refresh.sqlite3'));db.init_db(self.con)
        self.bid=schedule.save_branch(self.con,{'name':'ТЦ «Небо»'})
        self.gid=schedule.save_group(self.con,{'name':'Обычная группа','branch_id':self.bid,'weekdays':[1,4],'time':'18:00'})
        self.key='b'+str(self.bid);self.png=b'\x89PNG\r\n\x1a\nSYNTHETIC'
    def tearDown(self):self.con.close()
    def target(self):return next(t for t in memos.targets(self.con) if t['key']==self.key)
    def cached(self):
        target=self.target();self.assertTrue(memos.save(self.con,self.key,target['hash'],base64.b64encode(self.png).decode()));self.assertEqual(memos.image(self.con,self.key),self.png);return target
    def test_price_change_blocks_old_png_and_rejects_inflight_old_render(self):
        old=self.cached();pricing.save_price(self.con,{'valid_from':'2026-11','fixed':1500,'flex':1700,'late_pct':5})
        self.assertNotEqual(old['hash'],self.target()['hash']);self.assertIsNone(memos.image(self.con,self.key))
        self.assertFalse(memos.save(self.con,self.key,old['hash'],base64.b64encode(self.png).decode()))
        self.cached();self.assertTrue(next(m for m in memos.status(self.con) if m['key']==self.key)['fresh'])
    def test_actual_makeup_rule_change_invalidates_picture(self):
        old=self.cached()
        with patch.object(logic,'MAKEUP_MIN_WEEKLY_PAID',3):
            self.assertNotEqual(old['hash'],self.target()['hash']);self.assertIsNone(memos.image(self.con,self.key));self.assertIn('3 оплаченных',' '.join(self.target()['data']['makeup_rules']))
    def test_expiry_window_and_renewal_deadline_are_hashed(self):
        old=self.cached()
        with patch.object(logic,'MAKEUP_NEXT_MONTHS',2):self.assertNotEqual(old['hash'],self.target()['hash'])
        self.con.execute("UPDATE settings SET value='24' WHERE key='renewal_deadline_day'")
        self.assertNotEqual(old['hash'],self.target()['hash']);self.assertIsNone(memos.image(self.con,self.key))
    def test_makeup_schedule_and_render_template_are_hashed(self):
        old=self.cached();schedule.save_group(self.con,{'name':'Отработка','branch_id':self.bid,'weekdays':[5],'time':'12:00','is_makeup':1})
        self.assertNotEqual(old['hash'],self.target()['hash']);self.assertIsNone(memos.image(self.con,self.key))
        self.cached()
        with patch.object(memos,'RENDERER_REVISION','next-renderer'):self.assertIsNone(memos.image(self.con,self.key))
    def test_current_paid_rules_and_max_destinations(self):
        text=' '.join(makeuppolicy.parent_rules())
        for part in ['следующем','оплаченный абонемент','2 оплаченных','3-м','Только отмеченные','повторно не используются']:self.assertIn(part,text)
        p=chatscripts.profiles(self.con)
        self.assertEqual(p['nebo']['max'],'https://max.ru/id183210983596_biz');self.assertEqual(p['toddlers']['max'],p['nebo']['max']);self.assertEqual(p['western']['max'],'https://max.ru/id183210983596_biz3')
        self.assertEqual(p['arctic']['max'],'');self.assertNotEqual(p['nebo']['max'],comms.get(self.con,'admin_max'))

if __name__=='__main__':unittest.main()
