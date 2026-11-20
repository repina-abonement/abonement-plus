"""Owner-only reconciliation of the explicitly reviewed ERP single-sale case."""
from datetime import datetime
import copy
import os
from pathlib import Path
import re
import sqlite3
import subprocess
from zoneinfo import ZoneInfo

from . import erpimport
from .services import ApiError


def _metadata_commit(root):
    """Read only revision metadata; Git config and credentials are never read."""
    git_dir = Path(root) / '.git'
    try:
        if git_dir.is_file():
            pointer = git_dir.read_text().strip()
            if not pointer.startswith('gitdir: '):
                return None
            git_dir = (Path(root) / pointer[8:]).resolve()
        head = (git_dir / 'HEAD').read_text().strip()
        if re.fullmatch('[0-9a-f]{40}', head):
            return head
        if not head.startswith('ref: '):
            return None
        ref = head[5:]
        if not re.fullmatch(r'refs/[A-Za-z0-9._/-]+', ref) or '..' in Path(ref).parts:
            return None
        bases = [git_dir]
        common = git_dir / 'commondir'
        if common.is_file():
            bases.append((git_dir / common.read_text().strip()).resolve())
        for base in bases:
            loose = base / ref
            if loose.is_file():
                value = loose.read_text().strip()
                if re.fullmatch('[0-9a-f]{40}', value):
                    return value
            packed = base / 'packed-refs'
            if packed.is_file():
                for line in packed.read_text().splitlines():
                    fields = line.split()
                    if len(fields) == 2 and fields[1] == ref and re.fullmatch('[0-9a-f]{40}', fields[0]):
                        return fields[0]
    except (OSError, UnicodeError):
        return None
    return None


def _checkout_commit():
    root = Path(__file__).resolve().parent.parent
    metadata = _metadata_commit(root)
    if metadata:
        return metadata
    try:
        value = subprocess.check_output(['git', 'rev-parse', 'HEAD'],
            cwd=root, stderr=subprocess.DEVNULL, timeout=3).decode().strip()
        return value if re.fullmatch('[0-9a-f]{40}', value) else None
    except (OSError, subprocess.SubprocessError):
        return None


_RUNNING_COMMIT = _checkout_commit()
_LOADED_AT = datetime.now(ZoneInfo('Europe/Moscow')).isoformat(timespec='seconds')
ERP_ID = '133513'
SALES = [
    {'sale_id': '828081', 'invoice_id': '729454', 'operation_id': '1197471',
     'source_date': '2026-11-04', 'effective_date': '2026-12-02', 'amount': 2500},
    {'sale_id': '828083', 'invoice_id': '729456', 'operation_id': '1197475',
     'source_date': '2026-11-11', 'effective_date': '2026-11-11', 'amount': 2500},
    {'sale_id': '828084', 'invoice_id': '729457', 'operation_id': '1197477',
     'source_date': '2026-11-18', 'effective_date': '2026-11-18', 'amount': 2500},
    {'sale_id': '828085', 'invoice_id': '729458', 'operation_id': '1197479',
     'source_date': '2026-11-25', 'effective_date': '2026-11-25', 'amount': 2500},
]


def provenance():
    return {'running_commit': _RUNNING_COMMIT, 'checkout_commit': _checkout_commit(), 'loaded_at': _LOADED_AT}


def _data(body):
    if not isinstance(body, dict) or str(body.get('erp_id', ERP_ID)).strip() != ERP_ID:
        raise ApiError('Этот проверенный план относится к клиенту ERP 133513')
    # Source identities/dates are server-side reviewed evidence. Browser input
    # can select existing targets, but cannot replace the confirmed case.
    return {'erp_id': ERP_ID, 'sales': copy.deepcopy(SALES),
        'group_id': body.get('group_id'), 'aggregate_id': body.get('aggregate_id'),
        'payment_mapping': body.get('payment_mapping'),
        'mapping_reviewed': body.get('mapping_reviewed') is True,
        'payment_date': '2026-10-28', 'payment_method': 'card',
        'confirmed_group': {'branch': 'Небо', 'weekday': 2, 'time': '18:45'},
        'reason': str(body.get('reason') or '').strip()}


def _backup_current(con):
    """Back up this request's database, never an unrelated global DB_PATH."""
    from . import backup
    import secrets
    if con.in_transaction:
        raise ApiError('Резервную копию нужно создать до начала изменения данных')
    path = None
    try:
        folder = Path(backup.DIR)
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = folder / ('erp-single-recovery-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '-' + secrets.token_hex(4) + '.sqlite3')
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        target = sqlite3.connect(path)
        try:
            con.backup(target)
            if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('Backup integrity check failed')
        finally:
            target.close()
        return str(path)
    except (OSError, sqlite3.Error, ValueError) as exc:
        if path is not None:
            path.unlink(missing_ok=True)
        raise ApiError('Не удалось проверить резервную копию. Рабочие записи не изменены') from exc


def _runs(con, engine, student_id):
    if student_id is None:
        return []
    import json
    current = engine._hash(engine._snapshot(con, student_id))
    result = []
    for row in con.execute('''SELECT id,state,after_fingerprint,before_summary,after_summary,
        created_at,user_name,rolled_back_at,rolled_back_by FROM erp_single_recovery_runs
        WHERE student_id=? ORDER BY id DESC LIMIT 10''', (student_id,)):
        can_rollback = row['state'] == 'applied' and current == row['after_fingerprint']
        result.append({'run_id': row['id'], 'state': row['state'], 'created_at': row['created_at'],
            'user_name': row['user_name'], 'rolled_back_at': row['rolled_back_at'],
            'before_summary': json.loads(row['before_summary']), 'after_summary': json.loads(row['after_summary']),
            'can_rollback': can_rollback,
            'rollback_token': engine._hash([row['id'], row['after_fingerprint']]) if can_rollback else None})
    return result


def register(route):
    @route('GET', '/api/erp/singles/recovery/context', role='admin', transactional=False)
    def recovery_context(con, h, user, body, q):
        from . import erpsinglerecovery as engine
        if q.get('erp_id', ERP_ID) != ERP_ID:
            raise ApiError('Для этого восстановления выберите ERP 133513')
        try:
            result = engine.context(con, ERP_ID)
            result.update(sales=copy.deepcopy(SALES), provenance=provenance(),
                case={'erp_id': ERP_ID, 'payment_date': '2026-10-28', 'payment_method': 'card',
                    'branch': 'Небо', 'weekday': 2, 'time': '18:45', 'payment_group_time': '17:45',
                    'reason': 'Сверено по рабочей ERP; перенос 828081 по сообщению Ольги'})
            result['runs'] = _runs(con, engine, result['student']['id'] if result['student'] else None)
            result['last_run'] = result['runs'][0] if result['runs'] else None
            return result
        except erpimport.ImportError_ as exc:
            raise ApiError(str(exc)) from exc

    @route('POST', '/api/erp/singles/recovery/plan', role='admin', transactional=False)
    def recovery_plan(con, h, user, body, q):
        from . import erpsinglerecovery as engine
        try:
            result = engine.plan(con, _data(body))
            result.update(preview_on_copy=True, provenance=provenance())
            return result
        except erpimport.ImportError_ as exc:
            raise ApiError(str(exc)) from exc

    @route('POST', '/api/erp/singles/recovery/apply', role='admin', transactional=False)
    def recovery_apply(con, h, user, body, q):
        from . import erpsinglerecovery as engine
        try:
            data = _data(body)
            checked = engine.plan(con, data)
            if not body.get('token') or body['token'] != checked['token'] or not checked['can_apply']:
                raise ApiError('План недоступен или данные изменились. Повторите проверку на копии')
            path = _backup_current(con)
            result = engine.apply(con, data, body['token'], user, path)
            result.update(backup_created=True, backup_filename=Path(path).name, provenance=provenance())
            return result
        except erpimport.ImportError_ as exc:
            raise ApiError(str(exc)) from exc

    @route('POST', '/api/erp/singles/recovery/{rid}/rollback', role='admin', transactional=False)
    def recovery_rollback(con, h, user, body, q, rid):
        from . import erpsinglerecovery as engine
        run = con.execute('''SELECT r.id,r.student_id FROM erp_single_recovery_runs r
            JOIN students s ON s.id=r.student_id WHERE r.id=? AND s.erp_id=?''', (int(rid), ERP_ID)).fetchone()
        if not run or not body.get('token'):
            raise ApiError('Нужен действующий результат восстановления и подтверждение отката')
        current = next((row for row in _runs(con, engine, run['student_id']) if row['run_id'] == int(rid)), None)
        if not current or not current['can_rollback'] or current['rollback_token'] != body['token']:
            raise ApiError('После восстановления появились изменения. Автоматический откат их не затронет')
        try:
            path = _backup_current(con)
            result = engine.rollback(con, int(rid), user, path, token=body['token'])
            result.update(backup_created=True, backup_filename=Path(path).name, provenance=provenance())
            return result
        except erpimport.ImportError_ as exc:
            raise ApiError(str(exc)) from exc
