"""Private bank credentials: transactional replacement, masked UI and verified TLS."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import ssl
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from crm import sbercredentials as credentials


class SberCredentials(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.certs = tempfile.TemporaryDirectory()
        folder = Path(cls.certs.name)
        cls.password = 'Synthetic-P12-secret'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-keyout', str(folder/'key.pem'),
            '-out', str(folder/'cert.pem'), '-sha256', '-days', '30', '-nodes', '-subj', '/CN=Synthetic Test'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        subprocess.run(['openssl', 'pkcs12', '-export', '-out', str(folder/'client.p12'),
            '-inkey', str(folder/'key.pem'), '-in', str(folder/'cert.pem'), '-passout', 'stdin'],
            input=(cls.password+'\n').encode(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        cls.certificate = base64.b64encode((folder/'client.p12').read_bytes()).decode()

    @classmethod
    def tearDownClass(cls):
        cls.certs.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name)/'test.sqlite3'
        self.con = sqlite3.connect(self.database)
        self.addCleanup(self.con.close)
        self.addCleanup(self.temp.cleanup)

    def save(self):
        return credentials.save(self.con, {'client_id':'synthetic-client', 'client_secret':'SECRET-CLIENT',
            'accounts':['40702810000000001234'], 'certificate_base64':self.certificate,
            'certificate_password':self.password})

    def test_ui_never_returns_credentials_complete_accounts_or_private_path(self):
        view = self.save()
        self.assertTrue(view['configured'])
        self.assertFalse(view['connected'])
        self.assertEqual(view['accounts_masked'],['•••• 1234'])
        public = json.dumps(view)
        for secret in ('SECRET-CLIENT', self.password, '40702810000000001234', self.temp.name):
            self.assertNotIn(secret,public)
        self.assertNotIn('PRIVATE KEY',public)
        self.assertEqual(credentials.get(self.con,'client_secret'),'SECRET-CLIENT')
        self.assertTrue(view['certificate_expires_at'])

    def test_private_store_and_certificate_file_permissions(self):
        self.save()
        directory = credentials._directory(self.con)
        self.assertEqual(directory.stat().st_mode & 0o777,0o700)
        for path in directory.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
        # The P12 password and original P12 are transient, not persisted.
        contents = b''.join(p.read_bytes() for p in directory.iterdir())
        self.assertNotIn(self.password.encode(),contents)
        self.assertFalse(any(p.suffix=='.p12' for p in directory.iterdir()))

    def test_bad_replacement_keeps_previous_working_certificate_and_tokens(self):
        self.save()
        credentials.update_private(self.con, {'access_token':'ACCESS', 'refresh_token':'REFRESH'})
        before = {p.name:p.read_bytes() for p in credentials._directory(self.con).iterdir()}
        with self.assertRaises(credentials.SettingsError) as caught:
            credentials.save(self.con, {'client_secret':'REPLACEMENT', 'certificate_base64':self.certificate,
                'certificate_password':'Wrong-password-secret'})
        self.assertNotIn('Wrong-password-secret',str(caught.exception))
        self.assertEqual(credentials.get(self.con,'client_secret'),'SECRET-CLIENT')
        self.assertEqual(credentials.get(self.con,'refresh_token'),'REFRESH')
        after = {p.name:p.read_bytes() for p in credentials._directory(self.con).iterdir()}
        self.assertEqual(after,before)

    def test_secret_rotation_retains_token_but_identity_change_requires_consent(self):
        self.save()
        credentials.update_private(self.con, {'access_token':'ACCESS','refresh_token':'REFRESH'})
        credentials.save(self.con, {'client_secret':'NEW-SECRET'})
        self.assertEqual(credentials.get(self.con,'refresh_token'),'REFRESH')
        credentials.save(self.con, {'client_id':'another-client'})
        self.assertFalse(credentials.get(self.con,'refresh_token'))
        self.assertFalse(credentials.view(self.con)['certificate_configured'])
        self.assertTrue(credentials.view(self.con)['certificate_requires_replacement'])
        with self.assertRaises(credentials.SettingsError):credentials.tls_context(self.con)

    def test_password_does_not_travel_as_process_argument(self):
        original = subprocess.run
        with patch.object(subprocess,'run',wraps=original) as run:
            self.save()
        self.assertTrue(run.call_count)
        self.assertNotIn(self.password,repr([c.args for c in run.call_args_list]))
        self.assertTrue(any(c.kwargs.get('input')==(self.password+'\n').encode() for c in run.call_args_list))

    def test_tls_context_checks_server_hostname_and_is_not_shared_context(self):
        self.save()
        shared = ssl.create_default_context()
        with patch.object(credentials.fiscal,'ssl_ctx',return_value=shared):
            context = credentials.tls_context(self.con)
        self.assertIsNot(context,shared)
        self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertGreaterEqual(context.minimum_version,ssl.TLSVersion.TLSv1_2)
        self.assertEqual(shared.cert_store_stats()['x509'],ssl.create_default_context().cert_store_stats()['x509'])
        self.assertGreater(context.cert_store_stats()['x509'],shared.cert_store_stats()['x509'])

    def test_invalid_settings_are_rejected_without_changing_store(self):
        self.save()
        for body in ({'redirect_uri':'https://evil.example/callback'}, {'accounts':['not-an-account']},
                     {'certificate_base64':'@notbase64','certificate_password':''},
                     {'certificate_password':'no-file'}, {'client_secret':'line\nsecret'}):
            with self.subTest(keys=list(body)):
                with self.assertRaises(credentials.SettingsError):credentials.save(self.con,body)
                self.assertTrue(credentials.view(self.con)['configured'])

    def test_nested_lock_and_concurrent_updates_preserve_all_keys(self):
        self.save()
        with credentials.lock(self.con):
            credentials.update_private(self.con,{'refresh_token':'REFRESH'})
            self.assertEqual(credentials.get(self.con,'refresh_token'),'REFRESH')
        def update(i):
            con=sqlite3.connect(self.database)
            try:credentials.update_private(con,{f'key_{i}':i})
            finally:con.close()
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(update,range(8)))
        for i in range(8):self.assertEqual(credentials.get(self.con,f'key_{i}'),i)

    def test_accounts_are_discovered_without_manual_selection(self):
        result=credentials.save(self.con, {'client_id':'synthetic-client','client_secret':'SECRET',
            'certificate_base64':self.certificate,'certificate_password':self.password})
        self.assertTrue(result['configured'])
        self.assertFalse(result['accounts_set'])
        credentials.update_private(self.con,{'discovered_accounts':['40702810000000009876']})
        result=credentials.view(self.con)
        self.assertEqual(result['accounts_masked'],['•••• 9876'])
        self.assertFalse(result['accounts_set'])
        self.assertTrue(result['client_secret_updated_at'])
