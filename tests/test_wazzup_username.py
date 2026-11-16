import unittest
from unittest.mock import patch
from tests.test_wazzup_no_bridge import WazzupTest
from crm import comms, server

class UsernameTest(WazzupTest):
    def test_username_open_does_not_send_and_reuses_chat(self):
        with patch.object(comms,'wz_call',side_effect=AssertionError('must not send')):
            cid=comms.start_wazzup_username(self.con,'@Some_User')
            self.assertEqual(cid,comms.start_wazzup_username(self.con,'https://t.me/some_user'))
        self.assertEqual(self.con.execute('SELECT count(*) FROM messages').fetchone()[0],0)
        with patch.object(comms,'wz_call',return_value={'messageId':'ack','chatId':'123456'}) as provider:
            comms.send(self.con,cid,'Hello',self.user)
            self.assertEqual(provider.call_args.args[3]['username'],'some_user')
            comms.send(self.con,cid,'Second',self.user)
            self.assertEqual(provider.call_args.args[3]['chatId'],'123456')
            self.assertNotIn('username',provider.call_args.args[3])
    def test_phone_reuses_known_chat_and_attachment_uses_username(self):
        self.assertEqual(comms.start_wazzup_telegram(self.con,'+79000000111'),self.wz)
        cid=comms.start_wazzup_username(self.con,'@attachment_user')
        from crm import chatattachments as files
        import base64
        attachment=files.upload(self.con,cid,self.user,'test.pdf',base64.b64encode(b'%PDF-1.4 synthetic').decode())
        with patch.object(comms,'wz_call',side_effect=[{'messageId':'file','chatId':'6789'},{'messageId':'caption'}]) as provider:
            files.send(self.con,cid,attachment['id'],self.user,'Caption')
        self.assertEqual(provider.call_args_list[0].args[3]['username'],'attachment_user')
        self.assertEqual(provider.call_args_list[1].args[3]['chatId'],'6789')
        self.assertNotIn('username',provider.call_args_list[1].args[3])
    def test_invalid_and_no_ack(self):
        for value in ('1234567','@bad!','https://evil.test/username','https://t.me/+invite'):
            with self.assertRaises(comms.CommsError):comms.start_wazzup_username(self.con,value)
        with patch.object(comms,'wz_call',return_value={}):
            with self.assertRaises(comms.CommsError):comms.send(self.con,self.wz,'Test',self.user)
        self.assertEqual(self.con.execute("SELECT count(*) FROM messages WHERE status='sent'").fetchone()[0],0)
    def test_provider_phone_error_explained_and_key_hidden(self):
        with patch.object(comms,'http',return_value=(400,{'error':'CHANNEL_TGAPI_PHONE_NOT_OCCUPIED','description':'FAKE_WZ_KEY'})):
            with self.assertRaises(comms.CommsError) as err:comms.wz_call(self.con,'POST','message',{})
        self.assertIn('@нику',str(err.exception));self.assertNotIn('FAKE_WZ_KEY',str(err.exception))

if __name__=='__main__':unittest.main()
