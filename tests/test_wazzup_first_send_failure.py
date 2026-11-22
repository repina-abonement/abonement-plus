"""Keep rejected first-message attempts without claiming delivery or retrying."""
from unittest.mock import patch
from tests.test_wazzup_no_bridge import WazzupTest
from crm import comms, db, server


class FirstSendFailureTest(WazzupTest):
    def test_failed_first_whatsapp_attempt_commits_diagnostic_and_correct_payload(self):
        comms.put(self.con, 'wazzup_channel', 'test-wa')
        parent = self.con.execute('SELECT id FROM parents WHERE family_id=?', (self.fid,)).fetchone()[0]
        text = 'Здравствуйте! Подскажите, пожалуйста, сколько лет вашему чемпиону? ⚽️'
        self.con.commit()
        with self.con, patch.object(comms, 'wz_call', side_effect=comms.CommsError('Wazzup: POST_MESSAGE_ERROR · requestId=synthetic123456')) as provider:
            result = server.family_whatsapp(self.con, None, self.user, {'parent_id': parent, 'channel': 'whatsapp', 'text': text}, {}, str(self.fid))
            self.assertIsInstance(result, server.JsonResponse)
            self.assertEqual(result.status, 400)
        self.assertEqual(provider.call_count, 1)
        payload = provider.call_args.args[3]
        self.assertEqual(payload['channelId'], 'test-wa')
        self.assertEqual(payload['chatType'], 'whatsapp')
        self.assertEqual(payload['chatId'], '79000000111')
        self.assertEqual(payload['text'], text)
        self.assertNotIn('contentUri', payload)
        with db.connect(self.path) as check:
            row = check.execute('SELECT status,error,user_name FROM messages WHERE conversation_id=?', (result.body['conversation_id'],)).fetchone()
            self.assertEqual(row['status'], 'error')
            self.assertIn('synthetic123456', row['error'])
            self.assertEqual(row['user_name'], self.user['name'])
        self.assertEqual(self.con.execute("SELECT count(*) FROM messages WHERE status='sent'").fetchone()[0], 0)

    def test_successful_first_send_keeps_existing_response_shape(self):
        comms.put(self.con, 'wazzup_channel', 'test-wa')
        parent = self.con.execute('SELECT id FROM parents WHERE family_id=?', (self.fid,)).fetchone()[0]
        with patch.object(comms, 'wz_call', return_value={'messageId': 'synthetic-ack'}):
            result = server.family_whatsapp(self.con, None, self.user, {'parent_id': parent, 'channel': 'whatsapp', 'text': 'Synthetic'}, {}, str(self.fid))
        self.assertIsInstance(result, dict)
        self.assertIn('conversation_id', result)
        self.assertEqual(self.con.execute("SELECT external_id FROM messages WHERE id=?", (result['id'],)).fetchone()[0], 'synthetic-ack')

    def test_same_browser_attempt_cannot_resend_after_unconfirmed_failure(self):
        comms.put(self.con, 'wazzup_channel', 'test-wa')
        parent = self.con.execute('SELECT id FROM parents WHERE family_id=?', (self.fid,)).fetchone()[0]
        body={'parent_id':parent,'channel':'whatsapp','text':'Synthetic guarded message','request_id':'requestguard1234567890'}
        with patch.object(comms,'wz_call',side_effect=comms.CommsError('Wazzup did not confirm')) as provider:
            first=server.family_whatsapp(self.con,None,self.user,body,{},str(self.fid))
            second=server.family_whatsapp(self.con,None,self.user,body,{},str(self.fid))
        self.assertEqual(first.status,400)
        self.assertEqual(second.status,400)
        self.assertIn('Повтор этой попытки заблокирован',second.body['error'])
        self.assertEqual(provider.call_count,1)
        self.assertEqual(self.con.execute("SELECT count(*) FROM messages WHERE text='Synthetic guarded message'").fetchone()[0],1)

    def test_same_confirmed_request_returns_existing_message_without_resend(self):
        comms.put(self.con, 'wazzup_channel', 'test-wa')
        parent = self.con.execute('SELECT id FROM parents WHERE family_id=?', (self.fid,)).fetchone()[0]
        body={'parent_id':parent,'channel':'whatsapp','text':'Synthetic confirmed message','request_id':'requestsent123456789012'}
        with patch.object(comms,'wz_call',return_value={'messageId':'one-provider-message'}) as provider:
            first=server.family_whatsapp(self.con,None,self.user,body,{},str(self.fid))
            second=server.family_whatsapp(self.con,None,self.user,body,{},str(self.fid))
        self.assertEqual(first['id'],second['id'])
        self.assertTrue(second['already_sent'])
        self.assertEqual(provider.call_count,1)
        self.assertEqual(self.con.execute("SELECT count(*) FROM messages WHERE external_id='one-provider-message'").fetchone()[0],1)
