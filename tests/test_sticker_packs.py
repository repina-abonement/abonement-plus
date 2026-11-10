"""Свой Telegram-набор, безопасные превью и выбор транспорта без реальных отправок."""
import io
import tempfile
from pathlib import Path
from unittest.mock import patch
from crm import db, comms, stickerpacks


def main():
    con = db.connect(str(Path(tempfile.mkdtemp())/'stickers.sqlite3'))
    db.init_db(con)
    con.execute("INSERT OR REPLACE INTO integrations(key,value) VALUES ('tg_token','FAKE_TOKEN')")
    def tg(con,method,payload):
        if method == 'getStickerSet':
            return {'title':'Набор примера','stickers':[{'file_id':'fake-static','emoji':'⚽'}, {'file_id':'fake-animated','emoji':'👍','is_animated':True,'thumbnail':{'file_id':'fake-thumb'}}]}
        if method == 'getFile':
            return {'file_path':'stickers/fake.webp'}
        return {'message_id':12}
    with patch.object(comms,'tg_call',side_effect=tg), patch.object(stickerpacks,'urlopen',side_effect=lambda *a,**kw:io.BytesIO(b'RIFFxxxxWEBPfake')):
        result = stickerpacks.import_pack(con,'https://t.me/addstickers/Footbolika_Olya')
        assert result['total'] == 2
        assert stickerpacks.import_pack(con,'Footbolika_Olya')['added'] == 0
    catalog = stickerpacks.catalog(con)
    assert len(catalog['packs']) == 1 and len(catalog['packs'][0]['stickers']) == 2
    assert 'FAKE_TOKEN' not in str(catalog) and 'fake-static' not in str(catalog)
    sticker = catalog['packs'][0]['stickers'][0]['id']
    cid = con.execute("INSERT INTO conversations(channel,external_id,title,created_at) VALUES ('telegram','123','Пример','2026-11-10')").lastrowid
    with patch.object(comms,'tg_call',side_effect=tg) as call:
        comms.send_sticker(con,cid,sticker,{'name':'Пример'})
        assert call.call_args.args[1] == 'sendSticker' and call.call_args.args[2]['sticker'] == 'fake-static'
    con.execute("UPDATE conversations SET channel='wazzup_max',meta=? WHERE id=?", ('{"chatType":"max","channelId":"fake-max"}',cid))
    with patch.object(comms,'_wz_destination',return_value={'chatType':'max','channelId':'fake-max'}), patch.object(comms,'wz_call',return_value={'messageId':'fake-sent'}) as call:
        comms.send_sticker(con,cid,sticker,{'name':'Пример'})
        data=call.call_args.args[3]
        assert data['chatType'] == 'max' and '/api/sticker-image?t=' in data['contentUri'] and 'FAKE_TOKEN' not in data['contentUri']
    for link in ('https://example.invalid/addstickers/a','https://t.me/addstickers/../private','https://t.me/addstickers/a?token=x'):
        try:
            stickerpacks.pack_name(link)
        except comms.CommsError:
            pass
        else:
            raise AssertionError('Invalid link accepted')
    print('ok: import, animated preview, deduplication, no token in catalog, native TG, MAX image transport')


if __name__=='__main__':
    main()
