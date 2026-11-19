"""PayKeeper customer ledger -> daily card payout / individual SBP bank credit.

Only the payment gateway and readable bank statements are used. No acquiring
permission, invoice, payment, refund or receipt-creation endpoint is called.
"""
import hashlib
import json
import re
from datetime import date,datetime,timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from . import fiscal,paykeepercheck


def init(con):
    con.execute('''CREATE TABLE IF NOT EXISTS pk_daily_evidence(
        day TEXT PRIMARY KEY,checked_at TEXT NOT NULL,status TEXT NOT NULL,digest TEXT NOT NULL,result TEXT NOT NULL)''')
    con.execute('''CREATE TABLE IF NOT EXISTS pk_system_evidence(
        system_id TEXT PRIMARY KEY,observed_at TEXT NOT NULL,result TEXT NOT NULL)''')
    con.execute('''CREATE TABLE IF NOT EXISTS bank_payout_batches(
        id TEXT PRIMARY KEY,day TEXT NOT NULL,checked_at TEXT NOT NULL,result TEXT NOT NULL)''')
    con.execute('''CREATE TABLE IF NOT EXISTS bank_statement_observations(
        id INTEGER PRIMARY KEY CHECK(id=1),checked_at TEXT NOT NULL,result TEXT NOT NULL)''')
    con.execute('''CREATE TABLE IF NOT EXISTS bank_credit_allocations(
        bank_key TEXT PRIMARY KEY,target TEXT NOT NULL,checked_at TEXT NOT NULL)''')


def minor(value):
    amount=Decimal(str(value))*100
    if not amount.is_finite() or amount!=amount.to_integral_value() or amount<0:raise ValueError('Invalid amount')
    return int(amount)


def day_of(value):
    text=str(value or '').replace('Z','+00:00')
    moment=datetime.fromisoformat(text)
    if moment.tzinfo:moment=moment.astimezone(ZoneInfo('Europe/Moscow'))
    return moment.date().isoformat()


def normalized(value):
    return ' '.join(re.findall(r'[а-яa-z0-9]+',str(value or '').lower().replace('ё','е')))


def rail(info):
    label=normalized(str(info.get('system_description') or '')+' '+str(info.get('site_description') or ''))
    if re.search(r'\b(sbp|сбп|c2b|qr)\b',label):return 'sbp'
    if re.search(r'\b(card|cards|bankcard|vtb|sber|alfa|gpb|psb|mkb|втб|сбербанк)\b|банковск\w* карт',label):return 'card'
    return 'unknown'


def acquirer(info):
    label=normalized(str(info.get('system_description') or '')+' '+str(info.get('site_description') or ''))
    for key,pattern in [('vtb',r'\b(vtb|втб)\b'),('sber',r'сбер|\bsber'),('alfa',r'альфа|\balfa'),('tochka',r'точк|\btochka'),('gpb',r'газпром|\bgpb')]:
        if re.search(pattern,label):return key
    return ''


def metadata(con):
    result={r['system_id']:json.loads(r['result']) for r in con.execute('SELECT * FROM pk_system_evidence')}
    for row in con.execute('SELECT result FROM fiscal_payment_checks'):
        item=json.loads(row['result']).get('bank_payment',{})
        key=str(item.get('payment_system_id') or '')
        if key and item.get('available'):result[key]=item
    return result


def fetch_day(con,day):
    """Complete bounded pagination; an error or repeated ID never becomes an empty day."""
    rows=[];seen=set()
    for offset in range(0,10000,100):
        page=fiscal.pk_call(con,'/info/payments/bydate/',{'start':day,'end':day,'from':offset,'limit':100,
            'status[]':list(paykeepercheck.RECEIVED_STATUSES)+['refunded','partially_refunded','refunding'],
            'payment_system_id[]':list(range(201))})
        if not isinstance(page,list) or len(page)>100:raise ValueError('Unknown provider page')
        for item in page:
            if not isinstance(item,dict) or not item.get('id') or str(item['id']) in seen:raise ValueError('Ambiguous provider IDs')
            if day_of(item.get('obtain_datetime'))!=day:raise ValueError('Provider day mismatch')
            minor(item.get('pay_amount'));minor(item.get('refund_amount'))
            seen.add(str(item['id']));rows.append(item)
        if len(page)<100:return rows
    raise ValueError('Provider coverage truncated')


def refresh_source(con,current,limit=2):
    if not fiscal.configured(con)['paykeeper']:return {'days_checked':0}
    today=current.date()
    days={today-timedelta(days=i) for i in range(7)}
    for row in con.execute("SELECT DISTINCT substr(p.paid_at,1,10) AS day FROM payments p JOIN pay_links l ON l.payment_id=p.id WHERE p.cancelled=0 AND p.amount>0 AND l.pk_payment_id!=''"):
        try:
            candidate=date.fromisoformat(row['day'])
            if today-timedelta(days=31)<=candidate<=today:days.add(candidate)
        except ValueError:pass
    previous={r['day']:r['checked_at'] for r in con.execute('SELECT day,checked_at FROM pk_daily_evidence')}
    cutoff=(current-timedelta(minutes=10)).isoformat(timespec='seconds')
    due=[d.isoformat() for d in days if previous.get(d.isoformat(),'')<=cutoff]
    due.sort(key=lambda d:(d in previous,previous.get(d,''),0 if d==(today-timedelta(days=1)).isoformat() else 1,-date.fromisoformat(d).toordinal()))
    meta=metadata(con);attempted_systems=set();out={'days_checked':0,'days_unavailable':0}
    for day in due[:limit]:
        try:
            raw=fetch_day(con,day);items=[]
            for item in raw:
                info=meta.get(str(item.get('payment_system_id') or ''),{})|{k:item[k] for k in ('system_description','site_description') if item.get(k)}
                system=str(item.get('payment_system_id') or '')
                if system and rail(info)=='unknown' and system not in attempted_systems:
                    attempted_systems.add(system)
                    try:
                        detail=fiscal.pk_call(con,'/info/payments/byid/',{'id':str(item['id']),'advanced':'true'})
                        detail=detail[0] if isinstance(detail,list) and len(detail)==1 else detail
                        if not isinstance(detail,dict) or str(detail.get('id'))!=str(item['id']) or str(detail.get('payment_system_id'))!=system:raise ValueError('Unknown system metadata')
                        info={k:detail[k] for k in ('system_description','site_description') if detail.get(k)}
                        if rail(info)!='unknown':
                            meta[system]=info
                            con.execute('INSERT INTO pk_system_evidence VALUES(?,?,?) ON CONFLICT(system_id) DO UPDATE SET observed_at=excluded.observed_at,result=excluded.result',(system,current.isoformat(timespec='seconds'),json.dumps(info,ensure_ascii=False)))
                    except Exception:pass
                items.append({'id':str(item['id']),'orderid':str(item.get('orderid') or ''),'client_name':str(item.get('clientid') or '')[:200],
                    'amount_minor':minor(item['pay_amount']),'refund_minor':minor(item['refund_amount']),
                    'status':str(item.get('status') or ''),'paid_at':str(item['obtain_datetime']),
                    'system_id':str(item.get('payment_system_id') or ''),'rail':rail(info),'acquirer':acquirer(info)})
            digest=hashlib.sha256(json.dumps(sorted(items,key=lambda i:i['id']),ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            status='complete';result={'items':items,'coverage_complete':True,'verified_at':current.isoformat(timespec='seconds'),'source_available':True}
        except Exception:
            old=con.execute('SELECT digest,result FROM pk_daily_evidence WHERE day=?',(day,)).fetchone()
            digest=old['digest'] if old else '';status='unavailable';result=json.loads(old['result']) if old else {}
            result.update(note='Не удалось получить полный список оплат PayKeeper за день',source_available=False);out['days_unavailable']+=1
        con.execute('''INSERT INTO pk_daily_evidence VALUES(?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET
            checked_at=excluded.checked_at,status=excluded.status,digest=excluded.digest,result=excluded.result''',
            (day,current.isoformat(timespec='seconds'),status,digest,json.dumps(result,ensure_ascii=False)))
        out['days_checked']+=1
    return out


def linked(con,item):
    rows=con.execute('''SELECT DISTINCT p.id,p.student_id,p.amount,s.name AS client,s.family_id,l.orderid FROM pay_links l
        JOIN payments p ON p.id=l.payment_id JOIN students s ON s.id=p.student_id
        WHERE l.pk_payment_id=? AND p.cancelled=0''',(item['id'],)).fetchall()
    if len(rows)!=1 or rows[0]['orderid']!=item['orderid'] or fiscal.kop(rows[0]['amount'])!=item['amount_minor']:return None
    row=dict(rows[0]);row['names']=[item['client_name']]+[p['name'] for p in con.execute('SELECT name FROM parents WHERE family_id=?',(row['family_id'],))]
    return row


def bank_key(transaction):
    ident=str(transaction.get('transactionId') or '')
    return json.dumps([transaction.get('_account_id',''),ident]) if ident else ''


def booked(transaction):
    return transaction.get('status')=='Booked' and transaction.get('creditDebitIndicator')=='Credit' and (transaction.get('Amount') or {}).get('currency') in ('RUB','643') and bool(bank_key(transaction))


def same_name(names,payer):
    bank=set(normalized(payer).split())
    if not bank:return False
    for name in names:
        words=set(normalized(name).split())
        if not words:continue
        # Full names may omit patronymic / swap surname and given name.
        if len(words)>=2 and words<=bank:return True
        if words==bank:return True
        common={word for word in words&bank if len(word)>2}
        if common and len(words)>=2 and all(word in bank or any(len(initial)==1 and word.startswith(initial) for initial in bank) for word in words):return True
    return False


def period_in(description,day):
    """Read a named sales period, ignoring e.g. the older contract date."""
    token=r'(\d{2}\.\d{2}\.(?:\d{4}|\d{2})|\d{4}-\d{2}-\d{2})'
    selected=re.search(r'за\s+(?:период\s+)?'+token+r'(?:\s*(?:по|—|–|-)\s*'+token+r')?',description,re.I)
    matches=[v for v in selected.groups() if v] if selected else re.findall(token,description)
    parsed=set()
    for value in matches:
        try:parsed.add(datetime.strptime(value,('%d.%m.%Y' if len(value)==10 else '%d.%m.%y') if '.' in value else '%Y-%m-%d').date().isoformat())
        except ValueError:pass
    return parsed=={day}


def fee_in(description):
    match=re.search(r'комисс\w*\s*(?:банка\s*)?(?:[:=]|в размере)?\s*([\d ]+(?:[.,]\d{1,2})?)\s*(?:руб|р\.|₽)',description,re.I)
    return minor(match.group(1).replace(' ','').replace(',','.')) if match else None


def batch_candidate(batch,transaction):
    if not booked(transaction):return None
    description=str(transaction.get('description') or '')
    if not re.search(r'эквайр|возмещен|paykeeper|пейкипер|операци\w*.*карт',description,re.I):return None
    if not period_in(description,batch['day']):return None
    payer=transaction.get('DebtorParty') or {}
    identity={'system_description':str(payer.get('name') or '')+' '+description}
    if not batch['acquirer'] or acquirer(identity)!=batch['acquirer']:return None
    amount=minor(transaction['Amount']['amount']);fee=fee_in(description)
    if amount==batch['gross_minor']:
        return {'bank_key':bank_key(transaction),'bank_operation_id':str(transaction['transactionId']),
            'received_minor':amount,'fee_minor':fee,'commission_basis':'bank_purpose' if fee is not None else 'not_observed_separately',
            'balanced':True,'basis':'sales_day_acquirer_gross'}
    return {'bank_key':bank_key(transaction),'bank_operation_id':str(transaction['transactionId']),
        'received_minor':amount,'fee_minor':fee,'commission_basis':'bank_purpose' if fee is not None else 'unknown',
        'balanced':fee is not None and amount+fee==batch['gross_minor'],'basis':'sales_day_acquirer_net_plus_explicit_fee'}


def _save_payment(con,pid,provider,stamp,result):
    con.execute('''INSERT INTO bank_payment_checks VALUES(?,?,?,?) ON CONFLICT(payment_id) DO UPDATE SET
        provider_payment_id=excluded.provider_payment_id,checked_at=excluded.checked_at,result=excluded.result''',
        (pid,provider,stamp,json.dumps(result,ensure_ascii=False)))


def run_bank(con,current,limit=3):
    """Match one shared statement window, never request the acquiring API."""
    snapshots=list(con.execute("SELECT * FROM pk_daily_evidence WHERE day>=? ORDER BY day DESC",((current.date()-timedelta(days=31)).isoformat(),)))
    if not snapshots:return {'checked':0,'waiting':1}
    complete=[s for s in snapshots if s['digest'] and json.loads(s['result']).get('coverage_complete')]
    if not complete:return {'checked':0,'unavailable':1}
    stamp=current.isoformat(timespec='seconds');items=[];batches=[]
    for snapshot in complete:
        for item in json.loads(snapshot['result'])['items']:
            item=item|{'day':snapshot['day'],'digest':snapshot['digest']};item['link']=linked(con,item);items.append(item)
        groups={}
        for item in json.loads(snapshot['result'])['items']:
            if item['rail']!='card':continue
            key=item['acquirer'] or ('system-'+item['system_id']);groups.setdefault(key,[]).append(item)
        for system,members in groups.items():
            ident='pk-card:'+snapshot['day']+':'+system
            batches.append({'id':ident,'day':snapshot['day'],'group':system,'system_ids':sorted({i['system_id'] for i in members}),'acquirer':members[0]['acquirer'],
                'digest':snapshot['digest'],'full_day':day_of(json.loads(snapshot['result']).get('verified_at') or snapshot['checked_at'])>snapshot['day'],'gross_minor':sum(i['amount_minor'] for i in members),
                'refund_minor':sum(i['refund_minor'] for i in members),'members':members,
                'status':'waiting','note':'Ожидается общая выплата за день'})
    if not items:return {'checked':0}
    start=date.fromisoformat(min(s['day'] for s in complete))
    try:
        transactions=fiscal.fetch_tochka(con,start,current.date(),include_debits=True)
    except Exception:
        for item in items:
            if item['link']:_save_payment(con,item['link']['id'],item['id'],stamp,{'status':'unverified','note':'Не удалось прочитать выписку Точки. Проверка повторится автоматически'})
        return {'checked':0,'unavailable':1}
    examples=[]
    for transaction in transactions:
        if not booked(transaction):continue
        description=str(transaction.get('description') or '')
        if not re.search(r'эквайр|возмещен|paykeeper|пейкипер|сбп|c2b|qr|операци\w*.*карт',description,re.I) and not acquirer({'system_description':(transaction.get('DebtorParty') or {}).get('name')}):continue
        description=re.sub(r'https?://\S+','[ссылка]',description)
        description=re.sub(r'(?<!\d)\d{16,20}(?!\d)','[реквизиты]',description)
        examples.append({'bank_operation_id':str(transaction.get('transactionId') or ''),'day':str(transaction.get('documentProcessDate') or ''),
            'amount_minor':minor(transaction['Amount']['amount']),'payer_name':str((transaction.get('DebtorParty') or {}).get('name') or '')[:200],
            'description':description[:700]})
    con.execute('INSERT OR REPLACE INTO bank_statement_observations VALUES(1,?,?)',(stamp,json.dumps({'from':start.isoformat(),'to':current.date().isoformat(),'credit_count':sum(booked(t) for t in transactions),'examples':examples[:50]},ensure_ascii=False)))
    credits_by_value={}
    for transaction in transactions:
        if booked(transaction):credits_by_value.setdefault((str(transaction.get('documentProcessDate') or ''),minor(transaction['Amount']['amount'])),[]).append(transaction)
    claims={r['bank_key']:r['target'] for r in con.execute('SELECT * FROM bank_credit_allocations')}
    proposals={};out={'checked':0,'confirmed':0,'batches_checked':len(batches),'unavailable':0}
    for batch in batches:
        if batch['day']>=current.date().isoformat() or not batch['full_day']:batch['note']='День ещё не завершён';continue
        if batch['refund_minor'] or any(i['status'] not in paykeepercheck.RECEIVED_STATUSES for i in batch['members']):
            batch['note']='В составе есть возврат или корректировка; сумма выплаты требует проверки';continue
        candidates=[c for t in transactions if (c:=batch_candidate(batch,t))]
        batch['candidates']=candidates
        if len(candidates)==1 and candidates[0]['balanced']:
            candidate=candidates[0];proposals.setdefault(candidate['bank_key'],[]).append(('batch',batch,candidate))
        elif candidates:batch['note']='Найдена выплата, но сумма с комиссией не сошлась или есть несколько кандидатов'
    for item in items:
        if item['rail']!='sbp':continue
        item['bank_status']='unverified';item['bank_note']='Поимённое поступление СБП пока не сопоставлено'
        if item['refund_minor'] or item['status'] not in paykeepercheck.RECEIVED_STATUSES:continue
        candidates=[]
        for transaction in credits_by_value.get((item['day'],item['amount_minor']),[]):
            names=item['link']['names'] if item['link'] else [item['client_name']]
            if not (same_name(names,(transaction.get('DebtorParty') or {}).get('name')) or same_name(names,transaction.get('description'))):continue
            candidates.append(transaction)
        # Include other gateway payments to detect equal family payments without CRM links.
        if len(candidates)==1:
            transaction=candidates[0];proposals.setdefault(bank_key(transaction),[]).append(('sbp',item,transaction))
        elif candidates:item['bank_note']='Несколько одинаковых поступлений по плательщику и сумме — нужна проверка'
    for key,options in proposals.items():
        if len(options)!=1:continue
        kind,target,evidence=options[0];target_id=target['id'] if kind=='batch' else 'pk-sbp:'+target['id']
        if claims.get(key,target_id)!=target_id:continue
        con.execute('INSERT OR IGNORE INTO bank_credit_allocations VALUES(?,?,?)',(key,target_id,stamp))
        if con.execute('SELECT target FROM bank_credit_allocations WHERE bank_key=?',(key,)).fetchone()['target']!=target_id:continue
        if kind=='batch':target.update(status='confirmed',note='Общая выплата за день сопоставлена с составом оплат PayKeeper',bank=evidence)
        else:target.update(bank_status='confirmed',bank_note='СБП сопоставлено по плательщику, сумме и дню',bank=evidence)
    for batch in batches:
        con.execute('''INSERT INTO bank_payout_batches VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            checked_at=excluded.checked_at,result=excluded.result''',(batch['id'],batch['day'],stamp,json.dumps(batch,ensure_ascii=False)))
    for item in items:
        link=item['link']
        if not link:continue
        result={'status':'unverified','note':'Способ оплаты пока не определён','source_day':item['day'],'source_digest':item['digest'],'statement_status':'read'}
        if item['rail']=='sbp':
            result.update(status=item.get('bank_status','unverified'),note=item.get('bank_note','Нужна проверка корректировки СБП'))
            bank=item.get('bank',{});operation=str(bank.get('transactionId') or '')
            result['settlement_proof']={'method':'payer_amount_day_unique','bank_operation_id':operation,'payment_gross_minor':item['amount_minor']}
        elif item['rail']=='card':
            batch=next(b for b in batches if b['day']==item['day'] and b['group']==(item['acquirer'] or ('system-'+item['system_id'])))
            result.update(status=batch['status'] if batch['status']=='confirmed' else 'unverified',note=batch['note'],batch_id=batch['id'])
            result['settlement_proof']={'method':'daily_card_batch','batch_id':batch['id'],'bank_operation_id':batch.get('bank',{}).get('bank_operation_id'),
                'gross_minor':batch['gross_minor'],'net_minor':batch.get('bank',{}).get('received_minor'),
                'fee_minor':batch.get('bank',{}).get('fee_minor'),'payment_gross_minor':item['amount_minor']}
        _save_payment(con,link['id'],item['id'],stamp,result);out['checked']+=1;out['confirmed']+=result['status']=='confirmed'
    return out


def view(con,start,end):
    rows=[]
    for row in con.execute('SELECT * FROM bank_payout_batches WHERE day>=? AND day<=? ORDER BY day DESC,id',(start,end)):
        batch=json.loads(row['result'])
        snapshot=con.execute('SELECT status,digest FROM pk_daily_evidence WHERE day=?',(batch['day'],)).fetchone()
        if not snapshot or not snapshot['digest'] or snapshot['digest']!=batch['digest']:
            batch.update(status='waiting',note='Состав оплат обновился или источник недоступен; требуется повторная сверка')
        batch['checked_at']=row['checked_at']
        if batch.get('bank'):batch['bank'].pop('bank_key',None)
        for candidate in batch.get('candidates',[]):candidate.pop('bank_key',None)
        for member in batch['members']:
            match=linked(con,member);member['crm_payment_id']=match['id'] if match else None;member['student_id']=match['student_id'] if match else None;member['client_name']=match['client'] if match else member['client_name']
        rows.append(batch)
    return rows


def diagnostics(con):
    days=[]
    for row in con.execute('SELECT * FROM pk_daily_evidence ORDER BY day DESC LIMIT 32'):
        data=json.loads(row['result']);items=data.get('items',[])
        days.append({'day':row['day'],'checked_at':row['checked_at'],'status':row['status'],
            'payments':len(items),'cards':sum(i['rail']=='card' for i in items),'sbp':sum(i['rail']=='sbp' for i in items),
            'unknown_systems':sorted({i['system_id'] for i in items if i['rail']=='unknown'})})
    observation=con.execute('SELECT * FROM bank_statement_observations WHERE id=1').fetchone()
    return {'strategy':'paykeeper_daily_batches_and_named_sbp','acquiring_permission_required':False,
        'daily_sources':days,'statement':({'checked_at':observation['checked_at'],**json.loads(observation['result'])} if observation else None),
        'card_batches':view(con,(datetime.now(ZoneInfo('Europe/Moscow')).date()-timedelta(days=31)).isoformat(),datetime.now(ZoneInfo('Europe/Moscow')).date().isoformat())[:10]}
