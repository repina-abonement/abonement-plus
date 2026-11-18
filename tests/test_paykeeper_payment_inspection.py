import unittest
from unittest.mock import patch
from crm import fiscal,paykeepercheck
from tests.test_fiscal_payment_check import FiscalPaymentCheck


class PaymentInspection(unittest.TestCase):
    setUp=FiscalPaymentCheck.setUp
    new=FiscalPaymentCheck.new
    payment=FiscalPaymentCheck.payment

    def backend_data(self,path,params):
        if path=='/info/payments/byid/':
            order=self.con.execute("SELECT orderid FROM pay_links WHERE pk_payment_id='synthetic-11534'").fetchone()[0]
            return [{'id':'synthetic-11534','pay_amount':'1350.00','orderid':order,'status':'obtained',
                'RRN':'synthetic-rrn','unique_id':'synthetic-acquirer-id','clientid':'PRIVATE-CUSTOMER',
                'CARD_NUMBER':'PRIVATE-CARD','system_description':'Synthetic SBP'}]
        if path=='/info/receipts/bypaymentid/':return []
        if path=='/info/options/byid/':return [{'client_phone':'PRIVATE-PHONE','client_email':'PRIVATE-EMAIL','fop_receipt_key':'PRIVATE-RECEIPT-TOKEN'}]
        raise AssertionError('Unexpected endpoint')

    def test_exact_payment_empty_additional_receipts_not_missing_primary_no_writes(self):
        self.payment();before=self.con.total_changes
        with patch.object(fiscal,'pk_call',side_effect=lambda con,path,params:self.backend_data(path,params)) as api:
            r=paykeepercheck.inspect(self.con,'synthetic-11534')
        self.assertTrue(r['payment']['available']);self.assertEqual(r['payment']['RRN'],'synthetic-rrn')
        self.assertEqual(r['additional_receipts']['receipts'],[])
        self.assertEqual(r['primary_receipt']['status'],'unverified')
        self.assertEqual(r['bank_settlement']['status'],'unverified');self.assertEqual(r['ofd']['status'],'unverified')
        self.assertEqual(self.con.total_changes,before)
        self.assertEqual(len(api.call_args_list),3)
        self.assertTrue(all('/info/' in call.args[1] for call in api.call_args_list))
        for secret in ('PRIVATE-CUSTOMER','PRIVATE-CARD','PRIVATE-PHONE','PRIVATE-EMAIL','PRIVATE-RECEIPT-TOKEN'):
            self.assertNotIn(secret,str(r))

    def test_wrong_id_order_amount_or_source_does_not_confirm_payment(self):
        self.payment()
        original=self.backend_data
        for values in ({'id':'another-id'},{'orderid':'another-order'},{'pay_amount':'1351'}):
            def provider(con,path,params):
                raw=original(path,params)
                if path=='/info/payments/byid/':raw[0].update(values)
                return raw
            with patch.object(fiscal,'pk_call',side_effect=provider):
                self.assertFalse(paykeepercheck.inspect(self.con,'synthetic-11534')['payment']['available'])
        with patch.object(fiscal,'pk_call',side_effect=fiscal.FxError('PRIVATE-SOURCE-ERROR')):
            r=paykeepercheck.inspect(self.con,'synthetic-11534')
        self.assertNotIn('PRIVATE-SOURCE-ERROR',str(r))
        self.assertFalse(r['payment']['available'])

    def test_unknown_local_payment_never_queries_external_api(self):
        with patch.object(fiscal,'pk_call') as api:
            with self.assertRaises(Exception):paykeepercheck.inspect(self.con,'unlinked-provider-id')
        api.assert_not_called()

    def test_source_probes_only_read_and_do_not_disclose_accounts_or_credentials(self):
        fiscal.comms.put(self.con,'fx_tochka_token','PRIVATE-TOCHKA-TOKEN')
        fiscal.comms.put(self.con,'fx_ofd_token','PRIVATE-OFD-TOKEN')
        before=self.con.total_changes
        with patch.object(fiscal,'tochka_get',return_value={'Data':{'Account':[{'accountId':'PRIVATE-ACCOUNT'}]}}) as bank, patch.object(fiscal,'ofd_get',return_value={'kkts':[{'kktRegId':'PRIVATE-KKT'}]}) as ofd:
            r=paykeepercheck.source_status(self.con)
        self.assertTrue(r['tochka']['available']);self.assertEqual(r['tochka']['accounts_count'],1)
        self.assertTrue(r['ofd']['available']);self.assertEqual(r['ofd']['cash_registers_count'],1)
        self.assertEqual(bank.call_args.args[1:],('/open-banking/v1.0/accounts',))
        self.assertEqual(ofd.call_args.args[1:],('/kkts',))
        self.assertEqual(self.con.total_changes,before)
        for secret in ('PRIVATE-TOCHKA-TOKEN','PRIVATE-OFD-TOKEN','PRIVATE-ACCOUNT','PRIVATE-KKT'):
            self.assertNotIn(secret,str(r))
        self.assertFalse(r['automatic_reprint_enabled'])
