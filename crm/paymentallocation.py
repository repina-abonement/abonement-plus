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
    for statement in SCHEMA.split(';'):
        if statement.strip():
            con.execute(statement)


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
    from . import money, balancecredit
    obligations = money.obligations(con, sid)
    all_payments = {p['id']: dict(p) for p in con.execute(
        'SELECT * FROM payments WHERE student_id=? AND cancelled=0 ORDER BY paid_at,id', (sid,))}
    paid = sum(p['amount'] for p in all_payments.values())
    meta = balancecredit.context(con, sid)
    reviewed = meta['reviewed']
    quarantined = set()
    if meta['snapshot']:
        for pid, payment in all_payments.items():
            if pid not in reviewed and ((payment['user_name'] == 'ERP'
                    and payment['paid_at'][:10] <= meta['snapshot']['as_of_date'])
                    or payment['user_name'] == 'Система'
                    and payment['comment'] == 'Оплата до реестра платежей'):
                quarantined.add(pid)
    payments = {pid: p for pid, p in all_payments.items() if pid not in reviewed | quarantined}
    effective_paid = sum(p['amount'] for p in payments.values())
    result = {'obligations': obligations, 'covered': {s['id']: 0 for s in obligations},
              'sources': {s['id']: [] for s in obligations}, 'payment_remaining': {},
              'payment_locks': [], 'paid_total': paid, 'deposit': 0,
              'free_deposit': 0, 'restricted_deposit': 0, 'conflicts': list(meta['conflicts']),
              'fund_sources': {s['id']: [] for s in obligations}, 'historical_sources': {},
              'fund_remaining': {}, 'fund_labels': [], 'cash_free_deposit': 0, 'fund_free_deposit': 0,
              'active_snapshot': bool(meta['snapshot']), 'reviewed_payment_ids': sorted(reviewed),
              'balance_hold_payment_ids': sorted(meta.get('holds', set())),
              'quarantined_historical_payment_ids': sorted(quarantined)}
    by_id = {s['id']: s for s in obligations}
    conflicts = result['conflicts']
    for subid, amount in meta['frozen'].items():
        if subid not in by_id or type(amount) is not int or amount < 0 or amount > by_id[subid]['price']:
            conflicts.append('Историческое покрытие покупки расходится со снимком')
            continue
        result['covered'][subid] = amount
        result['historical_sources'][subid] = [{'snapshot_id': meta['snapshot']['id'], 'amount': amount,
            'evidence_id': meta['snapshot']['evidence_id'], 'kind': 'historical_settlement'}]

    def conservative():
        result['covered'] = {s['id']: sum(r['amount'] for r in result['historical_sources'].get(s['id'], []))
                             for s in obligations}
        result['sources'] = {s['id']: [] for s in obligations}
        result['fund_sources'] = {s['id']: [] for s in obligations}
        result['restricted_deposit'] = max(0, effective_paid) + sum(r['amount'] for r in meta['funds'].values())
        return result
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
    bindings = [r for r in bindings if r['payment_id'] not in reviewed | quarantined]
    for link in refunds:
        if ((link['original_payment_id'] in reviewed | quarantined)
                != (link['refund_payment_id'] in reviewed | quarantined)):
            conflicts.append('Возврат пересекает границу исторического снимка; требуется сверка')
    refunds = [r for r in refunds if r['original_payment_id'] not in reviewed | quarantined
               and r['refund_payment_id'] not in reviewed | quarantined]
    positives = {pid: p for pid, p in payments.items() if p['amount'] > 0}
    binding_by_key = {(r['payment_id'], r['subscription_id']): r for r in bindings}
    gross = defaultdict(int)
    refund_by_binding = defaultdict(int)
    refund_by_payment = defaultdict(int)
    debit_totals = defaultdict(int)
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
        return conservative()
    locked = set(gross)
    result['payment_locks'] = sorted(locked)
    remaining = {pid: p['amount'] - refund_by_payment[pid] for pid, p in positives.items()}
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
    # When a booked single is cancelled, its specifically bound ERP receipt
    # backs spendable client credit. Count it once, while preserving the original binding.
    if meta['funds'] and con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='single_cancelled_payment_credits'").fetchone():
        for credit in con.execute("""SELECT c.*,s.status FROM single_cancelled_payment_credits c
                JOIN subscriptions s ON s.id=c.subscription_id WHERE s.student_id=?""",(sid,)):
            fund = meta['funds'].get(credit['fund_id'])
            if not fund:
                continue
            pid, subid = credit['payment_id'], credit['subscription_id']
            binding = binding_by_key.get((pid,subid))
            if (credit['status'] != 'cancelled' or not binding or
                    binding['amount'] - refund_by_binding[(pid,subid)] < fund['amount'] or
                    remaining.get(pid,0) < fund['amount']):
                conflicts.append('Возврат разового на баланс требует сверки с исходной оплатой')
                return conservative()
            remaining[pid] -= fund['amount']
    funds = {fid: row['amount'] for fid, row in meta['funds'].items()}
    for use in meta['uses']:
        subid = use['subscription_id']
        target = subid if subid in by_id else -subid if -subid in by_id else None
        if target is None:
            # The immutable use remains in the audit trail; a cancelled future
            # purchase releases its source parts instead of manufacturing cash.
            continue
        parts = use['source_parts']
        if (type(use['amount']) is not int or use['amount'] < 0
                or any(not isinstance(p, dict) or type(p.get('amount')) is not int
                       or p['amount'] <= 0 or ('payment_id' in p) == ('fund_id' in p) for p in parts)
                or sum(p['amount'] for p in parts) != use['amount']):
            conflicts.append('Состав подтверждённого зачёта баланса изменён')
            continue
        if target < 0 and any('fund_id' in part for part in parts):
            conflicts.append('Удержанная стоимость с зачётом существующих средств требует отдельной сверки источника')
            continue
        take_left = min(use['amount'], max(0, by_id[target]['price'] - result['covered'][target]))
        if target > 0 and take_left != use['amount']:
            conflicts.append('Зачтённый баланс превышает текущую стоимость покупки')
            continue
        for part in parts:
            amount = min(part['amount'], take_left)
            take_left -= amount
            if not amount:
                continue
            if 'payment_id' in part:
                pid = part['payment_id']
                if pid not in remaining or pid in locked or remaining[pid] < amount:
                    conflicts.append('Средства исходной оплаты для зачёта уже недоступны')
                    continue
                remaining[pid] -= amount
                result['sources'][target].append({'payment_id': pid, 'amount': amount})
            else:
                fid = part['fund_id']
                if fid not in funds or funds[fid] < amount:
                    conflicts.append('Существующие средства для зачёта уже недоступны')
                    continue
                funds[fid] -= amount
                fund = meta['funds'][fid]
                result['fund_sources'][target].append({'fund_id': fid, 'amount': amount,
                    'label': fund['label'], 'source_kind': fund['source_kind'], 'source_ref': fund['source_ref'], 'evidence_id': fund['evidence_id']})
            result['covered'][target] += amount

    def direct(pid, subid, amount, strict):
        if subid not in by_id:
            return
        if pid not in remaining or pid in locked:
            conflicts.append('Прямая оплата пересекается с другим подтверждённым зачётом')
            return
        needed = max(0, by_id[subid]['price'] - result['covered'][subid])
        if strict and amount > remaining[pid]:
            conflicts.append('Прямой зачёт превышает остаток оплаты или покупки')
            return
        amount = min(amount, needed, remaining[pid])
        if amount:
            remaining[pid] -= amount
            result['covered'][subid] += amount
            result['sources'][subid].append({'payment_id': pid, 'amount': amount})

    targeted = set()
    targets_by_payment = defaultdict(list)
    for target in meta['direct']:
        pid, subid, amount = target['payment_id'], target['subscription_id'], target['amount']
        targeted.add(pid)
        if pid in reviewed | quarantined:
            continue
        if pid not in all_payments:
            # Cancelling an unspent real payment removes its coverage; the
            # original target row remains as a record of its former purpose.
            old = con.execute('SELECT cancelled,student_id FROM payments WHERE id=?', (pid,)).fetchone()
            if old and old['cancelled'] and old['student_id'] == sid:
                continue
        if type(amount) is not int or amount < 0:
            conflicts.append('Сумма прямого зачёта не подтверждена')
            continue
        target_sub = con.execute('SELECT student_id FROM subscriptions WHERE id=?', (subid,)).fetchone()
        if not target_sub or target_sub['student_id'] != sid:
            conflicts.append('Прямой зачёт связан с другой карточкой')
            continue
        if pid not in positives:
            conflicts.append('Исходная прямая оплата больше не доступна')
            continue
        targets_by_payment[pid].append((subid, amount))
    # Existing direct receipts keep their coverage when a late checkout
    # callback arrives for an already covered invoice. Receipt insertion order
    # is stable even when a provider reports an older payment timestamp.
    for pid in sorted(positives):
        payment = positives[pid]
        subid = payment.get('subscription_id')
        if pid in targets_by_payment:
            for subid, amount in targets_by_payment[pid]:
                direct(pid, subid, amount, True)
        elif subid in meta['protected'] and pid not in targeted and pid not in locked:
            direct(pid, subid, remaining[pid], False)
    dedicated = targeted | set(meta.get('holds', set())) | {pid for pid, p in positives.items() if p.get('subscription_id') in meta['protected']}
    # Unattributed old negatives retain the old budget rule for ordinary
    # payments. They may consume a released locked balance, never a proved
    # allocation to another purchase.
    cash = [p for pid, p in positives.items() if pid not in locked]
    ordinary = [p for p in cash if p['id'] not in dedicated]
    negatives = -sum(p['amount'] for pid, p in payments.items()
                     if p['amount'] < 0 and pid not in debit_totals)
    ordinary_total = sum(remaining[p['id']] for p in cash)
    budget = max(0, ordinary_total - negatives)
    overflow = max(0, negatives - ordinary_total)
    for pid in reversed(list(positives)):
        if pid in locked and overflow:
            take = min(remaining[pid], overflow)
            remaining[pid] -= take
            overflow -= take
    extended = bool(meta['protected'] or meta['direct'] or meta['uses'] or meta['funds'] or meta.get('holds') or meta['snapshot'])
    if overflow and (locked or extended):
        conflicts.append('Непривязанный возврат затрагивает подтверждённые зачёты; нужна сверка источника')
    for p in cash:
        remaining[p['id']] = min(remaining[p['id']], budget)
        budget -= remaining[p['id']]
    index = 0
    for sub in obligations:
        if sub['id'] in meta['protected'] or sub['id'] in meta['frozen']:
            continue
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
        return conservative()
    result['payment_remaining'] = {pid: 0 for pid, p in all_payments.items()
                                   if p['amount'] > 0 and pid in reviewed | quarantined}
    result['payment_remaining'].update(remaining)
    result['restricted_deposit'] = sum(v for pid, v in remaining.items() if pid in locked)
    free = sum(v for pid, v in remaining.items() if pid not in locked)
    if not locked and not extended:
        # Preserve even the historical negative-deposit behaviour in an
        # unbound account instead of silently changing existing ledgers.
        free = paid - sum(result['covered'].values())
    result['cash_free_deposit'] = free
    result['fund_free_deposit'] = sum(funds.values())
    result['fund_remaining'] = funds
    result['fund_labels'] = list(dict.fromkeys(meta['funds'][fid]['label'] for fid, value in funds.items() if value > 0))
    result['deposit'] = result['free_deposit'] = free + result['fund_free_deposit']
    result['accounting_funds_total'] = effective_paid + sum(r['amount'] for r in meta['funds'].values()) + sum(meta['frozen'].values())
    return result
