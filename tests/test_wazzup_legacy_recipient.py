"""Legacy phone rows must not send TG/MAX phone numbers as chat IDs."""
import base64
import json
import unittest
from unittest.mock import patch
from tests import test_wazzup_no_bridge as fixture
from crm import comms, chatattachments


class LegacyRecipientTest(unittest.TestCase):
    setUp = fixture.WazzupTest.setUp
    tearDown = fixture.WazzupTest.tearDown
    chat = fixture.WazzupTest.chat

    def legacy(self, channel):
        comms.put(self.con, 'wazzup_max_channel', 'test-max')
        comms.put(self.con, 'wazzup_channel', 'test-wa')
        return self.chat(channel, '79000000222', '79000000222')

    def test_text_sticker_file_and_caption_use_phone_in_both_transports(self):
        for channel in ('wazzup_telegram', 'wazzup_max'):
            with self.subTest(channel=channel):
                cid = self.legacy(channel)
                with patch.object(comms, 'wz_call', return_value={'messageId': channel + '-ack'}) as provider:
                    comms.send(self.con, cid, 'Synthetic question', self.user)
                    comms.send_sticker(self.con, cid, 'thanks', self.user)
                    item = chatattachments.upload(self.con, cid, self.user, 'test.pdf', base64.b64encode(b'%PDF-1.4 test').decode())
                    chatattachments.send(self.con, cid, item['id'], self.user, 'Synthetic caption')
                self.assertEqual(provider.call_count, 4)
                for call in provider.call_args_list:
                    body = call.args[3]
                    self.assertEqual(body['phone'], '79000000222')
                    self.assertNotIn('chatId', body)
                    self.assertEqual(body['chatType'], 'telegram' if channel.endswith('telegram') else 'max')
                    self.assertEqual(body['channelId'], 'test-tg' if channel.endswith('telegram') else 'test-max')

    def test_provider_ack_remembers_real_id_for_legacy_row(self):
        cid = self.legacy('wazzup_max')
        with patch.object(comms, 'wz_call', return_value={'messageId': 'ack', 'chatId': '29832608'}) as provider:
            comms.send(self.con, cid, 'Synthetic first', self.user)
            self.assertIn('phone', provider.call_args.args[3])
            comms.send(self.con, cid, 'Synthetic next', self.user)
            self.assertEqual(provider.call_args.args[3]['chatId'], '29832608')
            self.assertNotIn('phone', provider.call_args.args[3])

    def test_real_numeric_id_is_not_classified_by_length(self):
        cid = self.chat('wazzup_max', '79000000333', '79000000222')
        comms.put(self.con, 'wazzup_max_channel', 'test-max')
        with patch.object(comms, 'wz_call', return_value={'messageId': 'ack'}) as provider:
            comms.send(self.con, cid, 'Synthetic', self.user)
        self.assertEqual(provider.call_args.args[3]['chatId'], '79000000333')
        self.assertNotIn('phone', provider.call_args.args[3])

    def test_verified_incoming_id_is_preserved_even_if_equal_to_phone(self):
        cid = self.legacy('wazzup_max')
        comms.wz_hook(self.con, {'messages': [{'chatType': 'max', 'channelId': 'test-max', 'chatId': '79000000222', 'messageId': 'incoming-proof', 'text': 'Synthetic incoming', 'contact': {'phone': '79000000222'}}]})
        with patch.object(comms, 'wz_call', return_value={'messageId': 'ack'}) as provider:
            comms.send(self.con, cid, 'Synthetic reply', self.user)
        self.assertEqual(provider.call_args.args[3]['chatId'], '79000000222')
        self.assertNotIn('phone', provider.call_args.args[3])
        # Older verified inbound rows without the new metadata are also preserved.
        self.con.execute("UPDATE conversations SET meta='{}' WHERE id=?", (cid,))
        with patch.object(comms, 'wz_call', return_value={'messageId': 'ack-2'}) as provider:
            comms.send(self.con, cid, 'Synthetic reply again', self.user)
        self.assertEqual(provider.call_args.args[3]['chatId'], '79000000222')

    def test_whatsapp_still_uses_its_phone_based_chat_id(self):
        cid = self.legacy('whatsapp')
        with patch.object(comms, 'wz_call', return_value={'messageId': 'ack'}) as provider:
            comms.send(self.con, cid, 'Synthetic', self.user)
        self.assertEqual(provider.call_args.args[3]['chatId'], '79000000222')
        self.assertNotIn('phone', provider.call_args.args[3])

    def test_legacy_whatsapp_phone_prefix_uses_whatsapp_format(self):
        comms.put(self.con, 'wazzup_channel', 'test-wa')
        cid = self.chat('whatsapp', 'phone:79000000444', '79000000444')
        with patch.object(comms, 'wz_call', return_value={'messageId': 'ack'}) as provider:
            comms.send(self.con, cid, 'Synthetic', self.user)
        self.assertEqual(provider.call_args.args[3]['chatId'], '79000000444')
        self.assertNotIn('phone', provider.call_args.args[3])

    def test_contact_not_found_is_error_without_retry_or_fallback(self):
        cid = self.legacy('wazzup_max')
        with patch.object(comms, 'http', return_value=(400, {'error': 'POST_MESSAGE_ERROR', 'description': 'Cannot found contact by phone', 'requestId': 'synthetic12345678'})) as provider:
            with self.assertRaises(comms.CommsError) as caught:
                comms.send(self.con, cid, 'Synthetic', self.user)
        self.assertEqual(provider.call_count, 1)
        self.assertIn('MAX не нашёл контакт', str(caught.exception))
        self.assertIn('requestId=synthetic12345678', str(caught.exception))
        self.assertEqual(self.con.execute('SELECT status FROM messages WHERE conversation_id=?', (cid,)).fetchone()[0], 'error')

    def test_diagnostics_reports_effective_phone_mode(self):
        cid = self.legacy('wazzup_max')
        comms.add_message(self.con, cid, 'out', 'Synthetic', status='error', error='Wazzup: POST_MESSAGE_ERROR')
        with patch.object(comms, 'wz_call', return_value=[]):
            report = comms.wazzup_diagnostics(self.con)
        self.assertEqual(report['failures'][0]['recipient_mode'], 'phone')
        self.assertNotIn('79000000222', json.dumps(report))
