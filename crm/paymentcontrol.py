"""Read-only control projection covering every registry row, without false settlement proof."""
import json
from . import reconciliation,money,fiscal


def state(con,payment):
    if payment['cancelled']:
        return {'status':'excluded','label':'Отменённая запись','issues':[]}
    if payment['amount']<=0:
        return {'status':'attention','label':'Проверить возврат / корректировку',
                'issues':['Нужны подтверждение обратной операции и соответствующий чек']}
    chain=reconciliation.payment_state(con,payment)
    receipt=money.payment_receipt_info(con,payment)
    cached=con.execute('SELECT result FROM fiscal_payment_checks WHERE payment_id=?',(payment['id'],)).fetchone()
    check=json.loads(cached['result']) if cached else {}
    provider=check.get('provider_payment',{})
    links=con.execute("SELECT DISTINCT pk_payment_id FROM pay_links WHERE payment_id=? AND pk_payment_id!=''",(payment['id'],)).fetchall()
    provider_ok=False
    if links:
        provider_ok=len(links)==1 and provider.get('available') is True and str(provider.get('id'))==links[0]['pk_payment_id'] and provider.get('status') in ('obtained','success')
        try:provider_ok=provider_ok and fiscal.kop(provider.get('pay_amount'))==fiscal.kop(payment['amount']) and fiscal.kop(provider.get('refund_amount') or 0)==0
        except Exception:provider_ok=False
        source={'status':'confirmed' if provider_ok else 'unverified','label':'PayKeeper: оплата подтверждена' if provider_ok else 'PayKeeper: оплата требует проверки',
                'provider_payment_id':links[0]['pk_payment_id'] if len(links)==1 else None,'checked_at':check.get('checked_at'),
                'provider_status':provider.get('status'),'refund_amount':provider.get('refund_amount')}
    elif payment['method']=='cash':
        source={'status':'recorded','label':'Наличные внесены в CRM'}
    else:
        source={'status':'unlinked','label':'Не связана с исходной банковской операцией'}
    issues=[]
    if payment['method'] in ('paykeeper','paykeeper_manual','online') and not links:
        issues.append('Нет подтверждённой связи с исходным платежом PayKeeper; автоматический повторный зачёт недопустим')
    elif links and not provider_ok:issues.append('PayKeeper не подтвердил исходный платёж без возврата')
    bank_ok=chain['bank']['status'] in ('confirmed','not_applicable')
    if not bank_ok:issues.append(chain['bank'].get('note') or chain['bank']['label'])
    ofd_ok=chain['ofd']['status']=='confirmed'
    if not ofd_ok:
        ofd_error=check.get('ofd_check',{})
        reason=ofd_error.get('provider_message') or chain['ofd']['label']
        issues.append('ОФД: '+reason if not reason.startswith('ОФД') else reason)
    complete=bank_ok and ofd_ok and (provider_ok if links else payment['method']=='cash' and bool(payment['confirmed']))
    return {'status':'complete' if complete else 'attention','label':'Подтверждено' if complete else 'Требует проверки',
            'source':source,'bank':chain['bank'],'ofd':chain['ofd'],'receipt':receipt,'issues':issues,
            'checked_at':check.get('checked_at')}


def summarize(rows):
    active=[r for r in rows if r['control']['status']!='excluded']
    return {'total':len(active),'complete':sum(r['control']['status']=='complete' for r in active),
            'attention':sum(r['control']['status']=='attention' for r in active),
            'excluded':len(rows)-len(active)}
