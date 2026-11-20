"""Read-only payment allocation with explicit, audited ERP source bindings.

Unbound accounts retain the chronological ledger rules. An explicitly bound
payment is reserved for its evidenced purchases; its unused funds never pay an
unrelated invoice merely because that invoice comes first in the calendar.
Amounts use the existing money ledger's integer roubles, not fiscal kopecks.
"""
from __future__ import annotations

from collections import defaultdict


SCHEMA = """
CREATE TABLE IF NOT EXISTS erp_payment_allocations (
    payment_id INTEGER NOT NULL REFERENCES payments(id),
    subscription_id INTEGER NOT NULL REFERENCES subscriptions(id),
    amount INTEGER NOT NULL CHECK(amount>0),
    source_namespace TEXT NOT NULL,
    source_invoice_id TEXT NOT NULL,
    source_operation_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    PRIMARY KEY(payment_id,subscription_id)
);
CREATE TABLE IF NOT EXISTS erp_refund_allocations (
    refund_payment_id INTEGER NOT NULL REFERENCES payments(id),
    original_payment_id INTEGER NOT NULL REFERENCES payments(id),
    subscription_id INTEGER NOT NULL REFERENCES subscriptions(id),
    amount INTEGER NOT NULL CHECK(amount>0),
    PRIMARY KEY(refund_payment_id,original_payment_id,subscription_id)
);
"""


def init(con):
    con.executescript(SCHEMA)


def _amount(value):
    if type(value) is not int or value <= 0:
        raise ValueError('Укажите точную положительную сумму в рублях')
    return value


def bind_payment(con, payment_id, subscription_id, amount, *, source_namespace,
                 source_invoice_id, source_operation_id, evidence_id):
    """Insert one proved gross allocation; exact replays do not change it."""
    amount = _amount(amount)
    evidence = tuple('' if v is None else str(v).strip() for v in
                     (source_namespace, source_invoice_id, source_operation_id, evidence_id))
    if any(not v for v in evidence):
        raise ValueError('Для зачёта нужны идентификаторы источника и подтверждение связи')
    payment = con.execute('SELECT * FROM payments WHERE id=?', (payment_id,)).fetchone()
    sub = con.execute('SELECT * FROM subscriptions WHERE id=?', (subscription_id,)).fetchone()
    if (not payment or payment['cancelled'] or payment['amount'] <= 0 or not sub
            or payment['student_id'] != sub['student_id']):
        raise ValueError('Исходная оплата и покупка должны относиться к одной карточке')
    values = (payment_id, subscription_id, amount, *evidence)
    old = con.execute('SELECT * FROM erp_payment_allocations WHERE payment_id=? AND subscription_id=?',
                      (payment_id, subscription_id)).fetchone()
    if old:
        if tuple(old[k] for k in ('payment_id', 'subscription_id', 'amount', 'source_namespace',
                                  'source_invoice_id', 'source_operation_id', 'evidence_id')) != values:
            raise ValueError('У исходной оплаты уже сохранён другой зачёт')
        return
    allocated = con.execute('SELECT COALESCE(SUM(amount),0) FROM erp_payment_allocations WHERE payment_id=?',
                            (payment_id,)).fetchone()[0]
    if allocated + amount > payment['amount']:
        raise ValueError('Зачёты превышают сумму исходной оплаты')
    con.execute('INSERT INTO erp_payment_allocations VALUES (?,?,?,?,?,?,?)', values)


def register_refund(con, refund_payment_id, original_payment_id, subscription_id, amount):
    """Link part of an existing historical debit, without creating money rows.

    A multi-source debit is inserted in parts. The read-only allocator requires
    the complete set to equal the debit before an application can be accepted.
    """
    amount = _amount(amount)
    refund = con.execute('SELECT * FROM payments WHERE id=?', (refund_payment_id,)).fetchone()
    payment = con.execute('SELECT * FROM payments WHERE id=?', (original_payment_id,)).fetchone()
    binding = con.execute('SELECT * FROM erp_payment_allocations WHERE payment_id=? AND subscription_id=?',
                          (original_payment_id, subscription_id)).fetchone()
    if (not refund or refund['cancelled'] or refund['amount'] >= 0 or refund['method'] != 'refund' or not payment
            or payment['cancelled'] or payment['amount'] <= 0 or not binding
            or refund['student_id'] != payment['student_id']):
        raise ValueError('Возврат должен ссылаться на подтверждённый зачёт исходной оплаты')
    key = (refund_payment_id, original_payment_id, subscription_id)
    old = con.execute('SELECT amount FROM erp_refund_allocations WHERE refund_payment_id=? AND original_payment_id=? AND subscription_id=?', key).fetchone()
    if old:
        if old['amount'] != amount:
            raise ValueError('У возврата уже сохранено другое распределение')
        return
    total = con.execute('SELECT COALESCE(SUM(amount),0) FROM erp_refund_allocations WHERE refund_payment_id=?',
                        (refund_payment_id,)).fetchone()[0]
    used = con.execute('SELECT COALESCE(SUM(amount),0) FROM erp_refund_allocations WHERE original_payment_id=? AND subscription_id=?',
                       (original_payment_id, subscription_id)).fetchone()[0]
    if total + amount > -refund['amount'] or used + amount > binding['amount']:
        raise ValueError('Сумма возвратов превышает подтверждённый источник средств')
    con.execute('INSERT INTO erp_refund_allocations VALUES (?,?,?,?)', (*key, amount))


def allocation(con, sid):
    """Return one common allocation for balances, statuses and explanations.

    Never writes or calls providers. Invalid explicit links return conservative
    coverage and conflicts so a card can still open; mutating callers must stop.
    """
    from . import money
    obligations = money.obligations(con, sid)
    payments = {p['id']: dict(p) for p in con.execute(
        'SELECT * FROM payments WHERE student_id=? AND cancelled=0 ORDER BY paid_at,id', (sid,))}
    paid = sum(p['amount'] for p in payments.values())
    result = {'obligations': obligations, 'covered': {s['id']: 0 for s in obligations},
              'sources': {s['id']: [] for s in obligations}, 'payment_remaining': {},
              'payment_locks': [], 'paid_total': paid, 'deposit': 0,
              'free_deposit': 0, 'restricted_deposit': 0, 'conflicts': []}
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('erp_payment_allocations','erp_refund_allocations')")}
    bindings = []
    refunds = []
    if 'erp_payment_allocations' in tables:
        bindings = [dict(r) for r in con.execute('''SELECT a.* FROM erp_payment_allocations a
            LEFT JOIN payments p ON p.id=a.payment_id LEFT JOIN subscriptions s ON s.id=a.subscription_id
            WHERE p.student_id=? OR s.student_id=? ORDER BY a.payment_id,a.subscription_id''', (sid, sid))]
    if 'erp_refund_allocations' in tables:
        refunds = [dict(r) for r in con.execute('''SELECT a.* FROM erp_refund_allocations a
            LEFT JOIN payments p ON p.id=a.original_payment_id LEFT JOIN payments r ON r.id=a.refund_payment_id
            LEFT JOIN subscriptions s ON s.id=a.subscription_id
            WHERE p.student_id=? OR r.student_id=? OR s.student_id=?
            ORDER BY a.refund_payment_id,a.original_payment_id,a.subscription_id''', (sid, sid, sid))]
    positives = {pid: p for pid, p in payments.items() if p['amount'] > 0}
    binding_by_key = {(r['payment_id'], r['subscription_id']): r for r in bindings}
    gross = defaultdict(int)
    refund_by_binding = defaultdict(int)
    refund_by_payment = defaultdict(int)
    debit_totals = defaultdict(int)
    conflicts = result['conflicts']
    for binding in bindings:
        p = positives.get(binding['payment_id'])
        sub = con.execute('SELECT student_id FROM subscriptions WHERE id=?', (binding['subscription_id'],)).fetchone()
        if (not p or not sub or sub['student_id'] != sid or type(binding['amount']) is not int
                or binding['amount'] <= 0 or any(not str(binding[k]).strip() for k in
                ('source_namespace', 'source_invoice_id', 'source_operation_id', 'evidence_id'))):
            conflicts.append('Исходная оплата или подтверждение зачёта изменены; требуется сверка')
            continue
        gross[p['id']] += binding['amount']
    for pid, amount in gross.items():
        if amount > positives[pid]['amount']:
            conflicts.append('Зачёты превышают сумму исходной оплаты')
    for link in refunds:
        p = positives.get(link['original_payment_id'])
        debit = payments.get(link['refund_payment_id'])
        key = (link['original_payment_id'], link['subscription_id'])
        if (not p or not debit or debit['amount'] >= 0 or debit['method'] != 'refund' or key not in binding_by_key
                or type(link['amount']) is not int or link['amount'] <= 0):
            conflicts.append('Возврат не связан с действующей исходной оплатой и покупкой')
            continue
        debit_totals[debit['id']] += link['amount']
        refund_by_payment[p['id']] += link['amount']
        refund_by_binding[key] += link['amount']
    for pid, amount in debit_totals.items():
        if amount != -payments[pid]['amount']:
            conflicts.append('Состав возврата не равен сумме исторической операции')
    for key, amount in refund_by_binding.items():
        if amount > binding_by_key[key]['amount']:
            conflicts.append('Возвраты превышают зачёт исходной оплаты по покупке')
    if conflicts:
        result['restricted_deposit'] = max(0, paid)
        return result
    locked = set(gross)
    result['payment_locks'] = sorted(locked)
    remaining = {pid: p['amount'] - refund_by_payment[pid] for pid, p in positives.items()}
    by_id = {s['id']: s for s in obligations}
    for binding in bindings:
        pid, subid = binding['payment_id'], binding['subscription_id']
        amount = binding['amount'] - refund_by_binding[(pid, subid)]
        target = subid if subid in by_id else -subid if -subid in by_id else None
        # An explicitly cancelled purchase can release a locked balance, but
        # that balance cannot silently become payment for a different invoice.
        if target is None or not amount:
            continue
        if target < 0:
            # Cancelling a bound purchase retains only the evidenced past
            # service cost. The released part remains locked until a linked
            # refund (or an independently evidenced allocation) consumes it.
            amount = min(amount, max(0, by_id[target]['price'] - result['covered'][target]))
        if result['covered'][target] + amount > by_id[target]['price']:
            conflicts.append('Зачёты превышают текущую стоимость покупки; проверьте возврат и сохранённую стоимость')
            continue
        result['covered'][target] += amount
        result['sources'][target].append({'payment_id': pid, 'amount': amount})
        remaining[pid] -= amount
    # Unattributed old negatives retain the old budget rule for ordinary
    # payments. They may consume a released locked balance, never a proved
    # allocation to another purchase.
    ordinary = [p for pid, p in positives.items() if pid not in locked]
    negatives = -sum(p['amount'] for pid, p in payments.items()
                     if p['amount'] < 0 and pid not in debit_totals)
    ordinary_total = sum(p['amount'] for p in ordinary)
    budget = max(0, ordinary_total - negatives)
    overflow = max(0, negatives - ordinary_total)
    for pid in reversed(list(positives)):
        if pid in locked and overflow:
            take = min(remaining[pid], overflow)
            remaining[pid] -= take
            overflow -= take
    if overflow and locked:
        conflicts.append('Непривязанный возврат затрагивает подтверждённые зачёты; нужна сверка источника')
    for p in ordinary:
        remaining[p['id']] = min(p['amount'], budget)
        budget -= remaining[p['id']]
    index = 0
    for sub in obligations:
        need = sub['price'] - result['covered'][sub['id']]
        while need > 0 and index < len(ordinary):
            pid = ordinary[index]['id']
            take = min(need, remaining[pid])
            if take:
                result['covered'][sub['id']] += take
                result['sources'][sub['id']].append({'payment_id': pid, 'amount': take})
                remaining[pid] -= take
                need -= take
            if not remaining[pid]:
                index += 1
    if conflicts:
        result['covered'] = {s['id']: 0 for s in obligations}
        result['sources'] = {s['id']: [] for s in obligations}
        result['restricted_deposit'] = max(0, paid)
        return result
    result['payment_remaining'] = remaining
    result['restricted_deposit'] = sum(v for pid, v in remaining.items() if pid in locked)
    free = sum(v for pid, v in remaining.items() if pid not in locked)
    if not locked:
        # Preserve even the historical negative-deposit behaviour in an
        # unbound account instead of silently changing existing ledgers.
        free = paid - sum(result['covered'].values())
    result['deposit'] = result['free_deposit'] = free
    return result
