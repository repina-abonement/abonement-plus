"""Отмена абонемента с аудитом и подтверждаемым возвратом через исходный способ оплаты.

PayKeeper: возврат выполняется в кабинете, CRM только читает его статус.
До подтверждения сумма зарезервирована и не доступна для новых занятий.
"""
import json
from . import fiscal, money, services as sv


def init(con):
    con.executescript('''
    CREATE TABLE IF NOT EXISTS subscription_cancellations (
        id INTEGER PRIMARY KEY, subscription_id INTEGER NOT NULL UNIQUE REFERENCES subscriptions(id),
        student_id INTEGER NOT NULL, mode TEXT NOT NULL, amount INTEGER NOT NULL,
        reason TEXT NOT NULL, approval TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS subscription_refunds (
        id INTEGER PRIMARY KEY, cancellation_id INTEGER NOT NULL REFERENCES subscription_cancellations(id),
        payment_id INTEGER NOT NULL REFERENCES payments(id), reserve_id INTEGER NOT NULL REFERENCES payments(id),
        amount INTEGER NOT NULL, method TEXT NOT NULL, pk_payment_id TEXT NOT NULL DEFAULT '',
        baseline TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'pending',
        proof TEXT NOT NULL DEFAULT '', provider_refund_id TEXT NOT NULL DEFAULT '', provider_refund_number TEXT NOT NULL DEFAULT '', receipt_id TEXT NOT NULL DEFAULT '', confirmed_at TEXT, confirmed_by TEXT NOT NULL DEFAULT ''
    );
    ''')


    cols={r[1] for r in con.execute('PRAGMA table_info(subscription_cancellations)')}
    for field in ('effective_date TEXT', 'retained_amount INTEGER NOT NULL DEFAULT 0',
                  'retained_lessons INTEGER NOT NULL DEFAULT 0', 'future_amount INTEGER NOT NULL DEFAULT 0',
                  'period_verified INTEGER NOT NULL DEFAULT 0', 'balance_available INTEGER NOT NULL DEFAULT 0',
                  'applied_to_invoices INTEGER NOT NULL DEFAULT 0', 'paid_before INTEGER NOT NULL DEFAULT 0'):
        if field.split()[0] not in cols:
            con.execute('ALTER TABLE subscription_cancellations ADD COLUMN '+field)
    if 'lesson_schedule' not in {r[1] for r in con.execute('PRAGMA table_info(subscriptions)')}:
        con.execute("ALTER TABLE subscriptions ADD COLUMN lesson_schedule TEXT NOT NULL DEFAULT ''")


def today():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo('Europe/Moscow')).date()


def period_quote(con, sub, effective=None):
    """Только даты строго после сегодняшней; прошедший период удерживается независимо от явки."""
    from datetime import date, timedelta
    from . import schedule, closures, logic
    minimum=today()+timedelta(days=1)
    try:
        cutoff=date.fromisoformat(effective) if effective else minimum
    except (ValueError,TypeError):
        raise sv.ApiError('Укажите дату прекращения будущих занятий')
    if cutoff<minimum:
        raise sv.ApiError('Отмена возможна только на будущий период: не раньше завтрашнего дня')
    if sub['kind']=='single':
        raise sv.ApiError('Для разового занятия нужен отдельный расчёт; отмена месячного абонемента недоступна')
    saved=json.loads(sub['lesson_schedule'] or '[]')
    if not saved:
        saved=json.loads(sub['program_selection'] or '{}').get('contract',{}).get('components') or []
    if not saved:
        e=con.execute('SELECT * FROM enrollments WHERE id=?',(sub['enrollment_id'],)).fetchone()
        if not e:
            raise sv.ApiError('Не найдены проданные даты. Отмена заблокирована до сверки периода')
        g=con.execute('SELECT * FROM groups WHERE id=?',(e['group_id'],)).fetchone()
        if not g or g['program_id']:
            raise sv.ApiError('Нужно сверить исторические даты Арктики и ОФП перед отменой')
        ms,me=logic.month_bounds(sub['month'])
        closed=closures.closed_dates(con,g['id'],ms,me)
        exclusions=set(json.loads(sub['excluded_dates'] or '[]'))
        days=[d.isoformat() for d in schedule.month_dates(con,g,e['weekdays'],sub['month'])
              if d.isoformat() not in closed|exclusions and not logic.in_vacation(d,sv.vacations_of(con,sub['student_id']))
              and (not e['start_date'] or d.isoformat()>=e['start_date'])
              and (not e['end_date'] or d.isoformat()<e['end_date'])]
        saved=[{'group_id':g['id'],'rate':1,'dates':days}]
    entries=[]
    try:
        for component in saved:
            rate=int(component['rate'])
            gid=int(component['group_id']) if component.get('group_id') else None
            if rate<=0 and component['dates']:
                raise ValueError()
            for day in component['dates']:
                if not gid or date.fromisoformat(day).isoformat()!=day or day[:7]!=sub['month']:
                    raise ValueError()
                entries.append((gid,day,rate))
    except (ValueError,KeyError,TypeError):
        raise sv.ApiError('Не удалось проверить проданные даты и стоимость занятий; требуется сверка')
    if len(entries)!=sub['lessons'] or len({(g,d) for g,d,_ in entries})!=len(entries):
        raise sv.ApiError('Проданные даты не совпадают с числом занятий. Отмена заблокирована до сверки')
    future=[x for x in entries if x[1]>=cutoff.isoformat()]
    if not future:
        raise sv.ApiError('В этом абонементе нет занятий в будущем периоде. Возврат и перенос его стоимости недоступны')
    for gid,day,_ in future:
        if con.execute('SELECT 1 FROM attendance WHERE student_id=? AND group_id=? AND date=?',
                       (sub['student_id'],gid,day)).fetchone():
            raise sv.ApiError('В будущем периоде уже есть отметка посещения. Сначала проверьте журнал; стоимость не освобождена')
    total_weight=sum(r for _,_,r in entries)
    # Округление вниз не позволяет освободить больше фактической стоимости будущего периода.
    credit=sub['price']*sum(r for _,_,r in future)//total_weight if total_weight else 0
    retained=sub['price']-credit
    covered=max(0,money.balance(con,sub['student_id'])['covered'].get(sub['id'],0))
    available=max(0,covered-retained)
    return {'effective_date':cutoff.isoformat(),'retained_amount':retained,'retained_lessons':len(entries)-len(future),
            'future_amount':credit,'future_lessons':len(future),'releasable':available,
            'future_dates':[d for _,d,_ in future]}


def _sub(con, subid):
    row = con.execute('SELECT * FROM subscriptions WHERE id=?', (subid,)).fetchone()
    if not row:
        raise sv.ApiError('Абонемент не найден', 404)
    return row


def _sources(con, sub, amount=None, retain=0):
    """Воспроизводим FIFO баланса. Не угадываем происхождение переносов/старых корректировок."""
    rows = con.execute('SELECT * FROM payments WHERE student_id=? AND cancelled=0 ORDER BY paid_at,id', (sub['student_id'],)).fetchall()
    if any(p['amount'] < 0 for p in rows):
        raise sv.ApiError('Есть переносы или возвраты. Сначала согласуйте с Юлией сверку источника средств; автоматическое определение способа возврата недоступно')
    offset = 0
    for obligation in money.obligations(con,sub['student_id']):
        if obligation['id']==sub['id']:
            break
        offset+=obligation['price']
    offset+=retain
    left = max(0, money.balance(con, sub['student_id'])['covered'].get(sub['id'], 0)) if amount is None else amount
    parts = []
    for p in rows:
        skip = min(offset, p['amount']); offset -= skip
        take = min(left, p['amount'] - skip)
        if take <= 0:
            continue
        if not p['confirmed']:
            raise sv.ApiError('Сначала подтвердите исходную оплату у руководителя')
        link = con.execute("SELECT pk_payment_id FROM pay_links WHERE payment_id=? AND status='paid'", (p['id'],)).fetchone()
        pkid = link[0] if link else ''
        if p['method'] in ('online','paykeeper') and not pkid:
            raise sv.ApiError('У онлайн-оплаты не найден ID исходного платежа PayKeeper. Сначала сверьте платёж с Юлией; возврат другим способом заблокирован')
        if p['method'] not in ('cash','card','sbp','transfer','online','paykeeper','card_branch','sber_qr_branch','tochka_qr_link'):
            raise sv.ApiError('Способ исходной оплаты не подтверждён. Сначала сверьте его с Юлией')
        if pkid and con.execute("SELECT 1 FROM subscription_refunds WHERE pk_payment_id=? AND status='pending'", (pkid,)).fetchone():
            raise sv.ApiError('По этому платежу PayKeeper уже ожидается возврат. Сначала завершите его проверку')
        parts.append({'payment_id': p['id'], 'amount': take, 'method': 'paykeeper' if pkid else p['method'], 'pk_payment_id': pkid})
        left -= take
        if not left:
            break
    if left:
        raise sv.ApiError('Не удалось однозначно определить исходные платежи')
    return parts


def preview(con, subid, effective=None):
    sub = _sub(con, subid)
    paid = max(0, money.balance(con, sub['student_id'])['covered'].get(subid, 0)) if sub['status'] != 'cancelled' else 0
    error, period_error, parts, period = '', '', [], {}
    try:
        if sub['status']!='cancelled':
            period=period_quote(con,sub,effective)
    except sv.ApiError as e:
        period_error=e.message
    if period.get('releasable'):
        try:
            parts = _sources(con, sub,period['releasable'],period['retained_amount'])
        except sv.ApiError as e:
            error = e.message
    return {'subscription_id': subid, 'cancelled': sub['status']=='cancelled', 'paid': paid,
            'sources': parts, 'refund_blocker': error, 'period_blocker':period_error, **period,
            'paykeeper_url': fiscal.cfg(con,'fx_pk_server').rstrip('/')}


def _refunds(con, pkid):
    rows = fiscal.pk_call(con, '/info/refunds/bypaymentid/', {'id': pkid})
    if not isinstance(rows, list) or any(not isinstance(r,dict) or not r.get('id') or str(r.get('payment_id')) != str(pkid) for r in rows):
        raise sv.ApiError('PayKeeper вернул неподтверждённые сведения о возвратах. Деньги не отмечены как возвращённые')
    return rows


def cancel(con, subid, user, data):
    sub = _sub(con, subid)
    previous = con.execute('SELECT id FROM subscription_cancellations WHERE subscription_id=?', (subid,)).fetchone()
    if previous:
        return {'ok': True, 'already_cancelled': True}
    if sub['status'] == 'cancelled':
        raise sv.ApiError('Абонемент уже отменён')
    if data.get('approved_by_yulia') is not True or not str(data.get('approval') or '').strip():
        raise sv.ApiError('ПЕРЕД ЭТИМ ДЕЙСТВИЕМ СОГЛАСОВАТЬ С ЮЛИЕЙ. Подтвердите согласование и укажите дату')
    from datetime import date
    approval = str(data['approval']).strip()
    try:
        if date.fromisoformat(approval).isoformat() != approval:
            raise ValueError()
    except ValueError:
        raise sv.ApiError('Укажите дату согласования в формате ГГГГ-ММ-ДД')
    mode = data.get('mode')
    if mode not in ('balance','refund'):
        raise sv.ApiError('Выберите: оставить средства на балансе или вернуть клиенту')
    reason = str(data.get('reason') or '').strip()
    if not reason:
        raise sv.ApiError('Укажите причину отмены')
    paid = max(0, money.balance(con,sub['student_id'])['covered'].get(subid,0))
    period=period_quote(con,sub,data.get('effective_date'))
    release=period['releasable']
    if 'expected_releasable' in data and data['expected_releasable']!=release:
        raise sv.ApiError('Расчёт изменился. Обновите форму и согласуйте актуальную сумму')
    before_deposit=money.balance(con,sub['student_id'])['deposit']
    parts = _sources(con,sub,release,period['retained_amount']) if mode=='refund' and release else []
    for p in parts:
        if p['method']=='paykeeper':
            try:
                rows = _refunds(con,p['pk_payment_id'])
            except fiscal.FxError:
                raise sv.ApiError('Не удалось проверить исходный платёж PayKeeper. Отмена с возвратом не выполнена')
            # Старые возвраты означают, что локальный остаток требует отдельной сверки.
            if any(r.get('status') in ('started','done') for r in rows):
                raise sv.ApiError('В PayKeeper уже есть возврат по исходному платежу. Сначала сверьте его с Юлией')
            p['baseline'] = json.dumps([str(r['id']) for r in rows])
    sv.cancel_subscription(con,subid,user,False,reason)
    if period['retained_amount']:
        con.execute('INSERT INTO cancelled_service_charges(subscription_id,student_id,month,amount,lessons,created_at,cutoff) VALUES (?,?,?,?,?,?,?)',
                    (subid,sub['student_id'],sub['month'],period['retained_amount'],period['retained_lessons'],sv.now_iso(),period['effective_date']))
    money.reconcile(con,sub['student_id'])
    cid = con.execute('''INSERT INTO subscription_cancellations(subscription_id,student_id,mode,amount,reason,approval,created_at,created_by)
        VALUES (?,?,?,?,?,?,?,?)''', (subid,sub['student_id'],mode,release,reason,str(data['approval']).strip(),sv.now_iso(),user.get('name',''))).lastrowid
    for p in parts:
        reserve = money._row(con,sub['student_id'],-p['amount'],'refund_pending',user,
            f"Зарезервировано для возврата по абонементу #{subid}; деньги ещё не возвращены")
        con.execute('''INSERT INTO subscription_refunds(cancellation_id,payment_id,reserve_id,amount,method,pk_payment_id,baseline)
            VALUES (?,?,?,?,?,?,?)''',(cid,p['payment_id'],reserve,p['amount'],p['method'],p['pk_payment_id'],p.get('baseline','[]')))
    after_deposit=money.balance(con,sub['student_id'])['deposit']
    available=max(0,min(release,after_deposit-before_deposit)) if mode=='balance' else 0
    applied=max(0,release-available) if mode=='balance' else 0
    con.execute("""UPDATE subscription_cancellations SET effective_date=?,retained_amount=?,retained_lessons=?,
        future_amount=?,period_verified=1,balance_available=?,applied_to_invoices=?,paid_before=? WHERE id=?""",
        (period['effective_date'],period['retained_amount'],period['retained_lessons'],period['future_amount'],available,applied,paid,cid))
    sv.log(con,sub['student_id'],f"Удержана стоимость периода до {period['effective_date']}: {period['retained_amount']} ₽; "
        f"освобождена оплата будущего периода: {release} ₽; свободно после отмены: {after_deposit} ₽; зачтено в другие счета: {applied} ₽",user,'payment')
    sv.log(con,sub['student_id'],f"Отмена абонемента #{subid}: {reason}. Согласовано с Юлией: {data['approval']}. "
        + ('Средства оставлены на балансе' if mode=='balance' else 'Ожидается фактический возврат; средства зарезервированы'),user,'payment')
    # При отмене последнего абонемента обновляем срок проверки продления.
    from . import tasks
    if tasks.schedule_renewal(con,sub['student_id']) is None:
        con.execute("UPDATE tasks SET status='cancelled',done_at=?,result='Все месячные абонементы отменены' WHERE auto_key=? AND status='open'",
                    (sv.now_iso(),f"subscription-renew:{sub['student_id']}"))
    return {'ok':True,'amount':release,'balance_available':available,'applied_to_invoices':applied}


def listing(con,sid):
    rows = [dict(r) for r in con.execute('''SELECT r.*, c.subscription_id,c.approval,c.reason FROM subscription_refunds r
        JOIN subscription_cancellations c ON c.id=r.cancellation_id WHERE c.student_id=? ORDER BY r.id DESC''',(sid,))]
    from urllib.parse import urlparse
    url=fiscal.cfg(con,'fx_pk_server').rstrip('/')
    parsed=urlparse(url)
    for row in rows:
        row['paykeeper_url']=url if parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password else ''
    return rows


def confirm(con,rid,user,data):
    row=con.execute('''SELECT r.*,c.student_id,c.subscription_id,c.period_verified FROM subscription_refunds r
        JOIN subscription_cancellations c ON c.id=r.cancellation_id WHERE r.id=?''',(rid,)).fetchone()
    if not row:
        raise sv.ApiError('Возврат не найден',404)
    if not row['period_verified']:
        raise sv.ApiError('Старая отмена не проверена по будущему периоду. Сначала сверьте расчёт с Юлией; подтверждение всей суммы заблокировано')
    if row['status']=='done':
        return {'ok':True,'status':'done'}
    proof=''
    provider_refund_id=row['provider_refund_id']
    provider_refund_number=row['provider_refund_number']
    if row['status']=='receipt_pending':
        return _check_receipt(con,row,user,data)
    if row['method']=='paykeeper':
        try:
            rows=_refunds(con,row['pk_payment_id'])
        except fiscal.FxError:
            raise sv.ApiError('PayKeeper недоступен. Возврат ещё не подтверждён; повторная выплата не выполнялась')
        from decimal import Decimal, InvalidOperation
        baseline=set(json.loads(row['baseline']))
        matches=[]
        for r in rows:
            try:
                amount=Decimal(str(r.get('amount')))
            except InvalidOperation:
                continue
            if str(r['id']) not in baseline and amount==row['amount'] and r.get('status')=='done':
                matches.append(r)
        if len(matches)!=1:
            return {'ok':True,'status':'pending','message':'PayKeeper пока не подтвердил единственный успешный возврат согласованной суммы. Проверьте кабинет; повторно деньги не возвращайте'}
        provider_refund_id=str(matches[0]['id'])
        provider_refund_number=str(matches[0].get('refund_number') or '')
        proof=f"PayKeeper: платёж {row['pk_payment_id']}, возврат {matches[0]['id']}"
    else:
        if data.get('actually_returned') is not True or not str(data.get('proof') or '').strip():
            raise sv.ApiError('Подтвердите фактическую выдачу денег исходным способом и укажите номер документа/операции')
        proof=str(data['proof']).strip()
    con.execute("UPDATE subscription_refunds SET status='receipt_pending',proof=?,provider_refund_id=?,provider_refund_number=?,confirmed_at=?,confirmed_by=? WHERE id=?",
                (proof,provider_refund_id,provider_refund_number,sv.now_iso(),user.get('name',''),rid))
    con.execute("UPDATE payments SET method='refund',comment=?,paid_at=? WHERE id=?",
                (f"Возврат по абонементу #{row['subscription_id']}: {proof}",sv.now_iso(),row['reserve_id']))
    sv.log(con,row['student_id'],f"Подтверждён возврат {row['amount']} ₽: {proof}. Проверьте чек возврата",user,'payment')
    updated=dict(row);updated.update(proof=proof,provider_refund_id=provider_refund_id,provider_refund_number=provider_refund_number,status='receipt_pending')
    return _check_receipt(con,updated,user,data)



def _check_receipt(con,row,user,data=None):
    from decimal import Decimal, InvalidOperation
    receipt_id=''
    if row['method']=='paykeeper':
        try:
            response=fiscal.pk_call(con,'/info/receipts/bypaymentid/',{'payment_id':row['pk_payment_id']})
            receipts=response.get('receipts',[]) if isinstance(response,dict) else response
            if not isinstance(receipts,list):
                receipts=[]
            for r in receipts:
                if not isinstance(r,dict):
                    continue
                try:
                    total=sum(Decimal(str(r.get(k) or '0')) for k in ('sum_cash','sum_cashless','sum_advance','sum_credit','sum_counter_payment'))
                except InvalidOperation:
                    continue
                if (str(r.get('payment_id'))==row['pk_payment_id'] and row['provider_refund_number'] and str(r.get('refund_id'))==row['provider_refund_number']
                        and r.get('type')=='refund' and r.get('status')=='success' and total==row['amount']
                        and all(r.get(k) for k in ('id','fn','fnd','fpd'))):
                    receipt_id=str(r['id'])
                    break
        except Exception:
            # Возврат денег уже подтверждён. Ошибка проверки кассы не должна откатывать этот факт.
            pass
    elif data:
        fields=[str(data.get(k) or '').strip() for k in ('receipt_fn','receipt_fd','receipt_fp')]
        if all(x.isdigit() and int(x)>0 for x in fields):
            receipt_id='Внесён вручную: ФН '+fields[0]+', ФД '+fields[1]+', ФП '+fields[2]
    if not receipt_id:
        return {'ok':True,'status':'receipt_pending','message':'Деньги возвращены. Фискальный чек возврата ещё не подтверждён. Не выполняйте возврат повторно; проверьте кассу'}
    con.execute("UPDATE subscription_refunds SET status='done',receipt_id=? WHERE id=?",(receipt_id,row['id']))
    sv.log(con,row['student_id'],f"Чек возврата по абонементу #{row['subscription_id']}: {receipt_id}",user,'payment')
    return {'ok':True,'status':'done','receipt_id':receipt_id}


def cancellations(con,sid):
    return [dict(r) for r in con.execute('SELECT * FROM subscription_cancellations WHERE student_id=? ORDER BY id DESC',(sid,))]
