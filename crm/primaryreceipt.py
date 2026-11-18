"""Primary PayKeeper receipt attributes observed on its payment options endpoint.

Attributes are not an OFD acknowledgement or a bank-settlement confirmation.
"""
from datetime import datetime
from decimal import Decimal,InvalidOperation
from urllib.parse import parse_qs,urlsplit
import html
import re


def parse(options,payment,orderid,amount):
    result={'status':'unverified','note':'Первичные фискальные реквизиты не подтверждены'}
    if not payment.get('available') or payment.get('status') not in ('obtained','success','stuck','refunded','partially_refunded','refunding'):
        return result
    if options.get('orderid')!=orderid:
        result['note']='Опции не подтвердили тот же заказ';return result
    fields={k:str(options.get('fop_'+src) or '').strip() for k,src in (('fn','fn'),('fd','fnd'),('fp','fpd'))}
    result['attribute_fields_present']={k:bool(v) for k,v in fields.items()}
    if not all(re.fullmatch(r'\d+',v) for v in fields.values()):return result
    result['attributes']=fields
    qr=html.unescape(str(options.get('fop_url') or '')).strip()
    params=parse_qs(urlsplit(qr).query if '?' in qr else qr,keep_blank_values=True)
    def one(key):
        values=params.get(key,[])
        return values[0] if len(values)==1 else ''
    result['qr_fields']={key:one(key) for key in ('t','s','fn','i','fp','n') if re.fullmatch(r'[0-9T.,]{1,32}',one(key))}
    try:
        total=Decimal(one('s'))*100
        if not total.is_finite() or total!=total.to_integral_value() or total!=Decimal(str(amount))*100:
            raise ValueError('Receipt amount mismatch')
        if one('n')!='1' or any(one(key)!=fields[field] for key,field in (('fn','fn'),('i','fd'),('fp','fp'))):
            raise ValueError('Receipt identity or operation mismatch')
        timestamp=one('t')
        issued=datetime.strptime(timestamp,'%Y%m%dT%H%M%S' if len(timestamp)==15 else '%Y%m%dT%H%M')
    except (ValueError,InvalidOperation):
        result['note']='Фискальные поля не совпали с QR, суммой или операцией прихода';return result
    result.update(status='fiscal_details',**fields,total_minor=int(total),operation='sale',issued_at=issued.isoformat(),
                  note='Реквизиты первичного чека получены из PayKeeper. Наличие в ОФД проверяется отдельно')
    return result
