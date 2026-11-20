"""Local import of an already executed ERP refund, never a refund instruction.

Preview executes on a SQLite copy. Apply requires exact source identities, a
complete existing return receipt, reviewed allocations and a matching backup.
No provider, cheque-printing, messaging or ordinary cancellation API is used.
Ledger amounts are whole rubles; receipt amounts are integer kopecks.
"""
from contextlib import contextmanager
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from . import erpimport, money


class HistoricalRefundError(erpimport.ImportError_):
    pass


SCHEMA = '''
CREATE TABLE IF NOT EXISTS historical_refund_receipts (
 fn TEXT NOT NULL, fd TEXT NOT NULL, fp TEXT NOT NULL, operation INTEGER NOT NULL,
 total_minor INTEGER NOT NULL, cashless_minor INTEGER NOT NULL, issued_at TEXT NOT NULL,
 source_namespace TEXT NOT NULL, refund_operation_id TEXT NOT NULL,
 fx_receipt_id INTEGER, evidence_kind TEXT NOT NULL DEFAULT 'owner_document', PRIMARY KEY(fn,fd), UNIQUE(source_namespace,refund_operation_id));
CREATE TABLE IF NOT EXISTS historical_refund_runs (
 id INTEGER PRIMARY KEY, source_namespace TEXT NOT NULL, refund_operation_id TEXT NOT NULL,
 student_id INTEGER NOT NULL REFERENCES students(id), subscription_id INTEGER NOT NULL REFERENCES subscriptions(id),
 source_invoice_id TEXT NOT NULL, amount INTEGER NOT NULL, executed_at TEXT NOT NULL,
 refund_payment_id INTEGER NOT NULL UNIQUE REFERENCES payments(id), request_signature TEXT NOT NULL,
 request_json TEXT NOT NULL, before_fingerprint TEXT NOT NULL, after_fingerprint TEXT NOT NULL,
 before_summary TEXT NOT NULL, after_summary TEXT NOT NULL,
 old_price INTEGER NOT NULL, new_price INTEGER NOT NULL, old_status TEXT NOT NULL, new_status TEXT NOT NULL,
 created_at TEXT NOT NULL, created_by TEXT NOT NULL, backup_path TEXT NOT NULL,
 UNIQUE(source_namespace,refund_operation_id));
CREATE TABLE IF NOT EXISTS historical_refund_exclusions (
 subscription_id INTEGER NOT NULL REFERENCES subscriptions(id), student_id INTEGER NOT NULL REFERENCES students(id),
 group_id INTEGER NOT NULL, month TEXT NOT NULL, reason TEXT NOT NULL,
 PRIMARY KEY(subscription_id,group_id,month));
'''


def init(con):
    con.executescript(SCHEMA)
    if 'evidence_kind' not in {row[1] for row in con.execute('PRAGMA table_info(historical_refund_receipts)')}:
        con.execute("ALTER TABLE historical_refund_receipts ADD COLUMN evidence_kind TEXT NOT NULL DEFAULT 'owner_document'")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _exists(con, table):
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _rows(con, sql, args=()):
    return [dict(row) for row in con.execute(sql, args)]


def _integer(value, positive=True):
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
        if str(result) != str(value).strip() or (result <= 0 if positive else result < 0):
            return None
        return result
    except (ValueError, TypeError, OverflowError):
        return None


def _stamp(value):
    if not isinstance(value, str) or len(value) < 16 or 'T' not in value:
        return None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return value if result.year >= 2000 else None
    except ValueError:
        return None


def _executed(value):
    if isinstance(value, str) and len(value) == 10:
        try:
            return value if date.fromisoformat(value).isoformat() == value else None
        except ValueError:
            return None
    return _stamp(value)


def _same_execution(left, right):
    # Preserve date-only evidence as a date, never invent midnight/noon/time.
    if isinstance(left, str) and isinstance(right, str) and (len(left) == 10 or len(right) == 10):
        return len(left) == len(right) == 10 and left == right and _executed(left) is not None
    return _same_time(left, right)


def _same_time(left, right):
    if not _stamp(left) or not _stamp(right):
        return False
    try:
        values = [datetime.fromisoformat(value.replace('Z', '+00:00')) for value in (left, right)]
        values = [value if value.tzinfo else value.replace(tzinfo=ZoneInfo('Europe/Moscow')) for value in values]
        return values[0] == values[1]
    except (TypeError, ValueError):
        return False


def _signature(data):
    # Exact retries return the recorded event, but changed proof must not adopt it.
    return _hash(data)


def _snapshot(con, sid):
    result = {'student': _rows(con, 'SELECT * FROM students WHERE id=?', (sid,))}
    for table in ('subscriptions', 'payments', 'attendance', 'single_visits', 'enrollments', 'history',
                  'historical_refund_runs', 'historical_refund_exclusions', 'erp_full_rows'):
        if _exists(con, table):
            result[table] = _rows(con, 'SELECT * FROM '+table+' WHERE student_id=? ORDER BY 1', (sid,))
    for table in ('erp_full_operations', 'erp_payment_allocations'):
        if _exists(con, table):
            result[table] = _rows(con, 'SELECT * FROM '+table+' WHERE payment_id IN (SELECT id FROM payments WHERE student_id=?) ORDER BY 1,2', (sid,))
    if _exists(con, 'erp_refund_allocations'):
        result['erp_refund_allocations'] = _rows(con, '''SELECT * FROM erp_refund_allocations WHERE refund_payment_id IN
            (SELECT id FROM payments WHERE student_id=?) OR original_payment_id IN (SELECT id FROM payments WHERE student_id=?) ORDER BY 1,2,3''', (sid, sid))
    for table in ('pay_links', 'cancelled_service_charges', 'subscription_cancellations'):
        if _exists(con, table):
            result[table] = _rows(con, 'SELECT * FROM '+table+' WHERE student_id=? ORDER BY 1', (sid,))
    if _exists(con, 'pay_link_subscriptions'):
        result['pay_link_subscriptions'] = _rows(con, '''SELECT * FROM pay_link_subscriptions WHERE subscription_id IN
            (SELECT id FROM subscriptions WHERE student_id=?) ORDER BY 1,2''', (sid,))
    # Global fiscal/operation identities can collide with another card. Source
    # archive contents enter the hash only, never duplicated in an audit report.
    result['historical_refund_receipts'] = _rows(con, 'SELECT * FROM historical_refund_receipts ORDER BY fn,fd')
    if _exists(con, 'fx_receipts'):
        result['fx_receipts'] = _rows(con, 'SELECT * FROM fx_receipts ORDER BY id')
    if _exists(con, 'erp_full_files'):
        result['source_archive'] = [{'id': row['id'], 'hash': _hash(dict(row))} for row in con.execute('SELECT * FROM erp_full_files ORDER BY id')]
    result['all_operation_ids'] = _rows(con, 'SELECT * FROM erp_full_operations ORDER BY operation_id') if _exists(con, 'erp_full_operations') else []
    return result


def _summary(con, sid, report=None):
    from . import historicalrefundtrace, paymentallocation
    allocation = paymentallocation.allocation(con, sid)
    if allocation['conflicts']:
        raise HistoricalRefundError('; '.join(allocation['conflicts']))
    balance = money.balance(con, sid)
    purchases = _rows(con, 'SELECT id,price,status,month,lessons FROM subscriptions WHERE student_id=? ORDER BY id', (sid,))
    native = {row['id']:row for row in purchases}
    payments = {row['id']:dict(row) for row in con.execute('SELECT * FROM payments WHERE student_id=? AND cancelled=0', (sid,))}
    persisted = _rows(con, 'SELECT a.* FROM erp_payment_allocations a JOIN payments p ON p.id=a.payment_id WHERE p.student_id=?', (sid,))
    bindings = {(row['payment_id'],row['subscription_id']):row for row in persisted}
    # Proposed maps have already passed the strict crosswalk validation. They
    # identify source invoices during preview without changing actual allocation.
    identities = list(bindings.values()) + list((report or {}).get('payment_allocations') or [])
    invoices = {}
    def entry(invoice):
        return invoices.setdefault(invoice, {'source_invoice_id':invoice,'subscription_ids':set(),'source_payment_ids':set(),'source_prices':set()})
    for row in identities:
        item = entry(row['source_invoice_id'])
        item['subscription_ids'].add(row['subscription_id']);item['source_payment_ids'].add(row['payment_id'])
    student = con.execute('SELECT erp_id FROM students WHERE id=?', (sid,)).fetchone()
    traced = historicalrefundtrace.trace(con, student['erp_id']) if student else {}
    for proof in traced.get('invoice_resolutions', []):
        if proof.get('ambiguous'):
            continue
        item = entry(proof['invoice_id'])
        item['subscription_ids'].update(_invoice_ids(proof))
        item['source_payment_ids'].update(pid for pid in _payment_ids(proof) if pid in payments and payments[pid]['amount']>0)
        for row in proof.get('candidates', []):
            if type(row.get('source_price')) is int and row['source_price'] >= 0:item['source_prices'].add(row['source_price'])
    invoice_totals = {}
    for invoice, refs in sorted(invoices.items()):
        subids, pids = refs['subscription_ids'], refs['source_payment_ids']
        subids = {subid for subid in subids if subid in native}
        pids = {pid for pid in pids if pid in payments and payments[pid]['amount']>0}
        gross = sum(payments[pid]['amount'] for pid in pids)
        refunded = sum(row['amount'] for row in con.execute('SELECT * FROM erp_refund_allocations')
                       if row['original_payment_id'] in pids and row['subscription_id'] in subids)
        charge = covered = 0
        for subid in subids:
            sub = native[subid]
            if sub['status'] == 'cancelled':
                retained = con.execute('SELECT amount FROM cancelled_service_charges WHERE subscription_id=?', (subid,)).fetchone()
                charge += retained['amount'] if retained else 0
                covered += allocation['covered'].get(-subid, 0)
            else:
                charge += sub['price'];covered += allocation['covered'].get(subid, 0)
        exact = [row for row in bindings.values() if row['source_invoice_id']==invoice]
        invoice_totals[invoice] = {'source_invoice_id':invoice,'source_payment_ids':sorted(pids),'subscription_ids':sorted(subids),
            'source_payment_total':gross,'allocated_total':sum(row['amount'] for row in exact) if exact else covered,
            'refunded_total':refunded,'net_paid':gross-refunded,'current_charge':charge,'covered':covered,'due':max(0,charge-covered),
            'original_native_price':sum(native[subid]['price'] for subid in subids),
            'source_price':next(iter(refs['source_prices'])) if len(refs['source_prices'])==1 else None,
            'source_remaining':sum(allocation['payment_remaining'].get(pid,0) for pid in pids),'allocations_reviewed':bool(exact)}
    return {'paid_total':balance['paid_total'],'debt':balance['debt'],'upcoming':balance['upcoming'],
            'deposit':balance['deposit'],'free_deposit':allocation['free_deposit'],'restricted_deposit':allocation['restricted_deposit'],
            'covered':balance['covered'],'sources':allocation['sources'],'payment_remaining':allocation['payment_remaining'],
            'invoice_totals':invoice_totals,'purchases':purchases,
            'payment_count':con.execute('SELECT COUNT(*) FROM payments WHERE student_id=?', (sid,)).fetchone()[0]}


def context(con, erp_id):
    from . import historicalrefundtrace
    result = historicalrefundtrace.trace(con, str(erp_id).strip())
    result.setdefault('issues', [])
    result['historical_refunds'] = []
    if result.get('student'):
        sid = result['student']['id']
        result['historical_refunds'] = _rows(con, '''SELECT id,source_namespace,refund_operation_id,subscription_id,
            source_invoice_id,amount,executed_at,refund_payment_id,old_price,new_price,new_status,created_at
            FROM historical_refund_runs WHERE student_id=? ORDER BY id''', (sid,))
        try:
            result['balance'] = _summary(con, sid)
        except (ValueError, HistoricalRefundError) as error:
            result['issues'].append(str(error))
    result['read_only'] = True
    result['external_actions'] = False
    return result


def _receipt(con, receipt, amount, namespace, operation_id):
    issues = []
    if not isinstance(receipt, dict):
        return {}, ['Нужен существующий чек возврата с полными реквизитами']
    out = {key: str(receipt.get(key) or '').strip() for key in ('fn', 'fd', 'fp')}
    if len(out['fn']) != 16 or not out['fn'].isdigit() or int(out['fn'] or '0') <= 0:
        issues.append('Не указан точный ФН существующего чека возврата')
    for key, title in (('fd', 'ФД'), ('fp', 'ФПД')):
        if not out[key].isdigit() or not 0 < int(out[key] or '0') < 10**10:
            issues.append('Не указан корректный '+title+' чека возврата')
        else:
            out[key] = str(int(out[key]))
    out['operation'] = _integer(receipt.get('operation'))
    out['total_minor'] = _integer(receipt.get('total_minor'))
    out['cashless_minor'] = _integer(receipt.get('cashless_minor'), positive=False)
    out['issued_at'] = _stamp(receipt.get('issued_at'))
    if out['operation'] != 2:
        issues.append('Нужен чек «Возврат прихода» с признаком операции 2')
    if amount and out['total_minor'] != amount*100:
        issues.append('Сумма чека не совпадает с фактически выполненным возвратом')
    if out['cashless_minor'] is None or out['total_minor'] is None or out['cashless_minor'] > out['total_minor']:
        issues.append('Не подтверждена форма расчёта существующего чека')
    if not out['issued_at']:
        issues.append('Не указаны фактические дата и время существующего чека возврата')
    else:
        issued = datetime.fromisoformat(out['issued_at'].replace('Z', '+00:00'))
        if issued.tzinfo is None:
            # The protected form explicitly uses Moscow time. Keep its actual
            # precision; adding the known zone does not invent a clock value.
            issued = issued.replace(tzinfo=ZoneInfo('Europe/Moscow'))
            precision = 'minutes' if len(out['issued_at']) == 16 else 'auto'
            out['issued_at'] = issued.isoformat(timespec=precision)
    if issues:
        return out, issues
    for existing in con.execute('SELECT * FROM historical_refund_receipts WHERE fn=?', (out['fn'],)):
        if not str(existing['fd']).isdigit() or str(int(existing['fd'])) != out['fd']:
            continue
        if (existing['source_namespace'], existing['refund_operation_id']) != (namespace, operation_id):
            issues.append('Этот чек уже связан с другой операцией возврата; повторное списание запрещено')
        for key in ('fp', 'operation', 'total_minor', 'cashless_minor'):
            old_value = str(int(existing[key])) if key == 'fp' and str(existing[key]).isdigit() else existing[key]
            if old_value != out[key]:
                issues.append('Реквизиты существующего чека конфликтуют с уже сохранённым доказательством')
                break
        if not _same_time(existing['issued_at'], out['issued_at']):
            issues.append('Время существующего чека отличается от уже сохранённого')
    out['fx_receipt_id'] = None
    out['provider_payment_ids'] = []
    out['evidence_kind'] = 'owner_document'
    if _exists(con, 'fx_receipts'):
        for row in con.execute('SELECT * FROM fx_receipts WHERE fiscal_fn=? ORDER BY id', (out['fn'],)):
            if not str(row['fiscal_fd']).isdigit() or str(int(row['fiscal_fd'])) != out['fd']:
                continue
            old_fp = str(int(row['fiscal_fp'])) if str(row['fiscal_fp']).isdigit() else row['fiscal_fp']
            if (old_fp != out['fp'] or row['op'] != 2 or row['amount'] != out['total_minor']
                    or row['ecash'] != out['cashless_minor'] or not _same_time(str(row['ts']).replace(' ', 'T'), out['issued_at'])):
                issues.append('Чек с теми же ФН и ФД уже сохранён с другими реквизитами; данные не перезаписаны')
            else:
                out['fx_receipt_id'] = row['id']
                if row['source'] == 'paykeeper':
                    if row['pk_status'] != 'success':issues.append('Существующий чек PayKeeper ещё не подтверждён успешным статусом')
                    if row['pk_payment']:out['provider_payment_ids'].append(str(row['pk_payment']))
                if row['source'] in ('ofd', 'paykeeper'):out['evidence_kind'] = row['source']+'_record'
    return out, issues


def _invoice_ids(proof):
    return set(proof.get('subscription_ids') or [row['subscription_id'] for row in proof.get('candidates', []) if row.get('subscription_id')])


def _payment_ids(proof):
    return set(proof.get('payment_ids') or [row['payment_id'] for row in proof.get('candidates', []) if row.get('payment_id')])


def _source_method(value):
    canonical = ('cash', 'card', 'online', 'paykeeper', 'sbp', 'transfer', 'card_branch', 'sber_qr_branch', 'tochka_qr_link')
    return value if value in canonical else erpimport.pay_method(str(value or ''))


def _source_pay_date_matches(source, stored):
    if not _executed(source) or not isinstance(stored, str):
        return False
    return stored[:10] == source if len(source) == 10 else _same_time(source, stored)


def _income_method_matches(con, source_method, payment):
    source_method = _source_method(source_method)
    if source_method == payment['method']:
        return True
    # The local CRM may call the same verified online source PayKeeper. The
    # explicit paid provider link is required for this vocabulary difference.
    return ({source_method, payment['method']} == {'online', 'paykeeper'} and _exists(con, 'pay_links')
            and bool(con.execute("SELECT 1 FROM pay_links WHERE payment_id=? AND status='paid' AND pk_payment_id!=''", (payment['id'],)).fetchone()))


def _income_proof(con, sid, operation_id, invoice_id, payment, evidence_id=None):
    from . import historicalrefundtrace
    proof = historicalrefundtrace.resolve_payment_operation(con, sid, operation_id)
    if proof.get('ambiguous') or _payment_ids(proof) != {payment['id']}:
        return False
    raw = [row for row in proof.get('candidates', []) if row.get('source_kind') == 'payments'
           and row.get('source_invoice_id', row.get('invoice_id')) and row.get('amount') is not None]
    if not raw:
        return False
    identities = {(row.get('source_invoice_id', row.get('invoice_id')), row.get('amount'),
                   row.get('paid_at'), _source_method(row.get('source_method'))) for row in raw}
    if len(identities) != 1:
        return False
    for row in raw:
        if (row.get('source_invoice_id', row.get('invoice_id')) != invoice_id or row['amount'] != payment['amount']
                or not _source_pay_date_matches(row.get('paid_at'), payment['paid_at'])
                or not _income_method_matches(con, row.get('source_method'), payment)
                or not row.get('source_signature') or not row.get('evidence_id')):
            return False
    return evidence_id is None or evidence_id in {row['evidence_id'] for row in raw}


def _validate(con, data):
    from . import historicalrefundtrace, paymentallocation
    issues = []
    report = {'can_apply': False, 'issues': issues, 'student': None, 'subscription': None,
              'before_summary': None, 'after_summary': None, 'read_only': True, 'external_actions': False}
    if not isinstance(data, dict):
        issues.append('Передайте сведения об уже выполненном возврате ERP')
        return report
    namespace = str(data.get('source_namespace') or 'erp').strip()
    if namespace != 'erp':
        issues.append('Для этого переноса нужен источник ERP')
    report['source_namespace'] = namespace
    erp_id = str(data.get('erp_id') or '').strip()
    source_context = historicalrefundtrace.trace(con, erp_id) if erp_id else {}
    student = source_context.get('student')
    if not student:
        issues.append('Нужна единственная карточка с точным ID клиента ERP')
        return report
    report['student'] = student
    sid = student['id']
    operation_id = str(data.get('refund_operation_id') or '').strip()
    invoice_id = str(data.get('source_invoice_id') or '').strip()
    report.update(refund_operation_id=operation_id, source_invoice_id=invoice_id)
    amount_minor = _integer(data.get('amount_minor'))
    amount = amount_minor//100 if amount_minor and amount_minor % 100 == 0 else None
    if amount is None:
        issues.append('Нужна точная сумма возврата в копейках без округления до рублей')
    report['amount'] = amount
    if not operation_id:
        issues.append('Не указан точный ID уже выполненной операции возврата ERP')
    if not invoice_id:
        issues.append('Не указан ID исходного заказа или покупки ERP')
    executed_at = _executed(data.get('executed_at'))
    report['executed_at'] = executed_at
    if not executed_at:
        issues.append('Не указана фактическая дата исполнения возврата')
    if data.get('mapping_reviewed') is not True or len(str(data.get('reason') or '').strip()) < 5:
        issues.append('Подтвердите проверку исходных связей и укажите основание переноса')
    subid = _integer(data.get('subscription_id'))
    sub = con.execute('SELECT * FROM subscriptions WHERE id=? AND student_id=?', (subid, sid)).fetchone() if subid else None
    if not sub:
        issues.append('Не определена исходная покупка в этой карточке CRM')
    else:
        report['subscription'] = dict(sub)
        if sub['status'] == 'cancelled':
            issues.append('Покупка уже отменена; сначала сверьте существующую операцию')
        if amount and amount > sub['price']:
            issues.append('Возврат превышает оставшуюся стоимость исходной покупки')
        invoice = historicalrefundtrace.resolve_invoice(con, sid, invoice_id)
        if invoice.get('ambiguous') or _invoice_ids(invoice) != {subid}:
            issues.append('ID заказа ERP не связан однозначно с выбранной покупкой CRM')
    report['receipt'], receipt_issues = _receipt(con, data.get('receipt'), amount, namespace, operation_id)
    issues.extend(receipt_issues)
    proof = historicalrefundtrace.resolve_refund_operation(con, sid, operation_id) if operation_id else {}
    candidates = proof.get('candidates') or []
    if proof.get('ambiguous') or not candidates or len({(row.get('amount'),row.get('executed_at'),row.get('source_method'),row.get('source_invoice_id'),row.get('execution_confirmed')) for row in candidates}) != 1:
        issues.append('В архиве ERP не найдена единственная исходная операция этого возврата')
    else:
        source = candidates[0]
        report['source_refund'] = source
        if source.get('execution_confirmed') is not True:
            issues.append('Источник ERP не подтверждает фактическое исполнение возврата')
        if source.get('amount') != amount or source.get('source_invoice_id') != invoice_id:
            issues.append('Сумма или заказ возврата расходятся с исходной операцией ERP')
        if not _executed(source.get('executed_at')) or not executed_at or not _same_execution(source['executed_at'], executed_at):
            issues.append('Фактическое время возврата не подтверждено исходной операцией ERP')
        else:
            executed_at = source['executed_at']
            report['executed_at'] = executed_at
        if not source.get('evidence_id') or not source.get('source_signature'):
            issues.append('Не сохранено проверяемое доказательство исходной операции ERP')
        for field in ('fn','fd','fp'):
            source_value = str(source.get(field) or '').strip()
            if field in ('fd','fp') and source_value.isdigit():source_value = str(int(source_value))
            if source_value and source_value != report['receipt'].get(field):
                issues.append('Реквизиты чека расходятся с исходной операцией ERP: '+field.upper())
        source_method = _source_method(source.get('source_method'))
        report['source_method'] = source_method
        if source_method not in ('cash', 'card', 'online', 'paykeeper', 'sbp', 'transfer', 'card_branch', 'sber_qr_branch', 'tochka_qr_link'):
            issues.append('Не подтверждён исходный способ фактического возврата')
        elif report['receipt'].get('cashless_minor') is not None and amount:
            expected = 0 if source_method == 'cash' else amount*100
            if report['receipt']['cashless_minor'] != expected:
                issues.append('Форма расчёта чека отличается от исходного способа возврата')
    report['payment_allocations'] = []
    allocations = data.get('payment_allocations')
    if not isinstance(allocations, list) or not allocations:
        issues.append('Нужно проверить распределение исходных оплат между покупками и свободным остатком')
    else:
        seen = set()
        for item in allocations:
            if not isinstance(item, dict):
                issues.append('Некорректная строка распределения оплаты');continue
            pid, target, value = (_integer(item.get(key)) for key in ('payment_id', 'subscription_id', 'amount'))
            source_invoice = str(item.get('source_invoice_id') or '').strip()
            source_operation = str(item.get('source_operation_id') or '').strip()
            evidence = str(item.get('evidence_id') or '').strip()
            key = (pid, target)
            payment = con.execute('SELECT * FROM payments WHERE id=? AND student_id=?', (pid, sid)).fetchone() if pid else None
            target_row = con.execute('SELECT * FROM subscriptions WHERE id=? AND student_id=?', (target, sid)).fetchone() if target else None
            if not payment or payment['amount'] <= 0 or payment['cancelled'] or not payment['confirmed'] or not target_row or not value or key in seen:
                issues.append('Распределение содержит повтор, чужую, отменённую или неподтверждённую оплату');continue
            seen.add(key)
            invoice = historicalrefundtrace.resolve_invoice(con, sid, source_invoice)
            operation = historicalrefundtrace.resolve_payment_operation(con, sid, source_operation)
            if (invoice.get('ambiguous') or _invoice_ids(invoice) != {target} or operation.get('ambiguous')
                    or _payment_ids(operation) != {pid} or not evidence
                    or not _income_proof(con, sid, source_operation, source_invoice, payment, evidence)):
                issues.append('Распределение не подтверждено точными ID заказа и исходной операции ERP');continue
            report['payment_allocations'].append(dict(payment_id=pid, subscription_id=target, amount=value,
                source_namespace=namespace, source_invoice_id=source_invoice, source_operation_id=source_operation, evidence_id=evidence))
    report['refund_sources'] = []
    sources = data.get('refund_sources')
    if not isinstance(sources, list) or not sources:
        issues.append('Не проверены исходные оплаты и доли уже выполненного возврата')
    else:
        seen = set()
        for source in sources:
            if not isinstance(source, dict):issues.append('Некорректный источник возврата');continue
            pid, value = _integer(source.get('payment_id')), _integer(source.get('amount'))
            op = str(source.get('source_operation_id') or '').strip()
            proof = historicalrefundtrace.resolve_payment_operation(con, sid, op)
            payment = con.execute('SELECT * FROM payments WHERE id=? AND student_id=?', (pid, sid)).fetchone() if pid else None
            if (not payment or payment['amount'] <= 0 or payment['cancelled'] or not payment['confirmed'] or not value
                    or pid in seen or proof.get('ambiguous') or _payment_ids(proof) != {pid}
                    or not _income_proof(con, sid, op, invoice_id, payment)):
                issues.append('Источник возврата не подтверждён единственной исходной операцией ERP');continue
            seen.add(pid)
            report['refund_sources'].append(dict(payment_id=pid, subscription_id=subid, amount=value, source_operation_id=op))
        if amount and sum(source['amount'] for source in report['refund_sources']) != amount:
            issues.append('Сумма проверенных частей возврата не совпадает с выполненной операцией')
    receipt_provider_ids = set(report['receipt'].get('provider_payment_ids') or [])
    if receipt_provider_ids:
        expected_provider_ids = set()
        for source in report['refund_sources']:
            expected_provider_ids.update(str(row[0]) for row in con.execute("SELECT pk_payment_id FROM pay_links WHERE payment_id=? AND status='paid' AND pk_payment_id!=''", (source['payment_id'],)))
        if receipt_provider_ids != expected_provider_ids:
            issues.append('Существующий чек PayKeeper связан с другой исходной оплатой; перенос запрещён')
    if operation_id and _exists(con, 'erp_full_operations'):
        linked = con.execute('SELECT payment_id,signature FROM erp_full_operations WHERE operation_id=?', (operation_id,)).fetchone()
        if linked:
            payment = con.execute('SELECT * FROM payments WHERE id=?', (linked['payment_id'],)).fetchone()
            if (not payment or payment['student_id'] != sid or payment['cancelled'] or payment['method'] != 'refund' or payment['amount'] != -(amount or 0) or not executed_at or not _source_pay_date_matches(executed_at, payment['paid_at'])
                    or linked['signature'] != _json([sid,executed_at,-(amount or 0),report.get('source_method','')])):
                issues.append('ID возврата ERP уже связан с отличающейся финансовой записью')
            else:
                report['existing_refund_payment_id'] = payment['id']
    try:
        report['before_summary'] = _summary(con, sid, report)
    except (ValueError, HistoricalRefundError) as error:
        issues.append(str(error))
    report['state_fingerprint'] = _hash(_snapshot(con, sid))
    report['can_apply'] = not issues
    return report


def _clone(con):
    clone = sqlite3.connect(':memory:', isolation_level=None)
    clone.row_factory = sqlite3.Row
    clone.create_function('ulower', 1, lambda value: value.lower() if isinstance(value, str) else value)
    snapshot = bytearray(con.serialize())
    snapshot[18:20] = b'\x01\x01'  # A deserialized copy has no on-disk WAL.
    clone.deserialize(bytes(snapshot))
    clone.execute('PRAGMA foreign_keys=ON')
    return clone


def _execute(con, data, report, user, now):
    from . import paymentallocation
    sid, sub = report['student']['id'], report['subscription']
    before_income = _rows(con, 'SELECT * FROM payments WHERE student_id=? AND amount>0 ORDER BY id', (sid,))
    for item in report['payment_allocations']:
        paymentallocation.bind_payment(con, **item)
        # These proved invoices already have real source payments. Startup must
        # not synthesize another income merely because payments are pinned in
        # the allocation table rather than payments.subscription_id.
        con.execute('UPDATE subscriptions SET ledger_managed=1 WHERE id=?', (item['subscription_id'],))
    amount = report['amount']
    pid = report.get('existing_refund_payment_id')
    if not pid:
        pid = con.execute('''INSERT INTO payments(student_id,amount,method,paid_at,comment,user_name,confirmed,
            confirmed_by,confirmed_at,subscription_id,created_at,batch) VALUES (?,?,'refund',?,?,?,1,?,?,?,?,?)''',
            (sid, -amount, report['executed_at'], 'Исторический возврат ERP '+report['refund_operation_id']+': '+data['reason'],
             user['name'], user['name'], now, sub['id'], now, 'erp-refund:'+report['refund_operation_id'])).lastrowid
        source = report['source_refund']
        signature = _json([sid, report['executed_at'], -amount, report['source_method']])
        con.execute('INSERT INTO erp_full_operations(operation_id,payment_id,signature) VALUES (?,?,?)', (report['refund_operation_id'], pid, signature))
    report['refund_payment_id'] = pid
    for source in report['refund_sources']:
        paymentallocation.register_refund(con, pid, source['payment_id'], sub['id'], source['amount'])
    new_price = sub['price'] - amount
    if new_price:
        con.execute('UPDATE subscriptions SET price=?,corrected=1,ledger_managed=1 WHERE id=?', (new_price, sub['id']))
    else:
        con.execute("UPDATE subscriptions SET status='cancelled',ledger_managed=1 WHERE id=?", (sub['id'],))
        if _exists(con, 'single_visits'):
            con.execute("UPDATE single_visits SET status='cancelled' WHERE subscription_id=? AND status='booked'", (sub['id'],))
        groups = set()
        if sub['enrollment_id']:
            row = con.execute('SELECT group_id FROM enrollments WHERE id=? AND student_id=?', (sub['enrollment_id'], sid)).fetchone()
            if row and row['group_id']:groups.add(row['group_id'])
        try:
            for component in json.loads(sub['lesson_schedule'] or '[]'):
                if type(component.get('group_id')) is int and component['group_id'] > 0:groups.add(component['group_id'])
        except (ValueError, TypeError):
            pass
        for gid in sorted(groups):
            con.execute('INSERT OR IGNORE INTO historical_refund_exclusions VALUES (?,?,?,?,?)', (sub['id'], sid, gid, sub['month'], 'Полный исторический возврат ERP '+report['refund_operation_id']))
    receipt = report['receipt']
    fxid = receipt['fx_receipt_id']
    if fxid is None:
        fxid = con.execute('''INSERT INTO fx_receipts(source,ext_id,kkt,ts,amount,ecash,op,pk_status,fiscal_fn,fiscal_fd,fiscal_fp)
            VALUES ('historical_erp',?,?,?,?,?,2,'document',?,?,?)''',
            (receipt['fn']+':'+receipt['fd'], receipt['fn'], receipt['issued_at'], receipt['total_minor'], receipt['cashless_minor'], receipt['fn'], receipt['fd'], receipt['fp'])).lastrowid
    con.execute('''INSERT INTO historical_refund_receipts(fn,fd,fp,operation,total_minor,cashless_minor,issued_at,
        source_namespace,refund_operation_id,fx_receipt_id,evidence_kind) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
        (receipt['fn'],receipt['fd'],receipt['fp'],2,receipt['total_minor'],receipt['cashless_minor'],receipt['issued_at'],report['source_namespace'],report['refund_operation_id'],fxid,receipt['evidence_kind']))
    money._reconcile(con, sid)
    if before_income != _rows(con, 'SELECT * FROM payments WHERE student_id=? AND amount>0 ORDER BY id', (sid,)):
        raise HistoricalRefundError('Перенос изменил исходное поступление; изменения отменены')
    if _exists(con, 'history'):
        con.execute('INSERT INTO history(student_id,ts,user_name,kind,text) VALUES (?,?,?,?,?)',
            (sid,now,user['name'],'payment',f"В учёт внесён уже выполненный возврат ERP {report['refund_operation_id']}: {amount} ₽, исполнен {report['executed_at']}. "
             f"Покупка #{sub['id']}: {sub['price']} → {new_price} ₽. Новая выплата и новый чек не создавались."))


def plan(con, data):
    if isinstance(data, dict) and str(data.get('refund_operation_id') or '').strip():
        old = con.execute('SELECT * FROM historical_refund_runs WHERE source_namespace=? AND refund_operation_id=?',
            (str(data.get('source_namespace') or 'erp').strip(), str(data['refund_operation_id']).strip())).fetchone()
        if old:
            exact = old['request_signature'] == _signature(data)
            return {'can_apply': False, 'already_applied': exact, 'run_id': old['id'], 'state':'applied' if exact else 'conflict',
                    'issues': [] if exact else ['ID возврата ERP уже учтён с другими сведениями; повтор запрещён'],
                    'before_summary': json.loads(old['before_summary']), 'after_summary': json.loads(old['after_summary']),
                    'token': '', 'read_only': True, 'external_actions': False}
    report = _validate(con, data)
    if report['can_apply']:
        clone = _clone(con)
        try:
            _execute(clone, data, report, {'name':'Предварительная проверка исторического возврата'}, '2000-01-01T00:00:00')
            report['after_summary'] = _summary(clone, report['student']['id'], report)
            # The only net-money change is the historical debit. A fully returned
            # bill is no longer an obligation; unrelated real debt remains visible.
            expected_count = report['before_summary']['payment_count'] + (0 if report.get('existing_refund_payment_id') else 1)
            expected_paid = report['before_summary']['paid_total'] - (0 if report.get('existing_refund_payment_id') else report['amount'])
            if report['after_summary']['payment_count'] != expected_count or report['after_summary']['paid_total'] != expected_paid:
                raise HistoricalRefundError('Изменился исходный денежный итог вне исторического возврата')
        except (ValueError, sqlite3.Error, HistoricalRefundError) as error:
            report['issues'].append(str(error));report['can_apply'] = False;report['after_summary'] = None
        finally:
            clone.close()
    report['token'] = _hash({'data':data,'report':report})
    return report


preview = plan


@contextmanager
def _write(con):
    outer = con.in_transaction
    if not outer:con.execute('BEGIN IMMEDIATE')
    con.execute('SAVEPOINT historical_refund_apply')
    try:
        con.execute('UPDATE settings SET value=value WHERE 0')
        yield
        con.execute('RELEASE historical_refund_apply')
        if not outer:con.commit()
    except BaseException:
        con.execute('ROLLBACK TO historical_refund_apply');con.execute('RELEASE historical_refund_apply')
        if not outer:con.rollback()
        raise


def _verify_backup(con, backup_path, sid):
    path = Path(str(backup_path or ''))
    if not path.is_file() or path.stat().st_size == 0:
        raise HistoricalRefundError('Перед переносом нужна резервная копия именно текущей базы')
    try:
        check = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True);check.row_factory = sqlite3.Row
        try:
            if check.execute('PRAGMA quick_check').fetchone()[0] != 'ok' or _hash(_snapshot(check,sid)) != _hash(_snapshot(con,sid)):
                raise HistoricalRefundError('Резервная копия не соответствует текущим данным')
        finally:check.close()
    except sqlite3.Error as error:
        raise HistoricalRefundError('Не удалось проверить резервную копию') from error


def apply(con, data, token, user, backup_path):
    if (user or {}).get('role') != 'admin' or not str((user or {}).get('name') or '').strip():
        raise HistoricalRefundError('Перенос исторического возврата подтверждает только руководитель')
    with _write(con):
        report = plan(con, data)
        if report.get('already_applied'):
            return {'ok':True,'state':'applied','already_applied':True,'run_id':report['run_id'],'after_summary':report['after_summary']}
        if not token or report['token'] != token:
            raise HistoricalRefundError('Данные изменились после предварительной проверки; выполните её заново')
        if not report['can_apply']:
            raise HistoricalRefundError('; '.join(report['issues']))
        sid, sub = report['student']['id'], report['subscription']
        _verify_backup(con,backup_path,sid)
        before = report['state_fingerprint']
        now = datetime.now().isoformat(timespec='seconds')
        _execute(con,data,report,user,now)
        after_summary = _summary(con,sid,report)
        if _json(after_summary) != _json(report['after_summary']):
            raise HistoricalRefundError('Результат отличается от проверенной копии; изменения отменены')
        after = _hash(_snapshot(con,sid))
        new_price = sub['price']-report['amount']
        rid = con.execute('''INSERT INTO historical_refund_runs(source_namespace,refund_operation_id,student_id,subscription_id,
            source_invoice_id,amount,executed_at,refund_payment_id,request_signature,request_json,before_fingerprint,after_fingerprint,
            before_summary,after_summary,old_price,new_price,old_status,new_status,created_at,created_by,backup_path)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (report['source_namespace'],report['refund_operation_id'],sid,sub['id'],report['source_invoice_id'],report['amount'],report['executed_at'],report['refund_payment_id'],
             _signature(data),_json(data),before,after,_json(report['before_summary']),_json(after_summary),sub['price'],new_price,sub['status'],
             'cancelled' if new_price==0 else con.execute('SELECT status FROM subscriptions WHERE id=?',(sub['id'],)).fetchone()[0],now,user['name'],str(backup_path))).lastrowid
        return {'ok':True,'state':'applied','run_id':rid,'refund_payment_id':report['refund_payment_id'],
                'before_summary':report['before_summary'],'after_summary':after_summary,'external_actions':False}
