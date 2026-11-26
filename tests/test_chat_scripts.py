"""Rules routing, exact approved templates, missing-MAX guard and draft-only attachments."""
import json
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
        self.assertFalse(rule['reason']);self.assertIn('max.ru/id183210983596_biz',rule['text']);self.assertIn('Написать нам в MAX:',rule['text']);self.assertNotIn('t.me',rule['text'])
        scripts.save_profiles(self.con,{'nebo':{'max':'https://max.ru/test_school_channel'}})
        with patch.object(memos,'image',return_value=b'PNG'):
            rule=scripts.rules(self.con,self.sid,'max')
        self.assertFalse(rule['reason']);self.assertIn('max.ru/test_school_channel',rule['text']);self.assertNotIn('t.me',rule['text'])
    def test_arctic_max_without_channel_keeps_rules_offer_contact_and_picture(self):
        pid=self.con.execute("SELECT id FROM programs WHERE slug='sbornaya'").fetchone()[0]
        self.con.execute("UPDATE groups SET program_id=?,lesson_kind='arena' WHERE id=?",(pid,self.gid))
        self.con.execute("UPDATE conversations SET channel='max' WHERE id=?",(self.cid,))
        with patch.object(memos,'image',return_value=b'\x89PNG\r\n\x1a\nTEST'),patch.object(comms,'max_call') as network:
            rule=scripts.rules(self.con,self.sid,'max')
            draft=scripts.stage_rules(self.con,self.cid,self.user,self.sid)
        self.assertFalse(rule['reason']);self.assertTrue(rule['image']);self.assertIn('/4005/',rule['text']);self.assertIn('Написать нам в MAX:',rule['text']);self.assertIn('памятку',rule['text']);self.assertNotIn('подпишитесь',rule['text'].lower());self.assertNotIn('Именно в канале',rule['text']);self.assertNotIn('t.me',rule['text']);self.assertNotIn('{',rule['text']);self.assertEqual(draft['text'],rule['text']);network.assert_not_called()
        with patch.object(memos,'image',return_value=b'PNG'):
            telegram=scripts.rules(self.con,self.sid,'telegram')
        self.assertIn('Telegram',telegram['reason'])
    def test_missing_other_branch_channel_still_blocks(self):
        scripts.save_profiles(self.con,{'nebo':{'max':''}})
        with patch.object(memos,'image',return_value=b'PNG'):
            rule=scripts.rules(self.con,self.sid,'max')
        self.assertIn('MAX',rule['reason'])
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
        self.assertEqual(scripts.NEBO_OFFER,p['nebo']['offer']);self.assertEqual(scripts.NEBO_OFFER,p['toddlers']['offer'])
        with self.assertRaises(comms.CommsError):scripts.save_profiles(self.con,{'nebo':{'telegram':'https://evil.example/x'}})
        with self.assertRaises(comms.CommsError):scripts.save_profiles(self.con,{'nebo':{'offer':'javascript:alert(1)'}})
    def test_only_exact_legacy_offers_migrate_once_in_nebo_profiles(self):
        base = 'https://msk.footbolika.ru/assets/images/offer/1846/'
        august, previous = base + 'dgw2m3.pdf', base + 'j1k2f6.pdf'
        current = base + 'vzaxum.pdf'
        custom = previous + '?version=custom'
        for nebo, toddlers, expected_nebo, expected_toddlers in [
            (august, previous, current, current),
            (previous, august, current, current),
            (custom, previous, custom, current),
            (august, custom, current, custom),
        ]:
            with self.subTest(nebo=nebo, toddlers=toddlers):
                saved = {
                    'nebo': {'offer': nebo, 'text': 'Правила {offer}', 'max': 'https://max.ru/example'},
                    'toddlers': {'offer': toddlers, 'text': previous},
                    'western': {'offer': previous},
                    'arctic': {'offer': august},
                }
                comms.put(self.con, 'school_rules_profiles', json.dumps(saved))
                scripts.migrate(self.con)
                saved['nebo']['offer'] = expected_nebo
                saved['toddlers']['offer'] = expected_toddlers
                self.assertEqual(json.loads(comms.get(self.con, 'school_rules_profiles')), saved)
                before = self.con.total_changes
                scripts.migrate(self.con)
                self.assertEqual(self.con.total_changes, before)

    def test_current_offer_in_telegram_and_max_previews_for_both_nebo_groups(self):
        offer = 'https://msk.footbolika.ru/assets/images/offer/1846/vzaxum.pdf'
        regular_group = schedule.save_group(self.con, {
            'name': 'Основная группа', 'branch_id': self.branch, 'age': '4-5',
            'weekdays': [1, 4], 'time': '18:45',
        })
        regular_student = sv.create_student(self.con, {
            'name': 'Тест Основной', 'parent_name': 'Тест', 'phone': '+79000000002',
            'group_id': regular_group, 'funnel': 'members',
        }, self.user)
        with patch.object(memos, 'image', return_value=b'PNG'):
            for profile, student in [('nebo', regular_student), ('toddlers', self.sid)]:
                for channel in ('telegram', 'max'):
                    with self.subTest(profile=profile, channel=channel):
                        items = scripts.catalog(self.con, student, channel)['items']
                        preview = next(item for item in items if item['key'] == 'rules')
                        self.assertEqual(preview['profile'], profile)
                        self.assertFalse(preview['reason'])
                        self.assertIn(offer, preview['text'].splitlines())
                        self.assertEqual(preview['preview_offer_url'], offer)

    def test_custom_offer_and_template_without_offer_have_no_extra_preview_link(self):
        custom_offer = 'https://msk.footbolika.ru/assets/images/offer/1846/custom.pdf'
        scripts.save_profiles(self.con, {'toddlers': {'offer': custom_offer}})
        preview = scripts.rules(self.con, self.sid, 'telegram')
        self.assertIn(custom_offer, preview['text'])
        self.assertNotIn('preview_offer_url', preview)
        scripts.save_profiles(self.con, {'toddlers': {'offer': scripts.NEBO_OFFER, 'text': 'Особый текст'}})
        preview = scripts.rules(self.con, self.sid, 'max')
        self.assertEqual(preview['text'], 'Особый текст')
        self.assertNotIn('preview_offer_url', preview)

    def test_changed_rule_links_block_staged_send_before_network(self):
        with patch.object(memos,'image',return_value=b'\x89PNG\r\n\x1a\nTEST'):
            draft=scripts.stage_rules(self.con,self.cid,self.user,self.sid)
            scripts.save_profiles(self.con,{'nebo':{'telegram':'https://t.me/updated_test_channel'}})
            with patch.object(comms,'tg_call') as network:
                with self.assertRaises(comms.CommsError):chatattachments.send(self.con,self.cid,draft['id'],self.user,draft['text'])
            network.assert_not_called()
    def test_artwork_invalidated_by_tariff_change(self):
        import base64
        key='g'+str(self.gid);target=next(t for t in memos.targets(self.con) if t['key']==key)
        self.assertIsNone(memos.image(self.con,key)) # Old fixed-price PNG cannot bypass the current hash.
        self.assertTrue(memos.save(self.con,key,target['hash'],base64.b64encode(b'\x89PNG\r\n\x1a\nTEST').decode()))
        self.assertIsNotNone(memos.image(self.con,key))
        self.con.execute("INSERT INTO group_terms(group_id,valid_from,per_lesson,credit_limit) VALUES (?,'2026-11',1700,2)",(self.gid,))
        self.assertIsNone(memos.image(self.con,key))
    def test_native_max_file_uses_upload_token_not_file_url(self):
        self.con.execute("UPDATE conversations SET channel='max' WHERE id=?",(self.cid,))
        import base64
        draft=chatattachments.upload(self.con,self.cid,self.user,'test.pdf',base64.b64encode(b'%PDF-1.4 synthetic').decode())
        with patch.object(chatattachments,'_max_upload_document',return_value='UPLOADED_MEDIA_TOKEN') as upload,patch.object(comms,'max_call',return_value={'message':{'body':{'mid':'max-file-1'}}}) as call:
            sent=chatattachments.send(self.con,self.cid,draft['id'],self.user,'Подпись')
        self.assertTrue(sent['ok']);upload.assert_called_once();self.assertEqual(call.call_args.kwargs['data']['attachments'],[{'type':'file','payload':{'token':'UPLOADED_MEDIA_TOKEN'}}])

if __name__=='__main__':unittest.main()
