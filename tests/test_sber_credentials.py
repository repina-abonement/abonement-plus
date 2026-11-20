"""Private bank credentials: transactional replacement, masked UI and verified TLS."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import hashlib
import re
import os
from pathlib import Path
import sqlite3
import ssl
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from crm import sbercredentials as credentials, fiscal


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
        credentials.save(self.con, {'accounts':'40702810000000009876'})
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
        with patch.object(fiscal,'ssl_ctx',side_effect=AssertionError('No network/bootstrap from another bank')), patch('urllib.request.urlopen',side_effect=AssertionError('No network during TLS preparation')):
            context = credentials.tls_context(self.con)
        self.assertIsNot(context,shared)
        self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertGreaterEqual(context.minimum_version,ssl.TLSVersion.TLSv1_2)
        self.assertEqual(shared.cert_store_stats()['x509'],ssl.create_default_context().cert_store_stats()['x509'])
        self.assertGreater(context.cert_store_stats()['x509'],shared.cert_store_stats()['x509'])
        self.assertFalse(context.verify_flags & getattr(ssl,'VERIFY_X509_PARTIAL_CHAIN',0))
        fingerprints={hashlib.sha256(c).hexdigest() for c in context.get_ca_certs(binary_form=True)}
        self.assertTrue({'d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31',
            'bbbde2103e790b999ec62bd03cf625a5a2e7c316e10afe6a490eedead8b3fd9b',
            '2155785036c900dbb5f1bb2a1569c80c55595bd6bf94867a29bbddbc7d88a3f2'}.issubset(fingerprints))
        self.assertTrue({hashlib.sha256(c).hexdigest() for c in shared.get_ca_certs(binary_form=True)}.issubset(fingerprints))

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

    def test_status_reads_do_not_wait_for_bank_refresh_lock(self):
        self.save();entered=threading.Event();release=threading.Event()
        def refresh():
            con=sqlite3.connect(self.database)
            try:
                with credentials.lock(con):entered.set();release.wait(5)
            finally:con.close()
        def snapshot():
            con=sqlite3.connect(self.database)
            try:return credentials.view(con),credentials.get(con,'client_id')
            finally:con.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            held=pool.submit(refresh)
            self.assertTrue(entered.wait(2))
            try:
                view,client=pool.submit(snapshot).result(timeout=1)
                self.assertTrue(view['configured']);self.assertEqual(client,'synthetic-client')
            finally:release.set();held.result(timeout=2)

    def test_bundled_nca_intermediates_chain_to_the_pinned_root(self):
        bundle=Path(credentials.__file__).parent/'certs'/'russian_trust.pem'
        certificates=re.findall(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----',bundle.read_text(),re.S)
        self.assertEqual(len(certificates),3)
        hashes=[hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert)).hexdigest() for cert in certificates]
        self.assertEqual(hashes,[
            'd26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31',
            'bbbde2103e790b999ec62bd03cf625a5a2e7c316e10afe6a490eedead8b3fd9b',
            '2155785036c900dbb5f1bb2a1569c80c55595bd6bf94867a29bbddbc7d88a3f2'])
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'root.pem';root.write_text(certificates[0])
            for cert in certificates[1:]:
                intermediate=Path(folder)/'intermediate.pem';intermediate.write_text(cert)
                result=subprocess.run(['openssl','verify','-x509_strict','-CAfile',str(root),str(intermediate)],
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
                self.assertEqual(result.returncode,0)

    def test_ext_candidate_is_not_loaded_by_default_and_unknown_root_is_ignored(self):
        self.save()
        context=credentials.tls_context(self.con)
        before={hashlib.sha256(c).hexdigest() for c in context.get_ca_certs(binary_form=True)}
        system={hashlib.sha256(c).hexdigest() for c in ssl.create_default_context().get_ca_certs(binary_form=True)}
        if credentials.OFFICIAL_EXT_ROOT_SHA256 not in system:
            self.assertNotIn(credentials.OFFICIAL_EXT_ROOT_SHA256,before)
        with patch.object(credentials,'OFFICIAL_EXT_CHAIN',Path('/not/read/for/unknown/peer.pem')):
            for unknown in (None,'',{},'e77059','0'*64,credentials.OFFICIAL_EXT_INTERMEDIATE_SHA256):
                self.assertFalse(credentials.load_official_ext(context,unknown))
        self.assertEqual(before,{hashlib.sha256(c).hexdigest() for c in context.get_ca_certs(binary_form=True)})

    def test_ext_candidate_requires_observed_pinned_root_and_preserves_tls_checks(self):
        self.save()
        context=credentials.tls_context(self.con)
        flags=context.verify_flags
        self.assertTrue(credentials.load_official_ext(context,credentials.OFFICIAL_EXT_ROOT_SHA256))
        fingerprints={hashlib.sha256(c).hexdigest() for c in context.get_ca_certs(binary_form=True)}
        self.assertTrue({credentials.OFFICIAL_EXT_ROOT_SHA256,credentials.OFFICIAL_EXT_INTERMEDIATE_SHA256}.issubset(fingerprints))
        self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_flags,flags & ~getattr(ssl,'VERIFY_X509_PARTIAL_CHAIN',0))
        self.assertGreaterEqual(context.minimum_version,ssl.TLSVersion.TLSv1_2)

    def test_ext_candidate_tampered_bundle_and_insecure_context_fail_closed(self):
        context=ssl.create_default_context()
        before={hashlib.sha256(c).hexdigest() for c in context.get_ca_certs(binary_form=True)}
        tampered=Path(self.temp.name)/'tampered.pem'
        tampered.write_text((Path(credentials.__file__).parent/'certs'/'sber_chain.pem').read_text())
        with patch.object(credentials,'OFFICIAL_EXT_CHAIN',tampered):
            with self.assertRaises(credentials.SettingsError):credentials.load_official_ext(context,credentials.OFFICIAL_EXT_ROOT_SHA256)
        self.assertEqual(before,{hashlib.sha256(c).hexdigest() for c in context.get_ca_certs(binary_form=True)})
        insecure=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        insecure.check_hostname=False
        with self.assertRaises(credentials.SettingsError):credentials.load_official_ext(insecure,credentials.OFFICIAL_EXT_ROOT_SHA256)

    def test_ext_candidate_intermediate_validates_against_its_full_pinned_root(self):
        certificates=re.findall(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----',credentials.OFFICIAL_EXT_CHAIN.read_text(),re.S)
        self.assertEqual(len(certificates),2)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'root.pem';root.write_text(certificates[0])
            intermediate=Path(folder)/'intermediate.pem';intermediate.write_text(certificates[1])
            result=subprocess.run(['openssl','verify','-x509_strict','-CAfile',str(root),str(intermediate)],
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
            self.assertEqual(result.returncode,0)

    def test_ext_proof_cannot_be_set_through_settings_request_body(self):
        self.save()
        credentials.save(self.con,{'official_ext_trust_confirmed':True})
        self.assertIsNone(credentials.get(self.con,'official_ext_trust_confirmed',None))
        with patch.object(credentials,'load_official_ext',wraps=credentials.load_official_ext) as candidate:
            credentials.tls_context(self.con)
        candidate.assert_not_called()

    def test_internal_verified_ext_proof_reuses_only_the_pinned_official_bundle(self):
        self.save()
        credentials.update_private(self.con,{'official_ext_trust_confirmed':True})
        with patch.object(credentials,'load_official_ext',wraps=credentials.load_official_ext) as candidate:
            context=credentials.tls_context(self.con)
        candidate.assert_called_once_with(context,credentials.OFFICIAL_EXT_ROOT_SHA256)
        fingerprints={hashlib.sha256(c).hexdigest() for c in context.get_ca_certs(binary_form=True)}
        self.assertTrue({credentials.OFFICIAL_EXT_ROOT_SHA256,credentials.OFFICIAL_EXT_INTERMEDIATE_SHA256}.issubset(fingerprints))
        self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertFalse(context.verify_flags & getattr(ssl,'VERIFY_X509_PARTIAL_CHAIN',0))

    def test_non_boolean_ext_proof_does_not_expand_trust(self):
        self.save()
        for invalid in ('true',1,[],{}):
            credentials.update_private(self.con,{'official_ext_trust_confirmed':invalid})
            with patch.object(credentials,'load_official_ext',wraps=credentials.load_official_ext) as candidate:
                credentials.tls_context(self.con)
            candidate.assert_not_called()
