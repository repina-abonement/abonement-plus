"""Named SBP and daily card reconciliation using gateway feed + bank statement only."""
import hashlib
import json
import unittest
from datetime import datetime
from unittest.mock import patch
from crm import bankcheck,fiscal,money,payoutcheck,paymentcontrol
from tests.test_fiscal_payment_check import FiscalPaymentCheck

class PayoutMatching(unittest.TestCase):
    setUp=FiscalPaymentCheck.setUp
    new=FiscalPaymentCheck.new
    current=datetime(2026,11,19,14)

    def member(self,ident='p1',name='Петр Иванов',amount=1000,rail='card',system='10',day='2026-11-18',linked=True):
        link,_=self.new(amount)
        if linked:
            pid=money.add_payment(self.con,self.sid,amount,'paykeeper',{'id':0,'role':'admin','name':'Synthetic gateway'},paid_at=day+'T12:00:00')
            self.con.execute("UPDATE pay_links SET payment_id=?,pk_payment_id=?,status='paid' WHERE id=?",(pid,ident,link['id']))
        return {'id':ident,'client_name':name,'amount_minor':int(amount*100),'refund_minor':0,'orderid':self.con.execute('SELECT orderid FROM pay_links WHERE id=?',(link['id'],)).fetchone()[0],
            'status':'obtained','paid_at':day+' 12:00:00','system_id':system,'rail':rail,'acquirer':'vtb' if rail=='card' else 'tochka'}
    def snapshot(self,items,day='2026-11-18',stamp='2026-11-19T13:00:00'):
        digest=hashlib.sha256(json.dumps(items,sort_keys=True).encode()).hexdigest()
        self.con.execute('INSERT OR REPLACE INTO pk_daily_evidence VALUES(?,?,?,?,?)',(day,stamp,'complete',digest,json.dumps({'items':items,'coverage_complete':True,'verified_at':stamp})))
    def credit(self,amount=5910,name='Банк ВТБ',purpose='Возмещение эквайринга за 18.11.2026. Комиссия 90.00 руб.',ident='bank-credit-1',day='2026-11-19'):
        return {'_account_id':'synthetic-account','transactionId':ident,'status':'Booked','creditDebitIndicator':'Credit',
            'documentProcessDate':day,'Amount':{'amount':str(amount),'currency':'RUB'},'DebtorParty':{'name':name},'description':purpose}
    def run_bank(self,credits):
        fiscal.comms.put(self.con,'fx_tochka_token','synthetic')
        with patch.object(fiscal,'fetch_tochka',return_value=credits) as statement,patch.object(fiscal,'tochka_get',side_effect=AssertionError('No acquiring endpoint')):
            result=bankcheck.run_automatic(self.con,self.current,cached_only=True)
        self.assertTrue(statement.call_args.kwargs['include_debits'])
        return result
    def states(self):return [bankcheck.state(self.con,r['id']) for r in self.con.execute('SELECT id FROM payments ORDER BY id')]

    def test_three_customers_one_net_credit_no_acquiring_permission(self):
        self.snapshot([self.member('p1','Петр Иванов',1000),self.member('p2','Василий Петров',2000),self.member('p3','Людмила Сидорова',3000)])
        count=self.con.execute('SELECT count(*) FROM payments').fetchone()[0]
        result=self.run_bank([self.credit()])
        self.assertEqual(result['confirmed'],3)
        self.assertTrue(all(s['status']=='confirmed' for s in self.states()))
        self.assertEqual(len({s['evidence']['batch_id'] for s in self.states()}),1)
        batch=payoutcheck.view(self.con,'2026-11-18','2026-11-18')[0]
        self.assertEqual(len(batch['members']),3);self.assertEqual(batch['gross_minor'],600000)
        self.assertEqual(batch['bank']['received_minor'],591000);self.assertEqual(batch['bank']['fee_minor'],9000)
        self.assertEqual(self.con.execute('SELECT count(*) FROM payments').fetchone()[0],count)
        # Bank confirmation does not manufacture an OFD check.
        payment=self.con.execute('SELECT * FROM payments LIMIT 1').fetchone()
        self.assertEqual(paymentcontrol.state(self.con,payment)['status'],'attention')
    def test_sbp_full_name_individual_credit_not_card_batch(self):
        self.snapshot([self.member(amount=1350,rail='sbp',system='94')])
        result=self.run_bank([self.credit(1350,'Иванов Пётр Сергеевич','Оплата СБП',day='2026-11-18')])
        self.assertEqual(result['confirmed'],1)
        self.assertEqual(self.states()[0]['evidence']['method'],'payer_amount_day_unique')
        self.assertEqual(payoutcheck.view(self.con,'2026-11-18','2026-11-18'),[])
    def test_sbp_initials_and_name_in_purpose(self):
        self.snapshot([self.member(rail='sbp')])
        self.run_bank([self.credit(1000,'Иванов П.С.','СБП',day='2026-11-18')])
        self.assertEqual(self.states()[0]['status'],'confirmed')
    def test_equal_family_payments_do_not_reuse_one_credit(self):
        self.snapshot([self.member('p1',rail='sbp'),self.member('p2',rail='sbp')])
        self.run_bank([self.credit(1000,'Иванов Петр','СБП',day='2026-11-18')])
        self.assertTrue(all(s['status']=='unverified' for s in self.states()))
        self.assertEqual(self.con.execute('SELECT count(*) FROM bank_credit_allocations').fetchone()[0],0)
    def test_unknown_client_gateway_payment_still_prevents_double_allocation(self):
        self.snapshot([self.member('p1',rail='sbp'),self.member('p2',rail='sbp',linked=False)])
        self.run_bank([self.credit(1000,'Иванов Петр','СБП',day='2026-11-18')])
        self.assertEqual(self.states()[0]['status'],'unverified')
    def test_card_same_amount_wrong_day_or_bank_no_confirmation(self):
        self.snapshot([self.member(amount=6000)])
        for credit in [self.credit(name='Другой банк'),self.credit(purpose='Возмещение эквайринга за 17.11.2026. Комиссия 90 руб.'),self.credit(purpose='Обычный перевод')]:
            self.run_bank([credit]);self.assertEqual(self.states()[0]['status'],'unverified')
    def test_missing_fee_is_not_inferred_from_difference(self):
        self.snapshot([self.member(amount=6000)])
        self.run_bank([self.credit(purpose='Возмещение эквайринга за 18.11.2026')])
        self.assertEqual(self.states()[0]['status'],'unverified')
        batch=payoutcheck.view(self.con,'2026-11-18','2026-11-18')[0]
        self.assertIsNone(batch['candidates'][0]['fee_minor'])
    def test_gross_credit_records_unknown_separate_commission_honestly(self):
        self.snapshot([self.member(amount=6000)])
        self.run_bank([self.credit(6000,purpose='Возмещение эквайринга за 18.11.2026')])
        self.assertEqual(self.states()[0]['status'],'confirmed')
        batch=payoutcheck.view(self.con,'2026-11-18','2026-11-18')[0]
        self.assertIsNone(batch['bank']['fee_minor'])
    def test_two_candidate_payouts_pending_or_wrong_currency_never_green(self):
        self.snapshot([self.member(amount=6000)])
        one=self.credit();two=self.credit(ident='other-bank-credit')
        for credits in ([one,two],[one|{'status':'Pending'}],[one|{'Amount':{'amount':'5910','currency':'USD'}}]):
            self.run_bank(credits);self.assertEqual(self.states()[0]['status'],'unverified')
    def test_changed_roster_invalidates_prior_green(self):
        one=self.member(amount=6000);self.snapshot([one]);self.run_bank([self.credit()])
        self.assertEqual(self.states()[0]['status'],'confirmed')
        self.snapshot([one,self.member('late',amount=100)])
        self.assertEqual(self.states()[0]['status'],'unverified')
    def test_open_day_and_refund_do_not_confirm_full_payout(self):
        one=self.member(amount=6000);self.snapshot([one],stamp='2026-11-18T23:00:00');self.run_bank([self.credit()])
        self.assertEqual(self.states()[0]['status'],'unverified')
        one['refund_minor']=10000;self.snapshot([one]);self.run_bank([self.credit()])
        self.assertEqual(self.states()[0]['status'],'unverified')
    def test_complete_read_pagination_and_error_not_empty_success(self):
        rows=[{'id':str(i),'orderid':'o'+str(i),'obtain_datetime':'2026-11-18 12:00:00','pay_amount':'1','refund_amount':'0','status':'obtained'} for i in range(101)]
        with patch.object(fiscal,'pk_call',side_effect=[rows[:100],rows[100:]]) as api:
            result=payoutcheck.fetch_day(self.con,'2026-11-18')
        self.assertEqual(len(result),101);self.assertEqual(api.call_count,2)
        with patch.object(fiscal,'pk_call',return_value={'error':'failed'}):
            with self.assertRaises(ValueError):payoutcheck.fetch_day(self.con,'2026-11-18')
        with patch.object(fiscal,'pk_call',side_effect=[rows[:100],rows[:1]]):
            with self.assertRaises(ValueError):payoutcheck.fetch_day(self.con,'2026-11-18')

    def test_automatic_complete_gateway_feed_discovers_card_rail_and_excludes_sbp(self):
        cards=[self.member('c1','Петр Иванов',1000,system='10'),self.member('c2','Василий Петров',2000,system='11'),self.member('c3','Людмила Сидорова',3000,system='10')]
        sbp=self.member('s1','Петр Иванов',1350,rail='sbp',system='94')
        rows=[{'id':i['id'],'clientid':i['client_name'],'orderid':i['orderid'],'pay_amount':str(i['amount_minor']/100),
            'refund_amount':'0','status':'obtained','obtain_datetime':i['paid_at'],'payment_system_id':i['system_id']} for i in cards+[sbp]]
        calls=[]
        def provider(con,path,params):
            calls.append(path)
            if path=='/info/payments/bydate/':return rows if params['start']=='2026-11-18' else []
            if path=='/info/payments/byid/':
                item=next(i for i in rows if i['id']==params['id'])
                return [item|{'system_description':'SBP-TOCHKA' if item['payment_system_id']=='94' else 'VTB','site_description':'СБП Точка-Банк' if item['payment_system_id']=='94' else 'Банковская карта ВТБ'}]
            raise AssertionError('Read endpoints only')
        with patch.object(fiscal,'pk_call',side_effect=provider):result=payoutcheck.refresh_source(self.con,self.current,limit=2)
        self.assertEqual(result['days_checked'],2);self.assertEqual(result['days_unavailable'],0)
        self.assertTrue(all(path.startswith('/info/') for path in calls))
        result=self.run_bank([self.credit(),self.credit(1350,'Иванов Петр','СБП',ident='bank-sbp',day='2026-11-18')])
        self.assertEqual(result['confirmed'],4)
        batches=payoutcheck.view(self.con,'2026-11-18','2026-11-19')
        self.assertEqual(len(batches),1);self.assertEqual(batches[0]['gross_minor'],600000)
        self.assertEqual(len(batches[0]['members']),3)

    def test_different_source_dates_do_not_allocate_same_credit_twice(self):
        self.snapshot([self.member(amount=6000)])
        self.run_bank([self.credit()])
        other=self.member('new-day',amount=6000,day='2026-11-17')
        self.snapshot([other],day='2026-11-17')
        self.run_bank([self.credit(purpose='Возмещение эквайринга за 17.11.2026. Комиссия 90 руб.')])
        self.assertEqual(self.states()[1]['status'],'unverified')

    def test_source_failure_preserves_prior_complete_roster(self):
        self.snapshot([self.member(amount=6000)])
        from datetime import timedelta
        for offset in range(7):
            day=(self.current.date()-timedelta(days=offset)).isoformat()
            if day!='2026-11-18':self.snapshot([],day=day,stamp=self.current.isoformat())
        with patch.object(fiscal,'pk_call',side_effect=fiscal.FxError('unavailable')):
            result=payoutcheck.refresh_source(self.con,self.current,limit=1)
        self.assertEqual(result['days_unavailable'],1)
        snapshot=self.con.execute("SELECT * FROM pk_daily_evidence WHERE day='2026-11-18'").fetchone()
        self.assertEqual(snapshot['status'],'unavailable')
        self.assertTrue(json.loads(snapshot['result'])['items'])
        self.run_bank([self.credit()])
        self.assertEqual(self.states()[0]['status'],'confirmed')

    def test_reversed_name_order_and_initials_on_either_side(self):
        self.assertTrue(payoutcheck.same_name(['Иванов П.'],'Петр Иванов'))
        self.assertTrue(payoutcheck.same_name(['Петр Иванов'],'Иванов П.С.'))
        self.assertFalse(payoutcheck.same_name(['Иванов А.'],'Петр Иванов'))
