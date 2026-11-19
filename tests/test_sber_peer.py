"""Public TLS metadata only: no bank identity, HTTP request or secrets."""
from datetime import datetime
from pathlib import Path
import hashlib
import json
import os
import re
import ssl
import stat
import socket
import shutil
import tempfile
import threading
import subprocess
import unittest
from unittest.mock import patch

from crm import sberpeer


class SberPeer(unittest.TestCase):
    current = datetime(2026, 11, 20, 0, 30)

    @classmethod
    def setUpClass(cls):
        text = (Path(__file__).parents[1] / 'crm/certs/sber_chain.pem').read_text()
        cls.pem = re.search(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', text, re.S).group(0)
        cls.der = ssl.PEM_cert_to_DER_cert(cls.pem)

    def setUp(self):
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self.context.load_verify_locations(cadata=self.pem)
        self.context.verify_flags |= ssl.VERIFY_X509_STRICT
        self.proxies = patch.object(sberpeer, 'getproxies', return_value={}).start()
        self.bypass = patch.object(sberpeer, 'proxy_bypass', return_value=False).start()
        self.addCleanup(patch.stopall)

    def output(self):
        return ('SESSION SECRET DUMP SHOULD NOT BE KEPT\n' + self.pem + '\nVerify return code: 19 (self-signed certificate in certificate chain)\n').encode()

    def test_verified_failed_handshake_collects_only_public_certificate_fields(self):
        paths = []
        def run(args, **kwargs):
            self.assertEqual(args[:2], ['openssl', 's_client'])
            self.assertEqual(args[args.index('-connect')+1], sberpeer.TARGET)
            self.assertEqual(args[args.index('-servername')+1], sberpeer.HOST)
            self.assertIn('-verify_return_error', args)
            self.assertIn('-verify_hostname', args)
            self.assertIn('-x509_strict', args)
            for forbidden in ('-cert', '-key', '-pass', '-partial_chain', '-no-CAfile'):
                self.assertNotIn(forbidden, args)
            self.assertEqual(kwargs['input'], b'')
            self.assertEqual(kwargs['timeout'], 12)
            ca = Path(args[args.index('-CAfile')+1]); paths.append(ca.parent)
            self.assertEqual(stat.S_IMODE(ca.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(ca.parent.stat().st_mode), 0o700)
            self.assertIn(self.pem, ca.read_text())
            return subprocess.CompletedProcess(args, 1, self.output(), b'verify error:num=19:self-signed certificate')
        with patch.object(sberpeer.subprocess, 'run', side_effect=run):
            result = sberpeer.inspect(self.context, self.current)
        self.assertEqual(result['status'], 'captured')
        self.assertEqual(result['verify_codes'], [19])
        self.assertEqual(result['probe_route'], 'direct')
        cert = result['chain'][0]
        self.assertEqual(set(cert), {'sha256','subject_cn','issuer_cn','not_before','not_after','matched_loaded_anchor'})
        self.assertEqual(cert['sha256'], hashlib.sha256(self.der).hexdigest())
        self.assertEqual(cert['subject_cn'], 'SberAPI Root CA')
        self.assertTrue(cert['matched_loaded_anchor'])
        self.assertGreaterEqual(result['trust']['anchor_count'], 1)
        self.assertLessEqual(len(result['trust']['loaded_expected_sha256']), 7)
        self.assertNotIn('SESSION SECRET', json.dumps(result))
        self.assertNotIn('BEGIN CERTIFICATE', json.dumps(result))
        self.assertTrue(result['trust']['verify_required'])
        self.assertTrue(result['trust']['hostname_check'])
        self.assertTrue(all(not path.exists() for path in paths))

    def test_anonymous_http_proxy_matches_route_without_sending_proxy_credentials(self):
        self.proxies.return_value = {'https':'http://proxy.example:8080','http':'http://irrelevant:80'}
        with patch.object(sberpeer.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, self.output(), b'')) as run:
            result = sberpeer.inspect(self.context)
        args = run.call_args.args[0]
        self.assertEqual(args[args.index('-proxy')+1], 'proxy.example:8080')
        self.assertEqual(result['probe_route'], 'http_proxy')
        self.assertTrue(result['api_proxy_configured'])
        self.bypass.assert_called_once_with(sberpeer.TARGET)
        self.assertNotIn('proxy.example', json.dumps(result))

    def test_unsupported_or_authenticated_proxy_never_falls_back_to_direct(self):
        for proxy in ('https://proxy:443','http://username:SECRET@proxy:8080','http://username@proxy:8080','proxy:8080','http://proxy:bad'):
            self.proxies.return_value = {'https':proxy}
            with self.subTest(proxy=proxy), patch.object(sberpeer.subprocess, 'run') as run:
                result = sberpeer.inspect(self.context)
                run.assert_not_called()
                self.assertEqual(result['status'], 'route_unsupported')
                self.assertNotIn('SECRET', json.dumps(result))

    def test_proxy_bypass_is_direct_and_only_https_proxy_selects_route(self):
        self.proxies.return_value = {'https':'http://username:SECRET@proxy:8080'}
        self.bypass.return_value = True
        with patch.object(sberpeer.subprocess, 'run', return_value=subprocess.CompletedProcess([],1,b'',b'')) as run:
            result = sberpeer.inspect(self.context)
        self.assertNotIn('-proxy', run.call_args.args[0])
        self.assertTrue(result['proxy_bypassed'])
        self.assertEqual(result['probe_route'], 'direct')
        self.proxies.return_value = {'http':'http://unused:8080','all':'http://also-unused:8080'}
        with patch.object(sberpeer.subprocess, 'run', return_value=subprocess.CompletedProcess([],1,b'',b'')):
            result = sberpeer.inspect(self.context)
        self.assertFalse(result['api_proxy_configured'])

    def test_timeout_and_missing_openssl_do_not_disclose_exception_details(self):
        with patch.object(sberpeer.subprocess, 'run', side_effect=subprocess.TimeoutExpired('SECRET COMMAND',12,output=self.output(),stderr=b'verify error:num=19:private text')):
            result = sberpeer.inspect(self.context)
        self.assertEqual(result['status'], 'captured')
        self.assertTrue(result['probe_timed_out'])
        self.assertNotIn('SECRET', json.dumps(result))
        with patch.object(sberpeer.subprocess, 'run', side_effect=FileNotFoundError('/private/secret/path')):
            result = sberpeer.inspect(self.context)
        self.assertEqual(result['status'], 'unavailable')
        self.assertNotIn('/private', json.dumps(result))

    def test_unverified_context_never_opens_network(self):
        self.context.check_hostname = False
        self.context.verify_mode = ssl.CERT_NONE
        with patch.object(sberpeer.subprocess, 'run') as run:
            result = sberpeer.inspect(self.context)
        run.assert_not_called()
        self.assertEqual(result['status'], 'unavailable')
        self.assertFalse(result['trust']['verify_required'])

    def test_certificate_metadata_sanitizes_common_name(self):
        self.assertEqual(sberpeer._cn(((('commonName','bank\n\x00\u202ename'),),)), 'bankname')
        self.assertEqual(sberpeer._cn(()), '')

    @unittest.skipUnless(shutil.which('openssl'), 'OpenSSL CLI unavailable')
    def test_real_failed_verified_handshake_retains_public_chain_on_loopback(self):
        # Generated server-only fixture keys remain inside this temporary test.
        # The probe is never given a client key or certificate.
        real_run = subprocess.run
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            def generate(args):
                real_run(['openssl', *args], cwd=folder, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, check=True, timeout=10)
            generate(['req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                '-subj','/CN=Public loopback CA','-keyout','root.key','-out','root.pem'])
            generate(['req','-newkey','rsa:2048','-nodes','-subj','/CN=fintech.sberbank.ru',
                '-keyout','server.key','-out','server.csr'])
            (folder/'extensions').write_text('subjectAltName=DNS:fintech.sberbank.ru\n')
            generate(['x509','-req','-in','server.csr','-CA','root.pem','-CAkey','root.key',
                '-CAcreateserial','-days','1','-extfile','extensions','-out','server.pem'])
            (folder/'chain.pem').write_bytes((folder/'server.pem').read_bytes()+(folder/'root.pem').read_bytes())
            server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_context.load_cert_chain(folder/'chain.pem', folder/'server.key')
            listener = socket.socket(); listener.bind(('127.0.0.1',0)); listener.listen(1); listener.settimeout(5)
            port = listener.getsockname()[1]
            def serve():
                try:
                    peer,_ = listener.accept()
                    with peer:
                        try:
                            with server_context.wrap_socket(peer, server_side=True):
                                pass
                        except ssl.SSLError:
                            pass
                except OSError:
                    pass
            thread=threading.Thread(target=serve,daemon=True);thread.start()
            def local_probe(args, **kwargs):
                self.assertEqual(args[args.index('-connect')+1],sberpeer.TARGET)
                args=list(args);args[args.index('-connect')+1]='127.0.0.1:'+str(port)
                return real_run(args,**kwargs)
            try:
                with patch.object(sberpeer.subprocess,'run',side_effect=local_probe):
                    result=sberpeer.inspect(self.context)
            finally:
                listener.close();thread.join(5)
            self.assertEqual(result['status'],'captured')
            self.assertIn(19,result['verify_codes'])
            self.assertEqual(len(result['chain']),2)
            self.assertEqual(result['chain'][0]['subject_cn'],'fintech.sberbank.ru')
            self.assertEqual(result['chain'][1]['subject_cn'],'Public loopback CA')
            self.assertFalse(any(cert['matched_loaded_anchor'] for cert in result['chain']))
