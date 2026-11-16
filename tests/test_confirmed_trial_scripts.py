"""Approved school copy uses actual booking dates and each branch's messenger links."""
import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime
from crm import db,comms,schedule,tasks,trialscripts,chatscripts

class TrialScriptsTest(unittest.TestCase):
    def setUp(self):
        self.con=db.connect(str(Path(tempfile.mkdtemp())/'scripts.sqlite3'));db.init_db(self.con)
        self.nebo=schedule.save_branch(self.con,{'name':'ТРЦ «Небо»'});self.west=schedule.save_branch(self.con,{'name':'СК «Западный»'})
        self.ng=schedule.save_group(self.con,{'name':'Небо','branch_id':self.nebo,'weekdays':[1,4],'time':'19:45'})
        self.wg=schedule.save_group(self.con,{'name':'Западный','branch_id':self.west,'weekdays':[1,4],'time':'10:30'})
        chatscripts.save_profiles(self.con,{'western':{'telegram':'https://t.me/test_western_channel'},'nebo':{'telegram':'https://t.me/test_nebo_channel'}})
    def tearDown(self):self.con.close()
    def student(self,gid):return {'group_id':gid,'trial_at':'2026-11-17T10:30','name':'Synthetic Test'}
    def test_western_booking_and_two_hours_have_correct_time_and_own_channel(self):
        for kind in ['msg_booked','msg_2h_before']:
            text=trialscripts.render(self.con,kind,self.student(self.wg),'wazzup_telegram')
            self.assertIn('10:30',text);self.assertIn('test_western_channel',text);self.assertNotIn('footbolika_deti',text);self.assertNotIn('25 октября',text);self.assertIn('зелё',text);self.assertIn('20%',text);self.assertNotIn('{',text)
        self.assertIn('17 ноября',tasks.booked_text(self.con,self.student(self.wg),'wazzup_telegram'))
    def test_max_replaces_telegram_channel_and_nebo_day_before(self):
        text=tasks.render_template(self.con,'msg_day_before',self.student(self.ng),'wazzup_max')
        self.assertIn('завтра в 10:30',text);self.assertIn('3-м этаже',text);self.assertIn('max.ru/id183210983596_biz',text);self.assertNotIn('t.me',text)
        west=tasks.render_template(self.con,'msg_2h_before',self.student(self.wg),'wazzup_max')
        self.assertIn('max.ru/id183210983596_biz3',west);self.assertNotIn('t.me',west)
    def test_missing_required_link_blocks_render_and_saved_template_is_used(self):
        chatscripts.save_profiles(self.con,{'western':{'telegram':''}})
        with self.assertRaises(comms.CommsError):tasks.booked_text(self.con,self.student(self.wg),'wazzup_telegram')
        comms.put(self.con,'msg_booked_western','Согласовано {date_time} {channel}')
        self.assertTrue(tasks.booked_text(self.con,self.student(self.wg),'wazzup_max').startswith('Согласовано 17 ноября'))
    def test_after_trial_is_updated_once_and_previous_text_is_preserved(self):
        comms.put(self.con,'msg_after_approved_20261116','');comms.put(self.con,'msg_after','Предыдущий согласованный текст');tasks.init(self.con)
        self.assertEqual(tasks.setting(self.con,'msg_after'),trialscripts.AFTER_TEXT);self.assertEqual(comms.get(self.con,'msg_after_previous_20261116'),'Предыдущий согласованный текст')
        comms.put(self.con,'msg_after','Поздняя правка сотрудника');tasks.init(self.con)
        self.assertEqual(tasks.setting(self.con,'msg_after'),'Поздняя правка сотрудника');self.assertFalse(comms.auto_live(self.con))

if __name__=='__main__':unittest.main()
