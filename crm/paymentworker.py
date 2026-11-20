"""Independent, read-only payment control loops running inside the CRM service."""
import json
import os
import socket
import threading
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

SOURCES=('paykeeper','tochka','ofd','incoming_paykeeper','incoming_tochka','incoming_receipts','incoming_sber')
INTERVAL=600
LEASE=3600

def now():
    return datetime.now(ZoneInfo('Europe/Moscow')).replace(tzinfo=None)

def init(con):
    con.execute('''CREATE TABLE IF NOT EXISTS payment_control_jobs (
        source TEXT PRIMARY KEY, started_at TEXT, finished_at TEXT, next_at TEXT,
        lease_until TEXT, lease_owner TEXT, status TEXT NOT NULL DEFAULT 'waiting',
        result TEXT NOT NULL DEFAULT '{}')''')
    con.execute('''CREATE TABLE IF NOT EXISTS ofd_payment_checks (
        payment_id INTEGER PRIMARY KEY,provider_payment_id TEXT NOT NULL,
        checked_at TEXT NOT NULL,result TEXT NOT NULL)''')
    # Recheck Sber immediately after service restart / trust bundle upgrades.
    con.execute("UPDATE payment_control_jobs SET next_at=NULL WHERE source='incoming_sber' AND lease_owner IS NULL")
    # Bootstrap the new daily gateway feed once, without waiting for an old scheduler slot.
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pk_daily_evidence'").fetchone() and not con.execute('SELECT 1 FROM pk_daily_evidence LIMIT 1').fetchone():
        con.execute("UPDATE payment_control_jobs SET next_at=NULL WHERE source IN ('paykeeper','tochka') AND lease_owner IS NULL")

def configured(con, source):
    from . import fiscal
    if source=='incoming_sber':
        from . import sberbank
        return sberbank.credentials_ready(con)
    keys={'paykeeper':('fx_pk_server','fx_pk_user','fx_pk_password'),
          'tochka':('fx_tochka_token',),'ofd':('fx_ofd_token',),
          'incoming_paykeeper':('fx_pk_server','fx_pk_user','fx_pk_password'),
          'incoming_tochka':('fx_tochka_token',),
          'incoming_receipts':('fx_pk_server','fx_pk_user','fx_pk_password','fx_ofd_token')}
    return all(fiscal.cfg(con,k) for k in keys[source])

def cycle(con,source,current=None):
    """Short durable lease only; network requests never run in a write transaction."""
    from . import fiscalcheck, bankcheck
    supplied=current is not None
    current=current or now()
    if current.tzinfo is not None:current=current.astimezone(ZoneInfo('Europe/Moscow')).replace(tzinfo=None)
    stamp=current.isoformat(timespec='seconds');owner=f'{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}:{uuid.uuid4().hex}'
    previous=con.execute('SELECT lease_owner FROM payment_control_jobs WHERE source=?',(source,)).fetchone()
    if previous and previous['lease_owner']:
        parts=previous['lease_owner'].split(':')
        if len(parts) in (3,4) and parts[0]==socket.gethostname():
            if len(parts)==4 and parts[:3]==owner.split(':')[:3]:
                con.execute('UPDATE payment_control_jobs SET lease_until=NULL,next_at=NULL WHERE source=? AND lease_owner=?',(source,previous['lease_owner']))
            try:os.kill(int(parts[1]),0)
            except ProcessLookupError:
                con.execute('UPDATE payment_control_jobs SET lease_until=NULL,next_at=NULL WHERE source=? AND lease_owner=?',(source,previous['lease_owner']))
            except (PermissionError,ValueError):pass
    con.execute('INSERT OR IGNORE INTO payment_control_jobs(source) VALUES(?)',(source,))
    claimed=con.execute('''UPDATE payment_control_jobs SET started_at=?,lease_until=?,lease_owner=?,status='running'
        WHERE source=? AND (next_at IS NULL OR next_at<=?)
        AND (lease_until IS NULL OR lease_until<=?)''',
        (stamp,(current+timedelta(seconds=LEASE)).isoformat(timespec='seconds'),owner,source,stamp,stamp)).rowcount
    if not claimed:return {'skipped':True}
    try:
        if not configured(con,source):
            result={'configured':False,'checked':0};status='not_configured'
        else:
            if source=='paykeeper':
                from . import payoutcheck
                result=fiscalcheck.run_automatic(con,current,limit=3,include_ofd=False)
                daily=payoutcheck.refresh_source(con,current)
                result.update(daily)
                result['unavailable']=result.get('unavailable',0)+daily.get('days_unavailable',0)
                if not result['checked'] and daily.get('days_checked'):result['checked']=daily['days_checked']
            elif source=='tochka':result=bankcheck.run_automatic(con,current,limit=3,cached_only=True)
            elif source=='ofd':result=run_ofd(con,current,limit=3)
            elif source=='incoming_sber':
                from . import sberbank
                result=sberbank.discover(con,current)
            else:
                from . import incomingcontrol
                run={'incoming_paykeeper':incomingcontrol.discover_paykeeper,'incoming_tochka':incomingcontrol.discover_tochka,'incoming_receipts':incomingcontrol.check_receipts}[source]
                result=run(con,current)
            status='no_payments' if result.get('no_crm_payments') and not result.get('checked') else 'attention' if result.get('unavailable') or result.get('blocked') else 'waiting_data' if result.get('waiting') else 'ok' if result.get('checked') or result.get('completed') else 'idle'
    except Exception:
        # Provider exception text can contain credentials or client data.
        result={'error':'Не удалось завершить проверку. Повтор произойдёт автоматически'};status='error'
    finished=current if supplied else now()
    # A restart between per-payment due times must not add another full interval.
    delay=30 if status in ('idle','waiting_data') else INTERVAL
    con.execute('''UPDATE payment_control_jobs SET finished_at=?,next_at=?,lease_until=NULL,
        lease_owner=NULL,status=?,result=? WHERE source=? AND lease_owner=?''',
        (finished.isoformat(timespec='seconds'),(finished+timedelta(seconds=delay)).isoformat(timespec='seconds'),
         status,json.dumps(result,ensure_ascii=False),source,owner))
    return result

def run_ofd(con,current,limit=3):
    from . import fiscal,paykeepercheck
    cutoff=(current-timedelta(seconds=INTERVAL)).isoformat(timespec='seconds')
    rows=con.execute('''SELECT p.id,min(l.pk_payment_id) AS provider_id,c.result FROM payments p
        JOIN pay_links l ON l.payment_id=p.id JOIN fiscal_payment_checks c ON c.payment_id=p.id
        LEFT JOIN ofd_payment_checks o ON o.payment_id=p.id
        WHERE p.cancelled=0 AND p.amount>0 AND l.pk_payment_id!='' AND p.paid_at>=?
        AND (o.payment_id IS NULL OR o.checked_at<=? OR o.provider_payment_id!=l.pk_payment_id)
        GROUP BY p.id HAVING count(DISTINCT l.pk_payment_id)=1
        ORDER BY o.checked_at IS NOT NULL,o.checked_at,p.id LIMIT ?''',(fiscal.control_from(con),cutoff,limit)).fetchall()
    result={'checked':0,'confirmed':0,'unavailable':0}
    for row in rows:
        cached=json.loads(row['result']);primary=cached.get('primary_receipt',{})
        if cached.get('provider_payment_id')!=row['provider_id']:
            primary={}
        payment=con.execute('SELECT amount FROM payments WHERE id=?',(row['id'],)).fetchone()
        if primary.get('total_minor')!=fiscal.kop(payment['amount']):primary={}
        if primary.get('status')!='fiscal_details':
            receipts=con.execute("SELECT * FROM fx_receipts WHERE source='paykeeper' AND pk_payment=? AND pk_status IN ('success','fiscal_details') AND op=1 AND amount=? AND fiscal_fn!='' AND fiscal_fd!='' AND fiscal_fp!=''",(row['provider_id'],fiscal.kop(payment['amount']))).fetchall()
            if len(receipts)==1:
                receipt=receipts[0]
                primary={'status':'fiscal_details','fn':receipt['fiscal_fn'],'fd':receipt['fiscal_fd'],'fp':receipt['fiscal_fp'],
                    'total_minor':receipt['amount'],'issued_at':receipt['ts']}
        evidence=paykeepercheck.primary_ofd(con,primary)
        # Exact match, amount and operation are checked by primary_ofd, independently of PayKeeper availability.
        if evidence.get('status')=='confirmed':
            fiscal.store_ofd(con,[{'fiscalDriveNumber':evidence['fn'],'fiscalDocumentNumber':evidence['fd'],
                'fiscalSign':evidence['fp'],'totalSum':evidence['total_minor'],'operationType':1,'receiptDate':evidence['issued_at']}])
            result['confirmed']+=1
        if evidence.get('status')=='unavailable':result['unavailable']+=1
        con.execute('''INSERT INTO ofd_payment_checks VALUES(?,?,?,?) ON CONFLICT(payment_id) DO UPDATE SET
            provider_payment_id=excluded.provider_payment_id,checked_at=excluded.checked_at,result=excluded.result''',
            (row['id'],row['provider_id'],current.isoformat(timespec='seconds'),json.dumps(evidence,ensure_ascii=False)))
        result['checked']+=1
    return result

def states(con):
    rows={r['source']:dict(r) for r in con.execute('SELECT * FROM payment_control_jobs')}
    current=now().isoformat(timespec='seconds')
    out=[]
    for source in SOURCES:
        r=rows.get(source,{})
        status=r.get('status','waiting')
        if not configured(con,source):status='not_configured'
        elif status=='running' and (r.get('lease_until') or '')<current:status='interrupted'
        elif status!='running' and r.get('finished_at') and r['finished_at']<(now()-timedelta(seconds=3*INTERVAL)).isoformat(timespec='seconds'):status='stale'
        out.append({'source':source,'configured':configured(con,source),'status':status,
            'last_started_at':r.get('started_at'),'last_finished_at':r.get('finished_at'),
            'next_at':r.get('next_at') or r.get('lease_until'),'interval_seconds':INTERVAL,
            'result':json.loads(r.get('result','{}'))})
    return out

def start_worker(connect,stop=None,sources=None):
    stop=stop or threading.Event()
    def loop(source):
        con=None
        while not stop.is_set():
            try:
                if con is None:con=connect()
                cycle(con,source)
            except Exception:
                if con is not None:con.close();con=None
            stop.wait(5)
        if con is not None:con.close()
    threads=[]
    for source in (SOURCES if sources is None else sources):
        thread=threading.Thread(target=loop,args=(source,),name='payment-control-'+source,daemon=True)
        thread.start();threads.append(thread)
    return threads
