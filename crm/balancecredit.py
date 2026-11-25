"""Explicit use of existing balance; no receipts, payouts or new cash rows.

New purchases do not spend old money merely by being created. Source parts are
reserved only after a confirmed preview. Reviewed historical account snapshots
are an accounting boundary, never a synthetic incoming payment.
"""
from datetime import date, datetime
import hashlib
import json


SCHEMA = '''
CREATE TABLE IF NOT EXISTS invoice_funding_policies (
 subscription_id INTEGER PRIMARY KEY REFERENCES subscriptions(id),
 policy TEXT NOT NULL DEFAULT 'explicit',created_at TEXT NOT NULL,created_by TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS invoice_payment_targets (
 payment_id INTEGER NOT NULL REFERENCES payments(id),subscription_id INTEGER NOT NULL REFERENCES subscriptions(id),
 amount INTEGER NOT NULL CHECK(amount>=0),PRIMARY KEY(payment_id,subscription_id));
CREATE TABLE IF NOT EXISTS invoice_balance_uses (
 subscription_id INTEGER PRIMARY KEY REFERENCES subscriptions(id),request_id TEXT NOT NULL UNIQUE,
 payload_fingerprint TEXT NOT NULL,amount INTEGER NOT NULL CHECK(amount>=0),source_parts TEXT NOT NULL,
 result TEXT NOT NULL,created_at TEXT NOT NULL,created_by TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS balance_account_snapshots (
 id INTEGER PRIMARY KEY,student_id INTEGER NOT NULL REFERENCES students(id),amount INTEGER NOT NULL CHECK(amount>=0),
 as_of_date TEXT NOT NULL,cutoff_date TEXT NOT NULL,reviewed_payment_ids TEXT NOT NULL,
 historical_coverage TEXT NOT NULL,evidence_id TEXT NOT NULL UNIQUE,payload_fingerprint TEXT NOT NULL,
 created_at TEXT NOT NULL,created_by TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS balance_credit_funds (
 id INTEGER PRIMARY KEY,student_id INTEGER NOT NULL REFERENCES students(id),amount INTEGER NOT NULL CHECK(amount>0),
 source_kind TEXT NOT NULL,source_ref TEXT NOT NULL UNIQUE,label TEXT NOT NULL,evidence_id TEXT NOT NULL,
 snapshot_id INTEGER REFERENCES balance_account_snapshots(id),created_at TEXT NOT NULL,created_by TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS balance_payment_holds (
 payment_id INTEGER PRIMARY KEY REFERENCES payments(id),source_ref TEXT NOT NULL UNIQUE,
 label TEXT NOT NULL,created_at TEXT NOT NULL,created_by TEXT NOT NULL DEFAULT '');
'''


def init(con):
    # executescript would implicitly commit an enclosing financial transaction.
    for statement in SCHEMA.split(';'):
        if statement.strip():
            con.execute(statement)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _now():
    return datetime.now().isoformat(timespec='seconds')


def _amount(value, zero=False):
    if type(value) is not int or value < (0 if zero else 1):
        raise ValueError('Нужна точная сумма в целых рублях')
    return value


def _tables(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def is_protected(con, subid):
    return bool('invoice_funding_policies' in _tables(con) and con.execute(
        "SELECT 1 FROM invoice_funding_policies WHERE subscription_id=? AND policy='explicit'", (subid,)).fetchone())


def protect_invoice(con, subid, user=None):
    sub = con.execute('SELECT * FROM subscriptions WHERE id=?', (subid,)).fetchone()
    if not sub or sub['status'] == 'cancelled':
        raise ValueError('Действующая покупка не найдена')
    old = con.execute('SELECT policy FROM invoice_funding_policies WHERE subscription_id=?', (subid,)).fetchone()
    if old and old['policy'] != 'explicit':
        raise ValueError('Для покупки уже сохранён другой порядок зачёта')
    if not old:
        con.execute('INSERT INTO invoice_funding_policies VALUES (?,?,?,?)',
                    (subid, 'explicit', _now(), (user or {}).get('name', '')))


enforce_policy = protect_invoice


def target_payment(con, pid, subid, amount, *, allow_cancelled=False, allow_zero=False):
    amount = _amount(amount, zero=allow_zero is True)
    payment = con.execute('SELECT * FROM payments WHERE id=?', (pid,)).fetchone()
    sub = con.execute('SELECT * FROM subscriptions WHERE id=?', (subid,)).fetchone()
    if (not payment or payment['cancelled'] or payment['amount'] <= 0 or not sub
            or sub['status'] == 'cancelled' and allow_cancelled is not True
            or payment['student_id'] != sub['student_id']):
        raise ValueError('Оплата и покупка должны относиться к одной карточке')
    old = con.execute('SELECT amount FROM invoice_payment_targets WHERE payment_id=? AND subscription_id=?', (pid, subid)).fetchone()
    if old:
        if old['amount'] != amount:
            raise ValueError('Для оплаты уже сохранён другой зачёт')
        return
    total = con.execute('SELECT COALESCE(SUM(amount),0) FROM invoice_payment_targets WHERE payment_id=?', (pid,)).fetchone()[0]
    if total + amount > payment['amount']:
        raise ValueError('Зачёты превышают сумму оплаты')
    con.execute('INSERT INTO invoice_payment_targets VALUES (?,?,?)', (pid, subid, amount))


def context(con, sid):
    """Read accounting metadata only; usable before these optional tables exist."""
    tables = _tables(con)
    result = {'protected': set(), 'direct': [], 'uses': [], 'funds': {}, 'holds': set(), 'snapshot': None,
              'reviewed': set(), 'frozen': {}, 'conflicts': []}
    if 'invoice_funding_policies' in tables:
        result['protected'] = {r[0] for r in con.execute('''SELECT f.subscription_id FROM invoice_funding_policies f
            JOIN subscriptions s ON s.id=f.subscription_id WHERE s.student_id=? AND f.policy='explicit' ''', (sid,))}
    if 'invoice_payment_targets' in tables:
        result['direct'] = [dict(r) for r in con.execute('''SELECT f.* FROM invoice_payment_targets f
            LEFT JOIN payments p ON p.id=f.payment_id LEFT JOIN subscriptions s ON s.id=f.subscription_id
            WHERE p.student_id=? OR s.student_id=? ORDER BY f.payment_id,f.subscription_id''', (sid, sid))]
    if 'invoice_balance_uses' in tables:
        for row in con.execute('''SELECT u.* FROM invoice_balance_uses u JOIN subscriptions s ON s.id=u.subscription_id
                                  WHERE s.student_id=? ORDER BY u.created_at,u.subscription_id''', (sid,)):
            item = dict(row)
            try:
                item['source_parts'] = json.loads(item['source_parts'])
                if not isinstance(item['source_parts'], list):
                    raise ValueError()
            except (ValueError, TypeError):
                item['source_parts'] = []
                result['conflicts'].append('Состав зачтённого баланса не читается')
            result['uses'].append(item)
    if 'balance_account_snapshots' in tables:
        row = con.execute('SELECT * FROM balance_account_snapshots WHERE student_id=? ORDER BY id DESC LIMIT 1', (sid,)).fetchone()
        if row:
            result['snapshot'] = dict(row)
            try:
                result['reviewed'] = set(json.loads(row['reviewed_payment_ids']))
                result['frozen'] = {int(k): v for k, v in json.loads(row['historical_coverage']).items()}
            except (ValueError, TypeError, AttributeError):
                result['conflicts'].append('Исторический снимок счёта требует сверки')
    if 'balance_credit_funds' in tables:
        snapshot_id = result['snapshot']['id'] if result['snapshot'] else None
        result['funds'] = {r['id']: dict(r) for r in con.execute('''SELECT * FROM balance_credit_funds
            WHERE student_id=? AND (snapshot_id IS NULL OR snapshot_id=?) ORDER BY id''', (sid, snapshot_id))}
    if 'balance_payment_holds' in tables:
        result['holds'] = {r['payment_id'] for r in con.execute('''SELECT h.payment_id FROM balance_payment_holds h
            JOIN payments p ON p.id=h.payment_id WHERE p.student_id=? AND p.cancelled=0 ORDER BY h.payment_id''', (sid,))}
    return result


def snapshot_state(con, sid):
    """Stable, read-only state used by audit/backup fingerprints."""
    tables = _tables(con)
    queries = {
        'policies': ('invoice_funding_policies', '''SELECT f.* FROM invoice_funding_policies f
            JOIN subscriptions s ON s.id=f.subscription_id WHERE s.student_id=? ORDER BY f.subscription_id'''),
        'payment_targets': ('invoice_payment_targets', '''SELECT f.* FROM invoice_payment_targets f
            LEFT JOIN payments p ON p.id=f.payment_id LEFT JOIN subscriptions s ON s.id=f.subscription_id
            WHERE p.student_id=? OR s.student_id=? ORDER BY f.payment_id,f.subscription_id'''),
        'uses': ('invoice_balance_uses', '''SELECT u.* FROM invoice_balance_uses u
            JOIN subscriptions s ON s.id=u.subscription_id WHERE s.student_id=? ORDER BY u.subscription_id'''),
        'snapshots': ('balance_account_snapshots', 'SELECT * FROM balance_account_snapshots WHERE student_id=? ORDER BY id'),
        'funds': ('balance_credit_funds', 'SELECT * FROM balance_credit_funds WHERE student_id=? ORDER BY id'),
        'payment_holds': ('balance_payment_holds', '''SELECT h.* FROM balance_payment_holds h JOIN payments p ON p.id=h.payment_id
            WHERE p.student_id=? ORDER BY h.payment_id''')}
    return {key: [dict(r) for r in con.execute(query, (sid, sid) if key == 'payment_targets' else (sid,))]
            if table in tables else [] for key, (table, query) in queries.items()}


def hold_payment_as_balance(con, payment_id, *, source_ref, label, user=None):
    payment = con.execute('SELECT * FROM payments WHERE id=?', (payment_id,)).fetchone()
    if not payment or payment['cancelled'] or payment['amount'] <= 0:
        raise ValueError('Поступление для баланса не найдено')
    values = (payment_id, str(source_ref).strip(), str(label).strip())
    if not values[1] or not values[2]:
        raise ValueError('Укажите основание пополнения баланса')
    old = con.execute('SELECT * FROM balance_payment_holds WHERE payment_id=? OR source_ref=?',
                      (payment_id, values[1])).fetchall()
    if old:
        if len(old) != 1 or tuple(old[0][k] for k in ('payment_id','source_ref','label')) != values:
            raise ValueError('Пополнение баланса уже сохранено с другими данными')
        return
    con.execute('INSERT INTO balance_payment_holds VALUES (?,?,?,?,?)',
                (*values, _now(), (user or {}).get('name', '')))


def preview(con, sid, invoice_total, use_balance=False, subscription_id=None):
    from . import paymentallocation
    total = _amount(invoice_total, zero=True)
    allocated = paymentallocation.allocation(con, sid)
    already = 0
    covered = 0
    if subscription_id is not None:
        sub = con.execute('SELECT * FROM subscriptions WHERE id=?', (subscription_id,)).fetchone()
        if not sub or sub['student_id'] != sid or sub['status'] == 'cancelled' or sub['price'] != total:
            raise ValueError('Состав покупки изменился; обновите расчёт')
        covered = allocated['covered'].get(subscription_id, 0)
        if not is_protected(con, subscription_id) and sub['status'] == 'paid':
            covered = total
        old = con.execute('SELECT amount FROM invoice_balance_uses WHERE subscription_id=?', (subscription_id,)).fetchone()
        already = old['amount'] if old else 0
        used = old is not None
    else:
        used = False
    available = max(0, allocated['free_deposit']) if not allocated['conflicts'] else 0
    parts = []
    left = min(max(0, total - covered), available) if use_balance is True and not used else 0
    amount = left
    locks = set(allocated.get('payment_locks', []))
    for pid, remaining in allocated.get('payment_remaining', {}).items():
        if pid in locks or remaining <= 0 or not left:
            continue
        take = min(left, remaining)
        parts.append({'payment_id': pid, 'amount': take})
        left -= take
    for fid, remaining in allocated.get('fund_remaining', {}).items():
        if remaining <= 0 or not left:
            continue
        take = min(left, remaining)
        parts.append({'fund_id': fid, 'amount': take})
        left -= take
    if left:
        raise ValueError('Не удалось подтвердить состав доступного баланса')
    result = {'use_balance': use_balance is True, 'available_balance': available, 'apply_amount': amount,
              'invoice_total': total, 'payable': max(0, total - covered - amount),
              'remaining_balance': available - amount, 'already_applied': already,
              'source_parts': parts, 'allocation_conflicts': allocated['conflicts'],
              'labels': allocated.get('fund_labels', [])}
    result['token'] = _hash({'student_id': sid, **result,
        'payment_remaining': allocated.get('payment_remaining', {}),
        'fund_remaining': allocated.get('fund_remaining', {}), 'current_covered': covered})
    return result


def apply_to_invoice(con, subid, user=None, use_balance=False, expected_token=None, request_id=''):
    sub = con.execute('SELECT * FROM subscriptions WHERE id=?', (subid,)).fetchone()
    if not sub or sub['status'] == 'cancelled':
        raise ValueError('Действующая покупка не найдена')
    if use_balance is not True:
        return preview(con, sub['student_id'], sub['price'], False, subid)
    if not isinstance(request_id, str) or not request_id.strip() or not expected_token:
        raise ValueError('Для зачёта нужны подтверждённый расчёт и ID запроса')
    signature = _hash([subid, True, expected_token])
    old = con.execute('SELECT * FROM invoice_balance_uses WHERE subscription_id=? OR request_id=?', (subid, request_id)).fetchall()
    if old:
        if len(old) != 1 or old[0]['subscription_id'] != subid or old[0]['request_id'] != request_id or old[0]['payload_fingerprint'] != signature:
            raise ValueError('Баланс по этой покупке уже зачтён; повторное списание не выполнено')
        return json.loads(old[0]['result'])
    con.execute('SAVEPOINT explicit_invoice_balance')
    try:
        current = preview(con, sub['student_id'], sub['price'], True, subid)
        if current['allocation_conflicts'] or current['token'] != expected_token:
            raise ValueError('Баланс или покупка изменились; обновите расчёт перед зачётом')
        if not is_protected(con, subid):
            from . import paymentallocation
            before = paymentallocation.allocation(con, sub['student_id'])
            if sub['status'] == 'paid' and before['covered'].get(subid, 0) != sub['price']:
                raise ValueError('Старая оплаченная покупка требует сверки источника перед изменением зачёта')
            protect_invoice(con, subid, user)
            # Protecting an existing partial invoice must retain its observed
            # cash coverage; unlike a new invoice it has already received cash.
            affected = {p['payment_id'] for p in before['sources'].get(subid, [])
                        if p['payment_id'] not in before['payment_locks']}
            grouped = {}
            for target_subid, parts in before['sources'].items():
                for part in parts:
                    if part['payment_id'] in affected:
                        key = (part['payment_id'], target_subid)
                        grouped[key] = grouped.get(key, 0) + part['amount']
            for (pid, target_subid), amount in grouped.items():
                target_payment(con, pid, target_subid, amount)
            after = preview(con, sub['student_id'], sub['price'], True, subid)
            if after['allocation_conflicts'] or any(after[k] != current[k] for k in
                    ('available_balance', 'apply_amount', 'payable', 'remaining_balance', 'source_parts')):
                raise ValueError('Не удалось сохранить прежние зачёты; нужна сверка покупки')
        result = dict(current, already_applied=current['apply_amount'])
        con.execute('INSERT INTO invoice_balance_uses VALUES (?,?,?,?,?,?,?,?)',
            (subid, request_id, signature, current['apply_amount'], _json(current['source_parts']),
             _json(result), _now(), (user or {}).get('name', '')))
        from . import money
        money._reconcile(con, sub['student_id'])
        con.execute('RELEASE explicit_invoice_balance')
        return result
    except Exception:
        con.execute('ROLLBACK TO explicit_invoice_balance')
        con.execute('RELEASE explicit_invoice_balance')
        raise


def snapshot_fingerprint(con, sid, reviewed_payment_ids, historical_coverage):
    ids = sorted(set(reviewed_payment_ids))
    if any(type(i) is not int or i <= 0 for i in ids):
        raise ValueError('Для снимка нужны точные ID исходных операций')
    payments = []
    for pid in ids:
        row = con.execute('SELECT * FROM payments WHERE id=?', (pid,)).fetchone()
        if not row or row['student_id'] != sid:
            raise ValueError('Исходная операция не принадлежит выбранной карточке')
        payments.append([row[k] for k in ('id', 'amount', 'method', 'paid_at', 'subscription_id', 'cancelled')])
    purchases = []
    for subid, amount in sorted((int(k), v) for k, v in historical_coverage.items()):
        amount = _amount(amount, zero=True)
        row = con.execute('SELECT * FROM subscriptions WHERE id=?', (subid,)).fetchone()
        if not row or row['student_id'] != sid or row['status'] == 'cancelled' or amount > row['price']:
            raise ValueError('Историческая покупка или её стоимость изменились')
        purchases.append([row[k] for k in ('id', 'month', 'price', 'status')] + [amount])
    return _hash([sid, payments, purchases])


def record_snapshot(con, sid, *, amount, as_of_date, cutoff_date, reviewed_payment_ids,
                    historical_coverage, evidence_id, user=None, expected_fingerprint=None):
    amount = _amount(amount, zero=True)
    if not isinstance(evidence_id, str) or not evidence_id.strip():
        raise ValueError('Укажите подтверждение исторического снимка')
    for value in (as_of_date, cutoff_date):
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError('Укажите точные даты снимка')
    if cutoff_date > as_of_date:
        raise ValueError('Граница истории не может быть позже снимка')
    ids = sorted(set(reviewed_payment_ids))
    frozen = {int(k): v for k, v in historical_coverage.items()}
    current = snapshot_fingerprint(con, sid, ids, frozen)
    if expected_fingerprint is not None and current != expected_fingerprint:
        raise ValueError('Исходные записи изменились после подготовки снимка')
    for subid in frozen:
        if con.execute('SELECT month FROM subscriptions WHERE id=?', (subid,)).fetchone()[0] > as_of_date[:7]:
            raise ValueError('Будущий счёт не закрывается историческим снимком')
    signature = _hash([sid, amount, as_of_date, cutoff_date, ids, frozen, current])
    old = con.execute('SELECT * FROM balance_account_snapshots WHERE evidence_id=?', (evidence_id,)).fetchone()
    if old:
        if old['payload_fingerprint'] != signature:
            raise ValueError('С этим подтверждением уже сохранён другой снимок')
        return old['id']
    if con.execute('SELECT 1 FROM balance_account_snapshots WHERE student_id=?', (sid,)).fetchone():
        raise ValueError('Для замены исторического снимка нужна отдельная сверка уже выполненных зачётов')
    snapid = con.execute('''INSERT INTO balance_account_snapshots(student_id,amount,as_of_date,cutoff_date,
        reviewed_payment_ids,historical_coverage,evidence_id,payload_fingerprint,created_at,created_by)
        VALUES (?,?,?,?,?,?,?,?,?,?)''', (sid, amount, as_of_date, cutoff_date, _json(ids), _json(frozen),
        evidence_id, signature, _now(), (user or {}).get('name', ''))).lastrowid
    if amount:
        add_existing_fund(con, sid, amount, source_ref='snapshot:'+evidence_id,
            label='Остаток по подтверждённому снимку', evidence_id=evidence_id, snapshot_id=snapid,
            source_kind='opening_balance', user=user)
    return snapid


def add_existing_fund(con, sid, amount, *, source_ref, label, evidence_id=None, snapshot_id=None,
                      source_kind='existing_carry', user=None):
    amount = _amount(amount)
    evidence_id = source_ref if evidence_id is None else evidence_id
    if not all(isinstance(v, str) and v.strip() for v in (source_ref, label, evidence_id)):
        raise ValueError('Для существующих средств нужны подтверждение и назначение')
    if source_kind not in ('existing_carry', 'opening_balance', 'referral_bonus'):
        raise ValueError('Неизвестный источник существующего баланса')
    if snapshot_id is not None:
        snap = con.execute('SELECT student_id FROM balance_account_snapshots WHERE id=?', (snapshot_id,)).fetchone()
        if not snap or snap['student_id'] != sid:
            raise ValueError('Исторический снимок принадлежит другой карточке')
    values = (sid, amount, source_kind, source_ref, label, evidence_id, snapshot_id)
    old = con.execute('SELECT * FROM balance_credit_funds WHERE source_ref=?', (source_ref,)).fetchone()
    if old:
        if tuple(old[k] for k in ('student_id', 'amount', 'source_kind', 'source_ref', 'label', 'evidence_id', 'snapshot_id')) != values:
            raise ValueError('Существующие средства уже учтены с другой суммой или назначением')
        return old['id']
    return con.execute('''INSERT INTO balance_credit_funds(student_id,amount,source_kind,source_ref,label,
        evidence_id,snapshot_id,created_at,created_by) VALUES (?,?,?,?,?,?,?,?,?)''',
        (*values, _now(), (user or {}).get('name', ''))).lastrowid
