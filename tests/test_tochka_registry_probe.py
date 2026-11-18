import unittest
from unittest.mock import patch
from urllib.parse import urlsplit,parse_qs
from crm import fiscal,tochkacheck
from tests.test_wazzup_incoming_recovery import IncomingRecovery


class RegistryProbe(unittest.TestCase):
    def setUp(self):
        IncomingRecovery.setUp(self)
        probe=patch.object(fiscal,'fetch_tochka',return_value=[])
        probe.start();self.addCleanup(probe.stop)
    def payment(self):return {'available':True,'system_description':'SBP-TOCHKA','obtain_datetime':'2026-11-18 12:05:19','pay_amount':'1350'}
    def backend(self,con,path):
        if path=='/open-banking/v1.0/accounts':return {'Data':{'Account':[{'customerCode':'private-customer','accountId':'PRIVATE-ACCOUNT'}]}}
        if path.startswith('/acquiring/v1.0/retailers?'):return {'Data':{'Retailer':[{'merchantId':'private-merchant','name':'PRIVATE-NAME'}]}}
        if path.startswith('/acquiring/v1.0/registry?'):
            self.assertEqual(parse_qs(urlsplit(path).query)['date'],['2026-11-18'])
            return {'Data':{'Registry':[{'paymentType':'sbp','paymentId':'synthetic-trx','payments':[{
                'operationId':'synthetic-operation','amount':1350,'commission':'6.75','enrollmentAmount':'1343.25',
                'purpose':'PRIVATE-PURPOSE','paymentLink':'PRIVATE-LINK','status':'APPROVED','time':'2026-11-18T12:05:19+03:00'}]}]}}
        raise AssertionError('Unexpected endpoint')
    def test_exact_registry_ids_and_fee_do_not_prove_bank_posting(self):
        fiscal.comms.put(self.con,'fx_tochka_token','PRIVATE-TOKEN')
        before=self.con.total_changes
        with patch.object(fiscal,'tochka_get',side_effect=self.backend) as api:
            r=tochkacheck.inspect(self.con,self.payment(),{'sbp_trxId':'synthetic-trx'})
        self.assertEqual(api.call_count,3)
        self.assertEqual(r['registry_status'],'entry_found');self.assertEqual(r['status'],'unverified')
        self.assertEqual(r['registry_matches'][0]['fee_minor'],675)
        self.assertEqual(r['registry_matches'][0]['net_minor'],134325)
        self.assertTrue(r['registry_matches'][0]['totals_consistent'])
        self.assertEqual(self.con.total_changes,before)
        for secret in ('PRIVATE-ACCOUNT','PRIVATE-NAME','PRIVATE-PURPOSE','PRIVATE-LINK','PRIVATE-TOKEN','private-merchant','private-customer'):
            self.assertNotIn(secret,str(r))
    def test_same_amount_other_id_does_not_match(self):
        fiscal.comms.put(self.con,'fx_tochka_token','PRIVATE-TOKEN')
        with patch.object(fiscal,'tochka_get',side_effect=self.backend):
            r=tochkacheck.inspect(self.con,self.payment(),{'sbp_trxId':'different-trx'})
        self.assertEqual(r['registry_matches'],[])
    def test_permission_denied_reports_required_read_scope(self):
        fiscal.comms.put(self.con,'fx_tochka_token','PRIVATE-TOKEN')
        with patch.object(fiscal,'tochka_get',side_effect=fiscal.FxError('Read permission missing',http_status=403)):
            r=tochkacheck.inspect(self.con,self.payment(),{'sbp_trxId':'synthetic-trx'})
        self.assertEqual(r['http_status'],403)
        self.assertEqual(r['registry_status'],'permission_denied')
        self.assertEqual(r['required_permission'],'ReadAcquiringData')

    def test_bank_candidate_uses_long_exact_reference_without_claiming_settlement(self):
        fiscal.comms.put(self.con,'fx_tochka_token','PRIVATE-TOKEN')
        long_id='A6322090518524110G10070011890701'
        rows=[{'transactionId':'synthetic-bank-id','description':'СБП '+long_id+' получен',
            'status':'Booked','creditDebitIndicator':'Credit','Amount':{'amount':'1350','currency':'RUB'},'documentProcessDate':'2026-11-18'},
            {'transactionId':'unrelated-id','description':'операция 9191886',
            'status':'Booked','creditDebitIndicator':'Credit','Amount':{'amount':'1350','currency':'RUB'}}]
        with patch.object(fiscal,'tochka_get',side_effect=self.backend),patch.object(fiscal,'fetch_tochka',return_value=rows):
            r=tochkacheck.inspect(self.con,self.payment(),{'sbp_trxId':long_id,'sbp_operationId':'9191886'})
        self.assertEqual(r['statement_status'],'read')
        self.assertEqual(len(r['statement_candidates']),1)
        self.assertTrue(r['statement_candidates'][0]['booked_credit'])
        self.assertEqual(r['status'],'unverified')
        self.assertNotIn('СБП',str(r['statement_candidates']))
