"""Bounded GET-only acquiring registry probe. A registry is not a bank statement."""
from decimal import Decimal,InvalidOperation
from urllib.parse import urlencode
import re
from . import fiscal


def _minor(value):
    n=Decimal(str(value))*100
    if not n.is_finite() or n!=n.to_integral_value():raise ValueError('Invalid monetary amount')
    return int(n)


def inspect(con,payment,identifiers,as_of=None):
    result={'status':'unverified','note':'Фактическая проводка по расчётному счёту ещё не подтверждена',
            'registry_matches':[],'required_permission':'ReadAcquiringData'}
    if not fiscal.cfg(con,'fx_tochka_token') or not payment.get('available') or payment.get('system_description')!='SBP-TOCHKA':return result
    wanted={str(v) for v in identifiers.values() if v}
    if not wanted:return result
    accounts=[]
    registry_blocks=[]
    transactions=[]
    stage='accounts'
    try:
        raw=fiscal.tochka_get(con,'/open-banking/v1.0/accounts')
        accounts=raw.get('Data',{}).get('Account') if isinstance(raw,dict) else None
        if not isinstance(accounts,list):raise ValueError('Unverified account list')
        codes=sorted({str(a['customerCode']) for a in accounts if isinstance(a,dict) and a.get('customerCode')})
        result['customer_count']=len(codes)
        result['coverage_limited']=len(codes)>1
        day=str(payment.get('obtain_datetime') or '')[:10]
        from datetime import date
        date.fromisoformat(day)
        result['registry_date']=day
        result['responses']=[]
        for code in codes[:1]:
            stage='retailers'
            raw=fiscal.tochka_get(con,'/acquiring/v1.0/retailers?'+urlencode({'customerCode':code}))
            retailers=raw.get('Data',{}).get('Retailer') if isinstance(raw,dict) else None
            result['responses'].append({'stage':stage,'shape':fiscal.shape(raw)})
            if not isinstance(retailers,list):raise ValueError('Unverified retailer list')
            merchants=sorted({str(r['merchantId']) for r in retailers if isinstance(r,dict) and r.get('merchantId')})
            result['merchant_count']=len(merchants)
            result['coverage_limited']=result['coverage_limited'] or len(merchants)>2
            for merchant in merchants[:2]:
                stage='registry'
                raw=fiscal.tochka_get(con,'/acquiring/v1.0/registry?'+urlencode({'customerCode':code,'merchantId':merchant,'date':day}))
                result['responses'].append({'stage':stage,'shape':fiscal.shape(raw)})
                blocks=raw.get('Data',{}).get('Registry') if isinstance(raw,dict) else None
                if not isinstance(blocks,list):raise ValueError('Unverified registry')
                if not isinstance(raw.get('Meta'),dict) or raw['Meta'].get('totalPages')!=1 or raw.get('Links',{}).get('next'):result['coverage_limited']=True
                registry_blocks.extend(blocks)
                for block in blocks:
                    if not isinstance(block,dict) or block.get('paymentType')!='sbp':continue
                    for item in block.get('payments',[]):
                        if not isinstance(item,dict):continue
                        matches=[(field,str(value)) for field,value in (('paymentId',block.get('paymentId')),('operationId',item.get('operationId'))) if value and str(value) in wanted]
                        if not matches:continue
                        gross,fee,net=(_minor(item.get(k)) for k in ('amount','commission','enrollmentAmount'))
                        consistent=gross==_minor(payment['pay_amount']) and fee>=0 and net>=0 and gross-fee==net
                        result['registry_matches'].append({'matched_ids':dict(matches),'operation_id':str(item.get('operationId') or ''),
                            'gross_minor':gross,'fee_minor':fee,'net_minor':net,'totals_consistent':consistent,
                            'operation_status':item.get('status'),'operation_time':item.get('time')})
        result['registry_status']='entry_found' if result['registry_matches'] else 'not_matched_in_received_registry'
    except Exception as error:
        result.update(error_stage=stage,error_type=type(error).__name__)
        if isinstance(error,fiscal.FxError):
            result['http_status']=error.http_status;result['provider_message']=error.provider_message
            if error.http_status==403:result['registry_status']='permission_denied'
        result.setdefault('registry_status','unavailable')
    # Reading a bank statement creates a report task, never a payment order.
    # Bound the first live probe to the single account already observed in CRM.
    if len(accounts)==1:
        result['statement_candidates']=[]
        try:
            from datetime import date
            day=date.fromisoformat(str(payment.get('obtain_datetime') or '')[:10])
            from datetime import datetime,timedelta
            from zoneinfo import ZoneInfo
            today=as_of or datetime.now(ZoneInfo('Europe/Moscow')).date()
            end=min(today,day+timedelta(days=31))
            if end<day:raise ValueError('Future payment timestamp')
            result['statement_period']={'from':day.isoformat(),'to':end.isoformat()}
            result['coverage_limited']=result.get('coverage_limited',False) or end<today
            transactions=fiscal.fetch_tochka(con,day,end)
            result['statement_status']='read'
            for item in transactions:
                basis=[]
                for field in ('transactionId','paymentId','endToEndId','instructionId'):
                    value=str(item.get(field) or '')
                    if value and value in wanted:basis.append(field)
                description=str(item.get('description') or '')
                for target in wanted:
                    if len(target)>=16 and re.search(r'(?<![A-Za-z0-9])'+re.escape(target)+r'(?![A-Za-z0-9])',description):
                        basis.append('description_exact_long_identifier')
                if not basis:continue
                result['statement_candidates'].append({
                    'bank_operation_id':str(item.get('transactionId') or item.get('paymentId') or ''),
                    'amount_minor':_minor((item.get('Amount') or {}).get('amount')),
                    'booked_credit':item.get('status')=='Booked' and item.get('creditDebitIndicator')=='Credit',
                    'reference_basis':sorted(set(basis)),
                    'date':str(item.get('documentProcessDate') or '')})
            result['statement_note']='Кандидаты по точным идентификаторам; отсутствие совпадения не доказывает отсутствие зачисления. Комиссия и пакетные выплаты проверяются отдельно'
        except Exception as error:
            result['statement_status']='unavailable'
            result['statement_error_type']=type(error).__name__
            if isinstance(error,fiscal.FxError):
                result['statement_http_status']=error.http_status
                result['statement_provider_message']=error.provider_message
    from . import settlementproof
    if not result.get('coverage_limited') and result.get('registry_status') not in ('permission_denied','unavailable'):
        proof=settlementproof.prove(registry_blocks,transactions,{str(v) for v in identifiers.values() if v},payment)
        if proof['status']=='confirmed':
            result.update(status='confirmed',note=proof['note'],settlement_proof=proof)
        elif registry_blocks:result['note']=proof['note']
    return result
