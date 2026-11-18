"""Read-only provider inspection, with explicit primary-receipt coverage limits."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from . import fiscal,services as sv

PAYMENT_FIELDS=('id','status','pay_amount','refund_amount','payment_system_id','unique_id',
                'RRN','site_description','system_description','obtain_datetime','success_datetime')
RECEIPT_FIELDS=('id','payment_id','type','is_post_sale','status','sum_cashless','sum_cash',
                'sum_advance','sum_credit','sum_counter_payment','fn','fnd','fpd','ts','obtain_datetime')


def inspect(con,provider_id,include_bank=True):
    provider_id=str(provider_id or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',provider_id):
        raise sv.ApiError('Укажите ID платежа PayKeeper',400)
    link=con.execute("SELECT l.*,p.amount AS recorded_amount FROM pay_links l JOIN payments p ON p.id=l.payment_id WHERE l.pk_payment_id=? AND p.cancelled=0 ORDER BY l.id DESC LIMIT 1",(provider_id,)).fetchone()
    if not link:raise sv.ApiError('Связанный платёж CRM не найден',404)
    result={'inspection_version':3,'provider_payment_id':provider_id,'crm_payment_id':link['payment_id'],
            'checked_at':datetime.now(ZoneInfo('Europe/Moscow')).isoformat(timespec='seconds'),
            'payment':{'available':False},'additional_receipts':{'available':False},
            'primary_receipt':{'status':'unverified','note':'Метод receipts/bypaymentid не возвращает первый чек при получении платежа'},
            'bank_settlement':{'status':'unverified'},'ofd':{'status':'unverified'}}
    if not all(fiscal.cfg(con,k) for k in ('fx_pk_server','fx_pk_user','fx_pk_password')):
        result['error']='Не настроен доступ к API PayKeeper'
        return result
    try:
        raw=fiscal.pk_call(con,'/info/payments/byid/',{'id':provider_id,'advanced':'true'})
        rows=raw if isinstance(raw,list) else [raw] if isinstance(raw,dict) else []
        if len(rows)!=1 or not isinstance(rows[0],dict) or str(rows[0].get('id'))!=provider_id:
            raise ValueError('Unverified provider payment')
        row=rows[0]
        if fiscal.kop(row.get('pay_amount'))!=fiscal.kop(link['recorded_amount']) or row.get('orderid')!=link['orderid']:
            raise ValueError('Payment does not match recorded order and amount')
        result['payment']={'available':True,**{k:row[k] for k in PAYMENT_FIELDS if k in row},'order_matches':True}
        result['payment']['response_fields']=sorted(str(k) for k in row)
    except Exception:
        result['payment']['error']='Не удалось прочитать и подтвердить точный платёж по ID, заказу и сумме'
    try:
        raw=fiscal.pk_call(con,'/info/receipts/bypaymentid/',{'payment_id':provider_id})
        rows=raw.get('receipts') if isinstance(raw,dict) else raw
        if not isinstance(rows,list) or any(not isinstance(r,dict) or str(r.get('payment_id'))!=provider_id or not r.get('id') for r in rows):
            raise ValueError('Unverified additional receipts')
        result['additional_receipts']={'available':True,'receipts':[{k:r[k] for k in RECEIPT_FIELDS if k in r} for r in rows],
                                      'coverage':'Произвольные чеки и чеки окончательного расчёта; первичный чек не входит'}
    except Exception:
        result['additional_receipts']['error']='Не удалось прочитать и подтвердить дополнительные чеки'
    try:
        raw=fiscal.pk_call(con,'/info/options/byid/',{'id':provider_id})
        rows=raw if isinstance(raw,list) else [raw] if isinstance(raw,dict) else []
        if len(rows)!=1 or not isinstance(rows[0],dict):raise ValueError('Unverified options')
        # Option names help identify the server's capabilities. Values may contain PII.
        result['option_fields']={'available':True,'names':sorted(str(k) for k in rows[0])}
        options=rows[0]
        from . import primaryreceipt
        result['primary_receipt']=primaryreceipt.parse(options,result['payment'],link['orderid'],link['recorded_amount'])
        if result['payment'].get('available') and options.get('orderid')==link['orderid']:
            result['sbp_identifiers']={k:str(options[k]) for k in ('sbp_operationId','sbp_trxId')
                                       if options.get(k) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}',str(options[k]))}

    except Exception:
        result['option_fields']={'available':False}
    result['ofd']=primary_ofd(con,result['primary_receipt'])
    if include_bank:
        from . import tochkacheck
        result['bank_settlement']=tochkacheck.inspect(con,result['payment'],result.get('sbp_identifiers',{}))
    return result


def source_status(con):
    """Probe existing read credentials only; no statements requested, no print/run calls."""
    result={'checked_at':datetime.now(ZoneInfo('Europe/Moscow')).isoformat(timespec='seconds'),
            'tochka':{'configured':bool(fiscal.cfg(con,'fx_tochka_token')),'available':False},
            'ofd':{'configured':bool(fiscal.cfg(con,'fx_ofd_token')),'available':False},
            'sber':{'connector_implemented':False},
            'legacy_automation_enabled':fiscal.cfg(con,'fx_enabled')=='1',
            'automatic_reprint_enabled':fiscal.cfg(con,'fx_auto_retry')=='1',
            'note':'Доступ к источнику не подтверждает зачисление платежа или наличие конкретного чека'}
    if result['tochka']['configured']:
        try:
            raw=fiscal.tochka_get(con,'/open-banking/v1.0/accounts')
            rows=raw.get('Data',{}).get('Account') if isinstance(raw,dict) else None
            if not isinstance(rows,list):raise ValueError('Unverified account response')
            result['tochka'].update(available=True,accounts_count=len(rows),response_shape=fiscal.shape(raw))
        except Exception:
            result['tochka']['error']='Не удалось подтвердить чтение списка счетов Точки'
    if result['ofd']['configured']:
        try:
            raw=fiscal.ofd_get(con,'/kkts')
            if not isinstance(raw,(dict,list)):raise ValueError('Unverified cash registers response')
            result['ofd'].update(available=True,cash_registers_count=len(fiscal.ofd_kkt_ids(raw)),response_shape=fiscal.shape(raw))
        except Exception:
            result['ofd']['error']='Не удалось прочитать список касс ОФД через настроенный API'
    return result


def primary_ofd(con,primary):
    """Read exact receipt day. Unknown schema/pagination never proves absence."""
    result={'status':'unverified','note':'Наличие чека в ОФД не подтверждено'}
    if primary.get('status')!='fiscal_details' or not fiscal.cfg(con,'fx_ofd_token'):return result
    stage='cash_registers'
    try:
        kkts=fiscal.fetch_ofd_kkts(con)
        result['cash_registers_count']=len(kkts)
        if not kkts:raise ValueError('No accessible cash registers')
        day=primary['issued_at'][:10]
        result['responses']=[]
        for kkt in kkts:
            stage='receipts'
            result['request']={'dateFrom':day+' 00:00:00','dateTo':day+' 23:59:59','dateType':1,'kkt_parameter':'kktRegId'}
            raw=fiscal.ofd_get(con,'/receipts',{'dateFrom':day+' 00:00:00','dateTo':day+' 23:59:59','dateType':1,'kktRegId':kkt})
            result['responses'].append({'shape':fiscal.shape(raw)})
            rows=raw.get('receipts') if isinstance(raw,dict) else None
            if not isinstance(rows,list):
                result['status']='schema_unverified';continue
            for row in rows:
                if not isinstance(row,dict):continue
                fn=str(row.get('fiscalDriveNumber') or row.get('kktFN') or '')
                fd=str(row.get('fiscalDocumentNumber') or row.get('requestNumber') or '')
                fp=str(row.get('fiscalSign') or row.get('fiscalDocumentSign') or '')
                if (fn,fd,fp)!=(primary['fn'],primary['fd'],primary['fp']):continue
                if str(row.get('operationType'))!='1' or str(row.get('totalSum'))!=str(primary['total_minor']):continue
                return result|{'status':'confirmed','note':'В ответе ОФД совпали ФН, ФД, ФП, сумма и приход',
                               'fn':fn,'fd':fd,'fp':fp,'total_minor':primary['total_minor'],
                               'issued_at':primary['issued_at']}
        result['note']='Точного совпадения в полученных ответах нет; полнота и пагинация требуют проверки'
    except Exception as error:
        result.update(status='unavailable',note='Не удалось прочитать чеки ОФД за дату первичного чека',error_stage=stage,error_type=type(error).__name__)
        if isinstance(error,fiscal.FxError):
            result['http_status']=error.http_status
            result['provider_message']=error.provider_message
    return result
