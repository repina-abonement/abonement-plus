"""Selective, idempotent recovery of chat history from a client-reset archive."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import tempfile
import threading
from zoneinfo import ZoneInfo

from .services import ApiError


CONFIRMATION = 'ВОССТАНОВИТЬ ПЕРЕПИСКУ'
TABLES = ('conversations','messages','chat_attachments','conversation_pins',
          'conversation_preferences','message_deletions','wazzup_delivery_events')
SCHEMA = """CREATE TABLE IF NOT EXISTS chat_archive_restore_runs(
 id INTEGER PRIMARY KEY,created_at TEXT NOT NULL,created_by TEXT NOT NULL,
 archive_filename TEXT NOT NULL,before_backup TEXT NOT NULL,source_sha256 TEXT NOT NULL,
 result TEXT NOT NULL,source_commit TEXT NOT NULL DEFAULT '')"""
_plans={}; _lock=threading.Lock()


def init(con): con.execute(SCHEMA)


def _now(): return datetime.now(ZoneInfo('Europe/Moscow'))


def _sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()


def _archive(con):
    from . import backup
    row=con.execute('SELECT * FROM client_data_archives ORDER BY id DESC LIMIT 1').fetchone()
    if not row: raise ApiError('Архив до очистки не найден. Ничего не изменено')
    path=Path(backup.DIR)/row['local_filename']
    if not path.is_file() or _sha(path)!=row['sha256']:
        raise ApiError('Локальный архив не прошёл проверку контрольной суммы. Ничего не изменено')
    return path,dict(row)


def _tables(con): return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(con,table): return [r['name'] for r in con.execute('PRAGMA table_info("'+table+'")')]


def _counts(con):
    available=_tables(con)
    return {t:con.execute('SELECT COUNT(*) FROM "'+t+'"').fetchone()[0] for t in TABLES if t in available}


def _fingerprint(con):
    h=hashlib.sha256(); available=_tables(con)
    for table in TABLES:
        if table not in available: continue
        cols=_columns(con,table); order=','.join('"'+c+'"' for c in cols)
        h.update((table+'\n').encode())
        for row in con.execute('SELECT * FROM "'+table+'"'+(' ORDER BY '+order if order else '')):
            for value in row:
                data=(b'N' if value is None else bytes(value) if isinstance(value,bytes) else str(value).encode('utf-8','surrogatepass'))
                h.update(len(data).to_bytes(8,'big')); h.update(data)
    return h.hexdigest()


def _insert(con,table,values,preserve_id=True):
    cols=[c for c in values if c in _columns(con,table)]
    if not preserve_id and 'id' in cols: cols.remove('id')
    sql='INSERT INTO "'+table+'"('+','.join('"'+c+'"' for c in cols)+') VALUES('+','.join('?' for _ in cols)+')'
    return con.execute(sql,[values[c] for c in cols]).lastrowid


def _restore(target,source):
    available=_tables(source)&_tables(target); conv_map={}; msg_map={}
    added={t:0 for t in TABLES}; merged={t:0 for t in TABLES}
    if 'conversations' not in available or 'messages' not in available:
        raise ApiError('В архиве нет полной истории переписки. Ничего не изменено')
    for old in source.execute('SELECT * FROM conversations ORDER BY id'):
        values=dict(old); current=target.execute('SELECT id FROM conversations WHERE channel=? AND external_id=?',(old['channel'],old['external_id'])).fetchone()
        if current:
            cid=current['id']; merged['conversations']+=1
            # Preserve new traffic counters, but restore the historical identity and useful chat metadata.
            target.execute("""UPDATE conversations SET title=CASE WHEN title='' THEN ? ELSE title END,
                username=CASE WHEN username='' THEN ? ELSE username END,phone=CASE WHEN phone='' THEN ? ELSE phone END,
                phone_norm=CASE WHEN phone_norm='' THEN ? ELSE phone_norm END,
                created_at=MIN(created_at,?),last_at=CASE WHEN COALESCE(last_at,'')<? THEN ? ELSE last_at END,
                last_text=CASE WHEN COALESCE(last_at,'')<? THEN ? ELSE last_text END,unread=MAX(unread,?) WHERE id=?""",
                (old['title'],old['username'],old['phone'],old['phone_norm'],old['created_at'],old['last_at'] or '',old['last_at'],old['last_at'] or '',old['last_text'],old['unread'],cid))
        else:
            values['family_id']=None
            preserve=not target.execute('SELECT 1 FROM conversations WHERE id=?',(old['id'],)).fetchone()
            cid=_insert(target,'conversations',values,preserve); added['conversations']+=1
        conv_map[old['id']]=cid
    msg_fields=('direction','text','kind','media','external_id','user_name','status','error','ts')
    for old in source.execute('SELECT * FROM messages ORDER BY id'):
        cid=conv_map[old['conversation_id']]; current=None
        if old['external_id'] is not None:
            current=target.execute('SELECT id FROM messages WHERE conversation_id=? AND external_id=?',(cid,old['external_id'])).fetchone()
        if not current:
            where=' AND '.join(c+' IS ?' for c in msg_fields)
            current=target.execute('SELECT id FROM messages WHERE conversation_id=? AND '+where+' LIMIT 1',(cid,*(old[c] for c in msg_fields))).fetchone()
        if current:
            mid=current['id']; merged['messages']+=1
        else:
            values=dict(old); values['conversation_id']=cid
            preserve=not target.execute('SELECT 1 FROM messages WHERE id=?',(old['id'],)).fetchone()
            mid=_insert(target,'messages',values,preserve); added['messages']+=1
        msg_map[old['id']]=mid
    if 'wazzup_delivery_events' in available:
        for old in source.execute('SELECT * FROM wazzup_delivery_events'):
            cols=_columns(source,'wazzup_delivery_events'); vals=dict(zip(cols,old))
            before=target.total_changes
            target.execute('INSERT OR IGNORE INTO wazzup_delivery_events('+','.join(cols)+') VALUES('+','.join('?' for _ in cols)+')',[vals[c] for c in cols])
            added['wazzup_delivery_events']+=target.total_changes-before
    users={r[0] for r in target.execute('SELECT id FROM users')}
    for table in ('conversation_pins','conversation_preferences'):
        if table not in available: continue
        for old in source.execute('SELECT * FROM "'+table+'"'):
            values=dict(old)
            if values.get('user_id') not in users: continue
            values['conversation_id']=conv_map.get(values['conversation_id'])
            if not values['conversation_id']: continue
            cols=[c for c in values if c in _columns(target,table)]
            before=target.total_changes
            target.execute('INSERT OR IGNORE INTO "'+table+'"('+','.join(cols)+') VALUES('+','.join('?' for _ in cols)+')',[values[c] for c in cols])
            added[table]+=target.total_changes-before
    if 'message_deletions' in available:
        for old in source.execute('SELECT * FROM message_deletions'):
            values=dict(old); values['message_id']=msg_map.get(values['message_id'])
            if not values['message_id']: continue
            before=target.total_changes
            target.execute('INSERT OR IGNORE INTO message_deletions(message_id,state,user_id,error,updated_at) VALUES(?,?,?,?,?)',tuple(values[k] for k in ('message_id','state','user_id','error','updated_at')))
            added['message_deletions']+=target.total_changes-before
    if 'chat_attachments' in available:
        for old in source.execute('SELECT * FROM chat_attachments'):
            values=dict(old); values['conversation_id']=conv_map.get(values['conversation_id'])
            values['message_id']=msg_map.get(values.get('message_id')) if values.get('message_id') is not None else None
            if not values['conversation_id'] or values.get('user_id') not in users: continue
            try: _insert(target,'chat_attachments',values,True); added['chat_attachments']+=1
            except sqlite3.IntegrityError: merged['chat_attachments']+=1
    return {'source_counts':_counts(source),'after_counts':_counts(target),'added':added,'already_present':merged,
            'cards_restored':0,'payments_restored':0,'lessons_restored':0}


def plan(con,user):
    path,archive=_archive(con); digest=_fingerprint(con)
    source=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True); source.row_factory=sqlite3.Row
    try:
        with tempfile.NamedTemporaryFile(suffix='.sqlite3') as tmp:
            test=sqlite3.connect(tmp.name); test.row_factory=sqlite3.Row; con.backup(test)
            try: result=_restore(test,source); test.execute('PRAGMA foreign_key_check'); test.rollback()
            finally: test.close()
    finally: source.close()
    token=secrets.token_urlsafe(32); item={'user_id':user['id'],'fingerprint':digest,'archive':str(path),'sha':archive['sha256'],'expires':_now()+timedelta(minutes=15)}
    with _lock: _plans.clear(); _plans[token]=item
    return {'ready':True,'token':token,'confirmation':CONFIRMATION,'expires_at':item['expires'].isoformat(timespec='minutes'),'preview':result,'read_only':True}


def apply(con,user,body,source_commit=''):
    token=str((body or {}).get('token') or ''); phrase=str((body or {}).get('confirmation') or '')
    if phrase!=CONFIRMATION: raise ApiError('Введите фразу подтверждения без изменений')
    with _lock: item=_plans.pop(token,None)
    if not item or item['user_id']!=user['id'] or item['expires']<_now(): raise ApiError('Проверка устарела. Сначала снова проверьте восстановление')
    if _fingerprint(con)!=item['fingerprint']: raise ApiError('После проверки появились новые сообщения. Сначала сформируйте новый план — они не будут потеряны')
    path=Path(item['archive'])
    if not path.is_file() or _sha(path)!=item['sha']: raise ApiError('Архив изменился. Ничего не восстановлено')
    from .erpsinglerecoveryweb import _backup_current
    backup=_backup_current(con,prefix='chat-restore-before')
    source=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True); source.row_factory=sqlite3.Row
    try:
        con.execute('BEGIN IMMEDIATE')
        result=_restore(con,source)
        failures=list(con.execute('PRAGMA foreign_key_check'))
        if failures: raise ApiError('Проверка связей после восстановления не прошла. Изменения отменены')
        con.execute('INSERT INTO chat_archive_restore_runs(created_at,created_by,archive_filename,before_backup,source_sha256,result,source_commit) VALUES(?,?,?,?,?,?,?)',
                    (_now().isoformat(timespec='seconds'),user.get('name') or '',path.name,Path(backup).name,item['sha'],json.dumps(result,ensure_ascii=False),source_commit))
        con.execute('COMMIT')
    except BaseException:
        if con.in_transaction: con.execute('ROLLBACK')
        raise
    finally: source.close()
    return {'applied':True,'result':result,'before_backup':Path(backup).name,'archive_filename':path.name}


def register(route):
    @route('POST','/api/maintenance/chat-archive-restore/plan',role='admin',transactional=False)
    def restore_plan(con,h,user,body,q): return plan(con,user)
    @route('POST','/api/maintenance/chat-archive-restore/apply',role='admin',transactional=False)
    def restore_apply(con,h,user,body,q):
        from .erpbalancerecoveryweb import provenance
        return apply(con,user,body,provenance().get('running_commit') or '')
