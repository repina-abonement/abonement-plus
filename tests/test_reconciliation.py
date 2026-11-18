import unittest
from datetime import datetime
from crm import fiscal, reconciliation
from tests.test_fiscal_payment_check import FiscalPaymentCheck


class ExactReceiptChain(unittest.TestCase):
    setUp = FiscalPaymentCheck.setUp
    new = FiscalPaymentCheck.new
    payment = FiscalPaymentCheck.payment

    def seed(self, **changes):
        row = dict(fiscalDriveNumber='fn-123', fiscalDocumentNumber='42', fiscalSign='12345',
                   totalSum=135000, ecashTotalSum=135000, operationType=1,
                   receiptDate='2026-11-18 12:06:00')
        row.update(changes)
        fiscal.store_ofd(self.con,[row])

    def seed_provider_receipt(self):
        fiscal.store_pk(self.con,[],[dict(id='receipt',payment_id='synthetic-11534',status='success',
            fn='fn-123',fnd='42',fpd='12345',sum_cashless='1350',type='sale',obtain_datetime='2026-11-18 12:06:00')],datetime(2026,11,18,12,6))

    def test_full_identity_confirms_ofd_but_never_bank(self):
        pid=self.payment();self.seed_provider_receipt();self.seed()
        state=reconciliation.payment_state(self.con,self.con.execute('SELECT * FROM payments WHERE id=?',(pid,)).fetchone())
        self.assertEqual(state['ofd']['status'],'confirmed')
        self.assertEqual(state['bank']['status'],'unverified')
        self.assertFalse(state['complete'])

    def test_equal_amount_or_partial_identity_never_confirms(self):
        pid=self.payment();self.seed_provider_receipt()
        for change in ({'fiscalSign':'different'},{'fiscalSign':''},{'fiscalDocumentNumber':'43'},
                       {'fiscalDriveNumber':'other'},{'totalSum':135001},{'operationType':2}):
            self.con.execute("DELETE FROM fx_receipts WHERE source='ofd'")
            self.seed(**change)
            state=reconciliation.payment_state(self.con,self.con.execute('SELECT * FROM payments WHERE id=?',(pid,)).fetchone())
            self.assertNotEqual(state['ofd']['status'],'confirmed',change)

    def test_other_payment_and_legacy_cache_do_not_prove_ofd(self):
        pid=self.payment();self.seed_provider_receipt();self.seed()
        self.con.execute("UPDATE fx_receipts SET pk_payment='different' WHERE source='paykeeper'")
        payment=self.con.execute('SELECT * FROM payments WHERE id=?',(pid,)).fetchone()
        self.assertNotEqual(reconciliation.payment_state(self.con,payment)['ofd']['status'],'confirmed')
        self.con.execute("UPDATE fx_receipts SET pk_payment='synthetic-11534',fiscal_fp='' WHERE source='paykeeper'")
        self.assertNotEqual(reconciliation.payment_state(self.con,payment)['ofd']['status'],'confirmed')

    def test_idempotent_refresh_and_source_outage_preserve_evidence(self):
        pid=self.payment();self.seed_provider_receipt();self.seed();self.seed()
        self.assertEqual(self.con.execute("SELECT count(*) FROM fx_receipts WHERE source='ofd'").fetchone()[0],1)
        self.con.execute("INSERT INTO fx_runs(ts,source,ok) VALUES('2026-11-18 12:20:00','ofd',0)")
        state=reconciliation.payment_state(self.con,self.con.execute('SELECT * FROM payments WHERE id=?',(pid,)).fetchone())
        self.assertEqual(state['ofd']['status'],'confirmed')
        self.assertFalse(state['ofd']['source_available'])
        self.assertEqual(state['ofd']['source_checked_at'],'2026-11-18 12:20:00')

    def test_automatic_history_reads_receipt_day_and_retries_without_button(self):
        from unittest.mock import patch
        self.payment();self.seed_provider_receipt()
        fiscal.comms.put(self.con,'fx_ofd_token','synthetic-token')
        with patch.object(fiscal,'fetch_ofd_receipts',return_value=[]) as api:
            first=reconciliation.run_ofd_backlog(self.con,datetime(2026,11,19,12))
            second=reconciliation.run_ofd_backlog(self.con,datetime(2026,11,19,12,1))
            third=reconciliation.run_ofd_backlog(self.con,datetime(2026,11,19,12,11))
        self.assertTrue(first['ok'])
        self.assertEqual(second['checked'],0)
        self.assertEqual(third['checked'],1)
        self.assertEqual(api.call_count,2)
        self.assertEqual(str(api.call_args.args[1]),'2026-11-18')

    def test_automatic_history_outage_not_marked_confirmed(self):
        from unittest.mock import patch
        pid=self.payment();self.seed_provider_receipt()
        fiscal.comms.put(self.con,'fx_ofd_token','synthetic-token')
        with patch.object(fiscal,'fetch_ofd_receipts',side_effect=fiscal.FxError('synthetic outage')):
            result=reconciliation.run_ofd_backlog(self.con,datetime(2026,11,19,12))
        self.assertFalse(result['ok'])
        state=reconciliation.payment_state(self.con,self.con.execute('SELECT * FROM payments WHERE id=?',(pid,)).fetchone())
        self.assertEqual(state['ofd']['status'],'unavailable')
        self.assertEqual(self.con.execute('SELECT count(*) FROM payments').fetchone()[0],1)

    def test_old_schema_upgrade_preserves_rows_and_is_repeatable(self):
        import sqlite3
        con=sqlite3.connect(':memory:')
        con.row_factory=sqlite3.Row
        con.executescript(fiscal.SCHEMA)
        con.execute("INSERT INTO fx_receipts(source,ext_id,ts,amount) VALUES('ofd','historical','2026-01-01',100)")
        fiscal.init(con);fiscal.init(con)
        row=con.execute("SELECT * FROM fx_receipts WHERE ext_id='historical'").fetchone()
        self.assertEqual(row['amount'],100)
        self.assertEqual(row['fiscal_fp'],'')
        con.close()
