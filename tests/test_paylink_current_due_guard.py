"""Stored link amounts/scopes must remain current before sharing or checkout.

All invoices and conversations are synthetic, in a temporary SQLite database.
No payment, cheque or message is sent to an external provider.
"""
import base64
from datetime import date, datetime, timedelta
import unittest
from unittest.mock import patch

from tests import test_invoice_balance_flows as invoice_fixture
from crm import paylinks, paylinkguard, services as sv, comms, chatattachments, money, server


class PaylinkCurrentDueGuardTest(unittest.TestCase):
    quote=invoice_fixture.InvoiceBalanceFlowsTest.quote
    invoice=invoice_fixture.InvoiceBalanceFlowsTest.invoice

    def setUp(self):
        invoice_fixture.InvoiceBalanceFlowsTest.setUp(self)
        # This case has no available funds and must quote the whole invoice.
        self.con.execute('UPDATE payments SET cancelled=1 WHERE id=?',(self.deposit_id,))
        self.family=self.con.execute('SELECT family_id FROM students WHERE id=?',(self.sid,)).fetchone()[0]
        self.cid=comms.upsert_conversation(self.con,'wazzup_max','synthetic-guard-chat',title='Synthetic Parent')
        self.con.execute('UPDATE conversations SET family_id=?,phone_norm=? WHERE id=?',(self.family,'',self.cid))
        self.providers=[]
        for name in ('tg_call','max_call','_wz_send_raw','http'):
            transport=patch.object(comms,name,side_effect=AssertionError('External message forbidden'))
            self.providers.append(transport.start());self.addCleanup(transport.stop)

    def tearDown(self):
        self.assertTrue(all(provider.call_count==0 for provider in self.providers))

    def seven(self):
        q=self.quote()
        excluded=q['calendar_dates'][-2:]
        q=sv.quote_for(self.con,self.sid,'2026-12',group_id=self.gid,paid_date='2026-11-20',
            excluded_dates=excluded,user=self.u,use_balance=False)
        self.assertEqual((q['lessons_paid'],q['total']),(7,9450))
        invoice=sv.create_invoice(self.con,self.sid,'2026-12',self.u,group_id=self.gid,
            paid_date='2026-11-20',excluded_dates=excluded,use_balance=False,
            expected_balance_token=q['balance_preview']['token'],request_id='synthetic-seven-invoice-001')
        return invoice['id']

    def link(self,sub,amount=None,full=True):
        if amount is None:
            amount=sv.payment_quote(self.con,sub,'2026-11-20')['total']
        with patch.object(paylinks,'ready',return_value=True):
            return paylinks.create(self.con,self.sid,amount,self.u,subscription_id=sub,full_payment=full)

    def token(self,link):
        return link['url'].rsplit('/',1)[1]

    def check(self,link,**options):
        before=self.con.serialize()
        result=paylinkguard.by_id(self.con,link['id'],**options)
        self.assertEqual(self.con.serialize(),before)
        return result

    def block_send(self,text,cid=None):
        before=self.con.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
        with self.assertRaises(comms.CommsError):
            comms.send(self.con,self.cid if cid is None else cid,text,self.u)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM messages').fetchone()[0],before)

    def test_seven_lesson_9450_invoice_without_balance_has_exact_public_payment_page(self):
        sub=self.seven();link=self.link(sub)
        check=self.check(link,student_id=self.sid,subscription_id=sub,require_full=True)
        self.assertTrue(check['valid'],check['reason'])
        self.assertEqual((check['amount'],check['current_due']),(9450,9450))
        self.assertIs(check['is_full_payment'],True)
        page=paylinks.payment_page(self.con,self.token(link))
        self.assertEqual((page['amount'],page['subscription']['lessons'],page['subscription']['remaining']),(9450,7,9450))
        self.assertEqual(money.balance(self.con,self.sid)['deposit'],0)
        conv=dict(self.con.execute('SELECT * FROM conversations WHERE id=?',(self.cid,)).fetchone())
        before=self.con.serialize()
        paylinkguard.guard_message(self.con,'Оплата 9 450 ₽\n'+link['url'],conv)
        self.assertEqual(self.con.serialize(),before)

    def test_full_link_amount_does_not_silently_become_partial_after_invoice_increase(self):
        sub=self.seven();link=self.link(sub)
        self.con.execute('UPDATE subscriptions SET price=12150 WHERE id=?',(sub,))
        check=self.check(link,student_id=self.sid,subscription_id=sub,require_full=True)
        self.assertFalse(check['valid']);self.assertEqual(check['current_due'],12150)
        for action in (lambda:paylinks.payment_page(self.con,self.token(link)),
                       lambda:paylinks.checkout(self.con,self.token(link),'synthetic@example.com')):
            with self.assertRaises(paylinks.PayLinkError):action()
        self.block_send('Ссылка: '+link['url'])

    def test_full_link_issue_rejects_stale_amount_without_creating_payment_or_link(self):
        sub=self.seven()
        for amount in (12150,400):
            with self.subTest(amount=amount),self.assertRaises(paylinks.PayLinkError):
                self.link(sub,amount,True)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM pay_links').fetchone()[0],0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM payments WHERE cancelled=0').fetchone()[0],0)
        self.assertEqual(sv.payment_quote(self.con,sub,'2026-11-20')['total'],9450)

    def test_http_omitted_full_choice_is_not_a_silent_partial_link_escape(self):
        sub=self.seven()
        with patch.object(paylinks,'ready',return_value=True):
            for choice in ({},{'full_payment':'false'},{'full_payment':0}):
                with self.subTest(choice=choice),self.assertRaises(sv.ApiError):
                    server.paylinks_create(self.con,None,self.u,dict(amount=400,subscription_id=sub,**choice),{},str(self.sid))
            partial=server.paylinks_create(self.con,None,self.u,
                {'amount':400,'subscription_id':sub,'full_payment':False},{},str(self.sid))
        self.assertTrue(self.check(partial)['valid'])
        self.assertIs(self.check(partial)['is_full_payment'],False)

    def test_unassigned_amount_link_is_not_validated_as_a_full_invoice(self):
        with patch.object(paylinks,'ready',return_value=True):
            link=paylinks.create(self.con,self.sid,9450,self.u)
        self.assertTrue(self.check(link)['valid'])
        self.assertFalse(self.check(link,require_full=True)['valid'])

    def test_legacy_unassigned_link_without_scope_is_not_promoted_to_a_known_amount_request(self):
        with patch.object(paylinks,'ready',return_value=True):
            link=paylinks.create(self.con,self.sid,9450,self.u)
        self.assertTrue(self.check(link)['valid'])
        self.con.execute("UPDATE pay_links SET full_payment=NULL,scope_snapshot='' WHERE id=?",(link['id'],))
        check=self.check(link)
        self.assertFalse(check['valid']);self.assertIsNone(check['current_due'])
        self.block_send(link['url'])

    def test_full_link_is_blocked_after_invoice_reduced_to_9450(self):
        sub=self.invoice()['id'];link=self.link(sub)
        self.con.execute('UPDATE subscriptions SET price=9450,lessons=7 WHERE id=?',(sub,))
        check=self.check(link,require_full=True)
        self.assertEqual((check['amount'],check['current_due']),(12150,9450))
        self.assertFalse(check['valid'])
        self.block_send('Ссылка: '+link['url'])

    def test_current_amount_is_insufficient_when_scope_changed(self):
        sub=self.seven();link=self.link(sub)
        self.con.execute('UPDATE subscriptions SET lessons=6 WHERE id=?',(sub,))
        check=self.check(link,require_full=True)
        self.assertEqual(check['current_due'],9450);self.assertFalse(check['valid'])

    def test_known_partial_link_remains_partial_and_cannot_be_reused_as_full_invoice_link(self):
        sub=self.seven();link=self.link(sub,400,False)
        check=self.check(link)
        self.assertTrue(check['valid'],check['reason']);self.assertIs(check['is_full_payment'],False)
        self.assertEqual(paylinks.payment_page(self.con,self.token(link))['amount'],400)
        self.assertFalse(self.check(link,require_full=True)['valid'])

    def test_legacy_unknown_under_amount_is_not_assumed_to_be_intentional_partial(self):
        sub=self.seven();link=self.link(sub,400,False)
        self.con.execute("UPDATE pay_links SET full_payment=NULL,scope_snapshot='' WHERE id=?",(link['id'],))
        check=self.check(link)
        self.assertIsNone(check['is_full_payment']);self.assertFalse(check['valid'])
        self.block_send('Старая ссылка '+link['url'])

    def test_expected_invoice_and_client_scope_must_match(self):
        sub=self.seven();link=self.link(sub)
        self.assertFalse(self.check(link,student_id=self.sid+10000)['valid'])
        self.assertFalse(self.check(link,subscription_id=sub+10000)['valid'])
        self.assertFalse(self.check(link,subscription_ids=[sub,sub+1])['valid'])

    def test_opaque_link_is_rechecked_with_markdown_tracking_and_uppercase_host(self):
        sub=self.seven();link=self.link(sub)
        self.con.execute('UPDATE subscriptions SET price=12150 WHERE id=?',(sub,))
        text='[Оплатить]('+link['url'].replace('ab-plus.ru','AB-PLUS.RU')+'?from=chat).'
        self.block_send(text)
        self.block_send(link['url'].removeprefix('https://'))

    def test_http_redirect_and_default_ports_cannot_bypass_cancelled_link_guard(self):
        sub=self.seven();link=self.link(sub)
        paylinks.cancel(self.con,link['id'])
        token=self.token(link)
        for origin in ('http://ab-plus.ru','http://ab-plus.ru:80','https://AB-PLUS.RU:443'):
            with self.subTest(origin=origin):
                self.block_send(origin+'/pay/'+token)

    def test_current_http_and_uppercase_host_aliases_are_readonly_but_stale_aliases_block(self):
        sub=self.seven();link=self.link(sub)
        token=self.token(link)
        conv=dict(self.con.execute('SELECT * FROM conversations WHERE id=?',(self.cid,)).fetchone())
        variants=['http://AB-PLUS.RU/pay/'+token,'http://ab-plus.ru:80/pay/'+token,
                  'https://ab-plus.ru:443/pay/'+token]
        before=self.con.serialize()
        for value in variants:
            paylinkguard.guard_message(self.con,value,conv)
        self.assertEqual(self.con.serialize(),before)
        self.con.execute('UPDATE subscriptions SET price=12150 WHERE id=?',(sub,))
        for value in variants:
            self.block_send(value)

    def test_configured_public_port_is_recognized_and_userinfo_is_rejected(self):
        comms.put(self.con,'public_url','https://synthetic-crm.example:8443')
        sub=self.seven();link=self.link(sub)
        token=self.token(link)
        conv=dict(self.con.execute('SELECT * FROM conversations WHERE id=?',(self.cid,)).fetchone())
        before=self.con.serialize()
        paylinkguard.guard_message(self.con,'http://synthetic-crm.example:8443/pay/'+token,conv)
        self.assertEqual(self.con.serialize(),before)
        for origin in ('https://user%40name@ab-plus.ru','https://%61dmin:pw@AB-PLUS.RU:443'):
            with self.subTest(origin=origin):
                self.block_send(origin+'/pay/'+token)
        paylinks.cancel(self.con,link['id'])
        self.block_send('http://synthetic-crm.example:8443/pay/'+token)

    def test_token_case_is_not_normalized_with_the_host(self):
        sub=self.seven();link=self.link(sub)
        token=self.token(link)
        changed=token.swapcase()
        self.assertNotEqual(changed,token)
        self.block_send('https://AB-PLUS.RU/pay/'+changed)

    def test_unknown_or_foreign_recipient_is_blocked_before_transport_or_outgoing_record(self):
        sub=self.seven();link=self.link(sub)
        self.con.execute('UPDATE conversations SET family_id=NULL,phone_norm=?,username=?,title=? WHERE id=?',
            ('','','Synthetic Balance Child',self.cid))
        self.block_send(link['url'])
        foreign=self.con.execute("INSERT INTO families(name) VALUES ('Synthetic other family')").lastrowid
        self.con.execute('UPDATE conversations SET family_id=? WHERE id=?',(foreign,self.cid))
        self.block_send(link['url'])

    def test_stored_family_cannot_hide_conflicting_known_phone_and_telegram_handle(self):
        sub=self.seven();link=self.link(sub)
        foreign=self.con.execute("INSERT INTO families(name) VALUES ('Synthetic other family')").lastrowid
        self.con.execute("INSERT INTO parents(family_id,name,phone,phone_norm,messenger) VALUES (?,'Synthetic other parent','+79000000652','79000000652','@other_parent')",(foreign,))
        phone=self.con.execute('SELECT phone_norm FROM parents WHERE family_id=?',(self.family,)).fetchone()[0]
        self.con.execute("UPDATE conversations SET channel='wazzup_telegram',family_id=?,phone_norm=?,username='other_parent' WHERE id=?",(self.family,phone,self.cid))
        self.assertIsNone(comms.identity_family(self.con,'wazzup_telegram',phone,'other_parent'))
        self.block_send(link['url'])

    def test_unlinked_chat_with_unique_parent_phone_can_be_validated_without_name_guess(self):
        sub=self.seven();link=self.link(sub)
        phone=self.con.execute('SELECT phone_norm FROM parents WHERE family_id=?',(self.family,)).fetchone()[0]
        self.con.execute('UPDATE conversations SET family_id=NULL,phone_norm=? WHERE id=?',(phone,self.cid))
        conv=dict(self.con.execute('SELECT * FROM conversations WHERE id=?',(self.cid,)).fetchone())
        before=self.con.serialize()
        paylinkguard.guard_message(self.con,link['url'],conv)
        self.assertEqual(self.con.serialize(),before)

    def test_already_created_gateway_url_cannot_bypass_stale_check(self):
        sub=self.seven();link=self.link(sub)
        gateway='https://synthetic.paykeeper.ru/bill/SYNTHETIC-READY/'
        self.con.execute("UPDATE pay_links SET invoice_id='SYNTHETIC-READY',invoice_url=?,checkout_state='ready' WHERE id=?",(gateway,link['id']))
        self.con.execute('UPDATE subscriptions SET price=12150 WHERE id=?',(sub,))
        with self.assertRaises(paylinks.PayLinkError):
            paylinks.checkout(self.con,self.token(link),'synthetic@example.com')
        self.block_send(gateway)
        self.block_send(gateway+'?utm_source=chat#payment')
        self.block_send(gateway.replace('https://','http://').replace('synthetic.paykeeper.ru','SYNTHETIC.PAYKEEPER.RU'))

    def test_stale_attachment_caption_stays_draft_without_message_or_provider_call(self):
        sub=self.seven();link=self.link(sub)
        self.con.execute('UPDATE subscriptions SET price=12150 WHERE id=?',(sub,))
        chatattachments.init(self.con)
        attachment=chatattachments.upload(self.con,self.cid,self.u,'synthetic.txt',base64.b64encode(b'Synthetic attachment').decode())
        with self.assertRaises(comms.CommsError):
            chatattachments.send(self.con,self.cid,attachment['id'],self.u,link['url'].replace('https://','http://'))
        self.assertEqual(self.con.execute('SELECT status FROM chat_attachments WHERE id=?',(attachment['id'],)).fetchone()[0],'draft')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)

    def test_unknown_local_link_is_blocked_while_other_domains_are_not_treated_as_crm_links(self):
        self.block_send('https://ab-plus.ru/pay/'+'a'*43)
        conv=dict(self.con.execute('SELECT * FROM conversations WHERE id=?',(self.cid,)).fetchone())
        before=self.con.serialize()
        paylinkguard.guard_message(self.con,'https://ab-plus.ru.example/pay/'+'a'*43,conv)
        self.assertEqual(self.con.serialize(),before)

    def test_expired_and_cancelled_links_are_invalid_for_sharing(self):
        sub=self.seven();link=self.link(sub)
        self.con.execute('UPDATE pay_links SET created_at=? WHERE id=?',((datetime.now()-timedelta(days=15)).isoformat(),link['id']))
        self.assertFalse(self.check(link)['valid'])
        self.con.execute("UPDATE pay_links SET status='expired' WHERE id=?",(link['id'],))
        self.block_send(link['url'])

    def test_validate_route_is_readonly_and_has_no_gateway_side_effects(self):
        sub=self.seven();link=self.link(sub)
        before=self.con.serialize()
        result=server.paylink_validate(self.con,None,self.u,{},
            {'student_id':str(self.sid),'subscription_id':str(sub),'require_full':'1'},str(link['id']))
        self.assertTrue(result['valid']);self.assertTrue(result['read_only'])
        self.assertEqual(self.con.serialize(),before)

    def test_current_late_fee_changes_validation_without_persisting_native_price(self):
        sub=self.invoice()['id'];link=self.link(sub)
        self.con.execute('UPDATE price_list SET late_pct=10')
        self.con.execute("UPDATE subscriptions SET created_at='2026-11-20T10:00' WHERE id=?",(sub,))
        with patch.object(sv,'today',return_value=date(2026,11,26)):
            before=self.con.serialize()
            check=self.check(link,require_full=True)
            self.assertFalse(check['valid']);self.assertEqual(check['current_due'],13365)
            self.assertEqual(self.con.serialize(),before)
        self.assertEqual(self.con.execute('SELECT price FROM subscriptions WHERE id=?',(sub,)).fetchone()[0],12150)

    def test_legacy_public_summary_uses_effective_price_fee_and_discount_without_writing_them(self):
        sub=self.invoice()['id'];link=self.link(sub)
        self.con.execute("UPDATE subscriptions SET price=10935,discount_name='Synthetic discount',discount_amount=1215,created_at='2026-11-20T10:00' WHERE id=?",(sub,))
        self.con.execute('UPDATE price_list SET late_pct=10')
        self.con.execute("UPDATE pay_links SET amount=13365,full_payment=NULL,scope_snapshot='' WHERE id=?",(link['id'],))
        with patch.object(sv,'today',return_value=date(2026,11,26)):
            before=self.con.serialize()
            page=paylinks.payment_page(self.con,self.token(link))
            self.assertEqual(page['amount'],13365)
            summary=page['subscription']
            self.assertEqual((summary['price'],summary['paid'],summary['remaining'],summary['late_fee']),(13365,0,13365,1215))
            self.assertEqual((summary['discount_name'],summary['discount_amount']),('',0))
            self.assertEqual(self.con.serialize(),before)
        native=self.con.execute('SELECT price,discount_amount FROM subscriptions WHERE id=?',(sub,)).fetchone()
        self.assertEqual(tuple(native),(10935,1215))

    def test_bundle_public_summary_matches_effective_quote_without_persisting_native_prices(self):
        first=self.invoice()['id']
        second=self.invoice(month='2027-01',key='synthetic-second-summary-001')['id']
        self.con.execute("UPDATE subscriptions SET price=10935,discount_name='Synthetic discount',discount_amount=1215,created_at='2026-11-20T10:00' WHERE id=?",(first,))
        self.con.execute('UPDATE price_list SET late_pct=10')
        with patch.object(sv,'today',return_value=date(2026,11,26)),patch.object(paylinks,'ready',return_value=True):
            link=paylinks.create_bundle(self.con,self.sid,[first,second],self.u)
            before=self.con.serialize()
            page=paylinks.payment_page(self.con,self.token(link))
            item=page['subscriptions'][0]
            self.assertEqual((item['price'],item['amount'],item['remaining'],item['late_fee']),(13365,13365,13365,1215))
            self.assertEqual((item['discount_name'],item['discount_amount']),('',0))
            self.assertEqual(page['amount'],sum(item['amount'] for item in page['subscriptions']))
            self.assertEqual(self.con.serialize(),before)
        self.assertEqual(self.con.execute('SELECT price FROM subscriptions WHERE id=?',(first,)).fetchone()[0],10935)


if __name__=='__main__':
    unittest.main()
