"""Owner-confirmed archival reset of client data.

The club structure and operational settings remain in the live database.  A
verified local and encrypted remote archive is mandatory before the one-way
live cleanup.  Restoring the archive is a maintenance operation, not an HTTP
undo that could overwrite newly entered data.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import threading
import traceback
from zoneinfo import ZoneInfo

from .services import ApiError


CONFIRMATION = "ОЧИСТИТЬ КЛИЕНТСКИЕ ДАННЫЕ"
ARCHIVE_SCHEMA = """CREATE TABLE IF NOT EXISTS client_data_archives(
 id INTEGER PRIMARY KEY,created_at TEXT NOT NULL,created_by TEXT NOT NULL,
 local_filename TEXT NOT NULL,remote_filename TEXT NOT NULL,sha256 TEXT NOT NULL,
 before_counts TEXT NOT NULL,after_counts TEXT NOT NULL,source_commit TEXT NOT NULL)"""
JOB_SCHEMA = """CREATE TABLE IF NOT EXISTS client_data_reset_jobs(
 id INTEGER PRIMARY KEY,state TEXT NOT NULL,stage TEXT NOT NULL,created_at TEXT NOT NULL,
 started_at TEXT,finished_at TEXT,created_by TEXT NOT NULL,source_commit TEXT NOT NULL,
 error TEXT NOT NULL DEFAULT '',result TEXT NOT NULL DEFAULT '')"""

# Entire tables containing client cards, communication, attendance, sales,
# money, fiscal evidence, or raw ERP imports.  Structural club tables are
# deliberately absent from this list.
CLEAR_TABLES = frozenset({
    'alerts','attendance','audit','auto_messages','balance_account_snapshots','balance_credit_funds',
    'bank_credit_allocations','bank_payment_checks','bank_payout_batches','bank_statement_observations',
    'call_digests','calls','cancelled_service_charges','chain_runs','chat_attachments',
    'conversation_pins','conversation_preferences','conversations','deleted_client_cards','erp_balance_recovery_runs',
    'erp_batches','erp_full_files','erp_full_history_corrections','erp_full_identities',
    'erp_full_operations','erp_full_rows','erp_full_runs','erp_full_subscription_corrections',
    'erp_full_task_links','erp_group_map','erp_payment_allocations','erp_refund_allocations',
    'erp_rows','erp_single_payments','erp_single_recovery_runs','erp_single_reschedules',
    'erp_single_reviews','erp_single_sales','erp_task_review_audit','erp_task_transfer_runs','erp_trial_visits','erp_uploads',
    'families','fiscal_ofd_days','fiscal_payment_checks','fx_payments','fx_receipts','fx_runs',
    'historical_entries','historical_refund_exclusions','historical_refund_receipts',
    'historical_refund_runs','history','hook_log','incoming_money_events','incoming_receipt_checks',
    'incoming_source_checks','invoice_balance_uses','invoice_creation_requests',
    'invoice_funding_policies','invoice_payment_targets','lead_imports','makeups',
    'manual_payment_requests','merch_sales','message_deletions','messages','multi_month_orders','nodeadline',
    'not_duplicates','ofd_payment_checks','owner_sent','parents','pay_link_subscriptions','pay_links',
    'payments','pk_daily_evidence','pk_system_evidence','price_corrections','recent_clients',
    'refund_requests','single_visits','students','subscription_cancellations',
    'subscription_composition_reviews','subscription_lesson_adjustments','subscription_refunds','subscriptions','subscriptions_new','trial_holds',
    'trial_misses','vacations','waitlist','wazzup_delivery_events','enrollments',
})

# These tables also contain useful club-wide rows, so only rows tied to a
# client are removed.
PARTIAL = {
    'task_answers': 'student_id IS NOT NULL',
    'tasks': 'student_id IS NOT NULL OR family_id IS NOT NULL',
}

PRESERVED_LABELS = ('Правила и тарифы','Филиалы и группы','Расписание','Команда и роли',
                    'Настройки и подключения','Общие задачи и расходы')
_plans: dict[str, dict] = {}
_lock = threading.Lock()
_last_start_error: dict | None = None


def init(con):
    con.execute(ARCHIVE_SCHEMA)
    con.execute(JOB_SCHEMA)
    con.execute("""UPDATE client_data_reset_jobs SET state='interrupted',stage='interrupted',
        finished_at=?,error='Сервер был перезапущен до завершения. Проверьте результат перед новым планом.'
        WHERE state IN ('queued','running')""",(_now().isoformat(timespec='seconds'),))


def _tables(con):
    return {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _quote(name):
    if not name.replace('_','').isalnum() or name[:1].isdigit():
        raise ValueError('unsafe table')
    return '"' + name + '"'


def counts(con):
    available = _tables(con)
    result = {}
    for table in sorted(CLEAR_TABLES & available):
        result[table] = con.execute('SELECT COUNT(*) FROM ' + _quote(table)).fetchone()[0]
    for table, where in PARTIAL.items():
        if table in available:
            result[table + ':client'] = con.execute('SELECT COUNT(*) FROM ' + _quote(table) + ' WHERE ' + where).fetchone()[0]
    return result


def _value_hash(h, value):
    if value is None:
        h.update(b'N;'); return
    if isinstance(value, bytes):
        h.update(b'B'); h.update(len(value).to_bytes(8,'big')); h.update(value); return
    data = str(value).encode('utf-8','surrogatepass')
    h.update(b'T'); h.update(len(data).to_bytes(8,'big')); h.update(data)


def fingerprint(con):
    """Stream an exact digest without putting client rows in logs or JSON."""
    h = hashlib.sha256(); available = _tables(con)
    for table in sorted(CLEAR_TABLES & available):
        h.update(('TABLE '+table+'\n').encode())
        columns = [row['name'] for row in con.execute('PRAGMA table_info(' + _quote(table) + ')')]
        order = ','.join(_quote(c) for c in columns)
        for row in con.execute('SELECT * FROM ' + _quote(table) + (' ORDER BY ' + order if order else '')):
            for value in row: _value_hash(h,value)
    for table, where in sorted(PARTIAL.items()):
        if table not in available: continue
        h.update(('PARTIAL '+table+'\n').encode())
        columns = [row['name'] for row in con.execute('PRAGMA table_info(' + _quote(table) + ')')]
        order = ','.join(_quote(c) for c in columns)
        for row in con.execute('SELECT * FROM ' + _quote(table) + ' WHERE ' + where + (' ORDER BY ' + order if order else '')):
            for value in row: _value_hash(h,value)
    return h.hexdigest()


def _now():
    return datetime.now(ZoneInfo('Europe/Moscow'))


def _remote_ready(con):
    keys={row['key'] for row in con.execute("SELECT key FROM integrations WHERE key IN ('backup_yadisk_token','backup_key') AND value!=''")}
    return keys=={'backup_yadisk_token','backup_key'}


def plan(con, user):
    if not _remote_ready(con):
        raise ApiError('Перед очисткой подключите внешнюю зашифрованную резервную копию в настройках резервирования')
    before = counts(con); digest = fingerprint(con); token = secrets.token_urlsafe(32)
    item = {'user_id':user['id'],'fingerprint':digest,'counts':before,'expires':_now()+timedelta(minutes=15)}
    with _lock:
        _plans.clear(); _plans[token] = item
    return {'ready':True,'token':token,'expires_at':item['expires'].isoformat(timespec='minutes'),
            'counts':before,'total_rows':sum(before.values()),'preserved':PRESERVED_LABELS,
            'confirmation':CONFIRMATION,'remote_archive':True,'read_only':True}


def _sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()


def _verify_archive(path, expected_counts):
    uri=Path(path).resolve().as_uri()+'?mode=ro'; check=sqlite3.connect(uri,uri=True)
    check.row_factory=sqlite3.Row
    try:
        if check.execute('PRAGMA quick_check').fetchone()[0] != 'ok' or counts(check) != expected_counts:
            raise ApiError('Резервная копия не совпала с рабочими данными. Очистка отменена')
    finally: check.close()


def _consume_plan(con, user, body):
    token=str((body or {}).get('token') or ''); phrase=str((body or {}).get('confirmation') or '')
    if phrase != CONFIRMATION: raise ApiError('Введите фразу подтверждения без изменений')
    with _lock: item=_plans.pop(token,None)
    if not item or item['user_id'] != user['id'] or item['expires'] < _now():
        raise ApiError('Проверка устарела. Сначала снова проверьте архив и очистку')
    if fingerprint(con) != item['fingerprint'] or counts(con) != item['counts']:
        raise ApiError('Данные изменились после проверки. Сначала сформируйте новый план')
    return item


def _apply_item(con, user, item, source_commit='', stage=None):
    from .erpsinglerecoveryweb import _backup_current
    from . import backup
    if fingerprint(con) != item['fingerprint'] or counts(con) != item['counts']:
        raise ApiError('Данные изменились после проверки. Сначала сформируйте новый план')
    if stage: stage('local_archive')
    archive=_backup_current(con,prefix='client-data-archive')
    _verify_archive(archive,item['counts']); checksum=_sha(archive)
    # Lock writers after the local snapshot is proven unchanged. Reads remain
    # available in WAL mode while the encrypted external copy is uploaded.
    if stage: stage('external_archive')
    con.execute('BEGIN IMMEDIATE')
    try:
        if fingerprint(con) != item['fingerprint'] or counts(con) != item['counts']:
            raise ApiError('Во время архивации данные изменились. Очистка отменена; повторите проверку')
        try:
            remote=backup.upload(con,archive)
        except Exception as exc:
            raise ApiError('Внешняя зашифрованная копия не подтверждена. Очистка отменена') from exc
        if stage: stage('cleaning')
        con.execute('PRAGMA defer_foreign_keys=ON')
        available=_tables(con)
        for table,where in PARTIAL.items():
            if table in available: con.execute('DELETE FROM '+_quote(table)+' WHERE '+where)
        # Foreign keys are deferred, so every dependent table is empty before commit.
        for table in sorted(CLEAR_TABLES & available): con.execute('DELETE FROM '+_quote(table))
        failures=list(con.execute('PRAGMA foreign_key_check'))
        if failures: raise ApiError('Проверка связей после очистки не прошла. Изменения отменены')
        after=counts(con)
        con.execute('''INSERT INTO client_data_archives(created_at,created_by,local_filename,remote_filename,
            sha256,before_counts,after_counts,source_commit) VALUES(?,?,?,?,?,?,?,?)''',
            (_now().isoformat(timespec='seconds'),user.get('name') or '',Path(archive).name,remote,checksum,
             json.dumps(item['counts'],sort_keys=True),json.dumps(after,sort_keys=True),source_commit))
        con.execute("UPDATE payment_control_jobs SET started_at=NULL,finished_at=NULL,next_at=NULL,lease_until=NULL,lease_owner=NULL,status='waiting',result='{}'")
        con.execute('COMMIT')
    except BaseException:
        if con.in_transaction: con.execute('ROLLBACK')
        raise
    return {'applied':True,'archive':{'local_filename':Path(archive).name,'remote_filename':remote,
            'sha256':checksum},'before_counts':item['counts'],'after_counts':after,
            'preserved':PRESERVED_LABELS,'client_rows_remaining':sum(after.values()),
            'restoration':'Архив сохранён целиком; восстановление выполняется только как отдельная операция обслуживания'}


def apply(con, user, body, source_commit=''):
    """Synchronous core retained for isolated tests and maintenance scripts."""
    return _apply_item(con,user,_consume_plan(con,user,body),source_commit)


def _safe_error(error):
    return error.message if isinstance(error,ApiError) else 'Не удалось завершить архивирование. Рабочие данные не очищены или требуют проверки.'


def _job_worker(path, job_id, user, item, source_commit):
    from . import db
    con=db.connect(path)
    try:
        con.execute("UPDATE client_data_reset_jobs SET state='running',stage='local_archive',started_at=? WHERE id=?",
                    (_now().isoformat(timespec='seconds'),job_id))
        def stage(value):
            # Before BEGIN this is immediately visible; inside the final write
            # transaction it becomes visible together with the cleanup.
            con.execute('UPDATE client_data_reset_jobs SET stage=? WHERE id=?',(value,job_id))
        result=_apply_item(con,user,item,source_commit,stage)
        con.execute("""UPDATE client_data_reset_jobs SET state='completed',stage='completed',finished_at=?,result=?
            WHERE id=?""",(_now().isoformat(timespec='seconds'),json.dumps(result,ensure_ascii=False),job_id))
    except BaseException as error:
        if con.in_transaction: con.execute('ROLLBACK')
        con.execute("""UPDATE client_data_reset_jobs SET state='failed',stage='failed',finished_at=?,error=? WHERE id=?""",
                    (_now().isoformat(timespec='seconds'),_safe_error(error),job_id))
    finally:
        con.close()


def _record_start_error(error):
    global _last_start_error
    frames=[]
    for frame in traceback.extract_tb(error.__traceback__)[-8:]:
        path=Path(frame.filename)
        try: name='crm/'+str(path.resolve().relative_to(Path(__file__).resolve().parent))
        except ValueError: continue
        frames.append({'file':name,'line':frame.lineno,'function':frame.name})
    message='database is locked' if isinstance(error,sqlite3.OperationalError) and getattr(error,'sqlite_errorcode',None) in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED) else ''
    _last_start_error={'type':type(error).__name__,'message':message,'frames':frames,
                       'at':_now().isoformat(timespec='seconds')}


def start(con, user, body, source_commit=''):
    global _last_start_error
    _last_start_error=None
    try:
        return _start(con,user,body,source_commit)
    except BaseException as error:
        _record_start_error(error)
        if isinstance(error,sqlite3.OperationalError) and getattr(error,'sqlite_errorcode',None) in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED):
            raise ApiError('База была занята фоновой записью. Данные не очищены; сформируйте новый план и повторите запуск') from error
        raise


def _start(con, user, body, source_commit=''):
    active=con.execute("SELECT id FROM client_data_reset_jobs WHERE state IN ('queued','running') LIMIT 1").fetchone()
    if active: raise ApiError('Архивирование уже выполняется. Нажмите «Проверить результат»')
    item=_consume_plan(con,user,body)
    database=con.execute('PRAGMA database_list').fetchone(); path=database['file'] if hasattr(database,'keys') else database[2]
    if not path: raise ApiError('Не удалось определить рабочую базу. Очистка не начата')
    timeout=con.execute('PRAGMA busy_timeout').fetchone()[0]
    try:
        con.execute('PRAGMA busy_timeout=30000')
        job_id=con.execute("""INSERT INTO client_data_reset_jobs(state,stage,created_at,created_by,source_commit)
            VALUES('queued','queued',?,?,?)""",(_now().isoformat(timespec='seconds'),user.get('name') or '',source_commit)).lastrowid
    finally:
        con.execute('PRAGMA busy_timeout=' + str(int(timeout)))
    safe_user={key:user.get(key) for key in ('id','name','role')}
    threading.Thread(target=_job_worker,args=(path,job_id,safe_user,item,source_commit),daemon=True,
                     name='client-data-reset').start()
    return {'started':True,'job_id':job_id,'state':'queued','stage':'queued'}


def status(con):
    row=con.execute('SELECT * FROM client_data_archives ORDER BY id DESC LIMIT 1').fetchone()
    archive=None; archived_cleanup_complete=False
    if row:
        archive={'created_at':row['created_at'],'created_by':row['created_by'],
                 'local_filename':row['local_filename'],'remote_filename':row['remote_filename'],
                 'sha256':row['sha256'],'source_commit':row['source_commit']}
        try:
            archived_cleanup_complete=sum(json.loads(row['after_counts']).values()) == 0
        except (TypeError,ValueError,AttributeError):
            archived_cleanup_complete=False
    job=con.execute('SELECT * FROM client_data_reset_jobs ORDER BY id DESC LIMIT 1').fetchone()
    public_job=None
    if job:
        public_job={key:job[key] for key in ('id','state','stage','created_at','started_at','finished_at','error')}
    running=public_job and public_job['state'] in ('queued','running')
    if running:
        return {'completed':False,'archive':archive,'job':public_job,'last_start_error':_last_start_error,'counts':{},
                'client_rows_remaining':None,'read_only':True,'note':'Архивирование выполняется на сервере'}
    current=counts(con); current_rows=sum(current.values())
    # The archive row and its after_counts are committed atomically with the
    # cleanup.  They are the durable proof that this reset finished empty.
    # Technical activity resumes immediately afterwards (login audit, hooks,
    # etc.) and must not retroactively turn a successful reset into a failure.
    completed=bool(archive) and archived_cleanup_complete
    remaining=0 if completed else current_rows
    return {'completed':completed,'archive':archive,'job':public_job,
            'last_start_error':_last_start_error,'counts':current,
            'client_rows_remaining':remaining,'rows_observed_after_reset':current_rows if completed else None,
            'read_only':True,
            'note':'Архив и очистка подтверждены' if completed else
                   'Архивирование выполняется на сервере' if running else
                   'Клиентские данные остаются в рабочей CRM' if remaining else
                   'Клиентские строки отсутствуют, но завершённый архив не зарегистрирован'}


def register(route):
    @route('GET','/api/maintenance/client-reset/status',role='admin',transactional=False)
    def reset_status(con,h,user,body,q):
        return status(con)

    @route('POST','/api/maintenance/client-reset/plan',role='admin',transactional=False)
    def reset_plan(con,h,user,body,q):
        return plan(con,user)

    @route('POST','/api/maintenance/client-reset/apply',role='admin',transactional=False)
    def reset_apply(con,h,user,body,q):
        from .erpbalancerecoveryweb import provenance
        return start(con,user,body,provenance().get('running_commit') or '')
