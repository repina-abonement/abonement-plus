"""Wazzup routing and payloads never depend on the Telegram Bot API or relay."""
import base64,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from crm import db,comms,chatattachments as files,tasks,server

class WazzupTest(unittest.TestCase):
    def setUp(self):
        self.path=str(Path(tempfile.mkdtemp())/'wazzup.sqlite3');self.con=db.connect(self.path);db.init_db(self.con)
        self.user=dict(self.con.execute("SELECT * FROM users WHERE role='admin'").fetchone())
        self.fid=self.con.execute("INSERT INTO families(name) VALUES ('Synthetic')").lastrowid
        self.con.execute("INSERT INTO parents(family_id,name,phone,phone_norm,is_primary) VALUES (?,'Test','+79000000111','79000000111',1)",(self.fid,))
        comms.put(self.con,'wazzup_key','FAKE_WZ_KEY');comms.put(self.con,'wazzup_tg_channel','test-tg');comms.put(self.con,'tg_relay','https://unavailable-relay.invalid');comms.put(self.con,'tg_token','FAKE_BOT_TOKEN')
        self.bot=self.chat('telegram','12345','79000000111')
        self.wz=self.chat('wazzup_telegram','phone:79000000111','79000000111')
    def tearDown(self):self.con.close()
    def chat(self,kind,external,phone=''):
        return self.con.execute('INSERT INTO conversations(channel,external_id,phone_norm,family_id,meta,created_at,last_at) VALUES (?,?,?,?,?,?,?)',(kind,external,phone,self.fid,json.dumps({'channelId':'test-tg','chatType':'telegram'}) if kind=='wazzup_telegram' else '{}','2026-11-16','2026-11-16' if kind=='telegram' else '2026-11-15')).lastrowid
    def attachment(self):return files.upload(self.con,self.wz,self.user,'test.pdf',base64.b64encode(b'%PDF-1.4 synthetic').decode())
    def test_text_and_existing_stickers_work_without_bot_or_bridge(self):
        with patch.object(comms,'tg_call',side_effect=AssertionError('Bot must not be called')),patch.object(comms,'wz_call',return_value={'messageId':'WZ-ACK'}) as provider:
            comms.send(self.con,self.wz,'Текст',self.user);comms.send_sticker(self.con,self.wz,'thanks',self.user)
        self.assertEqual(provider.call_count,2)
        self.assertIn('text',provider.call_args_list[0].args[3]);self.assertNotIn('contentUri',provider.call_args_list[0].args[3]);self.assertIn('contentUri',provider.call_args_list[1].args[3]);self.assertNotIn('text',provider.call_args_list[1].args[3])
    def test_bundle_is_image_then_text_same_recipient_and_no_duplicates(self):
        item=self.attachment()
        with patch.object(comms,'tg_call',side_effect=AssertionError('No bot')),patch.object(comms,'wz_call',side_effect=[{'messageId':'file-ack'},{'messageId':'text-ack'}]) as provider:
            result=files.send(self.con,self.wz,item['id'],self.user,'Текст под памяткой')
            again=files.send(self.con,self.wz,item['id'],self.user,'Текст под памяткой')
        self.assertTrue(result['ok']);self.assertTrue(again['already_sent']);self.assertEqual(provider.call_count,2)
        one,two=[c.args[3] for c in provider.call_args_list]
        self.assertIn('contentUri',one);self.assertNotIn('text',one);self.assertEqual(two['text'],'Текст под памяткой');self.assertNotIn('contentUri',two)
        self.assertEqual(one['channelId'],two['channelId']);self.assertEqual(one['phone'],two['phone']);self.assertNotEqual(one['crmMessageId'],two['crmMessageId'])
        rows=list(self.con.execute('SELECT kind,status FROM messages ORDER BY id'));self.assertEqual([tuple(r) for r in rows],[('file','sent'),('text','sent')])
    def test_partial_text_failure_keeps_confirmed_file_and_never_repeats_it(self):
        item=self.attachment()
        with patch.object(comms,'wz_call',side_effect=[{'messageId':'file-ack'},comms.CommsError('timeout')]) as provider:
            result=files.send(self.con,self.wz,item['id'],self.user,'Текст')
            with self.assertRaises(comms.CommsError):files.send(self.con,self.wz,item['id'],self.user,'Текст')
        self.assertTrue(result['partial']);self.assertTrue(result['file_sent']);self.assertEqual(provider.call_count,2)
        self.assertEqual([tuple(r) for r in self.con.execute('SELECT kind,status FROM messages ORDER BY id')],[('file','sent'),('text','error')])
    def test_preference_requires_same_parent_and_is_unambiguous(self):
        d=comms.conversation(self.con,self.bot,False);self.assertEqual(d['preferred_reply_id'],self.wz)
        self.assertEqual(tasks.best_conversation(self.con,self.fid),self.wz)
        self.con.execute("UPDATE conversations SET phone_norm='79000000222' WHERE id=?",(self.wz,))
        self.assertEqual(comms.conversation(self.con,self.bot,False)['preferred_reply_id'],self.bot)
        self.con.execute("UPDATE conversations SET phone_norm='79000000111' WHERE id=?",(self.wz,))
        self.chat('wazzup_telegram','second-tg-chat','79000000111')
        self.assertEqual(comms.conversation(self.con,self.bot,False)['preferred_reply_id'],self.bot)
    def test_wazzup_save_does_not_disconnect_unchanged_bot(self):
        comms.put(self.con,'tg_ok','1')
        comms.save_settings(self.con,{'tg_relay':'https://unavailable-relay.invalid','tg_token':'••••OKEN','wazzup_key':'NEW_FAKE_KEY'})
        self.assertEqual(comms.get(self.con,'tg_ok'),'1')
    def test_settings_get_is_read_only_even_while_another_request_writes(self):
        reader=db.connect(self.path);reader.execute('PRAGMA busy_timeout=1')
        self.con.execute('BEGIN IMMEDIATE');comms.put(self.con,'lock-test','synthetic')
        before=reader.total_changes
        try:
            data=server.integrations_get(reader,SimpleNamespace(headers={'Host':'crm.example.invalid'}),self.user,{}, {})
            self.assertTrue(data['status']['wazzup_telegram']);self.assertEqual(reader.total_changes,before)
        finally:self.con.execute('ROLLBACK');reader.close()

if __name__=='__main__':unittest.main()
