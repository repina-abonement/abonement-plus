"""Safe Sber connection diagnostics with simulated transport failures only."""
import json
import io
from datetime import datetime
from pathlib import Path
import socket
import sqlite3
import ssl
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from crm import fiscal, sberbank, sbercredentials


class SberTransportDiagnostics(unittest.TestCase):
    secret_marker = 'SYNTHETIC-KEY-MUST-NEVER-LEAK'

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.con = sqlite3.connect(str(Path(directory.name) / 'diagnostics.sqlite3'), isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        sberbank.init(self.con)

    def connection_error(self, error):
        with patch.object(sbercredentials, 'tls_context', return_value=None), patch.object(sberbank, 'build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(fiscal.FxError) as caught:
                sberbank.request(self.con, sberbank.TOKEN, method='POST',
                    form={'client_id': 'syntheticclient', 'client_secret': self.secret_marker, 'code': 'synthetic-code'})
        message = str(caught.exception)
        self.assertNotIn(self.secret_marker, message)
        self.assertNotIn('https://', message)
        self.assertNotIn('client_secret=', message)
        return caught.exception

    def test_dns_error_is_distinct_and_does_not_expose_resolver_details(self):
        error = self.connection_error(URLError(socket.gaierror(-2, self.secret_marker)))
        self.assertEqual(error.transport_code, 'dns')

    def test_timeout_error_is_distinct_and_safe(self):
        for reason in (socket.timeout(self.secret_marker), TimeoutError(self.secret_marker)):
            with self.subTest(reason=type(reason).__name__):
                error = self.connection_error(URLError(reason))
                self.assertEqual(error.transport_code, 'timeout')

    def test_connection_refused_is_distinct_and_safe(self):
        error = self.connection_error(URLError(ConnectionRefusedError(111, self.secret_marker)))
        self.assertEqual(error.transport_code, 'connection_refused')

    def test_certificate_verification_failure_is_not_reported_as_wrong_bank_password(self):
        error = self.connection_error(URLError(ssl.SSLCertVerificationError(1, self.secret_marker)))
        self.assertEqual(error.transport_code, 'tls_certificate_verify')
        self.assertNotIn('client_secret', str(error))

    def test_tls_alert_is_distinct_without_server_error_body(self):
        error = self.connection_error(URLError(ssl.SSLError(1, '[SSL: TLSV1_ALERT_UNKNOWN_CA] ' + self.secret_marker)))
        self.assertEqual(error.transport_code, 'tls_alert')

    def test_arbitrary_exception_stays_generic_and_does_not_leak_url_or_keys(self):
        error = self.connection_error(RuntimeError('https://bank.invalid/?client_secret=' + self.secret_marker))
        self.assertEqual(error.transport_code, 'connection_error')

    def test_private_certificate_validation_error_remains_actionable(self):
        note = 'Загрузите сертификат Сбера в подключении CRM'
        with patch.object(sbercredentials, 'tls_context', side_effect=sbercredentials.SettingsError(note)), patch.object(sberbank, 'build_opener') as opener:
            with self.assertRaises(sbercredentials.SettingsError) as caught:
                sberbank.request(self.con, sberbank.TOKEN, method='POST', form={})
        self.assertEqual(str(caught.exception), note)
        opener.assert_not_called()

    def test_safe_diagnostic_survives_restart_and_is_exposed_without_secrets(self):
        error = self.connection_error(URLError(socket.gaierror(-2, self.secret_marker)))
        current = datetime(2026, 11, 19, 23, 30)
        sberbank.record_failure(self.con, 'token_exchange', error, current=current)
        database_path = self.con.execute('PRAGMA database_list').fetchone()[2]
        with sqlite3.connect(database_path, isolation_level=None) as restored:
            restored.row_factory = sqlite3.Row
            response = sberbank.settings(restored)
        last = response['last_error']
        self.assertEqual(last['stage'], 'token_exchange')
        self.assertEqual(last['code'], 'dns')
        self.assertEqual(last['checked_at'], current.isoformat(timespec='seconds'))
        self.assertEqual(set(last), {'stage', 'code', 'note', 'checked_at'})
        serialized = json.dumps(response, ensure_ascii=False)
        self.assertNotIn(self.secret_marker, serialized)
        self.assertNotIn('bank.invalid', serialized)
        self.assertNotIn('client_secret=', serialized)

    def test_http_failure_record_preserves_status_without_error_description(self):
        body = json.dumps({'error': 'ACTION_ACCESS_EXCEPTION', 'error_description': self.secret_marker}).encode()
        error = self.connection_error(HTTPError('https://bank.invalid/?client_secret=' + self.secret_marker,
              403, self.secret_marker, {}, io.BytesIO(body)))
        sberbank.record_failure(self.con, 'token_exchange', error)
        response = sberbank.settings(self.con)
        self.assertEqual(response['last_error']['code'], 'http_403')
        self.assertNotIn(self.secret_marker, json.dumps(response, ensure_ascii=False))

    def test_unknown_failure_record_never_saves_exception_string(self):
        error = RuntimeError('https://bank.invalid/?client_secret=' + self.secret_marker)
        sberbank.record_failure(self.con, 'token_exchange', error)
        response = sberbank.settings(self.con)
        self.assertIsInstance(response['last_error'], dict)
        serialized = json.dumps(response, ensure_ascii=False)
        self.assertNotIn(self.secret_marker, serialized)
        self.assertNotIn('https://bank.invalid', serialized)
        self.assertNotIn('client_secret=', serialized)


    def test_failure_stage_cannot_store_an_untrusted_url(self):
        sberbank.record_failure(self.con, 'https://bank.invalid/?client_secret=' + self.secret_marker,
                                RuntimeError(self.secret_marker))
        serialized = json.dumps(sberbank.settings(self.con), ensure_ascii=False)
        self.assertNotIn(self.secret_marker, serialized)
        self.assertNotIn('https://bank.invalid', serialized)

    def strict_verification_error(self, code):
        error = ssl.SSLCertVerificationError(1, self.secret_marker)
        error.verify_code = code
        error.verify_message = self.secret_marker
        return URLError(error)

    def test_invalid_policy89_may_retry_once_with_trust_and_hostname_checks_retained(self):
        context = ssl.create_default_context()
        context.verify_flags |= ssl.VERIFY_X509_STRICT
        initial_flags = context.verify_flags
        attempts = []
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok": true}'
        def open_request(*args, **kwargs):
            attempts.append((context.verify_flags, context.verify_mode, context.check_hostname))
            if len(attempts) == 1: raise self.strict_verification_error(89)
            return response
        with patch.object(sbercredentials, 'tls_context', return_value=context), patch.object(sberbank, 'build_opener') as opener:
            opener.return_value.open.side_effect = open_request
            result = sberbank.request(self.con, sberbank.TOKEN, method='POST', form={})
        self.assertEqual(result, {'ok': True})
        self.assertEqual(len(attempts), 2)
        self.assertTrue(attempts[0][0] & ssl.VERIFY_X509_STRICT)
        self.assertFalse(attempts[1][0] & ssl.VERIFY_X509_STRICT)
        self.assertEqual(attempts[1][0], initial_flags & ~ssl.VERIFY_X509_STRICT)
        self.assertEqual(attempts[1][1], ssl.CERT_REQUIRED)
        self.assertTrue(attempts[1][2])

    def test_untrusted_certificate_code20_never_retries_without_strict_checks(self):
        context = ssl.create_default_context()
        context.verify_flags |= ssl.VERIFY_X509_STRICT
        with patch.object(sbercredentials, 'tls_context', return_value=context), patch.object(sberbank, 'build_opener') as opener:
            opener.return_value.open.side_effect = self.strict_verification_error(20)
            with self.assertRaises(fiscal.FxError) as caught:
                sberbank.request(self.con, sberbank.TOKEN, method='POST', form={})
        opener.return_value.open.assert_called_once()
        self.assertEqual(caught.exception.transport_code, 'tls_certificate_verify')
        self.assertTrue(context.verify_flags & ssl.VERIFY_X509_STRICT)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_policy89_without_strict_flag_is_not_an_infinite_retry(self):
        context = ssl.create_default_context()
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        with patch.object(sbercredentials, 'tls_context', return_value=context), patch.object(sberbank, 'build_opener') as opener:
            opener.return_value.open.side_effect = self.strict_verification_error(89)
            with self.assertRaises(fiscal.FxError):
                sberbank.request(self.con, sberbank.TOKEN, method='POST', form={})
        opener.return_value.open.assert_called_once()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)



if __name__ == '__main__':
    unittest.main()
