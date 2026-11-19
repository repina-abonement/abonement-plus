"""Private Sber credentials and client certificate, outside tracked source files.

UI projections never contain secrets, complete account numbers or private paths.
Only the owner-only server routes should call save(). Network operations use the
fixed Sber endpoints in sberbank; these settings cannot select a destination.
"""
import base64
import binascii
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import tempfile
import threading

from . import fiscal

REDIRECT_URI = 'https://ab-plus.ru/api/sber/oauth/callback'
MAX_CERTIFICATE_BYTES = 2 * 1024 * 1024
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
_LOCAL = threading.local()
_TOKEN_KEYS = ('access_token', 'refresh_token', 'id_token', 'expires_at',
               'token_expires_at', 'refresh_expires_at', 'scope', 'token_scope',
               'authorized_scope', 'authorization_checked_at', 'discovered_accounts', 'accounts_checked_at')


class SettingsError(ValueError):
    """A safe user-facing error without bank response or credential contents."""


def _directory(con):
    row = next((r for r in con.execute('PRAGMA database_list') if r[1] == 'main'), None)
    if row is None or not row[2]:
        raise SettingsError('Для подключения Сбера нужна постоянная база CRM')
    return Path(str(Path(row[2]).absolute()) + '.sber')


def _secure_directory(directory):
    if directory.is_symlink():
        raise SettingsError('Не удалось открыть защищённое хранилище Сбера')
    try:
        directory.mkdir(mode=0o700, exist_ok=True)
        if not directory.is_dir():
            raise OSError()
        os.chmod(directory, 0o700)
    except OSError:
        raise SettingsError('Не удалось открыть защищённое хранилище Сбера') from None


@contextmanager
def lock(con):
    """Serialize settings and token rotation across threads and server processes."""
    directory = _directory(con)
    key = str(directory)
    with _LOCKS_GUARD:
        mutex = _LOCKS.setdefault(key, threading.RLock())
    with mutex:
        depths = getattr(_LOCAL, 'depths', {})
        _LOCAL.depths = depths
        if depths.get(key):
            depths[key] += 1
            try:
                yield
            finally:
                depths[key] -= 1
            return
        _secure_directory(directory)
        try:
            fd = os.open(directory / 'lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            os.fchmod(fd, 0o600)
        except OSError:
            raise SettingsError('Не удалось открыть защищённое хранилище Сбера') from None
        with os.fdopen(fd, 'r+') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            depths[key] = 1
            try:
                yield
            finally:
                depths.pop(key, None)
                fcntl.flock(handle, fcntl.LOCK_UN)


def _read(con):
    path = _directory(con) / 'credentials.json'
    if not path.exists():
        return {}
    if path.is_symlink():
        raise SettingsError('Не удалось открыть защищённое хранилище Сбера')
    try:
        os.chmod(path, 0o600)
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (OSError, ValueError):
        raise SettingsError('Не удалось прочитать защищённое хранилище Сбера') from None


def _atomic_write(path, value):
    fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write(con, data):
    try:
        encoded = json.dumps(data, ensure_ascii=False).encode()
        _atomic_write(_directory(con) / 'credentials.json', encoded)
    except (OSError, TypeError, ValueError):
        raise SettingsError('Не удалось сохранить подключение Сбера') from None


def get(con, key, default=''):
    with lock(con):
        return _read(con).get(key, default)


def update_private(con, values):
    """Internal token update. Never pass a request body directly to this method."""
    with lock(con):
        data = _read(con)
        data.update(values)
        _write(con, data)


def _accounts(value):
    rows = re.split(r'[\s,;]+', value.strip()) if isinstance(value, str) else value
    if not isinstance(rows, list) or not rows or len(rows) > 50:
        raise SettingsError('Укажите номера счетов Сбера: по одному в строке')
    if any(not isinstance(v, str) or not re.fullmatch(r'\d{10,40}', v, re.ASCII) for v in rows):
        raise SettingsError('Номер счёта должен содержать от 10 до 40 цифр')
    return list(dict.fromkeys(rows))


def _openssl(args, *, password=None):
    try:
        result = subprocess.run(['openssl', *args],
            input=(password + '\n').encode() if password is not None else None,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20,
            check=False)
        if result.returncode:
            raise SettingsError('Не удалось прочитать сертификат Сбера: проверьте файл и пароль')
        return result.stdout
    except (OSError, subprocess.TimeoutExpired):
        raise SettingsError('Не удалось прочитать сертификат Сбера: проверьте файл и пароль') from None


def _certificate(con, encoded, password):
    if not isinstance(encoded, str) or len(encoded) > (MAX_CERTIFICATE_BYTES * 4 // 3 + 8):
        raise SettingsError('Сертификат должен быть файлом P12 размером до 2 МБ')
    if not isinstance(password, str) or len(password) > 4096 or '\n' in password or '\r' in password:
        raise SettingsError('Проверьте пароль сертификата')
    try:
        blob = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise SettingsError('Не удалось прочитать файл сертификата') from None
    if not blob or len(blob) > MAX_CERTIFICATE_BYTES:
        raise SettingsError('Сертификат должен быть файлом P12 размером до 2 МБ')
    # Private temporary files are removed on every success/error path. Password
    # travels on stdin, never in a command argument or exception message.
    with tempfile.TemporaryDirectory(prefix='.certificate-', dir=_directory(con)) as temp:
        folder = Path(temp)
        source, pem = folder / 'source.p12', folder / 'client.pem'
        _atomic_write(source, blob)
        try:
            _openssl(['pkcs12', '-in', str(source), '-out', str(pem), '-nodes', '-passin', 'stdin'], password=password)
        except SettingsError:
            # Older bank P12 containers can require OpenSSL 3's legacy decoder;
            # this affects local decryption only, never the TLS trust policy.
            _openssl(['pkcs12', '-legacy', '-in', str(source), '-out', str(pem), '-nodes', '-passin', 'stdin'], password=password)
        if not pem.exists():
            raise SettingsError('Сертификат Сбера не содержит ключ')
        os.chmod(pem, 0o600)
        try:
            validator = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            validator.load_cert_chain(str(pem))
        except (ssl.SSLError, OSError):
            raise SettingsError('Сертификат и ключ Сбера не удалось проверить') from None
        _openssl(['x509', '-in', str(pem), '-checkend', '0', '-noout'])
        try:
            expiry = _openssl(['x509', '-in', str(pem), '-enddate', '-noout']).decode().strip().split('=', 1)[1]
            expires_at = datetime.strptime(expiry, '%b %d %H:%M:%S %Y %Z').replace(tzinfo=timezone.utc).isoformat()
        except (ValueError, IndexError, UnicodeError):
            raise SettingsError('Не удалось проверить срок сертификата Сбера') from None
        digest = hashlib.sha256(blob).hexdigest()
        name = 'client-' + digest + '.pem'
        _atomic_write(_directory(con) / name, pem.read_bytes())
        return name, expires_at


def save(con, body):
    if not isinstance(body, dict):
        raise SettingsError('Проверьте настройки Сбера')
    if body.get('redirect_uri') not in (None, '', REDIRECT_URI):
        raise SettingsError('Адрес возврата должен совпадать с адресом подключения CRM')
    changes = {}
    for key, limit in (('client_id', 256), ('client_secret', 4096)):
        value = body.get(key)
        if value not in (None, ''):
            if not isinstance(value, str) or len(value) > limit or any(c in value for c in '\r\n\x00'):
                raise SettingsError('Проверьте реквизиты сервиса Сбера')
            changes[key] = value.strip() if key == 'client_id' else value
    if body.get('accounts') not in (None, '', []):
        changes['accounts'] = _accounts(body['accounts'])
    has_certificate = body.get('certificate_base64') not in (None, '')
    if 'certificate_password' in body and not has_certificate:
        raise SettingsError('Пароль сертификата указывается вместе с файлом P12')
    if has_certificate and 'certificate_password' not in body:
        raise SettingsError('Укажите пароль файла P12')
    with lock(con):
        data = _read(con)
        before = dict(data)
        if 'client_secret' in changes and changes['client_secret'] != before.get('client_secret'):
            changes['client_secret_updated_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
        if has_certificate:
            name, expires = _certificate(con, body['certificate_base64'], body['certificate_password'])
            changes.update(certificate_file=name, certificate_expires_at=expires,
                certificate_client_id=changes.get('client_id') or before.get('client_id') or '')
        changes['redirect_uri'] = REDIRECT_URI
        data.update(changes)
        # Rotating just client_secret must retain the renewable token. A new
        # identity, certificate or account consent must be authorized again.
        if any(key in changes and changes[key] != before.get(key) for key in ('client_id', 'accounts', 'certificate_file', 'redirect_uri')):
            for key in _TOKEN_KEYS:
                data.pop(key, None)
        _write(con, data)
        return view(con)


def view(con):
    with lock(con):
        data = _read(con)
        explicit_accounts = data.get('accounts') or []
        accounts = explicit_accounts or data.get('discovered_accounts') or []
        name = data.get('certificate_file', '')
        cert_present = bool(re.fullmatch(r'client-[a-f0-9]{64}\.pem', name) and (_directory(con)/name).is_file())
        cert = cert_present and bool(data.get('client_id')) and data.get('certificate_client_id') == data.get('client_id')
        secret = bool(data.get('client_secret'))
        masked = ['•••• ' + str(a)[-4:] for a in accounts]
        return {'client_id': data.get('client_id', ''), 'redirect_uri': REDIRECT_URI,
            'client_secret_configured': secret, 'client_secret_set': secret,
            'certificate_configured': cert, 'certificate_set': cert,
            'certificate_expires_at': data.get('certificate_expires_at'),
            'certificate_requires_replacement': cert_present and not cert,
            'client_secret_updated_at': data.get('client_secret_updated_at'),
            'accounts': masked, 'accounts_masked': masked,
            'accounts_configured': bool(accounts), 'accounts_set': bool(explicit_accounts),
            'configured': bool(data.get('client_id') and secret and cert),
            'connected': bool(data.get('refresh_token')),
            'token_expires_at': data.get('token_expires_at') or data.get('expires_at')}


def tls_context(con):
    with lock(con):
        data = _read(con)
        name = data.get('certificate_file', '')
        if not data.get('client_id') or data.get('certificate_client_id') != data.get('client_id') or not re.fullmatch(r'client-[a-f0-9]{64}\.pem', name):
            raise SettingsError('Загрузите сертификат Сбера в подключении CRM')
        path = _directory(con) / name
        if path.is_symlink() or not path.is_file():
            raise SettingsError('Загрузите сертификат Сбера в подключении CRM')
        try:
            os.chmod(path, 0o600)
            base = fiscal.ssl_ctx()
            roots = ''.join(ssl.DER_cert_to_PEM_cert(cert) for cert in base.get_ca_certs(binary_form=True))
            context = ssl.create_default_context(cadata=roots)
            context.load_verify_locations(cafile=str(Path(__file__).parent / 'certs' / 'sber_chain.pem'))
            # Do not mutate fiscal's shared context or send this client identity to another bank.
            context.verify_mode = ssl.CERT_REQUIRED
            context.check_hostname = True
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(str(path))
            return context
        except (OSError, ssl.SSLError):
            raise SettingsError('Не удалось подготовить защищённое соединение со Сбером') from None
