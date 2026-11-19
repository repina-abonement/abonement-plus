import unittest
from unittest.mock import patch
from crm import fiscal

class OfdAccessErrors(unittest.TestCase):
    def test_unpaid_kkt_is_not_reported_as_bad_token(self):
        with patch.object(fiscal,'cfg',return_value=''),patch.object(fiscal,'ssl_ctx',return_value=None),patch.object(fiscal.comms,'http',return_value=(403,{'message':'Kkt is not paid'})):
            with self.assertRaises(fiscal.FxError) as caught:fiscal.ofd_get(None,'/receipts')
        self.assertIn('не оплачен или не активирован',str(caught.exception))
        self.assertEqual(caught.exception.http_status,403)
        self.assertEqual(caught.exception.provider_message,'Kkt is not paid')

    def test_other_forbidden_does_not_invent_payment_requirement(self):
        with patch.object(fiscal,'cfg',return_value=''),patch.object(fiscal,'ssl_ctx',return_value=None),patch.object(fiscal.comms,'http',return_value=(403,{'message':'Forbidden'})):
            with self.assertRaises(fiscal.FxError) as caught:fiscal.ofd_get(None,'/receipts')
        self.assertIn('доступ к данным запрещён',str(caught.exception))
        self.assertNotIn('не оплачен',str(caught.exception))
