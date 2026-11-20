"""Bounded, process-local evidence from actual failed student-card GETs.

Only sanitized exception metadata is retained. Tracebacks, exceptions, locals,
request data and source lines are never stored. This is not a persistent log.
The HTTP handler must call record only for a failed GET of the matched path;
the diagnostic reader must be exposed only to the owner.
"""
from collections import deque
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import re
import secrets
import sqlite3
from threading import Lock
from zoneinfo import ZoneInfo


MAX_ERRORS = 20
MAX_BYTES = 64 * 1024
MAX_RECORD_BYTES = 4096
MAX_FRAMES = 12
MAX_CHAIN = 3
_CRM = Path(__file__).resolve().parent
_errors = deque()
_lock = Lock()
_bytes = 0
_IDENTIFIER = r'[A-Za-z_][A-Za-z0-9_.]{0,95}'
_PRIVATE = re.compile(r'password|passwd|secret|token|cookie|authorization|credential|session|csrf|private|api_?key', re.I)


def _now():
    return datetime.now(ZoneInfo('Europe/Moscow')).isoformat(timespec='seconds')


def _identifier(value):
    return bool(isinstance(value, str) and re.fullmatch(_IDENTIFIER, value) and not _PRIVATE.search(value))


def _message(error):
    # Do not invoke arbitrary exception/value __str__. Known exception strings
    # can still contain SQL parameters, names, cookies or URLs: allow schemas,
    # field identifiers and fixed messages only, never raw fallback text.
    args = error.args
    raw = args[0] if args and isinstance(args[0], str) else ''
    if isinstance(error, KeyError):
        return 'Missing field: ' + raw if _identifier(raw) else 'Missing field (value hidden)'
    if isinstance(error, IndexError):
        return raw if raw in ('No item with that key', 'list index out of range',
            'tuple index out of range', 'string index out of range', 'deque index out of range') else 'Index out of range (details hidden)'
    if isinstance(error, sqlite3.Error):
        fixed = ('database is locked', 'database table is locked', 'database schema is locked',
            'database disk image is malformed', 'FOREIGN KEY constraint failed',
            'cannot start a transaction within a transaction', 'cannot commit - no transaction is active',
            'cannot rollback - no transaction is active', 'unable to open database file',
            'attempt to write a readonly database', 'disk I/O error', 'interrupted')
        if raw in fixed:
            return raw
        for prefix in ('no such column: ', 'no such table: ', 'no such function: ',
                       'ambiguous column name: ', 'NOT NULL constraint failed: ',
                       'CHECK constraint failed: '):
            if raw.startswith(prefix) and _identifier(raw[len(prefix):]):
                return prefix + raw[len(prefix):]
        prefix = 'UNIQUE constraint failed: '
        if raw.startswith(prefix):
            fields = raw[len(prefix):].split(', ')
            if len(fields) <= 4 and all(_identifier(field) for field in fields):
                return (prefix + ', '.join(fields))[:240]
        match = re.fullmatch(r'table (' + _IDENTIFIER + r') has no column named (' + _IDENTIFIER + r')', raw)
        if match and all(_identifier(value) for value in match.groups()):
            return 'table ' + match[1] + ' has no column named ' + match[2]
        return 'SQLite error (statement and values hidden)'
    if isinstance(error, (NameError, UnboundLocalError)):
        match = re.fullmatch(r"name '(" + _IDENTIFIER + r")' is not defined", raw)
        if match and _identifier(match[1]):
            return "Undefined name: " + match[1]
    if isinstance(error, AttributeError):
        match = re.fullmatch(r"'(" + _IDENTIFIER + r")' object has no attribute '(" + _IDENTIFIER + r")'", raw)
        if match and all(_identifier(value) for value in match.groups()):
            return match[1] + ' has no attribute ' + match[2]
    if isinstance(error, TypeError):
        fixed = ("'NoneType' object is not subscriptable", "'NoneType' object is not iterable",
            "'NoneType' object is not callable", 'list indices must be integers or slices, not str',
            'tuple indices must be integers or slices, not str')
        if raw in fixed:
            return raw
        match = re.fullmatch(r'Object of type (' + _IDENTIFIER + r') is not JSON serializable', raw)
        if match and _identifier(match[1]):
            return 'Object of type ' + match[1] + ' is not JSON serializable'
    return 'Exception details hidden; inspect the captured type and CRM frames'


def _frames(error):
    frames = []
    node = error.__traceback__
    while node is not None:
        code = node.tb_frame.f_code
        try:
            relative = Path(code.co_filename).resolve().relative_to(_CRM)
        except (ValueError, OSError):
            relative = None
        if relative is not None:
            filename = relative.as_posix()
            if re.fullmatch(r'[A-Za-z0-9_./-]+\.py', filename):
                name = code.co_name
                if not re.fullmatch(r'[A-Za-z0-9_<>.]{1,80}', name):
                    name = '<unknown>'
                frames.append({'file':'crm/' + filename, 'line':int(node.tb_lineno), 'function':name})
        node = node.tb_next
    return frames[-MAX_FRAMES:]


def _captured(error):
    chain, seen = [], set()
    node = error
    while node is not None and len(chain) < MAX_CHAIN and id(node) not in seen:
        seen.add(id(node))
        name = type(node).__name__
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', name):
            name = 'Exception'
        chain.append({'type':name, 'message':_message(node)[:240], 'frames':_frames(node)})
        node = node.__cause__ if node.__cause__ is not None else (
            None if node.__suppress_context__ else node.__context__)
    return chain


def record(path, error):
    """Return an opaque ID only for an actual raised card-GET exception.

    Unsupported paths, query strings, unraised exceptions and arbitrary objects
    do not enter the ring. The caller keeps HTTP exception details private.
    """
    global _bytes
    match = re.fullmatch(r'/api/students/([1-9][0-9]{0,17})', path) if isinstance(path, str) else None
    if not match or not isinstance(error, BaseException) or error.__traceback__ is None:
        return None
    chain = _captured(error)
    if not chain:
        return None
    entry = {'error_id':secrets.token_hex(6), 'student_id':int(match[1]), 'captured_at':_now(),
        'type':chain[0]['type'], 'message':chain[0]['message'], 'frames':chain[0]['frames'],
        'chain':chain}
    encoded = len(json.dumps(entry, ensure_ascii=False).encode('utf-8'))
    while encoded > MAX_RECORD_BYTES and any(item['frames'] for item in entry['chain']):
        for item in reversed(entry['chain']):
            if item['frames']:
                item['frames'].pop(0)
                break
        encoded = len(json.dumps(entry, ensure_ascii=False).encode('utf-8'))
    with _lock:
        _errors.append((entry, encoded)); _bytes += encoded
        while len(_errors) > MAX_ERRORS or _bytes > MAX_BYTES:
            _, size = _errors.popleft(); _bytes -= size
    return entry['error_id']


def latest(student_id, error_id=None):
    """Owner-route DTO. A restart clears this process-local ring by design."""
    sid = str(student_id)
    valid_student = bool(re.fullmatch(r'[1-9][0-9]{0,17}', sid))
    valid_error = error_id is None or isinstance(error_id, str) and bool(re.fullmatch(r'[a-f0-9]{12}', error_id))
    with _lock:
        rows = deepcopy([entry for entry, _ in reversed(_errors)
            if valid_student and valid_error and entry['student_id'] == int(sid)
            and (error_id is None or entry['error_id'] == error_id)])
    return {'available':bool(rows), 'errors':rows, 'source':'actual_student_get_failure',
        'checked_at':_now(), 'storage':'process_memory', 'resets_on_restart':True,
        'note':'' if rows else 'В этом процессе CRM нет сохранённой ошибки этой карточки. После перезапуска история очищается; повторите открытие карточки'}
