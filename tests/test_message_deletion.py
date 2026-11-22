"""Remote deletion must target one stored message, preserve audit and avoid retries."""
import tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta
from unittest.mock import patch
from crm import db,comms,messagedeletion as deletion,server

class DeleteTest(unittest.TestCase):
    def setUp(self):
        self.con=db.connect(str(Path(tempfile.mkdtemp())/'delete.sqlite3'));db.init_db(self.con)
        self.user=dict(self.con.execute("SELECT * FROM users WHERE role='admin'").fetchone())
        comms.put(self.con,'wazzup_key','FAKE_KEY')
        self.cid=comms.upsert_conversation(self.con,'wazzup_telegram','12345',meta={'channelId':'test-tg','chatType':'telegram'})
        self.mid=comms.add_message(self.con,self.cid,'out','Synthetic text',external_id='remote-message-1')
    def tearDown(self):self.con.close()
    def test_delete_calls_exact_remote_message_once_and_waits_for_confirmation(self):
        with patch.object(comms,'wz_call',return_value={}) as provider:
            result=deletion.delete(self.con,self.mid,self.user);again=deletion.delete(self.con,self.mid,self.user)
        self.assertTrue(result['ok']);self.assertEqual(result['state'],'requested');self.assertTrue(again['already_requested'])
        provider.assert_called_once_with(self.con,'DELETE','message/remote-message-1')
        self.assertEqual(self.con.execute('SELECT text FROM messages WHERE id=?',(self.mid,)).fetchone()[0],'Synthetic text')
        self.assertEqual(self.con.execute("SELECT count(*) FROM audit WHERE action='message.delete_request'").fetchone()[0],1)
        self.assertIn('запрошено',self.con.execute('SELECT last_text FROM conversations WHERE id=?',(self.cid,)).fetchone()[0])
    def test_error_never_hides_content_or_repeats_uncertain_request(self):
        with patch.object(comms,'wz_call',side_effect=comms.CommsError('network timeout')) as provider:
            result=deletion.delete(self.con,self.mid,self.user);again=deletion.delete(self.con,self.mid,self.user)
        self.assertFalse(result['ok']);self.assertEqual(result['state'],'uncertain');self.assertFalse(again['ok']);self.assertEqual(provider.call_count,1)
        row=comms.conversation(self.con,self.cid,False)['history'][0]
        self.assertEqual(row['text'],'Synthetic text');self.assertFalse(row['can_delete']);self.assertEqual(row['deletion_status'],'uncertain')
    def test_incoming_old_missing_id_demo_unsupported_and_coach_never_call_provider(self):
        with patch.object(comms,'wz_call') as provider:
            for field,value in [('direction','in'),('ts',(datetime.now()-timedelta(hours=25)).isoformat()),('external_id',None),('status','demo'),('status','error')]:
                self.con.execute(f'UPDATE messages SET {field}=? WHERE id=?',(value,self.mid))
                with self.assertRaises(comms.CommsError):deletion.delete(self.con,self.mid,self.user)
                self.con.execute("UPDATE messages SET direction='out',ts=?,external_id='remote-message-1',status='' WHERE id=?",(comms.ts_now(),self.mid))
            with self.assertRaises(comms.CommsError):deletion.delete(self.con,self.mid,{'id':99,'role':'coach'})
            self.con.execute("UPDATE conversations SET channel='tg_archive' WHERE id=?",(self.cid,))
            with self.assertRaises(comms.CommsError):deletion.delete(self.con,self.mid,self.user)
        provider.assert_not_called()
        route=next(r for r in server.ROUTES if r[0]=='DELETE' and r[1].pattern.startswith('^/api/messages/'))
        self.assertEqual(route[3],'inbox')
    def test_webhook_confirmation_is_scoped_to_conversation_and_survives_later_echo(self):
        wrong=comms.upsert_conversation(self.con,'wazzup_telegram','99999',meta={'channelId':'test-tg','chatType':'telegram'})
        deletion.confirmed(self.con,wrong,'remote-message-1')
        self.assertEqual(self.con.execute('SELECT count(*) FROM message_deletions').fetchone()[0],0)
        payload={'chatType':'telegram','chatId':'12345','channelId':'test-tg','messageId':'remote-message-1','isEcho':True,'isDeleted':True}
        comms.wz_hook(self.con,{'messages':[payload]});comms.wz_hook(self.con,{'messages':[payload|{'isDeleted':False,'text':'Old echo'}]})
        self.assertEqual(self.con.execute('SELECT count(*) FROM messages').fetchone()[0],1)
        row=comms.conversation(self.con,self.cid,False)['history'][0]
        self.assertEqual(row['deletion_status'],'deleted');self.assertFalse(row['can_delete'])
    def test_chat_merge_preserves_confirmed_deletion_when_deduplicating(self):
        other=comms.upsert_conversation(self.con,'wazzup_telegram','phone:79000000111',meta={'channelId':'test-tg','chatType':'telegram'})
        duplicate=comms.add_message(self.con,other,'out','Echo',external_id='remote-message-1')
        deletion.confirmed(self.con,other,'remote-message-1')
        comms.merge_conversations(self.con,other,self.cid)
        self.assertEqual(self.con.execute('SELECT count(*) FROM messages').fetchone()[0],1)
        self.assertEqual(comms.conversation(self.con,self.cid,False)['history'][0]['deletion_status'],'deleted')

    def test_all_three_wazzup_channels_expose_capability(self):
        for channel in comms.WZ_ROUTE:
            self.con.execute('UPDATE conversations SET channel=? WHERE id=?',(channel,self.cid))
            self.assertTrue(comms.conversation(self.con,self.cid,False)['history'][0]['can_delete'])

    def test_failed_unsent_message_is_removed_locally_without_provider_call(self):
        failed=comms.add_message(self.con,self.cid,'out','Never delivered',status='error',error='provider rejected')
        row=comms.conversation(self.con,self.cid,False)['history'][-1]
        self.assertTrue(row['can_delete']);self.assertTrue(row['local_delete'])
        with patch.object(comms,'wz_call') as provider:
            result=deletion.delete(self.con,failed,self.user)
        self.assertEqual(result,{'ok':True,'state':'deleted','local':True});provider.assert_not_called()
        self.assertIsNone(self.con.execute('SELECT id FROM messages WHERE id=?',(failed,)).fetchone())
        self.assertEqual(self.con.execute('SELECT last_text FROM conversations WHERE id=?',(self.cid,)).fetchone()[0],'Вы: Synthetic text')
        self.assertEqual(self.con.execute("SELECT count(*) FROM audit WHERE action='message.delete_local'").fetchone()[0],1)

if __name__=='__main__':unittest.main()
