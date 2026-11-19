"""Read-only public Sber server-certificate evidence, without a client identity.

A failed verified handshake may still expose the server's public certificate
chain. Evidence is never a trust decision; callers must not trust a peer root.
"""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import platform
import re
import ssl
import subprocess
import tempfile
import unicodedata
from urllib.parse import urlparse
from urllib.request import getproxies, proxy_bypass
from zoneinfo import ZoneInfo

HOST = 'fintech.sberbank.ru'
PORT = 9443
TARGET = HOST + ':' + str(PORT)
TIMEOUT = 12
MAX_CAPTURE = 1_000_000
KNOWN_CA_SHA256 = frozenset((
    'd26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31',
    'bbbde2103e790b999ec62bd03cf625a5a2e7c316e10afe6a490eedead8b3fd9b',
    '2155785036c900dbb5f1bb2a1569c80c55595bd6bf94867a29bbddbc7d88a3f2',
    '4908f11ad2cb9d2c8e92fec594accc208ea0e36f77a9179885d532fe48290681',
    'b0a88dc593160591b947c451793846f17f634d6e9a46140d382167dda7ea6d28',
    'e77059cd24606a8a24043100ec7e23db917bfc85054ee08b79ca71b43b8f4690',
    'b9b14bf1cbc063d106d095eac17f1bc93720031ac2894bac557b9bf91e2c1ae8',
))


def _route():
    # Match urllib ProxyHandler: an HTTPS request uses the 'https' entry, not
    # HTTP_PROXY or ALL_PROXY. Bypass is evaluated with Request.host (port too).
    proxies = getproxies()
    proxy = proxies.get('https')
    configured = bool(proxy)
    bypassed = bool(proxy_bypass(TARGET)) if configured else False
    if not configured or bypassed:
        return 'direct', configured, bypassed, None
    try:
        parsed = urlparse(proxy)
        if (parsed.scheme.lower() != 'http' or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise ValueError()
        host = parsed.hostname
        if any(unicodedata.category(c).startswith('C') for c in host):
            raise ValueError()
        port = parsed.port or 80
        if not 1 <= port <= 65535:
            raise ValueError()
        # OpenSSL -proxy accepts host:port; IPv6 literals must retain brackets.
        endpoint = ('[' + host + ']' if ':' in host else host) + ':' + str(port)
        return 'http_proxy', True, False, endpoint
    except (TypeError, ValueError):
        return 'unsupported', True, False, None


def _cn(subject):
    for group in subject or ():
        for key, value in group:
            if key == 'commonName':
                return ''.join(c for c in str(value) if not unicodedata.category(c).startswith('C'))[:160]
    return ''


def _date(value):
    if not value:
        return None
    try:
        return datetime.fromtimestamp(ssl.cert_time_to_seconds(value), timezone.utc).isoformat(timespec='seconds')
    except (ValueError, OverflowError):
        return None


def _evidence(stdout, stderr, folder):
    # Do not retain session dumps, raw DNs, HTTP payloads, endpoint URLs or logs.
    if not isinstance(stdout, bytes):
        stdout = b''
    if not isinstance(stderr, bytes):
        stderr = b''
    stdout = stdout[:MAX_CAPTURE]
    stderr = stderr[:MAX_CAPTURE]
    raw_codes = re.findall(rb'(?:verify error:num=|Verify return code:\s*)([0-9]{1,3})(?=[:\s(]|$)', stdout + b'\n' + stderr)
    codes = sorted(set(int(code) for code in raw_codes))
    chain, seen = [], set()
    for index, block in enumerate(re.findall(rb'-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\r\n]+-----END CERTIFICATE-----', stdout)):
        if index >= 12:
            break
        try:
            pem = block.decode('ascii')
            der = ssl.PEM_cert_to_DER_cert(pem)
            digest = hashlib.sha256(der).hexdigest()
            if digest in seen:
                continue
            path = Path(folder) / ('public-peer-' + str(index) + '.pem')
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as handle:
                handle.write(pem)
            decoded = ssl._ssl._test_decode_cert(str(path))
            chain.append({'sha256': digest, 'subject_cn': _cn(decoded.get('subject')),
                'issuer_cn': _cn(decoded.get('issuer')), 'not_before': _date(decoded.get('notBefore')),
                'not_after': _date(decoded.get('notAfter'))})
            seen.add(digest)
        except (ValueError, UnicodeError, OSError, ssl.SSLError):
            continue
    return codes, chain


def inspect(context, current=None):
    """Capture public server-chain metadata along urllib's route; never send mTLS keys."""
    current = current or datetime.now(ZoneInfo('Europe/Moscow'))
    if current.tzinfo is not None:
        current = current.astimezone(ZoneInfo('Europe/Moscow')).replace(tzinfo=None)
    result = {'checked_at': current.isoformat(timespec='seconds'), 'status': 'unavailable',
        'probe_route': 'direct', 'api_proxy_configured': False, 'proxy_bypassed': False,
        'verify_codes': [], 'chain': [], 'runtime': {'python': platform.python_version(), 'openssl': ssl.OPENSSL_VERSION},
        'trust': {'anchor_count': 0, 'verify_flags': 0, 'hostname_check': False, 'verify_required': False, 'loaded_expected_sha256': []}}
    try:
        route, configured, bypassed, proxy = _route()
        result.update(probe_route=route if route != 'unsupported' else 'http_proxy',
            api_proxy_configured=configured, proxy_bypassed=bypassed)
        if route == 'unsupported':
            result['status'] = 'route_unsupported'
            return result
        certificates = context.get_ca_certs(binary_form=True)
        flags = int(context.verify_flags)
        hashes = {hashlib.sha256(cert).hexdigest() for cert in certificates}
        result['trust'] = {'anchor_count': len(certificates), 'verify_flags': flags,
            'hostname_check': bool(context.check_hostname), 'verify_required': context.verify_mode == ssl.CERT_REQUIRED,
            'loaded_expected_sha256': sorted(hashes & KNOWN_CA_SHA256)}
        # Refuse an unverified context instead of silently running a weaker probe.
        if context.verify_mode != ssl.CERT_REQUIRED or not certificates:
            return result
        with tempfile.TemporaryDirectory(prefix='sber-public-peer-') as folder:
            os.chmod(folder, 0o700)
            ca = Path(folder) / 'public-ca.pem'
            fd = os.open(ca, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as handle:
                handle.write(''.join(ssl.DER_cert_to_PEM_cert(cert) for cert in certificates))
            args = ['openssl', 's_client', '-connect', TARGET, '-servername', HOST, '-showcerts',
                '-verify', '10', '-verify_return_error', '-verify_hostname', HOST, '-CAfile', str(ca)]
            capath = ssl.get_default_verify_paths().capath
            if capath and os.path.isdir(capath):
                args.extend(['-CApath', capath])
            if flags & int(ssl.VERIFY_X509_STRICT):
                args.append('-x509_strict')
            if flags & int(ssl.VERIFY_CRL_CHECK_CHAIN) == int(ssl.VERIFY_CRL_CHECK_CHAIN):
                args.append('-crl_check_all')
            elif flags & int(ssl.VERIFY_CRL_CHECK_LEAF):
                args.append('-crl_check')
            # Deliberately do not enable -partial_chain or provide -cert/-key.
            if proxy is not None:
                args.extend(['-proxy', proxy])
            try:
                completed = subprocess.run(args, input=b'', stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    timeout=TIMEOUT, check=False)
                stdout, stderr = completed.stdout, completed.stderr
            except subprocess.TimeoutExpired as error:
                stdout, stderr = error.stdout or b'', error.stderr or b''
                result['probe_timed_out'] = True
            result['verify_codes'], result['chain'] = _evidence(stdout, stderr, folder)
            for certificate in result['chain']:
                certificate['matched_loaded_anchor'] = certificate['sha256'] in hashes
            if result['chain']:
                result['status'] = 'captured'
    except (OSError, ValueError, TypeError, ssl.SSLError):
        # No exception text: subprocess/SSL errors can contain paths or addresses.
        pass
    return result
