"""Narrow, owner-confirmed accounting recovery; never calls payment providers.

Only the reviewed CRM-175 case may be applied. Other historical balance cases
remain previews. The immutable run and its guarded undo affect local accounting
metadata, never replace a database or create incoming money.
"""
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

from . import erpbalancerecovery as engine, historicalrefunds
from .erpsinglerecoveryweb import _backup_current, provenance
from .services import ApiError


APPLY_CASES = frozenset({'88202'})
RUNS = 'erp_balance_recovery_runs'
SCHEMA = '''CREATE TABLE IF NOT EXISTS erp_balance_recovery_runs (
 id INTEGER PRIMARY KEY,erp_id TEXT NOT NULL,student_id INTEGER NOT NULL REFERENCES students(id),
 subscription_id INTEGER NOT NULL REFERENCES subscriptions(id),evidence_id TEXT NOT NULL UNIQUE,
 request_id TEXT NOT NULL UNIQUE,request_token TEXT NOT NULL,case_fingerprint TEXT NOT NULL,
 before_json TEXT NOT NULL,after_json TEXT NOT NULL,before_fingerprint TEXT NOT NULL,
 after_fingerprint TEXT NOT NULL,before_summary TEXT NOT NULL,after_summary TEXT NOT NULL,
 state TEXT NOT NULL,created_at TEXT NOT NULL,user_name TEXT NOT NULL,backup_path TEXT NOT NULL,
 rolled_back_at TEXT,rolled_back_by TEXT,rollback_backup_path TEXT)'''


def init(con):
    # Startup-only DDL. Never implicitly commit a caller's financial write.
    con.execute(SCHEMA)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _stamp():
    return datetime.now(ZoneInfo('Europe/Moscow')).isoformat(timespec='seconds')


def _owner(user):
    if (user or {}).get('role') != 'admin' or not str((user or {}).get('name') or '').strip():
        raise ApiError('Восстановление подтверждает только руководитель')


def _data(body):
    if not isinstance(body, dict):
        raise ApiError('Укажите ID клиента ERP и подтверждённый план')
    erp_id = str(body.get('erp_id') or '').strip()
    if not erp_id or len(erp_id) > 64:
        raise ApiError('Укажите ID клиента ERP')
    # The browser can identify a reviewed case, never supply accounting amounts,
    # source payments, dates, purchases or a replacement case fingerprint.
    if set(body) - {'erp_id', 'token', 'request_id'}:
        raise ApiError('Исходные данные восстановления задаются проверенным планом сервера')
    return erp_id


def _tables(con):
    return {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _state(con, sid):
    """All card-owned rows and cross-card references guard apply and undo.

    In particular, a later receipt, balance use, payment link or attendance mark
    blocks undo. The run table is excluded to avoid self-referential hashes.
    """
    tables = _tables(con)
    result = {}
    for table in sorted(tables - {RUNS}):
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', table) or table.startswith('sqlite_'):
            continue
        columns = {row['name'] for row in con.execute('PRAGMA table_info(' + table + ')')}
        clauses, args = [], []
        if table == 'students':
            clauses.append('id=?'); args.append(sid)
        if 'student_id' in columns:
            clauses.append('student_id=?'); args.append(sid)
        if 'subscription_id' in columns:
            clauses.append('subscription_id IN (SELECT id FROM subscriptions WHERE student_id=?)'); args.append(sid)
        for field in ('payment_id', 'original_payment_id', 'refund_payment_id'):
            if field in columns and 'payments' in tables:
                clauses.append(field + ' IN (SELECT id FROM payments WHERE student_id=?)'); args.append(sid)
        for field in ('link_id', 'pay_link_id'):
            if field in columns and 'pay_links' in tables:
                clauses.append(field + ' IN (SELECT id FROM pay_links WHERE student_id=?)'); args.append(sid)
        if 'snapshot_id' in columns and 'balance_account_snapshots' in tables:
            clauses.append('snapshot_id IN (SELECT id FROM balance_account_snapshots WHERE student_id=?)'); args.append(sid)
        if clauses:
            rows = [dict(row) for row in con.execute('SELECT * FROM ' + table + ' WHERE ' + ' OR '.join(clauses), args)]
            result[table] = sorted(rows, key=_json)
    return result


def _case(erp_id):
    value = engine.CASES.get(erp_id)
    return dict(value) if value and value.get('erp_id') == erp_id else None


def _student(con, erp_id):
    rows = list(con.execute('SELECT id FROM students WHERE erp_id=?', (erp_id,)))
    return rows[0]['id'] if len(rows) == 1 else None


def _public_run(con, row):
    sid = row['student_id']
    newest = con.execute('SELECT id FROM ' + RUNS + ' WHERE student_id=? ORDER BY id DESC LIMIT 1', (sid,)).fetchone()
    changed = _hash(_state(con, sid)) != row['after_fingerprint']
    can_undo = row['state'] == 'applied' and newest['id'] == row['id'] and not changed
    case = _case(row['erp_id'])
    current_summary = engine._summary(con, sid, row['subscription_id'], case['as_of_date']) if case else None
    if current_summary and current_summary['invoice']['status'] == 'cancelled':
        # Stored recovery results remain historical. A later cancelled bill is
        # no longer payable in the separate live summary.
        current_summary['invoice']['payable'] = 0
    return {'run_id':row['id'], 'erp_id':row['erp_id'], 'state':row['state'],
        'student_id':sid, 'subscription_id':row['subscription_id'], 'created_at':row['created_at'],
        'user_name':row['user_name'], 'before_summary':json.loads(row['before_summary']),
        'after_summary':json.loads(row['after_summary']), 'can_rollback':can_undo,
        'rollback_token':_hash([row['id'], row['after_fingerprint']]) if can_undo else None,
        'rolled_back_at':row['rolled_back_at'], 'backup_filename':Path(row['backup_path']).name,
        'current_summary':current_summary, 'state_changed_since_apply':changed,
        'summary_note':'Состояние карточки изменилось после восстановления. Итоги до и после относятся к сохранённой операции' if changed else ''}


def _runs(con, erp_id):
    if RUNS not in _tables(con):
        return []
    return [_public_run(con, row) for row in con.execute('SELECT * FROM ' + RUNS +
        ' WHERE erp_id=? ORDER BY id DESC LIMIT 10', (erp_id,))]


def _metadata():
    return {'provenance':provenance(), 'checked_at':_stamp(), 'live_provider_requests':False,
            'external_actions':False, 'copy_scope':'full_server_database'}


def _planned(con, erp_id):
    # All calculations and any engine preview DDL are confined to this full copy.
    copy = historicalrefunds._clone(con)
    try:
        case = _case(erp_id)
        sid = _student(copy, erp_id)
        runs = _runs(copy, erp_id)
        if case and runs and runs[0]['state'] == 'applied':
            run = runs[0]
            return {'erp_id':erp_id, 'student_id':sid, 'subscription_id':run['subscription_id'],
                'read_only':True, 'preview_on_copy':True, 'snapshot_on_copy':True,
                'plan_ready':True, 'can_apply':False, 'production_apply_available':erp_id in APPLY_CASES,
                'already_applied':True, 'before':run['before_summary'],
                'without_balance':run['after_summary'], 'current_summary':run['current_summary'],
                'state_changed_since_apply':run['state_changed_since_apply'], 'summary_note':run['summary_note'],
                'runs':runs, 'last_run':run, 'issues':[]}
        result = engine.preview(copy, erp_id)
        allowed = bool(erp_id in APPLY_CASES and case and result.get('plan_ready') and sid is not None)
        initialized = RUNS in _tables(copy)
        if not initialized:
            result.setdefault('issues', []).append('Сервер нужно обновить перед применением восстановления')
        if runs:
            allowed = False
            result.setdefault('issues', []).append('Этот план уже был отменён. Для нового применения нужна отдельная сверка')
        state = _state(copy, sid) if sid is not None else None
        token = _hash([erp_id, case, state]) if allowed and initialized else None
        result.update(student_id=sid, subscription_id=case.get('subscription_id') if case else None,
            can_apply=bool(allowed and initialized), production_apply_available=bool(erp_id in APPLY_CASES and case),
            token=token, state_fingerprint=_hash(state) if state is not None else None,
            runs=runs, last_run=runs[0] if runs else None, read_only=True,
            preview_on_copy=True, snapshot_on_copy=True)
        return result
    finally:
        copy.close()


def plan(con, erp_id):
    return _planned(con, str(erp_id)) | _metadata()


@contextmanager
def _write(con):
    if con.in_transaction:
        raise ApiError('Восстановление нужно запустить отдельным подтверждением')
    con.execute('BEGIN IMMEDIATE')
    try:
        yield
        con.commit()
    except BaseException:
        con.rollback()
        raise


def _verify_backup(con, path, sid):
    backup = Path(path)
    try:
        if not backup.is_file() or backup.stat().st_size == 0:
            raise ValueError()
        check = sqlite3.connect(backup.resolve().as_uri() + '?mode=ro', uri=True)
        check.row_factory = sqlite3.Row
        try:
            if check.execute('PRAGMA quick_check').fetchone()[0] != 'ok' or _hash(_state(check, sid)) != _hash(_state(con, sid)):
                raise ValueError()
        finally:
            check.close()
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise ApiError('Резервная копия не соответствует текущей карточке. Рабочие записи не изменены') from exc


def _assert_delta(before, after, subid):
    allowed = {'invoice_funding_policies', 'balance_account_snapshots', 'balance_credit_funds', 'subscriptions'}
    for table in set(before) | set(after):
        if table not in allowed and before.get(table) != after.get(table):
            raise ApiError('Изменились исходные записи карточки. Восстановление отменено')
    old = {row['id']:row for row in before.get('subscriptions', [])}
    new = {row['id']:row for row in after.get('subscriptions', [])}
    if set(old) != set(new):
        raise ApiError('Восстановление не должно добавлять или удалять покупки')
    for key, row in old.items():
        fields = set(row) - ({'status', 'paid_at'} if key == subid else set())
        if any(row[field] != new[key][field] for field in fields):
            raise ApiError('Изменились условия покупки или прошлые начисления. Восстановление отменено')
    # The sole approved case has no opening money/carry. A new fund would be an
    # invented balance even if a future executor accidentally attempted it.
    if before.get('balance_credit_funds', []) != after.get('balance_credit_funds', []):
        raise ApiError('Этот план не создаёт средства на балансе')
    for table in ('invoice_funding_policies', 'balance_account_snapshots'):
        if not all(row in after.get(table, []) for row in before.get(table, [])):
            raise ApiError('Восстановление не должно изменять прежние зачёты')


def _replay(con, erp_id, token, request_id):
    row = con.execute('SELECT * FROM ' + RUNS + ' WHERE request_id=?', (request_id,)).fetchone()
    if not row:
        return None
    if row['erp_id'] != erp_id or row['request_token'] != token:
        raise ApiError('Это подтверждение уже относится к другому плану')
    if row['state'] != 'applied':
        raise ApiError('Восстановление по этому подтверждению уже отменено')
    return _public_run(con, row) | {'already_applied':True, 'read_only':False,
        'backup_created':True, 'before':json.loads(row['before_summary']),
        'after':json.loads(row['after_summary'])} | _metadata()


def apply(con, body, user):
    _owner(user)
    erp_id = _data(body)
    case = _case(erp_id)
    if erp_id not in APPLY_CASES or not case:
        raise ApiError('Для этой карточки доступна только проверка на копии')
    request_id, token = body.get('request_id'), body.get('token')
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9:_-]{12,160}', request_id):
        raise ApiError('Нужно действующее подтверждение восстановления')
    if not isinstance(token, str) or not re.fullmatch(r'[a-f0-9]{64}', token):
        raise ApiError('Сначала проверьте план на копии базы')
    replay = _replay(con, erp_id, token, request_id)
    if replay:
        return replay
    checked = plan(con, erp_id)
    if not checked.get('can_apply') or checked.get('token') != token:
        raise ApiError('План изменился или недоступен. Повторите проверку на копии')
    path = _backup_current(con, prefix='erp-balance-recovery')
    with _write(con):
        replay = _replay(con, erp_id, token, request_id)
        if replay:
            return replay
        checked = plan(con, erp_id)
        if not checked.get('can_apply') or checked.get('token') != token:
            raise ApiError('Данные изменились после проверки. Рабочие записи не изменены')
        sid, subid = checked['student_id'], checked['subscription_id']
        _verify_backup(con, path, sid)
        before = _state(con, sid)
        result = engine.apply_reviewed(con, erp_id, user)
        after = _state(con, sid)
        _assert_delta(before, after, subid)
        expected = {k:v for k,v in checked['without_balance'].items() if k not in ('use_balance', 'credit_label')}
        actual = result['after']
        if _json(actual) != _json(expected):
            raise ApiError('Результат отличается от проверенной копии. Изменения отменены')
        run_id = con.execute('''INSERT INTO erp_balance_recovery_runs(erp_id,student_id,subscription_id,
            evidence_id,request_id,request_token,case_fingerprint,before_json,after_json,before_fingerprint,
            after_fingerprint,before_summary,after_summary,state,created_at,user_name,backup_path)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'applied',?,?,?)''',
            (erp_id,sid,subid,case['evidence_id'],request_id,token,_hash(case),_json(before),_json(after),
             _hash(before),_hash(after),_json(checked['before']),_json(actual),_stamp(),user['name'],path)).lastrowid
        run = _public_run(con, con.execute('SELECT * FROM ' + RUNS + ' WHERE id=?', (run_id,)).fetchone())
    return result | run | _metadata() | {'backup_created':True, 'backup_filename':Path(path).name,
        'last_run':run, 'before_summary':checked['before'], 'after_summary':actual}


def rollback(con, run_id, token, user):
    _owner(user)
    row = con.execute('SELECT * FROM ' + RUNS + ' WHERE id=?', (run_id,)).fetchone()
    if not row or row['erp_id'] not in APPLY_CASES:
        raise ApiError('Подтверждённое восстановление не найдено')
    current = _public_run(con, row)
    if not current['can_rollback'] or not token or token != current['rollback_token']:
        raise ApiError('После восстановления появились изменения. Автоматическая отмена их не затронет')
    path = _backup_current(con, prefix='erp-balance-recovery-undo')
    with _write(con):
        row = con.execute('SELECT * FROM ' + RUNS + ' WHERE id=?', (run_id,)).fetchone()
        current = _public_run(con, row)
        if not current['can_rollback'] or token != current['rollback_token']:
            raise ApiError('Данные изменились. Автоматическая отмена недоступна')
        sid, subid = row['student_id'], row['subscription_id']
        _verify_backup(con, path, sid)
        before, after = json.loads(row['before_json']), json.loads(row['after_json'])
        _assert_delta(before, after, subid)
        for table, key in (('balance_credit_funds','id'), ('balance_account_snapshots','id'),
                           ('invoice_funding_policies','subscription_id')):
            existing = {value[key] for value in before.get(table, [])}
            for value in after.get(table, []):
                if value[key] not in existing:
                    con.execute('DELETE FROM ' + table + ' WHERE ' + key + '=?', (value[key],))
        original = next(value for value in before['subscriptions'] if value['id'] == subid)
        con.execute('UPDATE subscriptions SET status=?,paid_at=? WHERE id=?',
            (original['status'], original['paid_at'], subid))
        if _hash(_state(con, sid)) != row['before_fingerprint']:
            raise ApiError('Точечная отмена не восстановила исходное состояние. Изменения отменены')
        con.execute("UPDATE " + RUNS + " SET state='rolled_back',rolled_back_at=?,rolled_back_by=?,rollback_backup_path=? WHERE id=?",
            (_stamp(),user['name'],path,run_id))
        run = _public_run(con, con.execute('SELECT * FROM ' + RUNS + ' WHERE id=?', (run_id,)).fetchone())
    return run | _metadata() | {'backup_created':True, 'backup_filename':Path(path).name, 'last_run':run}


def register(route):
    @route('GET', '/api/erp/balance-recovery/context', role='admin', transactional=False)
    def context_route(con, h, user, body, q):
        _owner(user)
        return plan(con, str(q.get('erp_id') or '').strip())

    @route('POST', '/api/erp/balance-recovery/plan', role='admin', transactional=False)
    def plan_route(con, h, user, body, q):
        _owner(user)
        return plan(con, _data(body))

    @route('POST', '/api/erp/balance-recovery/apply', role='admin', transactional=False)
    def apply_route(con, h, user, body, q):
        try:
            return apply(con, body, user)
        except ValueError as exc:
            raise ApiError(str(exc)) from exc

    @route('POST', '/api/erp/balance-recovery/{rid}/rollback', role='admin', transactional=False)
    def rollback_route(con, h, user, body, q, rid):
        if not isinstance(body, dict) or set(body) - {'token'}:
            raise ApiError('Нужно подтверждение точечной отмены')
        return rollback(con, int(rid), body.get('token'), user)

    @route('POST', '/api/erp/balance-recovery/prior-credit', role='admin')
    def prior_credit(con, h, user, body, q):
        from . import balancecredit, money
        _owner(user)
        if body != {'erp_id': '129268', 'subscription': '825627', 'amount': 280}:
            raise ApiError('Нет согласованного остатка для этой записи')
        source = con.execute(
            "SELECT student_id FROM erp_history_subscriptions WHERE source_id=? "
            "AND month=? AND revenue_amount=? AND lessons=?",
            ('825627', '2026-11', 13950, 9)).fetchone()
        if not source:
            raise ApiError('Сначала перенесите проверенный ноябрьский абонемент')
        sid = source['student_id']
        ref = 'erp:129268:invoice:714842:overpayment'
        old = con.execute('SELECT student_id,amount FROM balance_credit_funds WHERE source_ref=?', (ref,)).fetchone()
        if old:
            if old['student_id'] != sid or old['amount'] != 280:
                raise ApiError('Исходная переплата уже связана с другой записью')
            return {'added': False, 'balance': money.balance(con, sid)['deposit']}
        before = money.balance(con, sid)
        if before['deposit'] or before['debt'] or before['upcoming'] or before['allocation_conflicts']:
            raise ApiError('Баланс изменился. Нужна повторная сверка')
        balancecredit.add_existing_fund(con, sid, 280, source_ref=ref,
            label='Переплата по предыдущему абонементу', user=user)
        after = money.balance(con, sid)
        if after['deposit'] != 280 or after['covered'] != before['covered']:
            raise ApiError('Остаток не прошёл проверку')
        return {'added': True, 'balance': after['deposit']}
