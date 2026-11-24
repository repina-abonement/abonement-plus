"""Atomic multi-month sales using the regular monthly calculation and ledger."""
import hashlib
import json
from . import services as sv, logic, money, balancecredit


def preview(con, sid, data, user):
    use_balance=data.get('use_balance',False)
    if type(use_balance) is not bool:
        raise sv.ApiError('Подтвердите выбор зачёта денег с баланса')
    months=data.get('months')
    if not isinstance(months,list) or not 2<=len(months)<=12 or any(not isinstance(m,str) for m in months) or len(set(months))!=len(months):
        raise sv.ApiError('Выберите от 2 до 12 разных месяцев')
    try:
        for month in months:logic.month_bounds(month)
        gid=int(data.get('group_id') or 0)
    except (TypeError,ValueError):raise sv.ApiError('Проверьте месяцы и группу')
    rows=[]
    exclusions=data.get('excluded_by_month') or {}
    if not isinstance(exclusions,dict):raise sv.ApiError('Проверьте исключённые занятия')
    for index,month in enumerate(sorted(months)):
        existing=con.execute('''SELECT x.id FROM subscriptions x JOIN enrollments e ON e.id=x.enrollment_id
            WHERE x.student_id=? AND e.group_id=? AND x.month=? AND x.status!='cancelled'
            AND COALESCE(x.kind,'month')='month' ''',(sid,gid,month)).fetchone()
        if existing:raise sv.ApiError(f'На {logic.month_label(month)} в выбранную группу уже есть абонемент — уберите этот месяц')
        args=dict(group_id=gid,tariff=data.get('tariff'),discount_id=data.get('discount_id'),ofp_group_id=data.get('ofp_group_id'),paid_date=data.get('paid_date'),user=user)
        # All months in one atomic first purchase share the trial-day discount.
        # A later separate purchase sees the completed/part-paid first purchase
        # through pricing.is_first_purchase and cannot receive it again.
        excluded=exclusions.get(month)
        quote=sv.quote_for(con,sid,month,excluded_dates=excluded,**args)
        if excluded is None and quote.get('trial_cutoff'):
            excluded=[d for d in quote.get('calendar_dates',quote['dates']) if d<=quote['trial_cutoff']]
            if excluded:quote=sv.quote_for(con,sid,month,excluded_dates=excluded,**args)
        rows.append({'month':month,'quote':quote,'excluded_dates':quote.get('excluded_dates',[]),'effective_discount_id':args['discount_id']})
    covered=money.balance(con,sid)['covered']
    if any(sub['month']<=max(months) and sub['price']>covered.get(sub['id'],0) for sub in money.obligations(con,sid)):
        raise sv.ApiError('Есть ранее выставленные неоплаченные абонементы. Сначала сверьте их, чтобы общая оплата попала в выбранные месяцы')
    total=sum(r['quote']['total'] for r in rows)
    balance=sv._invoice_balance_preview(con,sid,total,use_balance)
    credit=balance['apply_amount']
    token=hashlib.sha256(json.dumps({'rows':rows,'balance_preview':balance,'use_balance':use_balance},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return {'months':rows,'total':total,'credit':credit,'due':balance['payable'],'token':token,'balance_preview':balance}


def create(con,sid,data,user):
    con.execute('''CREATE TABLE IF NOT EXISTS multi_month_orders (
        request_id TEXT PRIMARY KEY,student_id INTEGER NOT NULL,signature TEXT NOT NULL,result TEXT NOT NULL)''')
    key=data.get('request_id')
    if not isinstance(key,str) or not 8<=len(key)<=80:raise sv.ApiError('Обновите форму продажи')
    signature=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    old=con.execute('SELECT * FROM multi_month_orders WHERE request_id=?',(key,)).fetchone()
    if old:
        if old['student_id']!=sid or old['signature']!=signature:raise sv.ApiError('Этот запрос уже использован с другими данными')
        return json.loads(old['result'])
    mode=data.get('mode')
    if mode not in ('invoice','pay','links'):raise sv.ApiError('Выберите способ оформления')
    quote=preview(con,sid,data,user)
    if data.get('expected_token')!=quote['token']:raise sv.ApiError('Расчёт изменился — проверьте суммы заново',409)
    if mode=='pay':
        amount=data.get('amount')
        if isinstance(amount,bool) or not isinstance(amount,int) or amount<1 or amount>quote['due']:raise sv.ApiError('Проверьте сумму оплаты')
        if data.get('method') not in money.MANUAL_METHODS:raise sv.ApiError('Выберите способ фактической оплаты')
    if mode=='links':
        from . import paylinks
        if not paylinks.ready(con):raise sv.ApiError('PayKeeper не подключён')
        if data.get('paid_date')!=sv.today().isoformat():raise sv.ApiError('Для прошедшей оплаты используйте «Отметить оплату»')
    con.execute('SAVEPOINT multi_month_sale')
    try:
        subs=[]
        for row in quote['months']:
            invoice=sv.create_invoice(con,sid,row['month'],user,discount_id=row['effective_discount_id'],tariff=data.get('tariff'),
                excluded_dates=row['excluded_dates'],group_id=data['group_id'],ofp_group_id=data.get('ofp_group_id'),
                group_action=data.get('group_action') or 'add',paid_date=data.get('paid_date'),exclusion_reason=data.get('exclusion_reason') or '',
                request_id='multi-invoice:'+hashlib.sha256((key+':'+row['month']).encode()).hexdigest())
            if con.execute('SELECT price FROM subscriptions WHERE id=?',(invoice['id'],)).fetchone()[0]!=row['quote']['total']:
                raise sv.ApiError('Стоимость месяца изменилась — обновите расчёт',409)
            subs.append({'id':invoice['id'],'month':row['month'],'price':row['quote']['total']})
        remaining_credit=quote['credit']
        if data.get('use_balance',False):
            for sub in subs:
                if not remaining_credit:
                    break
                balance=sv._invoice_balance_preview(con,sid,sub['price'],True,sub['id'])
                if balance['apply_amount']>remaining_credit:
                    raise sv.ApiError('Сумма зачёта изменилась — обновите расчёт',409)
                if balance['apply_amount']:
                    request='multi-balance:'+hashlib.sha256((key+':'+str(sub['id'])).encode()).hexdigest()
                    balancecredit.apply_to_invoice(con,sub['id'],user,True,balance['token'],request)
                    remaining_credit-=balance['apply_amount']
            if remaining_credit:
                raise sv.ApiError('Баланс изменился — обновите расчёт',409)
            money.reconcile(con,sid)
        remaining=data.get('amount',0) if mode=='pay' else 0
        for sub in subs:
            due=sv.payment_quote(con,sub['id'],data.get('paid_date'))['total']
            if remaining and due:
                allocated=min(remaining,due)
                sv.pay_subscription(con,sub['id'],user,data['method'],allocated,data.get('paid_date'),f"multi:{key}:{sub['id']}")
                remaining-=allocated
            sub['due']=sv.payment_quote(con,sub['id'],data.get('paid_date'))['total']
        if remaining:raise sv.ApiError('Сумма к оплате изменилась — обновите расчёт')
        link=None
        if mode=='links':
            from . import paylinks
            link=paylinks.create_bundle(con,sid,[s['id'] for s in subs],user)
        result={'link':link,'subscriptions':subs,'total':sum(s['price'] for s in subs),'due':sum(s['due'] for s in subs)}
        result['payable']=result['due']
        con.execute('INSERT INTO multi_month_orders VALUES (?,?,?,?)',(key,sid,signature,json.dumps(result,ensure_ascii=False)))
        con.execute('RELEASE multi_month_sale')
        return result
    except BaseException as error:
        con.execute('ROLLBACK TO multi_month_sale');con.execute('RELEASE multi_month_sale')
        if isinstance(error,ValueError):
            raise sv.ApiError(str(error),409) from error
        raise
