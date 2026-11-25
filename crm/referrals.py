"""Non-cash referral awards and their recognized use."""
from . import balancecredit

AMOUNT = 3000
LABEL = "Бонус за рекомендацию"
LEGACY_LABEL = "Бонус 3 000 ₽ за рекомендацию "


def is_bonus(fund):
    return fund['source_kind'] == 'referral_bonus' or (
        fund['source_ref'].startswith('chat-credit:') and fund['label'].startswith(LEGACY_LABEL))


def invoice_amounts(allocation):
    return {subid: sum(part['amount'] for part in parts if is_bonus(part))
            for subid, parts in allocation['fund_sources'].items()
            if any(is_bonus(part) for part in parts)}


def used(con, month=None):
    """Bonus value actually allocated to fully settled purchases."""
    from . import paymentallocation

    rows = con.execute("""SELECT DISTINCT x.student_id FROM subscriptions x
        JOIN invoice_balance_uses u ON x.id=u.subscription_id
        JOIN balance_credit_funds f ON f.student_id=x.student_id
        WHERE x.status='paid' AND (? IS NULL OR x.month=?)
        AND (f.source_kind='referral_bonus' OR
             (f.source_ref LIKE 'chat-credit:%' AND f.label LIKE ?))""", (month, month, LEGACY_LABEL + '%'))
    result = {}
    for (sid,) in rows:
        allocation = paymentallocation.allocation(con, sid)
        amounts = invoice_amounts(allocation)
        for bill in allocation['obligations']:
            if bill['status'] != 'paid' or (month and bill['month'] != month):
                continue
            amount = amounts.get(bill['id'], 0)
            if amount:
                result[bill['id']] = amount
    return result


def award(con, sid, friend_id, user):
    from . import money, services as sv

    recipient = sv.get_student_row(con, sid)
    friend = sv.get_student_row(con, friend_id)
    if sid == friend_id or recipient['family_id'] == friend['family_id']:
        raise ValueError('Выберите ребёнка из другой семьи')
    ref = f'referral-family:{friend["family_id"]}'
    previous = con.execute("""SELECT student_id FROM balance_credit_funds
        WHERE source_ref=? OR (source_ref LIKE 'chat-credit:%' AND label=?)""",
        (ref, LEGACY_LABEL + friend['name'])).fetchone()
    if previous:
        if previous['student_id'] != sid:
            raise ValueError('Бонус за эту рекомендацию уже начислен другой семье')
        return {'already_awarded': True, 'balance': money.balance(con, sid)}

    from . import paymentallocation

    allocation = paymentallocation.allocation(con, friend_id)
    bonuses = invoice_amounts(allocation)
    paid_months = con.execute("""SELECT id,price FROM subscriptions WHERE student_id=?
        AND status='paid' AND COALESCE(kind,'month')='month' AND price>0 ORDER BY month,id""", (friend_id,))
    bill = next((row for row in paid_months if row['price'] > bonuses.get(row['id'], 0)), None)
    if not bill or allocation['conflicts']:
        raise ValueError('У друга пока нет подтверждённого оплаченного абонемента')
    label = f'{LABEL} · {friend["name"]}'
    balancecredit.add_existing_fund(con, sid, AMOUNT, source_ref=ref, label=label,
        evidence_id=f'subscription:{bill["id"]}', source_kind='referral_bonus', user=user)
    sv.log(con, sid, f'Начислено {AMOUNT:,} ₽ за рекомендацию · {friend["name"]}'.replace(',', ' '), user, 'system')
    return {'already_awarded': False, 'balance': money.balance(con, sid)}
