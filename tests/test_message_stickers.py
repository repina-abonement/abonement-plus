"""Provider payloads, persistence and rejected sticker IDs, with no real sends."""
import os
import tempfile
from unittest.mock import patch
os.environ['CRM_DB'] = os.path.join(tempfile.mkdtemp(), 'stickers.sqlite3')
os.environ['CRM_ADMIN_PASSWORD'] = 'test-password-123'
os.environ['CRM_QUIET'] = '1'
from crm import db, comms

def main():
    con = db.connect()
    db.init_db(con)
    user = {'name': 'Тест'}
    for channel in ('telegram', 'max', 'wazzup_max', 'whatsapp'):
        cid = comms.upsert_conversation(con, channel, '12345', meta={'channelId':'test-channel','chatType':'max' if channel=='wazzup_max' else channel})
        with patch.object(comms, 'tg_call', return_value={'message_id': 123}) as tg, patch.object(comms, 'max_call', return_value={'message': {'body': {'mid':'test-max'}}}) as mx, patch.object(comms, 'wz_call', return_value={'messageId':'test-wz-'+channel}) as wz:
            r = comms.send_sticker(con, cid, 'thanks', user)
            msg = con.execute('SELECT * FROM messages WHERE id=?', (r['id'],)).fetchone()
            assert msg['kind'] == 'image' and msg['media'].endswith('/1f64f.png')
            if channel == 'telegram':
                assert tg.call_args.args[1] == 'sendPhoto'
            elif channel == 'max':
                assert mx.call_args.kwargs['data']['attachments'][0]['type'] == 'image'
            else:
                payload = wz.call_args.args[3]
                assert 'contentUri' in payload and 'text' not in payload
                assert payload['channelId'] == 'test-channel'
    try:
        comms.send_sticker(con, cid, 'https://untrusted.example/file', user)
        raise AssertionError('Unknown sticker accepted')
    except comms.CommsError:
        pass
    with patch.object(comms, 'wz_call', side_effect=comms.CommsError('Test failure')):
        try:
            comms.send_sticker(con, cid, 'smile', user)
            raise AssertionError('Provider error hidden')
        except comms.CommsError:
            assert con.execute('SELECT status FROM messages ORDER BY id DESC LIMIT 1').fetchone()[0] == 'error'
    print('Sticker payloads and failures: OK')
if __name__ == '__main__':
    main()
