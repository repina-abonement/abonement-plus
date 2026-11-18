"""Read-only receipt verification for one actual CRM payment."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from . import fiscal,services as sv


def init(con):
    con.execute('''CREATE TABLE IF NOT EXISTS fiscal_payment_checks (
        payment_id INTEGER PRIMARY KEY,checked_at TEXT NOT NULL,status TEXT NOT NULL,result TEXT NOT NULL)''')


def check(con,pid,user):
    if user.get('role')!='admin' and not(user.get('role')=='operator' and user.get('is_senior')):raise sv.ApiError('Проверка фискализации доступна руководителю и старшему администратору',403)
    payment=con.execute('SELECT * FROM payments WHERE id=? AND cancelled=0',(pid,)).fetchone()
    if not payment:raise sv.ApiError('Платёж не найден или отменён',404)
    stamp=datetime.now(ZoneInfo('Europe/Moscow')).replace(tzinfo=None,second=0,microsecond=0).isoformat(timespec='minutes')
    link=con.execute("SELECT pk_payment_id FROM pay_links WHERE payment_id=? AND pk_payment_id!='' ORDER BY id DESC LIMIT 1",(pid,)).fetchone()
    result={'checked_at':stamp,'status':'unknown','label':'Чек не подтверждён','source_available':None}
    if not link:
        result['note']='Для этой оплаты нужна подтверждённая связь с кассовым чеком / ОФД'
    elif not all(fiscal.cfg(con,k) for k in ('fx_pk_server','fx_pk_user','fx_pk_password')):
        result.update(status='unavailable',label='Источник недоступен',source_available=False,note='Не настроен доступ к API PayKeeper')
    else:
        try:
            # The first receipt is absent from receipts/bypaymentid. Inspect exact
            # payment options first; this is read-only and does not claim OFD success.
            from . import paykeepercheck
            inspection=paykeepercheck.inspect(con,link['pk_payment_id'])
            primary=inspection.get('primary_receipt',{})
            if primary.get('status')=='fiscal_details':
                fiscal.store_pk(con,[],[{'id':'primary:'+link['pk_payment_id'],
                    'payment_id':link['pk_payment_id'],'status':'fiscal_details','type':'sale',
                    'fn':primary['fn'],'fnd':primary['fd'],'fpd':primary['fp'],
                    'sum_cashless':str(primary['total_minor']/100),
                    'obtain_datetime':primary['issued_at']}],datetime.now(ZoneInfo('Europe/Moscow')).replace(tzinfo=None))
                result.update(status='fiscal_details',label='Реквизиты чека получены',source_available=True,note=primary['note'])
                ofd=inspection.get('ofd',{})
                if ofd.get('status')=='confirmed':
                    result['note']=ofd['note']
                    fiscal.store_ofd(con,[{'fiscalDriveNumber':ofd['fn'],'fiscalDocumentNumber':ofd['fd'],
                        'fiscalSign':ofd['fp'],'totalSum':ofd['total_minor'],'operationType':1,
                        'receiptDate':ofd['issued_at']}])

            else:
                additional=inspection.get('additional_receipts',{})
                if not additional.get('available'):raise ValueError('Unverified receipt response')
                receipts=additional['receipts']
                receipts=[r for r in receipts if r.get('type') in ('sale',None)]
                result['source_available']=True
                result['coverage']='Дополнительные чеки и окончательный расчёт; первичный чек этим методом не проверяется'
                confirmed=[r for r in receipts if r.get('status')=='success' and all(r.get(k) for k in ('fn','fnd','fpd'))
                           and sum(fiscal.kop(r.get(k)) for k in ('sum_cashless','sum_cash','sum_advance','sum_credit'))==fiscal.kop(payment['amount'])]
                if confirmed:
                    fiscal.store_pk(con,[],confirmed,datetime.now(ZoneInfo('Europe/Moscow')).replace(tzinfo=None))
                    r=confirmed[-1];result.update(status='confirmed',label='Чек пробит',receipt_id=str(r['id']))
                elif any(r.get('status') in ('created','sending','request_sent') for r in receipts):result.update(status='pending',label='Чек формируется')
                elif any(r.get('status') in ('failed','timeout') for r in receipts):result.update(status='failed',label='Ошибка фискализации')
                elif not receipts:result.update(status='unknown',label='Первичный чек не проверен',note='PayKeeper receipts/bypaymentid не возвращает первый чек при получении платежа. Пустой ответ не означает отсутствие чека')
                else:result['note']='API не подтвердил фискальные реквизиты и сумму этой оплаты'
        except Exception:
            result.update(status='unavailable',label='Источник недоступен',source_available=False,note='Не удалось получить и проверить ответ API PayKeeper. Новый чек не создавался')
    con.execute('INSERT INTO fiscal_payment_checks VALUES (?,?,?,?) ON CONFLICT(payment_id) DO UPDATE SET checked_at=excluded.checked_at,status=excluded.status,result=excluded.result',(pid,stamp,result['status'],json.dumps(result,ensure_ascii=False)))
    from . import money
    return money.payment_receipt_info(con,payment)


def run_automatic(con,now=None,limit=30):
    """Continuously check actual PayKeeper payments; no printing or money movement."""
    from datetime import timedelta
    current=now or datetime.now(ZoneInfo('Europe/Moscow'))
    if current.tzinfo is not None:current=current.astimezone(ZoneInfo('Europe/Moscow')).replace(tzinfo=None)
    cutoff=(current-timedelta(minutes=10)).isoformat(timespec='minutes')
    rows=con.execute('''SELECT DISTINCT p.id FROM payments p JOIN pay_links l ON l.payment_id=p.id
        LEFT JOIN fiscal_payment_checks c ON c.payment_id=p.id
        WHERE p.cancelled=0 AND p.amount>0 AND l.pk_payment_id!=''
          AND (c.payment_id IS NULL OR (c.checked_at<=? AND (c.status!='confirmed' OR NOT EXISTS (SELECT 1 FROM fx_receipts r WHERE r.source='paykeeper' AND r.pk_payment=l.pk_payment_id AND r.fiscal_fn!='' AND r.fiscal_fd!='' AND r.fiscal_fp!=''))))
        ORDER BY c.checked_at IS NOT NULL,c.checked_at,p.id LIMIT ?''',(cutoff,max(0,min(int(limit),100)))).fetchall()
    result={'checked':0,'confirmed':0,'unavailable':0}
    for row in rows:
        item=check(con,row['id'],{'id':0,'role':'admin','name':'Автопроверка фискализации'})
        result['checked']+=1
        if item['status']=='confirmed':result['confirmed']+=1
        if item['status']=='unavailable':result['unavailable']+=1
    return result
