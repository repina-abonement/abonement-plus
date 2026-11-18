"""Durable Wazzup inbox independent of the main SQLite writer lock.

HTTP acceptance means the full authenticated event is committed here. Processing
is at least once; the main message keys make retries idempotent.
"""
import hashlib
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager

_WAKE=threading.Event()
_SAVE_FAILED=False
MAX_PENDING_BYTES=256*1024*1024
SCHEMA='''
CREATE TABLE IF NOT EXISTS events(
 id INTEGER PRIMARY KEY,digest TEXT NOT NULL UNIQUE,payload TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending',created_at REAL NOT NULL,done_at REAL,
 attempts INTEGER NOT NULL DEFAULT 0,next_at REAL NOT NULL DEFAULT 0,
 lease_until REAL NOT NULL DEFAULT 0,last_error TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS events_due ON events(state,next_at,lease_until);
CREATE TABLE IF NOT EXISTS event_refs(
 event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
 message_id TEXT NOT NULL,PRIMARY KEY(event_id,message_id));
CREATE INDEX IF NOT EXISTS refs_message ON event_refs(message_id);
'''


def path_for(con):
    path=next((r[2] for r in con.execute('PRAGMA database_list') if r[1]=='main'),'')
    if not path:raise ValueError('Durable inbox requires a file-backed CRM database')
    return os.path.abspath(path)+'.wazzup-inbox.sqlite3'


@contextmanager
def connection(con,create=True):
    path=path_for(con)
    if not create and not os.path.exists(path):
        yield None;return
    fd=os.open(path,os.O_CREAT|os.O_RDWR,0o600)
    os.fchmod(fd,0o600)
    os.close(fd)
    q=sqlite3.connect(path,timeout=3,isolation_level=None)
    q.row_factory=sqlite3.Row
    try:
        q.execute('PRAGMA foreign_keys=ON')
        q.execute('PRAGMA synchronous=FULL')
        q.execute('PRAGMA secure_delete=ON')
        q.executescript(SCHEMA)
        yield q
    finally:q.close()


def enqueue(con,payload):
    global _SAVE_FAILED
    encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    digest=hashlib.sha256(encoded.encode()).hexdigest()
    with connection(con) as q:
        q.execute('BEGIN IMMEDIATE')
        try:
            old=q.execute('SELECT id FROM events WHERE digest=?',(digest,)).fetchone()
            if old:
                q.execute('COMMIT');_SAVE_FAILED=False;_WAKE.set();return old['id']
            size=q.execute("SELECT COALESCE(SUM(length(CAST(payload AS BLOB))),0) FROM events WHERE state!='done'").fetchone()[0]
            if size+len(encoded.encode())>MAX_PENDING_BYTES:
                raise OSError('Durable inbox capacity exceeded')
            eid=q.execute('INSERT INTO events(digest,payload,created_at) VALUES(?,?,?)',(digest,encoded,time.time())).lastrowid
            refs={str(item['messageId']) for key in ('messages','statuses')
                  for item in (payload.get(key) if isinstance(payload.get(key),list) else [])
                  if isinstance(item,dict) and item.get('messageId')}
            q.executemany('INSERT INTO event_refs VALUES(?,?)',[(eid,mid) for mid in refs])
            q.execute('COMMIT')
        except BaseException:
            if q.in_transaction:q.execute('ROLLBACK')
            raise
    _SAVE_FAILED=False
    _WAKE.set()
    return eid


def process_pending(con,limit=10,now=None,force=False):
    from . import comms
    if con.in_transaction:raise ValueError('Inbox worker requires its own connection without an active transaction')
    current=time.time() if now is None else now
    result={'processed':0,'deferred':0}
    with connection(con,create=False) as q:
        if q is None:return result
        rows=q.execute("""SELECT id FROM events WHERE
          (state='pending' AND (next_at<=? OR ?)) OR (state='processing' AND lease_until<=?)
          ORDER BY id LIMIT ?""",(current,int(force),current,max(0,min(int(limit),100)))).fetchall()
        for selected in rows:
            q.execute('BEGIN IMMEDIATE')
            row=q.execute('SELECT * FROM events WHERE id=?',(selected['id'],)).fetchone()
            eligible=row and ((row['state']=='pending' and (force or row['next_at']<=current)) or
                              (row['state']=='processing' and row['lease_until']<=current))
            if not eligible:
                q.execute('COMMIT');continue
            q.execute("UPDATE events SET state='processing',lease_until=?,attempts=attempts+1 WHERE id=?",(current+120,row['id']))
            q.execute('COMMIT')
            timeout=con.execute('PRAGMA busy_timeout').fetchone()[0]
            try:
                con.execute('PRAGMA busy_timeout=0')
                con.execute('BEGIN IMMEDIATE')
                payload=json.loads(row['payload'])
                comms.wz_hook(con,payload)
                comms.log_hook(con,'wazzup',payload)
                con.execute('COMMIT')
            except Exception as error:
                if con.in_transaction:con.execute('ROLLBACK')
                delay=min(60,2**min(row['attempts']+1,6))
                q.execute("UPDATE events SET state='pending',lease_until=0,next_at=?,last_error=? WHERE id=?",
                          (current+delay,type(error).__name__,row['id']))
                result['deferred']+=1
            else:
                # A crash after main COMMIT and before this update causes a safe replay.
                q.execute("UPDATE events SET state='done',payload='',done_at=?,lease_until=0,last_error='' WHERE id=?",(current,row['id']))
                result['processed']+=1
            finally:con.execute(f'PRAGMA busy_timeout={int(timeout)}')
        # Pending events are never removed. Finished payloads have already been erased.
        q.execute("DELETE FROM events WHERE state='done' AND done_at<?",(current-7*86400,))
    return result


def stats(con):
    with connection(con,create=False) as q:
        if q is None:return {'pending':0,'retrying':0,'oldest_at':None}
        row=q.execute("SELECT count(*) AS pending,COALESCE(SUM(attempts>0),0) AS retrying,min(created_at) AS oldest_at FROM events WHERE state!='done'").fetchone()
        return dict(row)


def note_save_failure():
    global _SAVE_FAILED
    _SAVE_FAILED=True


def health(con):
    try:
        state=stats(con)
        return {'incoming_waiting':state['pending'],
                'incoming_delayed':bool(state['oldest_at'] and time.time()-state['oldest_at']>=30),
                'incoming_storage_error':_SAVE_FAILED}
    except Exception:
        return {'incoming_waiting':None,'incoming_delayed':False,'incoming_storage_error':True}


def message_receipts(con,message_id):
    with connection(con,create=False) as q:
        if q is None:return []
        return [dict(r) for r in q.execute("""SELECT e.id AS queue_id,e.state,e.created_at,e.done_at,e.attempts,e.last_error
            FROM events e JOIN event_refs r ON r.event_id=e.id WHERE r.message_id=? ORDER BY e.id DESC LIMIT 50""",(message_id,))]


def start_worker(connect,stop=None):
    stop=stop or threading.Event()
    def loop():
        con=None
        try:
            while not stop.is_set():
                _WAKE.clear()
                try:
                    if con is None:con=connect()
                    process_pending(con)
                except Exception as error:
                    print('Wazzup inbox worker: '+type(error).__name__,flush=True)
                    if con is not None:
                        try:con.close()
                        except Exception:pass
                        con=None
                _WAKE.wait(1)
        finally:
            if con is not None:con.close()
    thread=threading.Thread(target=loop,name='wazzup-inbox-worker',daemon=True)
    thread.start()
    return thread
