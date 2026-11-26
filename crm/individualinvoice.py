"""Agreed prices and safe cancellation of unpaid monthly invoices."""
from . import money, services as sv


def terms(price, reason):
    if type(price) is not int or not 0 < price <= 10_000_000:
        raise sv.ApiError('Укажите согласованную сумму в целых рублях')
    if not isinstance(reason, str) or not 5 <= len(reason.strip()) <= 1000:
        raise sv.ApiError('Укажите причину и с кем согласовали условия')
    return reason.strip()


def quote(con, sid, q, price, reason, use_balance):
    reason = terms(price, reason)
    return dict(q, total=price, late_fee=0, discount=None,
                individual_reason=reason, standard_total=q['total'],
                balance_preview=sv._invoice_balance_preview(con, sid, price, use_balance))


def save(con, subid, sid, old_price, price, reason, user):
    con.execute("""UPDATE subscriptions SET price=?, corrected=1, late_fee=0,
        discount_name='', discount_amount=0, calculation_reason=? WHERE id=?""",
        (price, 'Индивидуальные условия: ' + reason, subid))
    con.execute("""INSERT INTO price_corrections(subscription_id,student_id,old_price,new_price,comment,user_name,created_at)
        VALUES (?,?,?,?,?,?,?)""", (subid, sid, old_price, price, reason, user.get('name',''), sv.now_iso()))
    sv.log(con, sid, f'Индивидуальные условия для счёта №{subid}. Сумма {price} ₽. {reason}', user, 'payment')


def unpaid(con, subid):
    sub = con.execute("SELECT * FROM subscriptions WHERE id=? AND kind='month'", (subid,)).fetchone()
    if not sub or sub['status'] != 'invoiced':
        raise sv.ApiError('Действие доступно для неоплаченного месячного счёта', 409)
    balance = money.balance(con, sub['student_id'])
    if balance['allocation_conflicts'] or balance['covered'].get(subid) or con.execute("""SELECT 1
        FROM payments p LEFT JOIN invoice_payment_targets t ON t.payment_id=p.id
        WHERE p.cancelled=0 AND (p.subscription_id=? OR t.subscription_id=?)""", (subid, subid)).fetchone():
        raise sv.ApiError('По счёту есть оплата или зачёт. Откройте расчёт по абонементу для сверки', 409)
    if con.execute("""SELECT 1 FROM pay_links WHERE
        (subscription_id=? OR id IN (SELECT link_id FROM pay_link_subscriptions WHERE subscription_id=?))
        AND (status='paid' OR invoice_id!='' OR invoice_url!=''
          OR checkout_state IN ('creating','uncertain') OR (status='open' AND COALESCE(receipt_token,'')=''))""",
        (subid, subid)).fetchone():
        raise sv.ApiError('По ссылке уже мог открыться счёт PayKeeper. Сначала проверьте и закройте его в PayKeeper', 409)
    return sub


def expire_links(con, subid):
    con.execute("""UPDATE pay_links SET status='expired' WHERE status='open' AND
        (subscription_id=? OR id IN (SELECT link_id FROM pay_link_subscriptions WHERE subscription_id=?))""",
        (subid, subid))


def change(con, subid, price, reason, user):
    from . import tasks
    reason = terms(price, reason)
    sub = unpaid(con, subid)
    if sub['corrected'] and sub['price'] == price and sub['calculation_reason'] == 'Индивидуальные условия: ' + reason:
        return {'ok': True}
    save(con, subid, sub['student_id'], sub['price'], price, reason, user)
    expire_links(con, subid)
    money.reconcile(con, sub['student_id'])
    tasks.sync_subscription_payment_tasks(con, sub['student_id'])
    return {'ok': True}


def cancel(con, subid, reason, user):
    from . import tasks
    if not isinstance(reason, str) or not 5 <= len(reason.strip()) <= 1000:
        raise sv.ApiError('Укажите причину отмены счёта')
    old = con.execute("SELECT status,calculation_reason FROM subscriptions WHERE id=?", (subid,)).fetchone()
    note = 'Неоплаченный счёт отменён: ' + reason.strip()
    if old and old['status'] == 'cancelled' and old['calculation_reason'] == note:
        return {'ok': True}
    sub = unpaid(con, subid)
    sv.cancel_subscription(con, subid, user, False, reason)
    expire_links(con, subid)
    con.execute('UPDATE subscriptions SET calculation_reason=? WHERE id=?', (note, subid))
    sv.log(con, sub['student_id'], f'Счёт №{subid} отменён. {reason.strip()}', user, 'payment')
    tasks.sync_subscription_payment_tasks(con, sub['student_id'])
    return {'ok': True}
