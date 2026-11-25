"""Read-only validation of stored payment links before sharing or checkout."""
from datetime import datetime, timedelta
import hashlib
import json
import re
from urllib.parse import urlsplit,unquote


def _digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
        separators=(',',':')).encode()).hexdigest()


def _scope(con,subid,sid):
    from . import pricing, services as sv
    row=con.execute('SELECT * FROM subscriptions WHERE id=? AND student_id=?',(subid,sid)).fetchone()
    if not row or row['status']=='cancelled':
        return None
    q=sv.payment_quote(con,subid,sv.today().isoformat())
    # The current calculation may include a temporary late fee. Reading this
    # scope never persists the repricing used to validate the link.
    fields={key:row[key] for key in ('id','student_id','enrollment_id','month','kind','tariff',
        'lessons','per_lesson','discount_name','discount_amount','excluded_dates',
        'lesson_schedule','program_selection','valid_from','valid_until')}
    fields['price']=q['price']
    fields['discount_name']=q['discount_name']
    fields['discount_amount']=q['discount_amount']
    standard_late_fee = (
        q['late_fee'] > row['late_fee'] and not row['program_selection']
        and pricing.deadline(con, row['month']).day == 25
        and pricing.prices_for(con, row['month'])['late_pct'] == 5
    )
    return {'standard_late_fee': standard_late_fee, 'subscription_id':subid,'current_due':q['total'],'price':q['price'],
        'month':row['month'],'fingerprint':_digest(fields)}


def snapshot(con,sid,subscription_id=None,parts=None,full_payment=False):
    items=parts or ([{'subscription_id':subscription_id}] if subscription_id else [])
    scopes=[]
    for item in items:
        scope=_scope(con,item['subscription_id'],sid)
        if scope is None:
            from .paylinks import PayLinkError
            raise PayLinkError('Абонемент не найден или отменён')
        scope.pop('standard_late_fee')
        scopes.append(scope)
    return json.dumps({'kind':'bundle' if parts else 'invoice' if subscription_id else 'amount',
        'full_payment':full_payment,'scopes':scopes},ensure_ascii=False,sort_keys=True,
        separators=(',',':'))


def inspect(con,link,*,student_id=None,subscription_id=None,subscription_ids=None,require_full=False):
    """Describe the current link without creating an invoice or contacting PayKeeper."""
    from . import paylinks, money
    from .services import ApiError
    link=dict(link)
    result={'valid':False,'reason':'','link_id':link['id'],'student_id':link['student_id'],
        'subscription_id':link.get('subscription_id'),'amount':link['amount'],'url':link['url'],
        'current_due':None,'invoice_scope':[],'read_only':True,'scope_kind':'amount',
        'is_full_payment':None if link.get('full_payment') is None else bool(link['full_payment'])}
    def fail(reason):
        result['reason']=reason
        return result
    if student_id is not None and student_id != link['student_id']:
        return fail('Ссылка относится к другому клиенту. Обновите сообщение перед отправкой')
    if link['status']!='open':
        return fail('Ссылка уже оплачена или недоступна. Обновите сообщение перед отправкой')
    try:
        expired=datetime.fromisoformat(link['created_at'])+timedelta(days=paylinks.LIVE_DAYS)<=datetime.now()
    except (TypeError,ValueError):
        expired=True
    if expired:
        return fail('Срок действия ссылки истёк. Создайте новую ссылку перед отправкой')
    parts=[dict(r) for r in con.execute('SELECT subscription_id,amount FROM pay_link_subscriptions WHERE link_id=? ORDER BY subscription_id',(link['id'],))]
    if parts and link.get('subscription_id'):
        return fail('Состав ссылки противоречив. Обновите её перед отправкой')
    items=parts or ([{'subscription_id':link['subscription_id'],'amount':link['amount']}] if link.get('subscription_id') else [])
    result['scope_kind']='bundle' if parts else 'invoice' if items else 'amount'
    selected={part['subscription_id'] for part in items}
    if subscription_id is not None and (len(selected)!=1 or subscription_id not in selected):
        return fail('Ссылка относится к другому счёту. Обновите сообщение перед отправкой')
    if subscription_ids is not None and set(subscription_ids)!=selected:
        return fail('В ссылке другой набор счетов. Обновите сообщение перед отправкой')
    try:
        stored=json.loads(link.get('scope_snapshot') or '{}')
        if not isinstance(stored,dict):
            raise ValueError()
        previous={r['subscription_id']:r for r in stored.get('scopes',[])}
    except (ValueError,TypeError,KeyError):
        return fail('Не удалось подтвердить исходный состав ссылки. Создайте новую ссылку')
    scope_changed=False
    for item in items:
        try:
            scope=_scope(con,item['subscription_id'],link['student_id'])
        except (ValueError,ApiError):
            # Only expected calculation failures are returned to the UI. A
            # malformed record must never make a stale link appear valid.
            scope=None
        if scope is None:
            return fail('Абонемент отменён или недоступен. Обновите ссылку перед отправкой')
        scope['amount']=item['amount']
        result['invoice_scope'].append(scope)
        old=previous.get(item['subscription_id'])
        if old and old.get('fingerprint')!=scope['fingerprint']:
            scope_changed=True
    if items:
        result['current_due']=sum(item['current_due'] for item in result['invoice_scope'])
        if result['current_due']<=0:
            return fail('Счёт уже оплачен. Эту ссылку отправлять не нужно')
        if parts:
            if sum(part['amount'] for part in parts)!=link['amount']:
                return fail('Сумма ссылки не совпадает с её составом. Создайте новую ссылку')
            if any(item['amount']!=item['current_due'] for item in result['invoice_scope']):
                result['amount_changed'] = True
                return fail('Сумма одного из счетов изменилась. Обновите общую ссылку перед отправкой')
            covered=money.balance(con,link['student_id'])['covered']
            last=max(item['month'] for item in result['invoice_scope'])
            if any(s['id'] not in selected and s['month']<=last and s['price']>covered.get(s['id'],0)
                    for s in money.obligations(con,link['student_id'])):
                return fail('Есть другие неоплаченные абонементы. Обновите общую ссылку')
        elif (require_full or result['is_full_payment'] is not False or not previous):
            if link['amount']!=result['current_due']:
                result['amount_changed'] = True
                return fail('Сумма счёта изменилась: по ссылке '+str(link['amount'])+' ₽, сейчас к оплате '+str(result['current_due'])+' ₽. Обновите ссылку перед отправкой')
        elif link['amount']>result['current_due']:
            return fail('Сумма счёта изменилась. Обновите ссылку перед отправкой')
        if scope_changed:
            return fail('Абонемент изменился. Обновите ссылку перед отправкой')
    elif require_full or subscription_id is not None or subscription_ids:
        return fail('Ссылка не привязана к выбранному счёту. Создайте новую ссылку')
    else:
        if stored.get('kind')!='amount' or previous:
            return fail('У старой ссылки не подтверждён исходный счёт. Создайте новую ссылку перед отправкой')
        result['current_due']=link['amount']
    if link['amount']<=0:
        return fail('В ссылке нет положительной суммы к оплате')
    result['valid']=True
    return result


def by_id(con,lid,**kwargs):
    from .paylinks import PayLinkError
    link=con.execute('SELECT * FROM pay_links WHERE id=?',(lid,)).fetchone()
    if not link:
        raise PayLinkError('Ссылка не найдена')
    return inspect(con,link,**kwargs)


def _message_links(con,text):
    from . import comms
    from .paylinks import PayLinkError
    base=urlsplit(comms.get(con,'public_url') or 'https://ab-plus.ru')
    configured_hosts={value.casefold().rstrip('.') for value in (base.hostname,'ab-plus.ru') if value}
    candidates=re.findall(r'https?://[^\s<>"\']+',str(text or ''),re.I)
    candidates+=['https://'+value for value in re.findall(
        r'(?<![\w@/:])(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?::\d+)?/(?:pay/[A-Za-z0-9_-]{40,64}|bill/[A-Za-z0-9_-]+)[^\s<>"\']*',
        str(text or ''))]
    seen=set()
    for candidate in candidates:
        candidate=candidate.rstrip('.,;:!?)]}')
        try:
            parsed=urlsplit(candidate)
            hostname=unquote(parsed.hostname or '').casefold().rstrip('.')
        except ValueError:
            continue
        token=re.fullmatch(r'/pay/([A-Za-z0-9_-]{40,64})/?',parsed.path)
        row=None
        if token:
            possible=con.execute('SELECT * FROM pay_links WHERE receipt_token=?',(token[1],)).fetchone()
            original=urlsplit(possible['url']) if possible else None
            allowed_hosts=configured_hosts|({original.hostname.casefold().rstrip('.')} if original and original.hostname else set())
            if hostname in allowed_hosts:
                if parsed.username is not None or parsed.password is not None:
                    raise PayLinkError('Адрес ссылки на оплату изменён. Вставьте исходную ссылку из CRM')
                try:
                    port=parsed.port
                    allowed=(port is None or (parsed.scheme.casefold(),port) in (('http',80),('https',443))
                        or base.port is not None and port==base.port and hostname==(base.hostname or '').casefold().rstrip('.')
                        or original and original.port is not None and port==original.port)
                except ValueError:
                    raise PayLinkError('Адрес ссылки на оплату изменён. Вставьте исходную ссылку из CRM') from None
                if allowed:
                    if not possible:
                        raise PayLinkError('Ссылка на оплату не найдена в CRM. Создайте её из нужного счёта')
                    row=possible
        if row is None:
            # Anchors and tracking parameters do not change the identity of a
            # known opaque CRM link or PayKeeper /bill/<id> resource. Required
            # query-only invoice identities are still matched exactly.
            normalized=parsed._replace(netloc=parsed.netloc.casefold())
            keys={candidate,parsed._replace(fragment='').geturl(),normalized._replace(fragment='').geturl()}
            if re.fullmatch(r'/(?:pay/[A-Za-z0-9_-]{40,64}|bill/[A-Za-z0-9_-]+)/?',parsed.path):
                keys.add(parsed._replace(query='',fragment='').geturl())
                keys.update(parsed._replace(scheme=scheme,query='',fragment='').geturl() for scheme in ('http','https'))
                keys.add(normalized._replace(query='',fragment='').geturl())
                keys.update(normalized._replace(scheme=scheme,query='',fragment='').geturl() for scheme in ('http','https'))
            variants=set(keys)
            for key in keys:
                value=urlsplit(key)
                if not value.query:
                    variants.update(value._replace(path=path).geturl() for path in (value.path.rstrip('/'),value.path.rstrip('/')+'/'))
            variants=tuple(variants)
            placeholders=','.join('?' for _ in variants)
            matches=list(con.execute('SELECT * FROM pay_links WHERE url IN ('+placeholders+') OR invoice_url IN ('+placeholders+') ORDER BY id DESC LIMIT 2',variants+variants))
            if len(matches)>1:
                raise PayLinkError('У этой ссылки несколько исходных записей. Создайте ссылку из нужного счёта')
            row=matches[0] if matches else None
        if row and row['id'] not in seen:
            if parsed.username is not None or parsed.password is not None:
                raise PayLinkError('Адрес ссылки на оплату изменён. Вставьте исходную ссылку из CRM')
            seen.add(row['id'])
            yield row


def guard_message(con,text,conversation):
    """Reject stale or foreign stored links before any message/provider write."""
    from . import comms
    from .paylinks import PayLinkError
    rows=list(_message_links(con,text))
    if not rows:
        return
    conversation=dict(conversation)
    phone_family=comms.family_by_phone(con,conversation.get('phone_norm') or conversation.get('phone') or '')
    handle_family=(comms.family_by_telegram_username(con,conversation.get('username') or '')
        if conversation['channel'] in ('telegram','wazzup_telegram','tg_archive') else None)
    # A combined matcher returns None both for "unknown" and for a conflict.
    # Keep independent known identities so a stored link cannot hide a phone /
    # Telegram handle disagreement and send another family's payment details.
    identities={value for value in (conversation.get('family_id'),phone_family,handle_family) if value}
    if len(identities)!=1:
        raise PayLinkError('Сначала свяжите чат с карточкой семьи, затем вставьте ссылку заново')
    family=next(iter(identities))
    for link in rows:
        student=con.execute('SELECT family_id FROM students WHERE id=?',(link['student_id'],)).fetchone()
        if not student or student['family_id']!=family:
            raise PayLinkError('Ссылка относится к другой семье. Выберите счёт нужного клиента')
        check=inspect(con,link)
        if not check['valid']:
            raise PayLinkError(check['reason'])
