"""Independent receipt discovery and owner review. Never moves money or prints."""
import hashlib
import json
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from . import fiscal,payoutcheck,paykeepercheck,reconciliation


def init(con):
    con.execute('''CREATE TABLE IF NOT EXISTS incoming_money_events(
        id TEXT PRIMARY KEY,source TEXT NOT NULL,source_id TEXT NOT NULL,
        event_day TEXT NOT NULL,amount_minor INTEGER NOT NULL,currency TEXT NOT NULL,
        first_seen TEXT NOT NULL,last_seen TEXT NOT NULL,data TEXT NOT NULL)''')
    con.execute('''CREATE TABLE IF NOT EXISTS incoming_source_checks(
        source TEXT PRIMARY KEY,checked_at TEXT NOT NULL,status TEXT NOT NULL,result TEXT NOT NULL)''')
    con.execute('''CREATE TABLE IF NOT EXISTS incoming_receipt_checks(
        event_id TEXT PRIMARY KEY,checked_at TEXT NOT NULL,result TEXT NOT NULL)''')


def event_key(source,account,ident):
    return hashlib.sha256(json.dumps([source,account,str(ident)],ensure_ascii=False).encode()).hexdigest()


def save_event(con,source,account,ident,day,amount,currency,data,current):
    ident=str(ident or '')
    if not ident:raise ValueError('Missing stable incoming operation ID')
    datetime.fromisoformat(day)
    key=event_key(source,account,ident);stamp=current.isoformat(timespec='seconds')
    previous=con.execute('SELECT amount_minor,currency,data FROM incoming_money_events WHERE id=?',(key,)).fetchone()
    if previous and (previous['amount_minor']!=amount or previous['currency']!=currency or json.loads(previous['data'])!=data):
        con.execute('DELETE FROM incoming_receipt_checks WHERE event_id=?',(key,))
    con.execute('''INSERT INTO incoming_money_events VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
        event_day=excluded.event_day,amount_minor=excluded.amount_minor,currency=excluded.currency,
        last_seen=excluded.last_seen,data=excluded.data''',
        (key,source,ident,day,amount,currency,stamp,stamp,json.dumps(data,ensure_ascii=False)))
    return key


def checked(con,source,current,status,result):
    con.execute('INSERT OR REPLACE INTO incoming_source_checks VALUES(?,?,?,?)',
        (source,current.isoformat(timespec='seconds'),status,json.dumps(result,ensure_ascii=False)))


def discover_paykeeper(con,current):
    """Today/yesterday plus durable history cursor, regardless of CRM sales."""
    row=con.execute("SELECT * FROM incoming_source_checks WHERE source='paykeeper'").fetchone()
    old=json.loads(row['result']) if row else {}
    today=current.date();floor=today-timedelta(days=31)
    cursor=old.get('history_cursor') or floor.isoformat()
    if cursor<floor.isoformat() or cursor>today.isoformat():cursor=floor.isoformat()
    days=list(dict.fromkeys([today.isoformat(),(today-timedelta(days=1)).isoformat(),cursor]))
    errors=[];count=0;successful=[]
    for day in days:
        try:
            payments=payoutcheck.fetch_day(con,day)
            for p in payments:
                save_event(con,'paykeeper','',p['id'],payoutcheck.day_of(p['obtain_datetime']),payoutcheck.minor(p['pay_amount']),'RUB',
                    {k:p[k] for k in ('orderid','clientid','unique_id','status','refund_amount','payment_system_id') if k in p},current)
            count+=len(payments);successful.append(day)
        except Exception as error:
            errors.append({'day':day,**payoutcheck.source_error(con,error,'reverse_discovery')})
    if cursor in successful:
        cursor=(datetime.fromisoformat(cursor).date()+timedelta(days=1)).isoformat()
        if cursor>today.isoformat():cursor=floor.isoformat()
    result={'checked':count,'completed':True,'days':days,'successful_days':successful,
            'history_cursor':cursor,'history_from':floor.isoformat(),'errors':errors,'unavailable':len(errors),'empty':not count and not errors}
    checked(con,'paykeeper',current,'unavailable' if errors else 'ok',result)
    return result


def ingest_bank(con,source,transactions,current):
    count=0
    for t in transactions:
        if t.get('status')!='Booked' or t.get('creditDebitIndicator')!='Credit':continue
        money=t.get('Amount') or {};amount=payoutcheck.minor(money.get('amount'))
        if amount<=0:continue
        if not money.get('currency'):raise ValueError('Unknown incoming currency')
        save_event(con,source,str(t.get('_account_id') or ''),t.get('transactionId'),str(t.get('documentProcessDate') or ''),amount,'RUB' if str(money['currency'])=='643' else str(money['currency']),
            {'payer':str((t.get('DebtorParty') or {}).get('name') or ''),'purpose':str(t.get('description') or ''),
             'bank_key':payoutcheck.bank_key(t),'paymentId':t.get('paymentId'),'suggested_kind':fiscal.classify_tochka(t,fiscal.cfg(con,'fx_inn'))[0]},current)
        count+=1
    return count


def discover_tochka(con,current):
    previous=con.execute("SELECT * FROM incoming_source_checks WHERE source='tochka'").fetchone()
    start=current.date()-timedelta(days=31)
    if previous and previous['status']=='ok':start=max(start,datetime.fromisoformat(previous['checked_at']).date()-timedelta(days=2))
    try:
        transactions=fiscal.fetch_tochka(con,start,current.date(),include_debits=True,all_currencies=True)
        count=ingest_bank(con,'tochka',transactions,current)
        result={'checked':count,'completed':True,'from':start.isoformat(),'to':current.date().isoformat(),'empty':not count,'currency_scope':'all_accessible_accounts'}
        checked(con,'tochka',current,'ok',result);return result
    except Exception:
        result={'checked':0,'unavailable':1,'note':'Не удалось получить полную выписку Точки для поиска новых поступлений'}
        checked(con,'tochka',current,'unavailable',result);return result


def crm_for_provider(con,pid,amount):
    rows=con.execute('''SELECT DISTINCT p.* FROM payments p JOIN pay_links l ON l.payment_id=p.id
        WHERE p.cancelled=0 AND l.pk_payment_id=?''',(pid,)).fetchall()
    return [dict(p) for p in rows] if len(rows)==1 and fiscal.kop(rows[0]['amount'])==amount else []


def related(con,event,events):
    data=json.loads(event['data'])
    if event['source']=='paykeeper':return crm_for_provider(con,event['source_id'],event['amount_minor']),event['source_id']
    # QR identity also correlates payments missing from CRM; no new CRM payment is created.
    transaction={'description':data.get('purpose'),'paymentId':data.get('paymentId')}
    providers=[]
    for p in events:
        if p['source']!='paykeeper' or p['amount_minor']!=event['amount_minor'] or p['currency']!=event['currency']:continue
        raw=json.loads(p['data'])
        if raw.get('unique_id') and payoutcheck.reference_match({'unique_id':str(raw['unique_id'])},transaction):providers.append(p)
    if len(providers)==1:
        p=providers[0];return crm_for_provider(con,p['source_id'],p['amount_minor']),p['source_id']
    allocation=con.execute('SELECT target FROM bank_credit_allocations WHERE bank_key=?',(data.get('bank_key',''),)).fetchone()
    if allocation:
        target=allocation['target']
        if target.startswith('pk-sbp:'):
            from . import bankcheck
            pid=target[len('pk-sbp:'):]
            links=crm_for_provider(con,pid,event['amount_minor'])
            if len(links)==1:
                state=bankcheck.state(con,links[0]['id']) or {}
                if state.get('status')=='confirmed' and state.get('evidence',{}).get('bank_operation_id')==event['source_id']:
                    return links,pid
            return [],None
        batch=con.execute('SELECT result FROM bank_payout_batches WHERE id=?',(target,)).fetchone()
        if batch:
            b=json.loads(batch['result']);snapshot=con.execute('SELECT digest FROM pk_daily_evidence WHERE day=?',(b['day'],)).fetchone()
            if b.get('status')=='confirmed' and b.get('matching_version')==payoutcheck.MATCHING_VERSION and snapshot and snapshot['digest']==b['digest'] and b.get('bank',{}).get('received_minor')==event['amount_minor']:
                links=[payoutcheck.linked(con,m) for m in b['members']]
                if all(links):
                    return [dict(con.execute('SELECT * FROM payments WHERE id=?',(m['id'],)).fetchone()) for m in links],None
    return [],None


def receipt_state(con,event,payments,pid,events):
    if payments:
        receipts=[reconciliation.payment_state(con,p)['ofd'] for p in payments]
        if all(r['status']=='confirmed' for r in receipts):return {'status':'confirmed','label':'Чеки клиентских оплат подтверждены в ОФД'}
        if any(r['status']=='unavailable' for r in receipts):return {'status':'unavailable','label':'Проверка ОФД недоступна'}
        return {'status':'unverified','label':'Чек клиентской оплаты не подтверждён'}
    target=event['id'] if event['source']=='paykeeper' else next((e['id'] for e in events if e['source']=='paykeeper' and e['source_id']==pid),None)
    check=con.execute('SELECT result FROM incoming_receipt_checks WHERE event_id=?',(target,)).fetchone() if target else None
    if check:return json.loads(check['result'])
    return {'status':'unverified','label':'Поступление не связано с подтверждённым чеком'}


def check_receipts(con,current,limit=3):
    from . import primaryreceipt
    cutoff=(current-timedelta(minutes=10)).isoformat(timespec='seconds')
    rows=con.execute('''SELECT e.* FROM incoming_money_events e LEFT JOIN incoming_receipt_checks c ON c.event_id=e.id
        WHERE e.source='paykeeper' AND (c.event_id IS NULL OR c.checked_at<=?)
        ORDER BY c.checked_at IS NOT NULL,c.checked_at LIMIT ?''',(cutoff,limit)).fetchall()
    result={'checked':0,'unavailable':0}
    for event in rows:
        try:
            data=json.loads(event['data'])
            raw=fiscal.pk_call(con,'/info/payments/byid/',{'id':event['source_id'],'advanced':'true'})
            p=raw[0] if isinstance(raw,list) and len(raw)==1 else raw
            if not isinstance(p,dict) or str(p.get('id'))!=event['source_id'] or p.get('orderid')!=data.get('orderid') or payoutcheck.minor(p.get('pay_amount'))!=event['amount_minor']:raise ValueError('Unknown exact payment')
            raw=fiscal.pk_call(con,'/info/options/byid/',{'id':event['source_id']})
            options=raw[0] if isinstance(raw,list) and len(raw)==1 else raw
            primary=primaryreceipt.parse(options,p|{'available':True},data.get('orderid'),event['amount_minor']/100)
            evidence=paykeepercheck.primary_ofd(con,primary)
            receipt={'status':evidence['status'],'label':'Чек найден в ОФД' if evidence['status']=='confirmed' else 'Чек в ОФД не подтверждён',
                     **{k:evidence[k] for k in ('note','http_status','provider_message','fn','fd','fp') if k in evidence}}
        except Exception:
            receipt={'status':'unavailable','label':'Не удалось проверить чек по исходной оплате'}
        if receipt['status']=='unavailable':result['unavailable']+=1
        con.execute('INSERT OR REPLACE INTO incoming_receipt_checks VALUES(?,?,?)',(event['id'],current.isoformat(timespec='seconds'),json.dumps(receipt,ensure_ascii=False)))
        result['checked']+=1
    return result


def view(con,summary_only=False,current=None):
    current=current or datetime.now(ZoneInfo('Europe/Moscow')).replace(tzinfo=None)
    events=list(con.execute('SELECT * FROM incoming_money_events ORDER BY event_day DESC,last_seen DESC LIMIT 2001'))
    truncated=len(events)>2000;events=events[:2000];cases={}
    for event in events:
        data=json.loads(event['data']);payments,pid=related(con,event,events)
        key='paykeeper:'+pid if pid else 'payment:'+str(payments[0]['id']) if len(payments)==1 else 'event:'+event['id']
        case=cases.setdefault(key,{'id':key,'sources':[],'day':event['event_day'],'amount_minor':event['amount_minor'],'currency':event['currency'],
            'payer':data.get('clientid') or data.get('payer') or '', 'purpose':data.get('purpose',''),
            'unrecorded':not payments,'crm_payment_ids':[p['id'] for p in payments],
            'receipt':receipt_state(con,event,payments,pid,events),'suggested_kind':data.get('suggested_kind')})
        if event['source'] not in case['sources']:case['sources'].append(event['source'])
    rows=list(cases.values())
    issues=[c for c in rows if c['unrecorded'] or c['receipt']['status']!='confirmed']
    coverage={}
    for source in ('paykeeper','tochka','sber'):
        check=con.execute('SELECT * FROM incoming_source_checks WHERE source=?',(source,)).fetchone()
        coverage[source]={'status':check['status'] if check else 'not_connected' if source=='sber' else 'waiting',
            'checked_at':check['checked_at'] if check else None,'detail':json.loads(check['result']) if check else {}}
    from . import sberbank
    if not sberbank.configured(con):coverage['sber']['status']='not_connected'
    elif coverage['sber']['status']=='not_connected':coverage['sber']['status']='waiting'
    for item in coverage.values():
        if item['status'] not in ('not_connected','unavailable') and item['checked_at'] and datetime.fromisoformat(item['checked_at'])<current-timedelta(minutes=30):item['status']='stale'
    # Rolling discovery is not a claim of complete historical/account coverage.
    complete=False
    result={'complete':complete,'coverage':coverage,'tracked_cases':len(rows),'attention':len(issues),
        'unrecorded':sum(c['unrecorded'] for c in rows),'receipt_unconfirmed':sum(c['receipt']['status']!='confirmed' for c in rows),
        'scope_note':'Поиск новых поступлений: PayKeeper — сегодня, вчера и поэтапная проверка предыдущих 31 дня; Точка — все доступные ключу счета, первоначально 31 день. Сбер — все счета, доступные разрешению, с поэтапной проверкой 31 дня. Более ранняя история и полнота доступа ко всем счетам пока не подтверждены.',
        'truncated':truncated,'sber_connected':coverage['sber']['status']=='ok'}
    if not summary_only:result['rows']=issues
    return result
