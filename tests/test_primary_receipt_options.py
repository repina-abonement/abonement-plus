import unittest
from unittest.mock import patch
from crm import primaryreceipt,fiscal,fiscalcheck,paykeepercheck,reconciliation
from tests.test_fiscal_payment_check import FiscalPaymentCheck


class PrimaryOptions(unittest.TestCase):
    setUp=FiscalPaymentCheck.setUp
    new=FiscalPaymentCheck.new
    payment=FiscalPaymentCheck.payment

    def options(self,order):
        return {'orderid':order,'fop_fn':'1234567890123456','fop_fnd':'123','fop_fpd':'1234567890',
                'fop_url':'t=20261118T121000&s=1350.00&fn=1234567890123456&i=123&fp=1234567890&n=1',
                'fop_receipt_key':'PRIVATE-RECEIPT-KEY','sbp_operationId':'synthetic-operation','sbp_trxId':'synthetic-transaction'}

    def test_qr_identity_amount_operation_and_order_must_all_agree(self):
        options=self.options('synthetic-order')
        payment={'available':True,'status':'obtained'}
        result=primaryreceipt.parse(options,payment,'synthetic-order',1350)
        self.assertEqual(result['status'],'fiscal_details')
        self.assertEqual(result['issued_at'],'2026-11-18T12:10:00')
        self.assertNotIn('PRIVATE-RECEIPT-KEY',str(result))
        for changed in ({'orderid':'wrong'}, {'fop_fpd':'different'}, {'fop_url':options['fop_url'].replace('n=1','n=2')},
                        {'fop_url':options['fop_url'].replace('1350.00','1351.00')}, {'fop_url':options['fop_url']+'&s=1350.00'}):
            self.assertEqual(primaryreceipt.parse(options|changed,payment,'synthetic-order',1350)['status'],'unverified')

    def test_primary_options_automatically_saved_and_ofd_remains_separate(self):
        pid=self.payment()
        order=self.con.execute("SELECT orderid FROM pay_links WHERE payment_id=?",(pid,)).fetchone()[0]
        def api(con,path,params):
            if path=='/info/payments/byid/':return [{'id':'synthetic-11534','orderid':order,'pay_amount':'1350','status':'obtained'}]
            if path=='/info/options/byid/':return [self.options(order)]
            if path=='/info/receipts/bypaymentid/':return []
            raise AssertionError('Only documented read endpoints')
        with patch.object(fiscal,'pk_call',side_effect=api) as request:
            result=fiscalcheck.check(self.con,pid,self.user)
            diagnostic=paykeepercheck.inspect(self.con,'synthetic-11534')
        self.assertEqual(result['status'],'fiscal_details')
        self.assertEqual(diagnostic['sbp_identifiers']['sbp_operationId'],'synthetic-operation')
        payment=self.con.execute('SELECT * FROM payments WHERE id=?',(pid,)).fetchone()
        self.assertNotEqual(reconciliation.payment_state(self.con,payment)['ofd']['status'],'confirmed')
        fiscal.store_ofd(self.con,[{'fiscalDriveNumber':'1234567890123456','fiscalDocumentNumber':'123','fiscalSign':'1234567890',
            'totalSum':135000,'operationType':1,'receiptDate':'2026-11-18 12:10:00'}])
        state=reconciliation.payment_state(self.con,payment)
        self.assertEqual(state['ofd']['status'],'confirmed')
        self.assertEqual(state['bank']['status'],'unverified')
        self.assertFalse(state['complete'])
        self.assertTrue(all('/info/' in call.args[1] for call in request.call_args_list))
        self.assertEqual(self.con.execute('SELECT count(*) FROM payments').fetchone()[0],1)

    def test_ofd_exact_read_and_unknown_schema_remain_distinct(self):
        primary=primaryreceipt.parse(self.options('order'),{'available':True,'status':'obtained'},'order',1350)
        fiscal.comms.put(self.con,'fx_ofd_token','synthetic-ofd-token')
        row={'fiscalDriveNumber':primary['fn'],'fiscalDocumentNumber':primary['fd'],'fiscalSign':primary['fp'],
             'totalSum':135000,'operationType':1}
        before=self.con.total_changes
        with patch.object(fiscal,'fetch_ofd_kkts',return_value=['synthetic-kkt']),patch.object(fiscal,'ofd_get',return_value={'receipts':[row]}):
            self.assertEqual(paykeepercheck.primary_ofd(self.con,primary)['status'],'confirmed')
        self.assertEqual(self.con.total_changes,before)
        with patch.object(fiscal,'fetch_ofd_kkts',return_value=['synthetic-kkt']),patch.object(fiscal,'ofd_get',return_value={'receiptList':[]}):
            self.assertEqual(paykeepercheck.primary_ofd(self.con,primary)['status'],'schema_unverified')

    def test_background_provider_wait_does_not_block_incoming_writer(self):
        from crm import db
        self.payment()
        path=self.con.execute('PRAGMA database_list').fetchone()[2]
        second=db.connect(path);self.addCleanup(second.close)
        second.execute('PRAGMA busy_timeout=50')
        writes=[]
        def api(con,path,params):
            second.execute("INSERT INTO hook_log(ts,source,payload) VALUES('2026-11-18','wazzup','{}')")
            writes.append(path)
            return []
        with patch.object(fiscal,'pk_call',side_effect=api):
            fiscal.run_worker_cycle(self.con)
        self.assertEqual(len(writes),3)
        self.assertFalse(self.con.in_transaction)
