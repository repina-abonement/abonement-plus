import io,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from crm import db,comms,stickerpacks

class StickerRelayTest(unittest.TestCase):
    def test_relay_403_uses_official_read_only_api(self):
        con=db.connect(str(Path(tempfile.mkdtemp())/'test.sqlite3'));db.init_db(con)
        comms.put(con,'tg_token','FAKE_TOKEN');comms.put(con,'tg_relay','https://relay.invalid')
        def official(method,url,**kw):
            assert url.startswith('https://api.telegram.org/botFAKE_TOKEN/')
            assert url.endswith('/getStickerSet') or url.endswith('/getFile')
            return 200,{'ok':True,'result':{'stickers':[{'file_id':'test-static','emoji':'⚽'}],'title':'Набор'} if url.endswith('/getStickerSet') else {'file_path':'stickers/a.webp'}}
        with patch.object(comms,'tg_call',side_effect=comms.CommsError('Telegram: ошибка 403')),patch.object(comms,'http',side_effect=official) as calls,patch.object(stickerpacks,'urlopen',return_value=io.BytesIO(b'RIFFxxxxWEBPdata')):
            r=stickerpacks.import_pack(con,'https://t.me/addstickers/Footbolika_Olya')
        self.assertEqual(r['added'],1);self.assertEqual(calls.call_count,2)
        self.assertNotIn('FAKE_TOKEN',str(stickerpacks.catalog(con)))
    def test_real_telegram_rejection_is_not_retried(self):
        con=db.connect(str(Path(tempfile.mkdtemp())/'test.sqlite3'));db.init_db(con)
        comms.put(con,'tg_relay','https://relay.invalid')
        with patch.object(comms,'tg_call',side_effect=comms.CommsError('Telegram: Forbidden: test')),patch.object(comms,'http') as direct:
            with self.assertRaises(comms.CommsError):stickerpacks.import_pack(con,'Footbolika_Olya')
        direct.assert_not_called()

if __name__=='__main__':unittest.main()
