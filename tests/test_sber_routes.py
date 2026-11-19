"""Real HTTP access checks for the private Sber onboarding routes."""
from contextlib import redirect_stdout
from datetime import datetime
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from crm import db, server, sberbank, sbercredentials


class SberRoutes(unittest.TestCase):
    account = '40702810000000000001'
    secret = 'SYNTHETIC-SECRET-MUST-NOT-LEAK'

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        previous = db.DB_PATH
        db.DB_PATH = str(Path(temporary.name) / 'sber-routes.sqlite3')
        self.addCleanup(setattr, db, 'DB_PATH', previous)
        con = db.connect()
        # Fixtures may generate local passwords; never put those in test output.
        with redirect_stdout(io.StringIO()): db.init_db(con)
        self.actors = {}
        for key, role in [('owner', 'admin'), ('operator', 'operator'), ('offline', 'operator')]:
            uid = con.execute('INSERT INTO users(login,name,role,pw_hash) VALUES (?,?,?,?)',
                              ('sber-' + key, 'Synthetic ' + key, role, 'disabled')).lastrowid
            if key == 'offline': server.set_post(con, uid, 'offline_admin')
            token = 'synthetic-session-' + key
            self.actors[key] = token
            con.execute('INSERT INTO sessions(token,user_id,created_at) VALUES (?,?,?)',
                        (token, uid, datetime.now().isoformat()))
        con.close()
        self.quiet = patch.object(server.Handler, 'log_message')
        self.quiet.start(); self.addCleanup(self.quiet.stop)
        self.srv = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.srv.shutdown(); self.srv.server_close(); self.thread.join(timeout=5)

    def call(self, actor, method, path, body=None):
        headers = {'Content-Type': 'application/json'}
        if actor is not None: headers['Cookie'] = 'crm_session=' + self.actors[actor]
        req = urllib.request.Request(f'http://127.0.0.1:{self.srv.server_port}' + path,
              method=method, headers=headers,
              data=json.dumps(body).encode() if body is not None else None)
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read().decode()
            data = json.loads(raw) if 'application/json' in response.headers.get('Content-Type', '') else raw
            return response.status, data, response.headers

    def test_admin_settings_are_sanitized_and_audit_contains_action_only(self):
        code, result, headers = self.call('owner', 'PUT', '/api/sber/settings',
                    {'client_id': 'syntheticclient', 'client_secret': self.secret, 'accounts': self.account})
        self.assertEqual(code, 200)
        self.assertTrue(result['client_secret_configured'])
        encoded = json.dumps(result)
        self.assertNotIn(self.secret, encoded); self.assertNotIn(self.account, encoded)
        code, result, headers = self.call('owner', 'GET', '/api/sber/settings')
        self.assertEqual(code, 200)
        self.assertIn('no-store', headers.get('Cache-Control'))
        self.assertEqual(result['client_id'], 'syntheticclient')
        self.assertIn('0001', json.dumps(result['accounts_masked']))
        self.assertNotIn(self.secret, json.dumps(result))
        con = db.connect()
        try:
            self.assertEqual(sbercredentials.get(con, 'client_secret'), self.secret)
            audit = con.execute("SELECT detail FROM audit WHERE action='sber.settings'").fetchone()
            self.assertIsNotNone(audit)
            self.assertNotIn(self.secret, audit['detail']); self.assertNotIn(self.account, audit['detail'])
        finally: con.close()

    def test_operator_offline_and_unsigned_cannot_configure_or_test_bank(self):
        calls = [('GET', '/api/sber/settings', None), ('PUT', '/api/sber/settings', {'client_secret': 'must-not-save'}),
                 ('POST', '/api/sber/authorize', {}), ('POST', '/api/sber/test', {}),
                 ('GET', '/api/sber/oauth/callback?state=' + '0' * 64 + '&code=must-not-exchange', None)]
        with patch.object(sbercredentials, 'save') as save, patch.object(sberbank, 'test_connection') as test, patch.object(sberbank, 'authorize') as authorize, patch.object(sberbank, 'complete_authorization') as complete:
            for actor in ('operator', 'offline', None):
                for method, path, body in calls:
                    with self.subTest(actor=actor, path=path):
                        code, _, _ = self.call(actor, method, path, body)
                        self.assertEqual(code, 401 if actor is None else 403)
            for call in (save, test, authorize, complete): call.assert_not_called()

    def test_authorize_route_returns_fixed_bank_read_only_authorization(self):
        def view(con):
            return {'client_id': 'syntheticclient', 'client_secret_configured': True,
                    'certificate_configured': True, 'redirect_uri': sbercredentials.REDIRECT_URI}
        with patch.object(sbercredentials, 'view', side_effect=view):
            code, result, headers = self.call('owner', 'POST', '/api/sber/authorize', {})
        self.assertEqual(code, 200)
        self.assertTrue(result['url'].startswith(sberbank.AUTHORIZE + '?'))
        self.assertNotIn('PAY_DOC', result['url'])
        self.assertIn('GET_STATEMENT_ACCOUNT', result['url'])
        self.assertIn('no-store', headers.get('Cache-Control'))

    def test_bank_test_network_runs_without_sqlite_write_transaction(self):
        observed = []
        def bank_read(con):
            observed.append(con.in_transaction)
            self.assertFalse(con.in_transaction)
            writer = db.connect()
            try:
                writer.execute('PRAGMA busy_timeout=50')
                writer.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('synthetic-sber-concurrent-writer','written')")
            finally: writer.close()
            return {'ok': True, 'accounts_count': 1, 'transactions_count': 2, 'note': 'Synthetic statement read'}
        with patch.object(sberbank, 'test_connection', side_effect=bank_read):
            code, result, _ = self.call('owner', 'POST', '/api/sber/test', {})
        self.assertEqual(code, 200); self.assertTrue(result['ok'])
        self.assertEqual(observed, [False])

    def test_wrong_callback_state_does_not_exchange_token_or_echo_query(self):
        code_value = 'SYNTHETIC-AUTH-CODE-NOT-FOR-RESPONSE'
        with patch.object(sberbank, 'request') as bank:
            code, result, headers = self.call('owner', 'GET', '/api/sber/oauth/callback?state=' + '0' * 64 + '&code=' + code_value)
        self.assertEqual(code, 200)
        bank.assert_not_called()
        self.assertNotIn(code_value, result)
        self.assertIn('no-store', headers.get('Cache-Control'))
        self.assertEqual(headers.get_all('Referrer-Policy'), ['no-referrer'])
        self.assertIn("default-src 'none'", headers.get('Content-Security-Policy'))

    def test_unavailable_bank_test_is_safe_error_and_does_not_claim_success(self):
        with patch.object(sberbank, 'test_connection', side_effect=sberbank.fiscal.FxError('Сбер: нет разрешения читать выбранный счёт', http_status=403)):
            code, result, _ = self.call('owner', 'POST', '/api/sber/test', {})
        self.assertEqual(code, 200)
        self.assertFalse(result['ok'])
        self.assertIn('нет разрешения', result['note'])


if __name__ == '__main__':
    unittest.main()
