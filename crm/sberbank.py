"""Read-only Sber Business statements. No payment/refund/fiscal write endpoints."""
import base64
import hashlib
import json
import re
import secrets
import time
import threading
import ssl
import socket
import errno
from datetime import date,datetime,timedelta
from decimal import Decimal
from urllib.parse import urlencode,urlparse,parse_qs,urljoin
from urllib.request import Request,build_opener,HTTPSHandler,HTTPRedirectHandler
from urllib.error import HTTPError,URLError
from zoneinfo import ZoneInfo
from . import fiscal,sbercredentials as credentials,sberpeer

API='https://fintech.sberbank.ru:9443'
AUTHORIZE='https://sbi.sberbank.ru:9443/ic/sso/api/v2/oauth/authorize'
TOKEN='/ic/sso/api/v2/oauth/token'
STATEMENT='/fintech/api/v2/statement/transactions'
USERINFO='/fintech/api/ic/sso/api/v2/oauth/user-info'
SCOPES=frozenset(('openid','accounts','GET_STATEMENT_ACCOUNT','GET_STATEMENT_TRANSACTION'))
_RATE_LOCK=threading.Lock()
_LAST_STATEMENT_REQUEST=0.0


def now():return datetime.now(ZoneInfo('Europe/Moscow')).replace(tzinfo=None)


def init(con):
    con.execute('''CREATE TABLE IF NOT EXISTS sber_oauth_states(
        state_hash TEXT PRIMARY KEY,actor_id INTEGER NOT NULL,expires_at TEXT NOT NULL,
        client_id TEXT NOT NULL,nonce TEXT NOT NULL)''')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


TRANSPORT_NOTES={
    'dns':'Сервер CRM не смог найти адрес Сбера. Проверка повторится автоматически',
    'timeout':'Сбер не ответил серверу CRM вовремя на порту 9443. Проверка повторится автоматически',
    'connection_refused':'Соединение сервера CRM со Сбером на порту 9443 отклонено',
    'network_unreachable':'У сервера CRM нет сетевого маршрута к Сберу на порт 9443',
    'tls_certificate_verify':'Сервер CRM не смог проверить сертификат сервера Сбера',
    'tls_alert':'Сбер прервал защищённое соединение. Нужна проверка клиентского сертификата и параметров TLS',
    'connection_reset':'Соединение сервера CRM со Сбером было прервано до получения ответа',
    'proxy_tunnel':'Прокси сервера CRM не пропустил соединение со Сбером на порт 9443',
    'connection_error':'Сбой связи сервера CRM со Сбером. Причина сохранена без секретных данных',
}


CERTIFICATE_NOTES={
    9:'Срок сертификата в цепочке Сбера ещё не наступил. Требуется проверить время сервера CRM',
    10:'В цепочке Сбера обнаружен сертификат с истёкшим сроком действия',
    18:'Сервер CRM не доверяет сертификату, представленному при соединении со Сбером',
    19:'Сервер CRM не смог построить доверенную цепочку сертификатов Сбера',
    20:'Сервер CRM не нашёл издателя сертификата Сбера в доверенной цепочке',
    21:'Сервер CRM получил неполную цепочку сертификатов Сбера',
    62:'Имя в сертификате не совпало с адресом сервера Сбера. Соединение остановлено',
}


def transport_failure(error):
    reason=error.reason if isinstance(error,URLError) else error
    if isinstance(reason,ssl.SSLCertVerificationError):code='tls_certificate_verify'
    elif isinstance(reason,ssl.SSLError):code='tls_alert'
    elif isinstance(reason,socket.gaierror):code='dns'
    elif isinstance(reason,(TimeoutError,socket.timeout)):code='timeout'
    elif isinstance(reason,ConnectionRefusedError) or getattr(reason,'errno',None)==errno.ECONNREFUSED:code='connection_refused'
    elif getattr(reason,'errno',None) in (errno.ENETUNREACH,errno.EHOSTUNREACH):code='network_unreachable'
    elif isinstance(reason,(ConnectionResetError,ConnectionAbortedError,EOFError)):code='connection_reset'
    elif isinstance(reason,OSError) and str(reason).startswith('Tunnel connection failed:'):code='proxy_tunnel'
    else:code='connection_error'
    verify_code=getattr(reason,'verify_code',None)
    note=CERTIFICATE_NOTES.get(verify_code,TRANSPORT_NOTES[code]) if code=='tls_certificate_verify' and type(verify_code) is int else TRANSPORT_NOTES[code]
    failure=fiscal.FxError(note);failure.transport_code=code
    # Only numeric enum diagnostics, never the original exception, host URL or key.
    if isinstance(getattr(reason,'verify_code',None),int):failure.verify_code=reason.verify_code
    return failure


def record_failure(con,stage,error,current=None):
    current=current or now()
    if stage not in ('token_exchange','connection_check','token_refresh','statement','accounts'):stage='connection_check'
    if isinstance(error,credentials.SettingsError):code='local_certificate';note=str(error)
    elif isinstance(error,fiscal.FxError):
        code=getattr(error,'transport_code',None) or ('http_'+str(error.http_status) if error.http_status else 'bank_response')
        note=TRANSPORT_NOTES.get(code,str(error))
        verify_code=getattr(error,'verify_code',None)
        if code=='tls_certificate_verify' and type(verify_code) is int:note=CERTIFICATE_NOTES.get(verify_code,note)
    else:code='connection_error';note=TRANSPORT_NOTES[code]
    value={'stage':stage,'code':code,'note':note[:500],'checked_at':current.isoformat(timespec='seconds')}
    verify_code=getattr(error,'verify_code',None)
    if type(verify_code) is int and 0<=verify_code<=99999:value['details']={'verify_code':verify_code}
    credentials.update_private(con,{'last_error':value})
    return value


def request(con,path,*,method='GET',params=None,form=None,token=None,bearer=False,text=False):
    # Never take URLs/paths from settings or bank pagination. Client cert cannot leak via redirect.
    if path not in (TOKEN,STATEMENT,USERINFO):raise fiscal.FxError('Недопустимый метод Сбера')
    headers={'Accept':'application/jwt' if text else 'application/json'}
    if token:headers['Authorization']=('Bearer ' if bearer else '')+token
    data=None
    if form is not None:
        data=urlencode(form).encode();headers['Content-Type']='application/x-www-form-urlencoded'
    url=API+path+('?' + urlencode(params) if params else '')
    peer_report=None;ext_loaded=False;strict_retried=False
    def remember_verified(status):
        if ext_loaded and type(status) is int and 100<=status<=599:
            evidence=dict(peer_report or {})
            evidence.update(official_ext_root_verified=credentials.OFFICIAL_EXT_ROOT_SHA256,verified_http_status=status)
            credentials.update_private(con,{'official_ext_trust_confirmed':True,'tls_diagnostics':evidence})
    try:
        context=credentials.tls_context(con)
        for attempt in range(3):
            opener=build_opener(HTTPSHandler(context=context),NoRedirect())
            if path==STATEMENT:
                global _LAST_STATEMENT_REQUEST
                with _RATE_LOCK:
                    delay=.21-(time.monotonic()-_LAST_STATEMENT_REQUEST)
                    if delay>0:time.sleep(delay)
                    _LAST_STATEMENT_REQUEST=time.monotonic()
            try:
                with opener.open(Request(url,data=data,headers=headers,method=method),timeout=30) as response:
                    raw=response.read(8_000_001)
                    if len(raw)>8_000_000:raise fiscal.FxError('Сбер вернул слишком большой ответ')
                    remember_verified(getattr(response,'status',None))
                break
            except (URLError,ssl.SSLError) as error:
                reason=error.reason if isinstance(error,URLError) else error
                strict=getattr(ssl,'VERIFY_X509_STRICT',0)
                if attempt<2 and not ext_loaded and isinstance(reason,ssl.SSLCertVerificationError) and getattr(reason,'verify_code',None)==19:
                    try:
                        peer_report=sberpeer.inspect(context)
                        credentials.update_private(con,{'tls_diagnostics':peer_report})
                        matched=peer_report.get('status')=='captured' and peer_report.get('probe_route') in ('direct','http_proxy') and any(
                            c.get('sha256')==credentials.OFFICIAL_EXT_ROOT_SHA256 for c in peer_report.get('chain',[]) if isinstance(c,dict))
                    except Exception:
                        matched=False  # Inspection failure must preserve the original certificate failure.
                    if matched and credentials.load_official_ext(context,credentials.OFFICIAL_EXT_ROOT_SHA256):
                        ext_loaded=True
                        continue
                if attempt<2 and not strict_retried and isinstance(reason,ssl.SSLCertVerificationError) and getattr(reason,'verify_code',None)==89 and strict and context.verify_flags & strict:
                    # Published Sber intermediate (2018) has non-critical BasicConstraints.
                    # Python 3.13 enables strict by default. Use standard CA verification
                    # only for this bank context; keep signature, trust, expiry and hostname.
                    context.verify_flags &= ~strict
                    strict_retried=True
                    continue
                raise
    except HTTPError as error:
        status=error.code
        remember_verified(status)
        try:error_code=json.loads(error.read(4096)).get('error','')
        except Exception:error_code=''
        error.close()
        note='Сбер: требуется обновить разрешение доступа' if status==401 else 'Сбер: нет разрешения читать выбранный счёт или выписку' if status==403 else 'Сбер временно не вернул данные; проверка повторится автоматически'
        if path==TOKEN and error_code=='invalid_client':note='Сбер не принял client_id или client_secret. Сверьте оба значения с параметрами промышленного сервиса и сохраните в подключении CRM'
        elif path==TOKEN and error_code=='invalid_grant':note='Сбер: требуется заново разрешить чтение выписки в подключении CRM'
        raise fiscal.FxError(note,http_status=status) from None
    except (fiscal.FxError,credentials.SettingsError):raise
    except Exception as error:raise transport_failure(error) from None
    try:return raw.decode('utf-8') if text else json.loads(raw.decode('utf-8'),parse_float=Decimal)
    except Exception:raise fiscal.FxError('Сбер вернул неизвестный формат ответа') from None


def credentials_ready(con):
    try:return bool(credentials.view(con).get('configured'))
    except credentials.SettingsError:return False


def transport_check(con,current=None):
    """GET without OAuth credentials: check server-side mTLS, never create an account/payment."""
    current=current or now()
    try:
        request(con,TOKEN)  # Unsupported GET normally returns 400/401/403/405 after TLS succeeds.
        result={'ok':True,'code':'reachable','note':'Защищённое соединение со Сбером установлено. Нажмите «Разрешить чтение в Сбере»'}
    except fiscal.FxError as error:
        if error.http_status in (400,401,403,404,405):
            result={'ok':True,'code':'reachable','http_status':error.http_status,'note':'Сервер Сбера доступен. Для чтения выписки нажмите «Разрешить чтение в Сбере»'}
        else:
            failure=record_failure(con,'connection_check',error,current)
            result={'ok':False,**failure}
    except credentials.SettingsError as error:
        failure=record_failure(con,'connection_check',error,current);result={'ok':False,**failure}
    result['checked_at']=current.isoformat(timespec='seconds')
    updates={'last_connection_check':result}
    previous=credentials.get(con,'last_error') or {}
    if result['ok'] and previous.get('code') in set(TRANSPORT_NOTES)|{'local_certificate'}:updates['last_error']=None
    credentials.update_private(con,updates)
    return result


def configured(con):
    try:return bool(credentials.get(con,'client_id') and credentials.get(con,'client_secret') and credentials.view(con).get('certificate_configured') and credentials.get(con,'refresh_token'))
    except credentials.SettingsError:return False


def maintenance(con,current=None):
    current=current or now();notes=[]
    stamp=credentials.get(con,'client_secret_updated_at')
    if stamp:
        changed=datetime.fromisoformat(stamp).astimezone(ZoneInfo('Europe/Moscow')).replace(tzinfo=None)
        days=40-(current-changed).days
        if days<=10:notes.append('Сбер: срок client_secret скоро истечёт или уже истёк. Обновите его в карточке сервиса и сохраните в подключении CRM')
    certificate=credentials.get(con,'certificate_expires_at')
    if certificate:
        expires=datetime.fromisoformat(certificate).astimezone(ZoneInfo('Europe/Moscow')).replace(tzinfo=None)
        if expires<current+timedelta(days=30):notes.append('Сбер: обновите сертификат в карточке сервиса и загрузите новый файл в подключении CRM')
    return notes


def settings(con):
    result=credentials.view(con)
    result['scope']=' '.join(sorted(SCOPES))
    result['maintenance']=maintenance(con)
    result['connected']=configured(con)
    result['token_expires_at']=credentials.get(con,'token_expires_at') or None
    result['last_error']=credentials.get(con,'last_error',None)
    result['last_connection_check']=credentials.get(con,'last_connection_check',None)
    result['tls_diagnostics']=credentials.get(con,'tls_diagnostics',None)
    return result


def authorize(con,actor_id,current=None):
    current=current or now()
    view=credentials.view(con)
    if not view.get('client_id') or not view.get('client_secret_configured') or not view.get('certificate_configured'):
        raise fiscal.FxError('Сначала сохраните реквизиты сервиса Сбера и сертификат')
    state=secrets.token_hex(32);nonce=secrets.token_hex(24)
    con.execute('DELETE FROM sber_oauth_states WHERE expires_at<=?',(current.isoformat(timespec='seconds'),))
    con.execute('INSERT INTO sber_oauth_states VALUES(?,?,?,?,?)',
        (hashlib.sha256(state.encode()).hexdigest(),actor_id,(current+timedelta(minutes=15)).isoformat(timespec='seconds'),view['client_id'],nonce))
    return {'url':AUTHORIZE+'?'+urlencode({'response_type':'code','client_id':view['client_id'],
        'redirect_uri':view['redirect_uri'],'scope':' '.join(sorted(SCOPES)),'state':state,'nonce':nonce})}


def store_tokens(con,response,current):
    if not isinstance(response,dict):raise fiscal.FxError('Сбер не подтвердил доступ')
    scopes=set(str(response.get('scope') or '').split())
    if not {'openid','accounts','GET_STATEMENT_ACCOUNT'}.issubset(scopes) or scopes-SCOPES:
        raise fiscal.FxError('Разрешения Сбера отличаются от доступа только к чтению. Подключите отдельный сервис CRM')
    if not isinstance(response.get('access_token'),str) or not response['access_token'] or not isinstance(response.get('refresh_token'),str) or not response['refresh_token']:
        raise fiscal.FxError('Сбер не вернул пару ключей доступа')
    lifetime=response.get('expires_in')
    if isinstance(lifetime,bool) or not isinstance(lifetime,int) or not 60<=lifetime<=86400 or str(response.get('token_type','')).lower()!='bearer':
        raise fiscal.FxError('Сбер не подтвердил срок доступа')
    credentials.update_private(con,{'access_token':response['access_token'],'refresh_token':response['refresh_token'],
        'token_expires_at':(current+timedelta(seconds=lifetime)).isoformat(timespec='seconds'),
        'authorized_scope':' '.join(sorted(scopes)),'authorization_checked_at':current.isoformat(timespec='seconds'),'last_error':None})
    return response['access_token']


def complete_authorization(con,actor_id,query,current=None):
    current=current or now();state=str(query.get('state') or '')
    if not re.fullmatch('[a-f0-9]{64}',state):raise fiscal.FxError('Ссылка подключения Сбера недействительна. Начните подключение заново')
    digest=hashlib.sha256(state.encode()).hexdigest()
    if con.in_transaction:raise fiscal.FxError('Подключение Сбера требует отдельного запроса без транзакции базы')
    # Consume state before taking the private-file lock: never invert SQL/file lock order.
    record=con.execute('DELETE FROM sber_oauth_states WHERE state_hash=? AND actor_id=? AND expires_at>? RETURNING *',
        (digest,actor_id,current.isoformat(timespec='seconds'))).fetchone()
    with credentials.lock(con):
        if not record or record['client_id']!=credentials.get(con,'client_id'):
            raise fiscal.FxError('Ссылка подключения Сбера истекла или уже использована. Начните подключение заново')
        if query.get('error') or not query.get('code'):raise fiscal.FxError('Сбер не разрешил подключение. Повторите вход и разрешите чтение выписки')
        try:
            response=request(con,TOKEN,method='POST',form={'grant_type':'authorization_code','client_id':record['client_id'],
                'client_secret':credentials.get(con,'client_secret'),'code':query['code'],'redirect_uri':credentials.get(con,'redirect_uri')})
            store_tokens(con,response,current)
        except (fiscal.FxError,credentials.SettingsError) as error:
            record_failure(con,'token_exchange',error,current);raise
    return {'connected':True}


def access_token(con,force=False,current=None):
    current=current or now()
    with credentials.lock(con):
        expiry=credentials.get(con,'token_expires_at')
        if not force and credentials.get(con,'access_token') and expiry and datetime.fromisoformat(expiry)>current+timedelta(seconds=60):
            return credentials.get(con,'access_token')
        refresh=credentials.get(con,'refresh_token')
        if not refresh:raise fiscal.FxError('Сбер: сначала разрешите CRM читать выписку в разделе подключения')
        response=request(con,TOKEN,method='POST',form={'grant_type':'refresh_token','client_id':credentials.get(con,'client_id'),
            'client_secret':credentials.get(con,'client_secret'),'refresh_token':refresh})
        return store_tokens(con,response,current)


def read(con,path,params=None,*,text=False):
    for attempt in range(2):
        token=access_token(con,force=attempt==1)
        try:return request(con,path,params=params,token=token,bearer=path==USERINFO,text=text)
        except fiscal.FxError as error:
            if error.http_status!=401 or attempt:raise


class AccountListMissing(fiscal.FxError):
    pass


def accounts(con):
    """Accounts data received directly from bank through verified mTLS, not a browser JWT login."""
    raw=read(con,USERINFO,text=True)
    try:
        # The envelope is used only for account data from the bank's verified connection.
        header,payload,signature=raw.strip().split('.')
        if not header or not payload or not signature:
            raise ValueError()
        claims=json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))
        if not isinstance(claims,dict):
            raise ValueError()
    except (AttributeError,TypeError,ValueError):
        raise fiscal.FxError('Сбер вернул список счетов в неожиданном формате') from None
    if 'accounts' not in claims:
        raise AccountListMissing('В ответе Сбера нет списка счетов')
    rows=claims['accounts']
    if rows==[]:
        raise fiscal.FxError('Сбер вернул пустой список счетов, доступных для чтения')
    if not isinstance(rows,list) or len(rows)>30:
        raise fiscal.FxError('Сбер вернул список счетов в неожиданном формате')
    numbers=[row.get('accountNumber') if isinstance(row,dict) else None for row in rows]
    if any(not isinstance(number,str) or not re.fullmatch(r'[0-9]{20}',number) for number in numbers):
        raise fiscal.FxError('В списке Сбера есть счёт с неподдерживаемым форматом номера')
    if len(numbers)!=len(set(numbers)):
        raise fiscal.FxError('В списке Сбера есть повторяющиеся счета')
    return numbers


def normalize(row,account,day):
    if not isinstance(row,dict):raise fiscal.FxError('Неизвестная операция в выписке Сбера')
    ident=row.get('uuid');direction=row.get('direction');amount=row.get('amount')
    if not isinstance(ident,str) or not ident or direction not in ('CREDIT','DEBIT') or not isinstance(amount,dict):
        raise fiscal.FxError('В операции Сбера отсутствуют ID, сумма или направление')
    try:
        value=Decimal(str(amount['amount']));currency=amount['currencyName']
        if not value.is_finite() or not 0<=value<=Decimal('99999999999999.99') or value*100!=(value*100).to_integral_value() or not re.fullmatch('[A-Z]{3}',currency):raise ValueError()
        at=datetime.fromisoformat(row['operationDate'].replace('Z','+00:00'))
        if at.tzinfo:at=at.astimezone(ZoneInfo('Europe/Moscow'))
        if at.date().isoformat()!=day:raise ValueError()
    except Exception:raise fiscal.FxError('Не удалось подтвердить сумму, валюту или дату операции Сбера') from None
    transfer=row.get('rurTransfer') or row.get('curTransfer') or row.get('swiftTransfer') or {}
    if not isinstance(transfer,dict):raise fiscal.FxError('Неизвестные реквизиты операции Сбера')
    if direction=='CREDIT' and transfer.get('payeeAccount') and transfer['payeeAccount']!=account:
        raise fiscal.FxError('Счёт операции Сбера не совпал с запрошенным счётом')
    return {'_account_id':'sber:'+account,'transactionId':ident,'status':'Booked',
        'creditDebitIndicator':'Credit' if direction=='CREDIT' else 'Debit','documentProcessDate':day,
        'Amount':{'amount':str(value),'currency':currency},'description':str(row.get('paymentPurpose') or ''),
        'DebtorParty':{'name':str(transfer.get('payerName') or transfer.get('orderingCustomerName') or ''),'inn':str(transfer.get('payerInn') or '')}}


def statement(con,account,day):
    rows=[];seen=set()
    for page in range(1,101):
        response=read(con,STATEMENT,{'accountNumber':account,'statementDate':day,'page':page})
        if not isinstance(response,dict) or not isinstance(response.get('transactions'),list) or not isinstance(response.get('_links',[]),list):
            raise fiscal.FxError('Сбер не вернул полную проверяемую выписку')
        for row in response['transactions']:
            normalized=normalize(row,account,day)
            if normalized['transactionId'] in seen:raise fiscal.FxError('Сбер повторил операцию при чтении страниц выписки')
            seen.add(normalized['transactionId']);rows.append(normalized)
        links=response.get('_links',[])
        if any(not isinstance(link,dict) or not isinstance(link.get('href'),str) or link.get('rel') not in ('next','prev','self','first','last') for link in links):raise fiscal.FxError('Сбер вернул неизвестную структуру страниц')
        nexts=[link for link in links if link.get('rel')=='next']
        if not nexts:return rows
        if len(nexts)!=1:raise fiscal.FxError('Сбер вернул неоднозначную следующую страницу')
        link=urlparse(urljoin(API+STATEMENT,str(nexts[0].get('href') or '')));params=parse_qs(link.query)
        if link.scheme!='https' or link.netloc!='fintech.sberbank.ru:9443' or link.path!=STATEMENT or params.get('accountNumber')!=[account] or params.get('statementDate')!=[day] or params.get('page')!=[str(page+1)]:
            raise fiscal.FxError('Сбер вернул несогласованную следующую страницу выписки')
    raise fiscal.FxError('Выписка Сбера превышает предел страниц; полнота не подтверждена')


def statement_accounts(con):
    selected=credentials.get(con,'accounts') or []
    try:
        visible=accounts(con)
    except AccountListMissing:
        if selected and all(re.fullmatch(r'[0-9]{20}', account) for account in selected):
            return selected, True
        raise AccountListMissing('В ответе Сбера нет списка счетов. Укажите 20-значный номер в настройках подключения Сбер Бизнес') from None
    if set(selected)-set(visible):
        raise fiscal.FxError('В списке Сбера отсутствует один из указанных счетов')
    return visible, False


def discover(con,current):
    from . import incomingcontrol
    if not configured(con):
        probe=transport_check(con,current)
        result={'checked':0,'completed':True,'blocked':1,'authorization_required':True,'unavailable':0 if probe['ok'] else 1,'note':probe['note'],'transport':probe}
        incomingcontrol.checked(con,'sber',current,'authorization_required' if probe['ok'] else 'unavailable',result)
        return result
    previous=con.execute("SELECT result FROM incoming_source_checks WHERE source='sber'").fetchone()
    old=json.loads(previous['result']) if previous else {}
    floor=max((current.date()-timedelta(days=31)).isoformat(),fiscal.control_from(con))
    cursor=old.get('history_cursor') or floor
    if not floor<=cursor<=current.date().isoformat():cursor=floor
    days=list(dict.fromkeys(day for day in (current.date().isoformat(),(current.date()-timedelta(days=1)).isoformat(),cursor)
                            if floor<=day<=current.date().isoformat()))
    errors=[];successful=[];count=0;account_count=0;selected_only=False
    try:
        visible,selected_only=statement_accounts(con)
        if not selected_only:
            credentials.update_private(con,{'discovered_accounts':visible,'accounts_checked_at':current.isoformat(timespec='seconds')})
        account_count=len(visible)
        for day in days:
            try:
                transactions=[]
                for account in visible:transactions.extend(statement(con,account,day))
                count+=incomingcontrol.ingest_bank(con,'sber',transactions,current);successful.append(day)
            except (fiscal.FxError,credentials.SettingsError) as error:errors.append({'day':day,'note':str(error),'http_status':getattr(error,'http_status',None)})
            except Exception:errors.append({'day':day,'note':'Сбер: не удалось подтвердить полноту выписки'})
        if cursor in successful:
            cursor=(date.fromisoformat(cursor)+timedelta(days=1)).isoformat()
            if cursor>current.date().isoformat():cursor=floor
    except (fiscal.FxError,credentials.SettingsError) as error:errors.append({'note':str(error),'http_status':getattr(error,'http_status',None)})
    except Exception:errors.append({'note':'Сбер: проверка временно недоступна; повтор произойдёт автоматически'})
    result={'checked':count,'completed':True,'days':days,'successful_days':successful,'history_cursor':cursor,
        'accounts_count':account_count,'maintenance':maintenance(con,current),'unavailable':len(errors),'errors':errors,'empty':not count and not errors}
    if selected_only:
        result['note']='Проверяются счета из настроек CRM. Общий список счетов Сбер не вернул.'
    incomingcontrol.checked(con,'sber',current,'unavailable' if errors else 'partial' if selected_only else 'ok',result)
    return result


def test_connection(con):
    if not configured(con):return transport_check(con)
    visible,selected_only=statement_accounts(con)
    current=now().date()
    day=max((current-timedelta(days=1)).isoformat(),fiscal.control_from(con))
    count=sum(len(statement(con,account,day)) for account in visible)
    note=('Выписка по указанным счетам читается. Общий список счетов Сбер не вернул.'
          if selected_only else 'Счета и выписка Сбера читаются. Поступления проверяются в фоне каждые 10 минут')
    return {'ok':True,'accounts_count':len(visible),'transactions_count':count,'note':note}
