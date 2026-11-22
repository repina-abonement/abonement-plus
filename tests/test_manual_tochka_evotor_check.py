import unittest
from datetime import datetime
from unittest.mock import patch

from crm import fiscal, incomingcontrol, manualpaymentcheck, money, reconciliation
from tests import test_paylinks as fixture


class ManualTochkaEvotorCheck(unittest.TestCase):
    setUp = fixture.PayLinksTest.setUp

    def payment(self, amount=9450):
        return money.add_payment(self.con, self.sid, amount, 'tochka_qr_link', self.user,
                                 paid_at='2026-11-20T15:30')

    def evidence(self, amount=9450, operation='bank-1', fd='42'):
        current = datetime(2026, 11, 20, 16)
        event = incomingcontrol.save_event(self.con, 'tochka', 'account-1', operation, '2026-11-20', amount*100, 'RUB',
            {'payer':'Синтетический родитель','purpose':'Оплата занятий','suggested_kind':'income'}, current)
        fiscal.store_ofd(self.con, [{'fiscalDriveNumber':'fn-1','fiscalDocumentNumber':fd,'fiscalSign':'fp-1',
            'totalSum':amount*100,'ecashTotalSum':amount*100,'operationType':1,'receiptDate':'2026-11-20 15:35:00'}])
        receipt = self.con.execute("SELECT id FROM fx_receipts WHERE source='ofd' AND fiscal_fd=?", (fd,)).fetchone()['id']
        return event, receipt

    def test_amount_candidates_require_explicit_pair_then_confirm_both(self):
        pid = self.payment(); event, receipt = self.evidence()
        before = manualpaymentcheck.candidates(self.con, pid, self.user)
        self.assertEqual(len(before['bank_candidates']), 1)
        self.assertEqual(len(before['receipt_candidates']), 1)
        self.assertEqual(before['state']['status'], 'unverified')
        result = manualpaymentcheck.bind(self.con, pid, {'bank_event_id':event,'receipt_id':receipt}, self.user)
        self.assertEqual(result['status'], 'confirmed')
        payment = self.con.execute('SELECT * FROM payments WHERE id=?', (pid,)).fetchone()
        self.assertEqual(money.payment_receipt_info(self.con, payment)['status'], 'confirmed')
        chain = reconciliation.payment_state(self.con, payment)
        self.assertEqual(chain['bank']['status'], 'confirmed')
        self.assertEqual(chain['ofd']['status'], 'confirmed')
        self.assertTrue(chain['complete'])

    def test_wrong_amount_refund_and_reuse_are_rejected(self):
        pid = self.payment(); event, receipt = self.evidence(amount=9451)
        self.assertFalse(manualpaymentcheck.candidates(self.con, pid, self.user)['bank_candidates'])
        with self.assertRaises(Exception):
            manualpaymentcheck.bind(self.con, pid, {'bank_event_id':event,'receipt_id':receipt}, self.user)
        event, receipt = self.evidence(operation='bank-2', fd='43')
        manualpaymentcheck.bind(self.con, pid, {'bank_event_id':event,'receipt_id':receipt}, self.user)
        other = self.payment()
        with self.assertRaises(Exception):
            manualpaymentcheck.bind(self.con, other, {'bank_event_id':event,'receipt_id':receipt}, self.user)

    def test_refresh_reads_sources_but_never_creates_money_or_receipts(self):
        pid = self.payment(); count = self.con.execute('SELECT count(*) FROM payments').fetchone()[0]
        tx = {'transactionId':'bank-live','documentProcessDate':'2026-11-20','status':'Booked','creditDebitIndicator':'Credit',
              'Amount':{'amount':'9450','currency':'643'},'DebtorParty':{'name':'Родитель'},'description':'Оплата'}
        rec = {'fiscalDriveNumber':'fn-live','fiscalDocumentNumber':'44','fiscalSign':'fp-live','totalSum':945000,
               'ecashTotalSum':945000,'operationType':1,'receiptDate':'2026-11-20 15:40:00'}
        with patch.object(fiscal,'fetch_tochka',return_value=[tx]), patch.object(fiscal,'fetch_ofd_receipts',return_value=[rec]):
            result = manualpaymentcheck.refresh(self.con, pid, self.user)
        self.assertEqual(len(result['bank_candidates']), 1)
        self.assertEqual(len(result['receipt_candidates']), 1)
        self.assertEqual(self.con.execute('SELECT count(*) FROM payments').fetchone()[0], count)
        self.assertEqual(self.con.execute('SELECT count(*) FROM manual_payment_evidence').fetchone()[0], 0)


if __name__ == '__main__': unittest.main()
