"""Separate relay/network refusals from Telegram responses; never retry sends."""
import tempfile,json,io,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from crm import db,comms,stickerpacks


class TelegramTransportTest(unittest.TestCase):
    def setUp(self):
        self.con=db.connect(str(Path(tempfile.mkdtemp())/'telegram.sqlite3'));db.init_db(self.con)
        comms.put(self.con,'tg_token','FAKE_TEST_TOKEN');comms.put(self.con,'tg_relay','https://relay.invalid')

    def test_relay_403_never_retries_send(self):
        with patch.object(comms,'http',return_value=(403,{'_text':'Forbidden'})) as request:
            with self.assertRaises(comms.TelegramTransportError) as result:comms.tg_call(self.con,'sendMessage',{'chat_id':1,'text':'test'})
        self.assertEqual(request.call_count,1)
        self.assertEqual(result.exception.route,'relay');self.assertEqual(result.exception.code,403)
        self.assertIn('IP',str(result.exception));self.assertNotIn('FAKE_TEST_TOKEN',str(result.exception))

    def test_telegram_forbidden_is_not_transport(self):
        with patch.object(comms,'http',return_value=(403,{'ok':False,'description':'Forbidden: bot was blocked by the user'})) as request:
            with self.assertRaises(comms.CommsError) as result:comms.tg_call(self.con,'sendMessage',{'chat_id':1,'text':'test'})
        self.assertNotIsInstance(result.exception,comms.TelegramTransportError)
        self.assertIn('bot was blocked',str(result.exception));self.assertEqual(request.call_count,1)

    def test_network_error_has_no_token_and_no_send_retry(self):
        with patch.object(comms,'http',side_effect=comms.CommsError('No route to host FAKE_TEST_TOKEN')) as request:
            with self.assertRaises(comms.TelegramTransportError) as result:comms.tg_call(self.con,'sendSticker',{})
        self.assertEqual(request.call_count,1);self.assertNotIn('FAKE_TEST_TOKEN',str(result.exception))

    def test_read_only_diagnostics_does_not_change_settings_or_messages(self):
        before=self.con.total_changes
        def http(method,url,**kw):
            self.assertTrue(url.endswith('/getMe'));self.assertEqual(kw['data'],{})
            if url.startswith('https://relay.invalid'):return 403,{'_text':'Forbidden'}
            raise comms.CommsError('No route to host FAKE_TEST_TOKEN')
        with patch.object(comms,'http',side_effect=http) as request:result=comms.telegram_diagnostics(self.con)
        self.assertEqual(request.call_count,2);self.assertEqual(self.con.total_changes,before)
        self.assertEqual([p['ok'] for p in result['probes']],[False,False])
        self.assertNotIn('FAKE_TEST_TOKEN',json.dumps(result));self.assertNotIn('relay.invalid',json.dumps(result))

    def test_read_only_sticker_metadata_network_error_uses_official(self):
        def http(method,url,**kw):
            self.assertTrue(url.startswith('https://api.telegram.org/'))
            if url.endswith('/getStickerSet'):return 200,{'ok':True,'result':{'title':'Test','stickers':[{'file_id':'one','emoji':'⚽'}]}}
            self.assertTrue(url.endswith('/getFile'));return 200,{'ok':True,'result':{'file_path':'stickers/one.webp'}}
        with patch.object(comms,'tg_call',side_effect=comms.TelegramTransportError('No route','relay')),patch.object(comms,'http',side_effect=http),patch.object(stickerpacks,'urlopen',return_value=io.BytesIO(b'RIFFxxxxWEBPdata')):
            result=stickerpacks.import_pack(self.con,'Test_Pack')
        self.assertEqual(result['added'],1)

    def test_both_routes_down_are_explained(self):
        with patch.object(comms,'tg_call',side_effect=comms.TelegramTransportError('No route','relay')),patch.object(comms,'http',side_effect=comms.CommsError('No route FAKE_TEST_TOKEN')):
            with self.assertRaises(comms.CommsError) as result:stickerpacks.import_pack(self.con,'Test_Pack')
        self.assertIn('прямое чтение',str(result.exception));self.assertNotIn('FAKE_TEST_TOKEN',str(result.exception))

    def test_sticker_file_network_error_can_use_official(self):
        def call(con,method,payload):
            return {'title':'Test','stickers':[{'file_id':'one'}]} if method=='getStickerSet' else {'file_path':'stickers/one.webp'}
        with patch.object(comms,'tg_call',side_effect=call),patch.object(stickerpacks,'urlopen',side_effect=[URLError('No route'),io.BytesIO(b'RIFFxxxxWEBPdata')]) as files:
            result=stickerpacks.import_pack(self.con,'Test_Pack')
        self.assertEqual(result['added'],1);self.assertEqual(files.call_count,2)
        self.assertTrue(files.call_args_list[1].args[0].startswith('https://api.telegram.org/'))

if __name__=='__main__':unittest.main()
