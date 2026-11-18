import unittest
from crm import comms,wazzuptrace
from tests.test_wazzup_incoming_recovery import IncomingRecovery


class MessageTrace(unittest.TestCase):
    setUp=IncomingRecovery.setUp

    def trace(self):
        return wazzuptrace.trace(self.con,'synthetic-incoming-id','synthetic-max-chat','synthetic-max-channel')

    def test_exact_failed_message_found_and_eligible_for_saved_replay(self):
        comms.log_hook(self.con,'wazzup',self.payload,'ValueError: synthetic failure')
        before=self.con.total_changes
        r=self.trace()
        self.assertEqual(r['state'],'event_found_not_stored')
        self.assertEqual(r['incoming_message_events'],1)
        self.assertTrue(r['events'][0]['failed'])
        self.assertEqual(len(r['replay_hook_ids']),1)
        self.assertEqual(self.con.total_changes,before)
        self.assertNotIn('Synthetic incoming message',str(r))
        self.assertNotIn('79000007942',str(r))

    def test_existing_message_does_not_clear_unread_or_offer_duplicate_import(self):
        comms.wz_hook(self.con,self.payload);comms.log_hook(self.con,'wazzup',self.payload)
        before=self.con.total_changes
        r=self.trace()
        self.assertEqual(r['state'],'stored')
        self.assertTrue(r['messages'][0]['in_current_history'])
        self.assertEqual(r['messages'][0]['direction'],'in')
        self.assertEqual(r['replay_hook_ids'],[])
        self.assertEqual(self.con.total_changes,before)
        self.assertEqual(self.con.execute('SELECT unread FROM conversations').fetchone()[0],1)

    def test_echo_status_or_other_chat_cannot_be_replayed_as_missing_incoming(self):
        self.payload['messages'][0]['isEcho']=True
        comms.log_hook(self.con,'wazzup',self.payload)
        r=self.trace()
        self.assertEqual(r['incoming_message_events'],0)
        self.assertEqual(r['outgoing_echo_events'],1)
        self.assertEqual(r['replay_hook_ids'],[])
        self.payload['messages'][0]['isEcho']=False
        self.payload['messages'][0]['chatId']='another-chat'
        comms.log_hook(self.con,'wazzup',self.payload)
        self.assertEqual(self.trace()['replay_hook_ids'],[])
        comms.log_hook(self.con,'wazzup',{'statuses':[{'messageId':'synthetic-incoming-id','status':'sent'}]})
        self.assertEqual(self.trace()['events'][0]['category'],'statuses')

    def test_missing_or_truncated_log_never_proves_no_webhook(self):
        r=self.trace()
        self.assertEqual(r['state'],'not_observed_in_retained_log')
        self.assertIn('не доказывает',r['scope'])
        self.con.execute("INSERT INTO hook_log(ts,source,payload) VALUES('2026-11-18','wazzup',?)",('{"messageId":"synthetic-incoming-id",',))
        r=self.trace()
        self.assertEqual(len(r['unreadable_candidates']),1)
        self.assertEqual(r['replay_hook_ids'],[])

    def test_ui_candidate_can_differ_from_actual_provider_id(self):
        self.payload['messages'][0]['messageId']='actual-provider-id'
        comms.log_hook(self.con,'wazzup',self.payload)
        r=self.trace()
        self.assertEqual(r['state'],'not_observed_in_retained_log')
        self.assertEqual(r['chat_events'][0]['message_id'],'actual-provider-id')
        self.assertFalse(r['chat_events'][0]['is_echo'])
        self.assertEqual(r['replay_hook_ids'],[])

    def test_webhook_status_reads_official_settings_without_exposing_secret(self):
        from unittest.mock import patch
        comms.put(self.con,'wazzup_key','SYNTHETIC-API-SECRET')
        raw={'webhooksUri':'https://ab-plus.ru/hooks/wazzup/synthetic-hook-token',
             'subscriptions':{'messagesAndStatuses':'true'}}
        before=self.con.total_changes
        with patch.object(comms,'wz_call',return_value=raw) as api:
            result=wazzuptrace.webhook_status(self.con,'https://ab-plus.ru')
        self.assertEqual(api.call_args.args[1:],('GET','webhooks'))
        self.assertTrue(result['destination_matches']);self.assertTrue(result['messages_and_statuses'])
        self.assertEqual(self.con.total_changes,before)
        self.assertNotIn('synthetic-hook-token',str(result));self.assertNotIn('SYNTHETIC-API-SECRET',str(result))
        raw['webhooksUri']='https://old.example/hooks/wazzup/old-secret'
        raw['subscriptions']['messagesAndStatuses']=False
        with patch.object(comms,'wz_call',return_value=raw):
            result=wazzuptrace.webhook_status(self.con,'https://ab-plus.ru')
        self.assertFalse(result['destination_matches']);self.assertFalse(result['messages_and_statuses'])
        self.assertNotIn('old-secret',str(result))

    def test_webhook_status_bad_source_is_unknown_not_disabled(self):
        from unittest.mock import patch
        comms.put(self.con,'wazzup_key','SYNTHETIC-API-SECRET')
        with patch.object(comms,'wz_call',side_effect=comms.CommsError('SYNTHETIC-API-SECRET')):
            result=wazzuptrace.webhook_status(self.con,'https://ab-plus.ru')
        self.assertFalse(result['available'])
        self.assertNotIn('messages_and_statuses',result)
        self.assertNotIn('SYNTHETIC-API-SECRET',str(result))
