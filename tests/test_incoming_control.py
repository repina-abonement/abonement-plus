"""Read-only reverse discovery independent of recorded CRM sales."""
import unittest
from datetime import datetime,timedelta
from unittest.mock import patch
from crm import incomingcontrol as incoming,fiscal,payoutcheck
from tests.test_fiscal_payment_check import FiscalPaymentCheck

class IncomingControl(unittest.TestCase):
    setUp=FiscalPaymentCheck.setUp
    current=datetime(2026,11,19,14)

    def bank(self):
        return {'_account_id':'account','transactionId':'operation','status':'Booked','creditDebitIndicator':'Credit',
            'documentProcessDate':'2026-11-18','Amount':{'amount':'1350','currency':'RUB'},
            'DebtorParty':{'name':'Банк'},'description':'Зачисление по QR коду ID ABCDEFGH12345678 от 18.11.2026'}

    def test_unrecorded_bank_credit_visible_without_crm_sales_no_duplicate(self):
        before=self.con.execute('SELECT count(*) FROM payments').fetchone()[0]
        with patch.object(fiscal,'fetch_tochka',return_value=[self.bank()]):
            incoming.discover_tochka(self.con,self.current)
            incoming.discover_tochka(self.con,self.current)
        result=incoming.view(self.con,current=self.current)
        self.assertEqual(result['unrecorded'],1)
        self.assertEqual(result['tracked_cases'],1)
        self.assertEqual(result['coverage']['sber']['status'],'not_connected')
        self.assertFalse(result['complete'])
        self.assertEqual(self.con.execute('SELECT count(*) FROM payments').fetchone()[0],before)

    def test_provider_and_bank_exact_qr_grouped_one_case(self):
        incoming.save_event(self.con,'paykeeper','','pk1','2026-11-18',135000,'RUB',{'unique_id':'ABCDEFGH12345678','clientid':'Имя'},self.current)
        incoming.ingest_bank(self.con,'tochka',[self.bank()],self.current)
        result=incoming.view(self.con,current=self.current)
        self.assertEqual(result['tracked_cases'],1)
        self.assertEqual(set(result['rows'][0]['sources']),{'paykeeper','tochka'})
        self.assertEqual(result['receipt_unconfirmed'],1)

    def test_suggestion_never_exempts_receipt(self):
        row=self.bank();row['description']='Возмещение эквайринга за 18.11.2026'
        incoming.ingest_bank(self.con,'tochka',[row],self.current)
        result=incoming.view(self.con,current=self.current)
        self.assertEqual(result['receipt_unconfirmed'],1)
        self.assertFalse(result['complete'])

    def test_failed_source_preserves_rows_and_stale_not_green(self):
        incoming.ingest_bank(self.con,'tochka',[self.bank()],self.current)
        incoming.checked(self.con,'paykeeper',self.current-timedelta(hours=1),'ok',{})
        with patch.object(fiscal,'fetch_tochka',side_effect=fiscal.FxError('unavailable')):
            incoming.discover_tochka(self.con,self.current)
        result=incoming.view(self.con,current=self.current)
        self.assertEqual(result['coverage']['tochka']['status'],'unavailable')
        self.assertEqual(result['coverage']['paykeeper']['status'],'stale')
        self.assertEqual(result['tracked_cases'],1)

    def test_control_start_limits_reads_and_overview_without_removing_history(self):
        fiscal.comms.put(self.con,'payment_control_from','2026-11-19')
        incoming.ingest_bank(self.con,'tochka',[self.bank()],self.current)
        new=self.bank()|{'transactionId':'new','documentProcessDate':'2026-11-19'}
        with patch.object(fiscal,'fetch_tochka',return_value=[new]) as bank:
            incoming.discover_tochka(self.con,self.current)
        self.assertEqual(bank.call_args.args[1].isoformat(),'2026-11-19')
        with patch.object(payoutcheck,'fetch_day',return_value=[]) as gateway:
            incoming.discover_paykeeper(self.con,self.current)
        self.assertEqual([call.args[1] for call in gateway.call_args_list],['2026-11-19'])
        self.assertEqual(incoming.view(self.con,current=self.current)['tracked_cases'],1)
        self.assertEqual(self.con.execute('SELECT count(*) FROM incoming_money_events').fetchone()[0],2)

    def test_zero_gateway_day_is_success_and_independent_of_crm(self):
        with patch.object(payoutcheck,'fetch_day',return_value=[]) as read:
            result=incoming.discover_paykeeper(self.con,self.current)
        self.assertEqual(read.call_count,3)
        self.assertTrue(result['empty'])
        self.assertEqual(incoming.view(self.con,current=self.current)['coverage']['paykeeper']['status'],'ok')

    def test_null_page_needs_documented_zero_count(self):
        with patch.object(fiscal,'pk_call',side_effect=[None,[{'fullcount':[{'count':'0'}]}]]):
            self.assertEqual(payoutcheck.fetch_day(self.con,'2026-11-18'),[])
        with patch.object(fiscal,'pk_call',side_effect=[None,[{'fullcount':[{'count':'1'}]}]]):
            with self.assertRaises(payoutcheck.DailyEvidenceError):payoutcheck.fetch_day(self.con,'2026-11-18')

    def test_independent_reverse_worker_retries_after_source_recovery(self):
        from crm import paymentworker
        fiscal.comms.put(self.con,'fx_tochka_token','synthetic')
        with patch.object(fiscal,'fetch_tochka',side_effect=fiscal.FxError('unavailable')):
            paymentworker.cycle(self.con,'incoming_tochka',self.current)
        self.assertEqual(incoming.view(self.con,current=self.current)['coverage']['tochka']['status'],'unavailable')
        with patch.object(fiscal,'fetch_tochka',return_value=[self.bank()]):
            paymentworker.cycle(self.con,'incoming_tochka',self.current+timedelta(minutes=11))
        result=incoming.view(self.con,current=self.current+timedelta(minutes=11))
        self.assertEqual(result['coverage']['tochka']['status'],'ok')
        self.assertEqual(result['unrecorded'],1)
