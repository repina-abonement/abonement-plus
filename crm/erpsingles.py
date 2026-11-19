"""ERP single-lesson purchases: explicit identities, dates and payment links.

An ERP sale is not a monthly entitlement. Missing lesson dates stay missing;
neither the group schedule nor payment/sale dates provide that evidence.
"""
import hashlib
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation


SCHEMA = """
CREATE TABLE IF NOT EXISTS erp_single_sales (
 source_id TEXT PRIMARY KEY,
 student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
 group_id INTEGER NOT NULL REFERENCES groups(id),
 subscription_id INTEGER NOT NULL UNIQUE REFERENCES subscriptions(id) ON DELETE CASCADE,
 source_signature TEXT NOT NULL, source_payload TEXT NOT NULL DEFAULT '{}', lesson_date TEXT,
 payment_operation_id TEXT NOT NULL DEFAULT '', native_signature TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS erp_single_reviews (
 id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE,
 student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
 group_id INTEGER, source_id TEXT NOT NULL DEFAULT '', payload TEXT NOT NULL,
 reason TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS erp_single_payments (
 operation_id TEXT PRIMARY KEY,
 payment_id INTEGER NOT NULL UNIQUE REFERENCES payments(id) ON DELETE CASCADE,
 source_sale_id TEXT NOT NULL DEFAULT '', signature TEXT NOT NULL);
"""


def init(con):
    con.executescript(SCHEMA)
    if 'source_payload' not in {r[1] for r in con.execute('PRAGMA table_info(erp_single_sales)')}:
        con.execute("ALTER TABLE erp_single_sales ADD COLUMN source_payload TEXT NOT NULL DEFAULT '{}'")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _date(value):
    from . import erpimport as ei
    try:
        value = ei.parse_date(value)
        return datetime.fromisoformat(value).date().isoformat() if value else None
    except (TypeError, ValueError, OverflowError):
        return None


def _amount(value):
    try:
        number = Decimal(str(value).replace('\u00a0', '').replace(' ', '').replace('₽', '').replace(',', '.'))
        return int(number) if number.is_finite() and number == number.to_integral_value() else None
    except (InvalidOperation, ValueError, OverflowError):
        return None


def review(con, record, sid, gid, reason, now=''):
    """Retain unresolved source evidence without inventing an operational row."""
    payload = _json(record)
    # Import callers supply the source file digest + row number. This keeps
    # identical but distinct source rows while making file replays idempotent.
    key = record.get('_source_key') or _hash([sid, gid, record])
    fingerprint = _hash([key, sid, gid])
    previous = con.execute('SELECT id FROM erp_single_reviews WHERE fingerprint=?', (fingerprint,)).fetchone()
    if previous:
        con.execute('UPDATE erp_single_reviews SET reason=? WHERE id=?', (reason, previous['id']))
        return {'created': []}
    row_id = con.execute('INSERT INTO erp_single_reviews(fingerprint,student_id,group_id,source_id,payload,reason,created_at) VALUES (?,?,?,?,?,?,?)',
                         (fingerprint, sid, gid, str(record.get('sale_id') or '').strip(), payload, reason, now)).lastrowid
    return {'created': [('erp_single_reviews', row_id)]}


def _result(reason='', target=None, created=None, counts=None):
    return {'state': 'unmatched' if reason else 'native', 'reason': reason,
            'target_table': 'subscriptions' if target else '', 'target_id': target,
            'created': created or [], 'counts': counts or {}}


def _native_signature(row):
    return _json([row[key] for key in ('student_id', 'enrollment_id', 'month', 'tariff', 'kind',
                                      'lessons', 'price', 'per_lesson', 'status', 'created_at', 'paid_at')])


def _payment_signature(row):
    return _json([row[key] for key in ('student_id', 'amount', 'method', 'paid_at', 'cancelled')])


def import_sale(con, record, sid, gid, now):
    """Import one identified sale; return state/reason, target and created rows.

    A sale without an explicit lesson date is retained as kind=single, but is
    reported for review and creates no booking. Conflicting native data is kept.
    """
    from . import erpimport as ei
    record = dict(record)
    record.setdefault('_record_kind', 'subs')
    source_id = str(record.get('sale_id') or '').strip()
    raw_day = record.get('lesson_date') or ''
    day = _date(raw_day)
    ym = ei.parse_month(record.get('month', '')) or (day[:7] if day else None)
    price = _amount(record.get('price', ''))
    op = str(record.get('operation_id') or '').strip()
    status = ei.sub_status(record.get('status', ''), price or 0)
    reason = ''
    if not source_id:
        reason = 'Разовое: нет ID продажи ERP; нужны отдельные продажи и даты занятий для сверки'
    elif not sid or not gid or gid < 0 or not ym or price is None or price < 0:
        reason = 'Разовое: нужны ребёнок, группа, месяц и точная стоимость продажи ERP'
    elif ei.sub_lessons(record) != 1:
        reason = 'Разовое: в продаже не одно занятие; нужны отдельные даты и состав покупки ERP'
    elif raw_day and not day:
        reason = 'Разовое: некорректная дата занятия ERP'
    elif day and day[:7] != ym:
        reason = 'Разовое: дата занятия и месяц продажи ERP различаются'
    if reason:
        return _result(reason, created=review(con, record, sid, gid, reason, now)['created'])
    signature = _json([sid, gid, ym, price, 1, status, record.get('sale_at', '')])
    linked = con.execute('SELECT * FROM erp_single_sales WHERE source_id=?', (source_id,)).fetchone()
    created, counts = [], {}
    if linked:
        sub = con.execute('SELECT * FROM subscriptions WHERE id=?', (linked['subscription_id'],)).fetchone()
        if not sub or signature != linked['source_signature'] or _native_signature(sub) != linked['native_signature']:
            reason = 'Разовое: продажа ERP или сохранённая покупка CRM изменились; нужна ручная сверка'
        elif linked['lesson_date'] and linked['lesson_date'] != day:
            reason = 'Разовое: дата занятия отличается от уже перенесённой; запись CRM сохранена'
        elif linked['payment_operation_id'] and op and linked['payment_operation_id'] != op:
            reason = 'Разовое: ID операции оплаты ERP изменился; связь CRM сохранена'
        if reason:
            return _result(reason, linked['subscription_id'], review(con, record, sid, gid, reason, now)['created'])
        sub_id = sub['id']
        day = day or linked['lesson_date']
        op = op or linked['payment_operation_id']
    else:
        # A pre-existing month may be an old collapsed collection of single
        # sales. Never add its component purchases again or rewrite that bill.
        monthly = con.execute("SELECT x.id FROM subscriptions x JOIN enrollments e ON e.id=x.enrollment_id WHERE x.student_id=? AND e.group_id=? AND x.month=? AND x.status!='cancelled' AND COALESCE(x.kind,'month')='month'", (sid, gid, ym)).fetchall()
        if monthly:
            reason = 'Разовое: за этот месяц уже есть абонемент CRM; состав старого переноса требует ручной сверки'
            return _result(reason, created=review(con, record, sid, gid, reason, now)['created'])
        # Do not adopt a pre-existing booking or bill by matching amount/date.
        if day and con.execute("SELECT 1 FROM single_visits WHERE student_id=? AND date=?", (sid, day)).fetchone():
            reason = 'Разовое: на эту дату уже есть запись CRM без связи с ID продажи ERP'
            return _result(reason, created=review(con, record, sid, gid, reason, now)['created'])
        unknown_single = con.execute("""SELECT 1 FROM subscriptions x
            LEFT JOIN erp_single_sales l ON l.subscription_id=x.id
            WHERE x.student_id=? AND x.month=? AND x.kind='single' AND x.status!='cancelled'
              AND l.source_id IS NULL AND (? IS NULL OR NOT EXISTS
                (SELECT 1 FROM single_visits v WHERE v.subscription_id=x.id AND v.status='booked'))""", (sid, ym, day)).fetchone()
        if unknown_single:
            reason = 'Разовое: есть покупка CRM без связи с ID продажи ERP и подтверждённой даты; нужна ручная сверка'
            return _result(reason, created=review(con, record, sid, gid, reason, now)['created'])
        created_at = ei.parse_date(record.get('sale_at', '')) or now
        sub_id = con.execute("INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at,paid_at,kind,erp_full_managed,ledger_managed) VALUES (?,NULL,?,'single',1,?,?,?,?,NULL,'single',1,1)",
                             (sid, ym, price, price, status, created_at)).lastrowid
        created.append(('subscriptions', sub_id))
        counts['added_single_sales'] = 1
        sub = con.execute('SELECT * FROM subscriptions WHERE id=?', (sub_id,)).fetchone()
        con.execute('INSERT INTO erp_single_sales(source_id,student_id,group_id,subscription_id,source_signature,source_payload,lesson_date,payment_operation_id,native_signature) VALUES (?,?,?,?,?,?,?,?,?)',
                    (source_id, sid, gid, sub_id, signature, _json(record), day, op, _native_signature(sub)))
    if day and status != 'cancelled':
        visits = con.execute('SELECT * FROM single_visits WHERE subscription_id=?', (sub_id,)).fetchall()
        if visits:
            if len(visits) != 1 or (visits[0]['student_id'], visits[0]['group_id'], visits[0]['date'], visits[0]['status']) != (sid, gid, day, 'booked'):
                reason = 'Разовое: запись на занятие изменена в CRM; изменения сохранены'
        elif linked and linked['lesson_date']:
            reason = 'Разовое: сохранённая запись на занятие удалена; автоматически не восстановлена'
        elif con.execute('SELECT 1 FROM single_visits WHERE student_id=? AND group_id=? AND date=?', (sid, gid, day)).fetchone():
            reason = 'Разовое: на эту дату уже есть запись CRM без связи с ID продажи ERP'
        else:
            visit_id = con.execute("INSERT INTO single_visits(student_id,group_id,date,status,note,created_at,created_by,subscription_id) VALUES (?,?,?,'booked',?,?,'ERP',?)",
                                   (sid, gid, day, 'ERP, продажа '+source_id, now, sub_id)).lastrowid
            created.append(('single_visits', visit_id))
            counts['added_single_visits'] = 1
    elif not day:
        reason = 'Разовое: покупка сохранена; дата занятия в ERP не указана, нужна ручная сверка'
    if not reason or not day:
        con.execute('UPDATE erp_single_sales SET lesson_date=?,payment_operation_id=? WHERE source_id=?', (day, op, source_id))
        if linked and ((day and not linked['lesson_date']) or (op and not linked['payment_operation_id'])):
            payload = json.loads(linked['source_payload'])
            payload.setdefault('_source_updates', []).append(record)
            con.execute('UPDATE erp_single_sales SET source_payload=? WHERE source_id=?', (_json(payload), source_id))
    if reason:
        created.extend(review(con, record, sid, gid, reason, now)['created'])
    else:
        key = record.get('_source_key') or _hash([sid, gid, record])
        con.execute('DELETE FROM erp_single_reviews WHERE fingerprint=?', (_hash([key, sid, gid]),))
    return _result(reason, sub_id, created, counts)


def register_payment(con, payment_id, operation_id, sale_id='', now=''):
    """Remember explicit ERP operation identity; do not guess its purchase."""
    operation_id, sale_id = str(operation_id or '').strip(), str(sale_id or '').strip()
    payment = con.execute('SELECT * FROM payments WHERE id=?', (payment_id,)).fetchone()
    if not operation_id or not payment:
        return _result('Разовое: для связи оплаты нужен ID операции ERP')
    signature = _payment_signature(payment)
    old = con.execute('SELECT * FROM erp_single_payments WHERE operation_id=?', (operation_id,)).fetchone()
    if old:
        if old['payment_id'] != payment_id or old['signature'] != signature or (old['source_sale_id'] and sale_id and old['source_sale_id'] != sale_id):
            return _result('Разовое: операция ERP уже связана с другой или изменённой оплатой CRM')
        if sale_id and not old['source_sale_id']:
            con.execute('UPDATE erp_single_payments SET source_sale_id=? WHERE operation_id=?', (sale_id, operation_id))
    elif con.execute('SELECT 1 FROM erp_single_payments WHERE payment_id=?', (payment_id,)).fetchone():
        return _result('Разовое: платёж CRM уже связан с другой операцией ERP')
    else:
        con.execute('INSERT INTO erp_single_payments(operation_id,payment_id,source_sale_id,signature) VALUES (?,?,?,?)', (operation_id, payment_id, sale_id, signature))
    return _result()


def reconcile_links(con):
    """Link existing payments only using explicit sale/operation identifiers."""
    result = {'counts': {}, 'issues': []}
    linked_count = 0
    for sale in con.execute('SELECT * FROM erp_single_sales'):
        candidates = con.execute('SELECT * FROM erp_single_payments WHERE source_sale_id=? OR (?!=\'\' AND operation_id=?)',
                                 (sale['source_id'], sale['payment_operation_id'], sale['payment_operation_id'])).fetchall()
        sub = con.execute('SELECT * FROM subscriptions WHERE id=?', (sale['subscription_id'],)).fetchone()
        valid, issues = [], []
        for candidate in candidates:
            payment = con.execute('SELECT * FROM payments WHERE id=?', (candidate['payment_id'],)).fetchone()
            conflicting = con.execute("SELECT 1 FROM erp_single_sales WHERE source_id!=? AND payment_operation_id!='' AND payment_operation_id=?", (sale['source_id'], candidate['operation_id'])).fetchone()
            reason = ''
            if not payment or _payment_signature(payment) != candidate['signature'] or payment['cancelled']:
                reason = 'Разовое: связанная оплата CRM изменена или отменена'
            elif candidate['source_sale_id'] and candidate['source_sale_id'] != sale['source_id'] or conflicting:
                reason = 'Разовое: ID операции указан у разных продаж ERP'
            elif payment['student_id'] != sale['student_id'] or payment['amount'] <= 0:
                reason = 'Разовое: ребёнок или направление оплаты не совпадают с продажей ERP'
            elif payment['subscription_id'] not in (None, sale['subscription_id']):
                reason = 'Разовое: платёж CRM уже связан с другой покупкой; связь сохранена'
            else:
                if not sub or _native_signature(sub) != sale['native_signature']:
                    reason = 'Разовое: сохранённая покупка CRM изменена; связь требует сверки'
                elif payment['amount'] > sub['price']:
                    reason = 'Разовое: сумма операции больше покупки; нужно проверить распределение оплаты ERP'
            if reason:
                issues.append({'sale_id': sale['source_id'], 'operation_id': candidate['operation_id'], 'reason': reason})
            else:
                valid.append(payment)
        # Validate the entire proposed allocation before linking any member.
        # Multiple explicit partial payments must not silently over-cover it.
        if not issues and valid:
            ids = {p['id'] for p in valid}
            already = sum(p['amount'] for p in con.execute('SELECT id,amount FROM payments WHERE subscription_id=? AND cancelled=0', (sale['subscription_id'],)) if p['id'] not in ids)
            if sum(p['amount'] for p in valid) + already > sub['price']:
                issues.append({'sale_id': sale['source_id'], 'reason': 'Разовое: сумма связанных операций больше покупки; распределение не изменено'})
        if not candidates and sub and sub['status'] == 'paid' and sub['price'] > 0:
            issues.append({'sale_id': sale['source_id'], 'reason': 'Разовое: оплата не связана по ID продажи или ID операции ERP; нужна сверка реестра оплат'})
        if issues:
            result['issues'].extend(issues)
            continue
        for payment in valid:
            if payment['subscription_id'] is None:
                con.execute('UPDATE payments SET subscription_id=? WHERE id=? AND subscription_id IS NULL', (sale['subscription_id'], payment['id']))
                linked_count += 1
    if linked_count:
        result['counts']['linked_single_payments'] = linked_count
    return result


def subscription_evidence(con, sid, ym=None):
    """Prove the original type of legacy month aggregates from retained rows.

    Used only for truthful display of old imports. It neither repairs an old
    bill nor turns source dates into bookings.
    """
    from . import erpfull, erpimport as ei
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='erp_full_rows'").fetchone():
        return {}
    result, seen, cache = {}, set(), {}
    sql = "SELECT r.target_id,r.row_num,f.* FROM erp_full_rows r JOIN erp_full_files f ON f.id=r.file_id WHERE r.student_id=? AND r.target_table='subscriptions' AND f.kind='subs'"
    for row in con.execute(sql, (sid,)):
        if row['id'] not in cache:
            f = dict(row)
            for key in ('headers', 'rows', 'mapping'):
                f[key] = json.loads(f[key])
            cache[row['id']] = f
        f = cache[row['id']]
        record = erpfull._record(f, f['rows'][row['row_num']-2])
        month = ei.parse_month(record.get('month', ''))
        if ym and month != ym:
            continue
        identity = (row['target_id'], record.get('sale_id') or (row['digest'], row['row_num']))
        if identity in seen:
            continue
        seen.add(identity)
        evidence = result.setdefault(row['target_id'], {'kind': 'single', 'single_lessons': 0, 'known_dates': [], 'source': 'erp_full_rows', '_kinds': set()})
        kind = ei.sub_kind(record.get('tariff', ''))
        evidence['_kinds'].add(kind)
        if kind == 'single':
            evidence['single_lessons'] += ei.sub_lessons(record)
            day = _date(record.get('lesson_date', ''))
            if day and day not in evidence['known_dates']:
                evidence['known_dates'].append(day)
    for evidence in result.values():
        kinds = evidence.pop('_kinds')
        evidence['kind'] = 'single' if kinds == {'single'} else 'mixed' if 'single' in kinds else 'month'
        evidence['known_dates'].sort()
    return result
