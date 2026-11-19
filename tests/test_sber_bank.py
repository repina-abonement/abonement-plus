"""Sber read-only connector contract and recovery tests, without bank credentials."""
import base64
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timedelta
import json
import sqlite3
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from urllib.error import HTTPError

from crm import fiscal, incomingcontrol, sberbank


class SberBank(unittest.TestCase):
    current = datetime(2026, 11, 19, 18)
    account = '40702810000000000001'
    other_account = '40702810000000000002'
    day = '2026-11-18'

    def setUp(self):
        self.con = sqlite3.connect(':memory:', isolation_level=None)
        self.con.row_factory = sqlite3.Row
        sberbank.init(self.con)
        incomingcontrol.init(self.con)
        self.private = {'client_id': 'syntheticclient', 'client_secret': 'synthetic-secret',
                        'redirect_uri': 'https://ab-plus.ru/api/sber/oauth/callback',
                        'refresh_token': 'synthetic-refresh', 'accounts': []}
        self.patches = [
            patch.object(sberbank.credentials, 'get', side_effect=lambda con, key, default='': self.private.get(key, default)),
            patch.object(sberbank.credentials, 'update_private', side_effect=lambda con, values: self.private.update(values)),
            patch.object(sberbank.credentials, 'lock', side_effect=lambda con: nullcontext()),
            patch.object(sberbank.credentials, 'view', return_value={
                'client_id': self.private['client_id'], 'client_secret_configured': True,
                'certificate_configured': True, 'redirect_uri': self.private['redirect_uri']}),
        ]
        for context in self.patches: context.start()
        self.addCleanup(self.con.close)
        self.addCleanup(lambda: [context.stop() for context in reversed(self.patches)])

    def row(self, ident='synthetic-operation', direction='CREDIT', amount='1350.00', day=None):
        return {'uuid': ident, 'direction': direction,
                'amount': {'amount': amount, 'currencyName': 'RUB'},
                'operationDate': (day or self.day) + 'T12:05:00',
                'paymentPurpose': 'Оплата по QR ID SYNTHETICREFERENCE',
                'rurTransfer': {'payerName': 'Тестовый плательщик', 'payeeAccount': self.account}}

    def page(self, rows, number=None):
        links = [] if number is None else [{'rel': 'next', 'href':
            f'?accountNumber={self.account}&statementDate={self.day}&page={number}'}]
        return {'transactions': rows, '_links': links}

    def tokens(self, **changes):
        result = {'access_token': 'new-synthetic-access', 'refresh_token': 'new-synthetic-refresh',
                  'token_type': 'Bearer', 'expires_in': 3600,
                  'scope': ' '.join(sorted(sberbank.SCOPES))}
        result.update(changes)
        return result

    def authorization(self, actor=7):
        result = sberbank.authorize(self.con, actor, self.current)
        return parse_qs(urlparse(result['url']).query)

    def test_statement_reads_all_pages_with_stable_ids(self):
        with patch.object(sberbank, 'read', side_effect=[self.page([self.row('one')], 2), self.page([self.row('two')])]) as read, patch.object(sberbank.time, 'sleep'):
            rows = sberbank.statement(self.con, self.account, self.day)
        self.assertEqual([row['transactionId'] for row in rows], ['one', 'two'])
        self.assertEqual([call.args[2]['page'] for call in read.call_args_list], [1, 2])
        self.assertTrue(all(row['status'] == 'Booked' for row in rows))
        self.assertEqual(rows[0]['Amount'], {'amount': '1350.00', 'currency': 'RUB'})

    def test_pagination_cannot_change_host_account_date_or_repeat_page(self):
        valid = self.page([self.row()], 2)
        bad_hrefs = [
            f'https://attacker.invalid/?accountNumber={self.account}&statementDate={self.day}&page=2',
            f'?accountNumber={self.other_account}&statementDate={self.day}&page=2',
            f'?accountNumber={self.account}&statementDate=2026-11-17&page=2',
            f'?accountNumber={self.account}&statementDate={self.day}&page=1',
        ]
        for href in bad_hrefs:
            with self.subTest(href=href):
                page = deepcopy(valid); page['_links'][0]['href'] = href
                with patch.object(sberbank, 'read', return_value=page) as read:
                    with self.assertRaises(fiscal.FxError): sberbank.statement(self.con, self.account, self.day)
                read.assert_called_once()

    def test_duplicate_operation_on_next_page_is_incomplete(self):
        with patch.object(sberbank, 'read', side_effect=[self.page([self.row()], 2), self.page([self.row()])]), patch.object(sberbank.time, 'sleep'):
            with self.assertRaises(fiscal.FxError): sberbank.statement(self.con, self.account, self.day)

    def test_unknown_empty_response_is_not_zero_operations(self):
        for response in ({}, None, [], {'transactions': None}, {'transactions': [], '_links': {}}):
            with self.subTest(response=response), patch.object(sberbank, 'read', return_value=response):
                with self.assertRaises(fiscal.FxError): sberbank.statement(self.con, self.account, self.day)
        with patch.object(sberbank, 'read', return_value=self.page([])):
            self.assertEqual(sberbank.statement(self.con, self.account, self.day), [])

    def test_operation_requires_checkable_amount_date_id_account_and_direction(self):
        changes = [
            {'uuid': ''}, {'direction': 'UNKNOWN'}, {'amount': {'amount': 'NaN', 'currencyName': 'RUB'}},
            {'amount': {'amount': '-1', 'currencyName': 'RUB'}},
            {'amount': {'amount': '1.001', 'currencyName': 'RUB'}},
            {'amount': {'amount': '10', 'currencyName': ''}},
            {'operationDate': '2026-11-17T12:05:00'},
            {'rurTransfer': {'payeeAccount': self.other_account}},
        ]
        for change in changes:
            with self.subTest(change=change):
                row = self.row(); row.update(change)
                with self.assertRaises(fiscal.FxError): sberbank.normalize(row, self.account, self.day)
        debit = sberbank.normalize(self.row(direction='DEBIT'), self.account, self.day)
        self.assertEqual(debit['creditDebitIndicator'], 'Debit')

    def test_authorization_requests_only_read_permissions_and_random_state(self):
        one = self.authorization(); two = self.authorization()
        self.assertEqual(set(one['scope'][0].split()), sberbank.SCOPES)
        self.assertNotEqual(one['state'], two['state'])
        self.assertEqual(len(one['state'][0]), 64)
        self.assertEqual(one['redirect_uri'], [self.private['redirect_uri']])
        self.assertEqual(self.con.execute('SELECT count(*) FROM sber_oauth_states').fetchone()[0], 2)
        stored = self.con.execute('SELECT state_hash FROM sber_oauth_states LIMIT 1').fetchone()[0]
        self.assertNotEqual(stored, one['state'][0])

    def test_callback_bound_to_actor_and_one_use(self):
        query = {'state': self.authorization()['state'][0], 'code': 'synthetic-code'}
        with patch.object(sberbank, 'request', return_value=self.tokens()) as request:
            with self.assertRaises(fiscal.FxError): sberbank.complete_authorization(self.con, 8, query, self.current)
            request.assert_not_called()
            self.assertEqual(sberbank.complete_authorization(self.con, 7, query, self.current), {'connected': True})
            with self.assertRaises(fiscal.FxError): sberbank.complete_authorization(self.con, 7, query, self.current)
        request.assert_called_once()
        form = request.call_args.kwargs['form']
        self.assertEqual(form['grant_type'], 'authorization_code')
        self.assertEqual(form['redirect_uri'], self.private['redirect_uri'])
        self.assertEqual(self.private['access_token'], 'new-synthetic-access')
        self.assertEqual(self.private['refresh_token'], 'new-synthetic-refresh')

    def test_callback_expiry_client_change_and_denial_never_exchange_code(self):
        for reason in ('expired', 'client_changed', 'bank_denied'):
            with self.subTest(reason=reason):
                query = {'state': self.authorization()['state'][0], 'code': 'synthetic-code'}
                current = self.current
                if reason == 'expired': current += timedelta(minutes=16)
                if reason == 'client_changed': self.private['client_id'] = 'another-client'
                if reason == 'bank_denied': query['error'] = 'access_denied'
                with patch.object(sberbank, 'request') as request:
                    with self.assertRaises(fiscal.FxError): sberbank.complete_authorization(self.con, 7, query, current)
                    request.assert_not_called()
                self.private['client_id'] = 'syntheticclient'

    def test_token_scope_with_payment_permission_rejected_without_store(self):
        before = dict(self.private)
        with self.assertRaises(fiscal.FxError):
            sberbank.store_tokens(self.con, self.tokens(scope='openid GET_STATEMENT_ACCOUNT PAY_DOC_RU'), self.current)
        self.assertEqual(self.private, before)

    def test_refresh_saves_rotated_pair_together_and_uses_real_expiry(self):
        self.private.update(access_token='old-access', token_expires_at='2026-11-19T17:00:00')
        with patch.object(sberbank, 'request', return_value=self.tokens(expires_in=2400)) as request, patch.object(sberbank.credentials, 'update_private', wraps=sberbank.credentials.update_private) as store:
            self.assertEqual(sberbank.access_token(self.con, current=self.current), 'new-synthetic-access')
        store.assert_called_once()
        self.assertEqual(request.call_args.kwargs['form']['grant_type'], 'refresh_token')
        self.assertEqual(request.call_args.kwargs['form']['refresh_token'], 'synthetic-refresh')
        self.assertEqual(self.private['token_expires_at'], '2026-11-19T18:40:00')
        self.assertEqual(self.private['refresh_token'], 'new-synthetic-refresh')

    def test_read_refreshes_once_on_unauthorized_but_not_forbidden(self):
        with patch.object(sberbank, 'access_token', side_effect=['old', 'new']) as token, patch.object(sberbank, 'request', side_effect=[fiscal.FxError('expired', http_status=401), self.page([])]):
            self.assertEqual(sberbank.read(self.con, sberbank.STATEMENT)['transactions'], [])
        self.assertEqual([call.kwargs['force'] for call in token.call_args_list], [False, True])
        with patch.object(sberbank, 'access_token', return_value='synthetic') as token, patch.object(sberbank, 'request', side_effect=fiscal.FxError('forbidden', http_status=403)):
            with self.assertRaises(fiscal.FxError): sberbank.read(self.con, sberbank.STATEMENT)
        token.assert_called_once()

    def test_account_envelope_requires_complete_unique_account_list(self):
        def envelope(accounts):
            payload = base64.urlsafe_b64encode(json.dumps({'accounts': accounts}).encode()).decode().rstrip('=')
            return 'synthetic-header.' + payload + '.synthetic-signature'
        with patch.object(sberbank, 'read', return_value=envelope([{'accountNumber': self.account}])):
            self.assertEqual(sberbank.accounts(self.con), [self.account])
        for accounts in ([], [{'accountNumber': self.account}] * 2, [{'accountNumber': 'not-account'}]):
            with self.subTest(accounts=accounts), patch.object(sberbank, 'read', return_value=envelope(accounts)):
                with self.assertRaises(fiscal.FxError): sberbank.accounts(self.con)

    def test_discovery_preserves_bank_ids_and_does_not_create_crm_payments(self):
        self.con.execute('CREATE TABLE payments(id INTEGER PRIMARY KEY,amount INTEGER)')
        def statement(con, account, day):
            return [sberbank.normalize(self.row('stable-op', day=day), account, day)] if day == self.day else []
        with patch.object(sberbank, 'accounts', return_value=[self.account]), patch.object(sberbank, 'statement', side_effect=statement), patch.object(fiscal, 'cfg', return_value=''):
            first = sberbank.discover(self.con, self.current)
            sberbank.discover(self.con, self.current + timedelta(minutes=11))
        self.assertEqual(first['unavailable'], 0)
        events = self.con.execute('SELECT * FROM incoming_money_events').fetchall()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['source'], 'sber')
        self.assertEqual(events[0]['source_id'], 'stable-op')
        self.assertEqual(events[0]['amount_minor'], 135000)
        self.assertEqual(self.con.execute('SELECT count(*) FROM payments').fetchone()[0], 0)

    def test_source_error_is_visible_without_erasing_previous_events(self):
        incomingcontrol.save_event(self.con,'sber','synthetic-account','previous-id',self.day,135000,'RUB',{'purpose':'synthetic'},self.current)
        with patch.object(sberbank, 'accounts', side_effect=fiscal.FxError('scope missing', http_status=403)):
            result = sberbank.discover(self.con, self.current)
        self.assertEqual(result['unavailable'], 1)
        self.assertEqual(result['errors'][0]['http_status'], 403)
        status = self.con.execute("SELECT status FROM incoming_source_checks WHERE source='sber'").fetchone()[0]
        self.assertEqual(status, 'unavailable')
        self.assertEqual(self.con.execute('SELECT source_id FROM incoming_money_events').fetchone()[0], 'previous-id')

    def test_http_errors_do_not_expose_provider_url_or_body(self):
        error = HTTPError('https://bank.invalid/?client_secret=NEVER-EXPOSE-THIS',403,'provider-secret',{},None)
        with patch.object(sberbank.credentials,'tls_context',return_value=None), patch.object(sberbank,'build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(fiscal.FxError) as caught:sberbank.request(self.con,sberbank.STATEMENT)
        self.assertEqual(caught.exception.http_status,403)
        self.assertNotIn('NEVER-EXPOSE',str(caught.exception))
        self.assertNotIn('provider-secret',str(caught.exception))



if __name__ == '__main__':
    unittest.main()
