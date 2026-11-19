"""Fail-closed reconciliation of a complete registry block and a booked bank credit.

paymentId must be the same structured bank/provider reference, never a date,
amount, free-text substring or an invented payout ID. Unknown refund schemas block
confirmation rather than silently treating refunds as zero.
"""
from decimal import Decimal
from datetime import date


def minor(value):
    number=Decimal(str(value))*100
    if not number.is_finite() or number!=number.to_integral_value():raise ValueError('Invalid amount')
    return int(number)


def prove(blocks,transactions,operation_ids,payment):
    result={'status':'unverified','note':'Нужна точная связь полного реестра с проводкой по идентификаторам'}
    try:
        if not payment.get('available') or payment.get('status') not in ('obtained','success'):
            return result
        if minor(payment.get('refund_amount'))!=0:
            return result|{'note':'Есть возврат PayKeeper. Нужен отдельный реестр возвратов и его связь с выплатой'}
        matches=[]
        for block in blocks:
            if not isinstance(block,dict) or block.get('paymentType')!='sbp':continue
            items=block.get('payments')
            if not isinstance(items,list) or not items:continue
            target=[i for i in items if str(i.get('operationId') or '') in operation_ids]
            if target:matches.append((block,target))
        if len(matches)!=1 or len(matches[0][1])!=1:return result
        block,target=matches[0];item=target[0]
        reference=str(block.get('paymentId') or '')
        if not reference:return result
        ids=[str(i.get('operationId') or '') for i in block['payments']]
        if not all(ids) or len(set(ids))!=len(ids):return result|{'note':'Реестр содержит повторяющиеся или пустые идентификаторы операций'}
        if any(i.get('status')!='APPROVED' for i in block['payments']):
            return result|{'note':'В реестре есть возвраты или незавершённые операции. Их состав и суммы должны быть подтверждены отдельно'}
        gross=fee=net=0
        for operation in block['payments']:
            g,c,n=(minor(operation.get(k)) for k in ('amount','commission','enrollmentAmount'))
            if min(g,c,n)<0 or g-c!=n:return result|{'note':'Не сходятся сумма, комиссия и сумма к зачислению в реестре'}
            gross+=g;fee+=c;net+=n
        if minor(item.get('amount'))!=minor(payment.get('pay_amount')) or minor(block.get('totalAmount'))!=gross:
            return result|{'note':'Не сходится сумма платежа или полный итог реестра'}
        # Only structured bank paymentId is the common cross-system reference.
        candidates=[t for t in transactions if str(t.get('paymentId') or '')==reference]
        if len(candidates)!=1:return result|{'note':'В выписке нет единственной проводки с paymentId полного реестра'}
        credit=candidates[0]
        if not credit.get('transactionId') or credit.get('status')!='Booked' or credit.get('creditDebitIndicator')!='Credit':return result
        amount=credit.get('Amount') or {}
        if amount.get('currency') not in ('RUB','643') or minor(amount.get('amount'))!=net:
            return result|{'note':'Сумма или валюта проводки не совпадает с реестром за вычетом комиссии'}
        date.fromisoformat(str(credit.get('documentProcessDate') or ''))
        return {'status':'confirmed','note':'Операция связана с полным реестром и проводкой по operationId и paymentId; суммы и комиссии сошлись',
            'reference_basis':['registry.operationId','registry.paymentId=statement.paymentId'],
            'bank_operation_id':str(credit['transactionId']),'registry_payment_id':reference,
            'operation_id':str(item['operationId']),'operation_count':len(ids),
            'gross_minor':gross,'fee_minor':fee,'refund_minor':0,'net_minor':net,
            'payment_gross_minor':minor(item['amount']),'booked_at':str(credit.get('documentProcessDate') or '')}
    except (ValueError,TypeError,ArithmeticError,KeyError):
        return result|{'note':'Неполный или неизвестный формат реестра/выписки; подтверждение не выставлено'}
