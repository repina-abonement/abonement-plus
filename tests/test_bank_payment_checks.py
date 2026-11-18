import unittest
from datetime import datetime
from unittest.mock import patch
from crm import bankcheck, fiscal, paykeepercheck, reconciliation, db
from tests.test_fiscal_payment_check import FiscalPaymentCheck


class AutomaticBankChecks(unittest.TestCase):
    setUp=FiscalPaymentCheck.setUp
    new=FiscalPaymentCheck.new
    payment=FiscalPaymentCheck.payment

    def configured(self):
        fiscal.comms.put(self.con,'fx_tochka_token','synthetic-token')

    def test_automatic_retry_preserves_payment_and_explains_missing_permission(self):
        pid=self.payment();self.configured()
        denied={'status':'unverified','registry_status':'permission_denied','http_status':403}
        with patch.object(paykeepercheck,'inspect',return_value={'bank_settlement':denied}) as api:
            self.assertEqual(bankcheck.run_automatic(self.con,datetime(2026,11,18,14))['checked'],1)
            self.assertEqual(bankcheck.run_automatic(self.con,datetime(2026,11,18,14,1))['checked'],0)
            self.assertEqual(bankcheck.run_automatic(self.con,datetime(2026,11,18,14,11))['checked'],1)
        self.assertEqual(api.call_count,2)
        self.assertEqual(api.call_args.kwargs,{'include_bank':True,'include_ofd':False})
        payment=self.con.execute('SELECT * FROM payments WHERE id=?',(pid,)).fetchone()
        state=reconciliation.payment_state(self.con,payment)
        self.assertIn('ReadAcquiringData',state['bank']['note'])
        self.assertEqual(state['bank']['checked_at'],'2026-11-18T14:11:00')
        self.assertFalse(state['complete'])
        self.assertEqual(payment['amount'],1350)
        self.assertEqual(self.con.execute('SELECT count(*) FROM payments').fetchone()[0],1)

    def test_provider_wait_does_not_lock_database_and_failure_retries(self):
        pid=self.payment();self.configured()
        second=db.connect(self.con.execute('PRAGMA database_list').fetchone()[2]);self.addCleanup(second.close)
        second.execute('PRAGMA busy_timeout=50')
        def inspect(*args,**kwargs):
            self.assertFalse(self.con.in_transaction)
            second.execute("INSERT INTO hook_log(ts,source,payload) VALUES('2026-11-18','wazzup','{}')")
            raise ValueError('Synthetic failure')
        with patch.object(paykeepercheck,'inspect',side_effect=inspect):
            self.assertEqual(bankcheck.run_automatic(self.con,datetime(2026,11,18,14))['checked'],1)
        self.assertEqual(bankcheck.state(self.con,pid)['status'],'unverified')
        with patch.object(paykeepercheck,'inspect',return_value={'bank_settlement':{'statement_status':'read','statement_candidates':[{'amount_minor':135000}]}}):
            bankcheck.run_automatic(self.con,datetime(2026,11,18,14,11))
        self.assertEqual(bankcheck.state(self.con,pid)['status'],'unverified')
        self.assertIn('требуют подтверждения',bankcheck.state(self.con,pid)['note'])
        self.con.execute("UPDATE pay_links SET pk_payment_id='corrected'")
        self.assertIsNone(bankcheck.state(self.con,pid))

    def test_missing_credentials_and_manual_unlinked_payment_are_not_guessed(self):
        from crm import money
        pid=money.add_payment(self.con,self.sid,1350,'paykeeper_manual',self.user)
        with patch.object(paykeepercheck,'inspect') as api:
            self.assertFalse(bankcheck.run_automatic(self.con)['configured'])
            self.configured()
            self.assertEqual(bankcheck.run_automatic(self.con)['checked'],0)
            api.assert_not_called()
        self.assertIsNone(bankcheck.state(self.con,pid))
