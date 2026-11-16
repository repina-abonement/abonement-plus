"""Rules routing, exact approved templates, missing-MAX guard and draft-only attachments."""
import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from crm import db,services as sv,schedule,smart,comms,chatscripts as scripts,memos,chatattachments

class ScriptsTest(unittest.TestCase):
    def setUp(self):
        self.con=db.connect(str(Path(tempfile.mkdtemp())/'scripts.sqlite3'));db.init_db(self.con)
        self.user=dict(self.con.execute("SELECT * FROM users WHERE role='admin'").fetchone());smart.REQUIRE=False
        self.branch=schedule.save_branch(self.con,{'name':'ТЦ «Небо»'})
        self.gid=schedule.save_group(self.con,{'name':'Малыши','branch_id':self.branch,'age':'1,5–3','weekdays':[0,2],'time':'10:30'})
        self.sid=sv.create_student(self.con,{'name':'Тестовый Ребёнок','parent_name':'Тест','phone':'+79000000001','group_id':self.gid,'funnel':'members'},self.user)
        self.fid=self.con.execute('SELECT family_id FROM students WHERE id=?',(self.sid,)).fetchone()[0]
        self.cid=self.con.execute("INSERT INTO conversations(channel,external_id,family_id,created_at) VALUES ('telegram','12345',?,'2026-11-16')",(self.fid,)).lastrowid
    def tearDown(self):self.con.close()
    def test_distinct_toddler_picture_and_same_nebo_channel(self):
        with patch.object(memos,'image',return_value=b'\x89PNG\r\n\x1a\nSYNTHETIC'):
            rule=scripts.rules(self.con,self.sid,'telegram')
        self.assertEqual(rule['profile'],'toddlers');self.assertFalse(rule['reason']);self.assertIn('/1846/',rule['text']);self.assertIn('footbolika_deti',rule['text']);self.assertIn('key=g'+str(self.gid),rule['image']);self.assertNotIn('{',rule['text']);self.assertLessEqual(len(rule['text']),1024)
    def test_max_never_falls_back_to_telegram_or_another_branch(self):
        with patch.object(memos,'image',return_value=b'PNG'):
            rule=scripts.rules(self.con,self.sid,'wazzup_max')
        self.assertIn('MAX',rule['reason']);self.assertNotIn('t.me',rule['text'])
        scripts.save_profiles(self.con,{'nebo':{'max':'https://max.ru/test_school_channel'}})
        with patch.object(memos,'image',return_value=b'PNG'):
            rule=scripts.rules(self.con,self.sid,'max')
        self.assertFalse(rule['reason']);self.assertIn('max.ru/test_school_channel',rule['text']);self.assertNotIn('t.me',rule['text'])
    def test_stage_rules_does_not_send_and_is_bound_to_family(self):
        with patch.object(memos,'image',return_value=b'\x89PNG\r\n\x1a\nTEST'),patch.object(comms,'tg_call') as send:
            draft=scripts.stage_rules(self.con,self.cid,self.user,self.sid)
        send.assert_not_called();self.assertEqual(self.con.execute('SELECT status FROM chat_attachments WHERE id=?',(draft['id'],)).fetchone()[0],'draft')
        self.assertEqual(self.con.execute('SELECT count(*) FROM messages').fetchone()[0],0)
        self.con.execute('UPDATE conversations SET family_id=NULL WHERE id=?',(self.cid,))
        with self.assertRaises(comms.CommsError):scripts.stage_rules(self.con,self.cid,self.user,self.sid)
    def test_stale_or_missing_picture_blocks_stage(self):
        with patch.object(memos,'image',return_value=None):
            with self.assertRaises(comms.CommsError):scripts.stage_rules(self.con,self.cid,self.user,self.sid)
        self.assertEqual(self.con.execute('SELECT count(*) FROM chat_attachments').fetchone()[0],0)
    def test_saved_templates_are_used_and_no_trial_is_not_a_crash(self):
        comms.put(self.con,'msg_after','Согласованный текст администратора')
        items=scripts.catalog(self.con,self.sid)['items']
        self.assertEqual(next(i for i in items if i['key']=='msg_after')['text'],'Согласованный текст администратора')
        self.assertTrue(next(i for i in items if i['key']=='msg_booked')['reason'])
    def test_offers_are_separate_and_unsafe_links_rejected(self):
        p=scripts.profiles(self.con);self.assertEqual(len({p[k]['offer'] for k in ('nebo','western','arctic')}),3)
        with self.assertRaises(comms.CommsError):scripts.save_profiles(self.con,{'nebo':{'telegram':'https://evil.example/x'}})
        with self.assertRaises(comms.CommsError):scripts.save_profiles(self.con,{'nebo':{'offer':'javascript:alert(1)'}})
    def test_changed_rule_links_block_staged_send_before_network(self):
        with patch.object(memos,'image',return_value=b'\x89PNG\r\n\x1a\nTEST'):
            draft=scripts.stage_rules(self.con,self.cid,self.user,self.sid)
            scripts.save_profiles(self.con,{'nebo':{'telegram':'https://t.me/updated_test_channel'}})
            with patch.object(comms,'tg_call') as network:
                with self.assertRaises(comms.CommsError):chatattachments.send(self.con,self.cid,draft['id'],self.user,draft['text'])
            network.assert_not_called()
    def test_artwork_invalidated_by_tariff_change(self):
        target=next(t for t in memos.targets(self.con) if t['key']=='g'+str(self.gid))
        self.assertTrue(memos.designed_image(target).startswith(b'\x89PNG'))
        target['data']['per_lesson']=1700
        self.assertIsNone(memos.designed_image(target))
    def test_native_max_file_uses_upload_token_not_file_url(self):
        self.con.execute("UPDATE conversations SET channel='max' WHERE id=?",(self.cid,))
        import base64
        draft=chatattachments.upload(self.con,self.cid,self.user,'test.pdf',base64.b64encode(b'%PDF-1.4 synthetic').decode())
        with patch.object(chatattachments,'_max_upload_document',return_value='UPLOADED_MEDIA_TOKEN') as upload,patch.object(comms,'max_call',return_value={'message':{'body':{'mid':'max-file-1'}}}) as call:
            sent=chatattachments.send(self.con,self.cid,draft['id'],self.user,'Подпись')
        self.assertTrue(sent['ok']);upload.assert_called_once();self.assertEqual(call.call_args.kwargs['data']['attachments'],[{'type':'file','payload':{'token':'UPLOADED_MEDIA_TOKEN'}}])

if __name__=='__main__':unittest.main()
