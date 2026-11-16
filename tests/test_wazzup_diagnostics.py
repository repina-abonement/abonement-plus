import json
from unittest.mock import patch
from tests.test_wazzup_no_bridge import WazzupTest
from crm import comms

class DiagnosticsTest(WazzupTest):
    def test_read_only_diagnostics_exclude_customer_data_and_keys(self):
        comms.add_message(self.con,self.wz,'out','Private message for synthetic customer',status='error',error='Wazzup: POST_MESSAGE_ERROR 79000000111 Secret Name FAKE_WZ_KEY requestId=abc123456789')
        before=self.con.total_changes
        with patch.object(comms,'wz_call',return_value=[{'channelId':'test-tg','transport':'tgapi','state':'active','plainId':'79000000111'}]) as provider:
            report=comms.wazzup_diagnostics(self.con)
        self.assertEqual(self.con.total_changes,before)
        self.assertEqual(provider.call_args.args[1:],('GET','channels'))
        encoded=json.dumps(report)
        for secret in ('79000000111','Secret Name','FAKE_WZ_KEY','Private message'):
            self.assertNotIn(secret,encoded)
        self.assertEqual(report['failures'][0]['codes'],['POST_MESSAGE_ERROR'])
        self.assertEqual(report['failures'][0]['request_id'],'abc123456789')
        self.assertEqual(report['missing_selected'],0)
    def test_request_id_retained_and_provider_failure_is_safe(self):
        with patch.object(comms,'http',return_value=(400,{'error':'POST_MESSAGE_ERROR','requestId':'abc123456789','description':'provider rejected'})):
            with self.assertRaises(comms.CommsError) as caught:comms.wz_call(self.con,'POST','message',{})
        self.assertIn('requestId=abc123456789',str(caught.exception))
        with patch.object(comms,'wz_call',side_effect=comms.CommsError('FAKE_WZ_KEY 79000000111')):
            report=comms.wazzup_diagnostics(self.con)
        self.assertNotIn('FAKE_WZ_KEY',json.dumps(report));self.assertNotIn('79000000111',json.dumps(report))
