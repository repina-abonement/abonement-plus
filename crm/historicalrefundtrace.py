"""Read-only ERP invoice, payment and executed-refund provenance.

An amount, date, comment, hyperlink caption or PayKeeper invoice is never an
ERP identifier. Only explicit source fields and retained native crosswalks are
used. Nothing in this module calls a provider or writes to the database.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json

from . import erpimport as ei


ALIASES = {
    'client_id': ('id клиента', 'id посетителя', 'номер клиента в erp', 'id клиента erp', 'erp_client_id', 'erp_id'),
    'invoice_id': ('id счета', 'id заказа', 'invoice_id', 'order_id'),
    'sale_id': ('id продажи', 'id покупки', 'sale_id'),
    'sale_type': ('тип', 'тип продажи', 'вид продажи', 'тип абонемента', 'тариф'),
    'event_id': ('id мероприятия', 'id события', 'event_id'),
    'event_type': ('тип мероприятия', 'вид мероприятия', 'event_type'),
    'event_at': ('дата мероприятия', 'дата события', 'event_at'),
    'event_status': ('статус мероприятия', 'статус события', 'event_status'),
    'credit_amount': ('сумма зачета', 'сумма переноса', 'зачтено', 'credit_amount'),
    'credit_operation_id': ('id зачета', 'id операции зачета', 'id переноса', 'credit_operation_id'),
    'credit_status': ('статус зачета', 'статус переноса', 'credit_status'),
    'credit_target_invoice_id': ('id счета зачета', 'id целевого счета', 'credit_target_invoice_id'),
    'operation_id': ('id операции', 'id оплаты', 'operation_id'),
    'amount': ('сумма', 'сумма операции', 'сумма платежа', 'сумма возврата'),
    'price': ('стоимость', 'стоимость со скидкой', 'стоимость со скидкой, руб.(выручка для пиу)', 'сумма счета', 'сумма заказа'),
    'payment_type': ('тип платежа', 'тип операции', 'вид операции'),
    'method': ('вид оплаты', 'способ оплаты', 'форма оплаты', 'способ возврата'),
    'payment_at': ('дата оплаты', 'дата платежа', 'дата операции'),
    'executed_at': ('дата фактического возврата', 'дата исполнения возврата', 'дата исполнения', 'дата возврата'),
    'requested_at': ('дата запроса', 'дата заявки', 'дата запроса возврата'),
    'execution_status': ('статус возврата', 'статус операции', 'статус исполнения', 'статус'),
    'provider_payment_id': ('id платежа paykeeper', 'paykeeper payment id', 'provider_payment_id'),
    'fn': ('фн', 'номер фн', 'fn'),
    'fd': ('фд', 'номер фд', 'fd'),
    'fp': ('фп', 'фпд', 'фискальный признак', 'fp', 'fpd'),
}
NORMALIZED_ALIASES = {key: {ei._norm(label) for label in values} for key, values in ALIASES.items()}
EXECUTED = {'исполнен', 'исполнено', 'исполнена', 'выполнен', 'выполнено', 'выполнена',
            'проведен', 'проведено', 'проведена', 'завершен', 'завершено', 'завершена',
            'успешно', 'возвращен', 'возвращено', 'возврат выполнен', 'возврат проведен',
            'done', 'completed', 'executed', 'success'}
REFUND_TYPES = {'возврат', 'возврат оплаты', 'возврат платежа', 'возврат денежных средств', 'refund'}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _exists(con, table):
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _dicts(con, sql, args=()):
    return [dict(row) for row in con.execute(sql, args)]


def _amount(raw):
    try:
        value = Decimal(str(raw).replace(' ', '').replace('\u00a0', '').replace('₽', '').replace(',', '.'))
        return int(value) if value.is_finite() and value == value.to_integral_value() else None
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        return None


def _date(raw):
    try:
        value = ei.parse_date(raw)
        if value:
            datetime.fromisoformat(value)
        return value
    except (TypeError, ValueError, OverflowError):
        return None


def _fields(headers, row):
    result = {key: [] for key in ALIASES}
    for index, header in enumerate(headers):
        value = str(row[index] if index < len(row) and row[index] is not None else '').strip()
        if not value:
            continue
        label = ei._norm(str(header))
        for key, aliases in NORMALIZED_ALIASES.items():
            if label in aliases:
                result[key].append({'field': str(header), 'value': value})
    return result


def _values(record, key):
    return sorted({item['value'] for item in record['fields'].get(key, [])})


def _one(record, key):
    values = _values(record, key)
    return values[0] if len(values) == 1 else None


def _student(con, erp_id):
    if not str(erp_id or '').strip():
        return None, ['Укажите точный ID клиента ERP; карточка без ID не выбирается автоматически']
    ids = {row[0] for row in con.execute('SELECT id FROM students WHERE erp_id=?', (str(erp_id).strip(),))}
    if _exists(con, 'erp_full_identities'):
        ids.update(row[0] for row in con.execute("SELECT student_id FROM erp_full_identities WHERE namespace='student' AND source_id=?", (str(erp_id).strip(),)))
    if len(ids) != 1:
        return None, ['Карточка по ID клиента ERP не найдена однозначно; выбор по имени, сумме или дате не выполняется']
    row = con.execute('SELECT id,name,erp_id FROM students WHERE id=?', (next(iter(ids)),)).fetchone()
    return (dict(row), []) if row else (None, ['Связь ERP ведёт к недоступной карточке'])


def _records(con, sid):
    """Return only this child's strictly identified financial source fields."""
    student = con.execute('SELECT erp_id FROM students WHERE id=?', (sid,)).fetchone()
    if not student:
        return [], ['Карточка клиента не найдена']
    erp_ids = {str(student[0]).strip()} if student[0] else set()
    if _exists(con, 'erp_full_identities'):
        erp_ids.update(str(row[0]) for row in con.execute("SELECT source_id FROM erp_full_identities WHERE namespace='student' AND student_id=?", (sid,)))
    records, issues = [], []
    if _exists(con, 'erp_full_files') and _exists(con, 'erp_full_rows'):
        for source in con.execute('SELECT * FROM erp_full_files ORDER BY id'):
            try:
                headers, rows = json.loads(source['headers']), json.loads(source['rows'])
            except (TypeError, ValueError):
                continue
            links = {row['row_num']: dict(row) for row in con.execute('SELECT * FROM erp_full_rows WHERE file_id=?', (source['id'],))}
            client_columns = [index for index, label in enumerate(headers) if ei._norm(str(label)) in NORMALIZED_ALIASES['client_id']]
            for number, raw in enumerate(rows, 2):
                if not isinstance(raw, list):
                    continue
                client_ids = {str(raw[index]).strip() for index in client_columns if index < len(raw) and raw[index] is not None and str(raw[index]).strip()}
                link = links.get(number, {})
                assigned = link.get('student_id')
                identified = bool(client_ids & erp_ids)
                if assigned != sid and not identified:
                    continue
                if client_ids and not identified:
                    issues.append('Сохранённая строка источника этой карточки содержит другой ID клиента; она не используется как доказательство')
                    continue
                fields = _fields(headers, raw)
                owned = assigned in (None, sid)
                record = {'store': 'erp_full_files', 'file_id': source['id'], 'row_num': number,
                          'source_kind': source['kind'], 'digest': source['digest'],
                          'state': link.get('state', 'source'), 'fields': fields,
                          'owner_matches': owned, 'target_table': '', 'target_id': None}
                target_table, target_id = link.get('target_table'), link.get('target_id')
                if owned and record['state'] == 'native' and target_table in ('payments', 'subscriptions'):
                    native = con.execute('SELECT student_id FROM '+target_table+' WHERE id=?', (target_id,)).fetchone()
                    if native and native['student_id'] == sid:
                        record.update(target_table=target_table, target_id=target_id)
                    else:
                        record['owner_matches'] = False
                record['source_signature'] = _hash([source['digest'], number, fields, record['state'], record['target_table'], record['target_id']])
                record['evidence_id'] = 'erp-file:'+str(source['id'])+':row:'+str(number)
                record['evidence'] = [item for values in fields.values() for item in values]
                records.append(record)
    if _exists(con, 'erp_single_sales'):
        for sale in con.execute('SELECT * FROM erp_single_sales WHERE student_id=? ORDER BY source_id', (sid,)):
            native = con.execute('SELECT student_id FROM subscriptions WHERE id=?', (sale['subscription_id'],)).fetchone()
            if not native or native['student_id'] != sid:
                issues.append('Сохранённая разовая продажа имеет конфликтующую связь с карточкой; чужая покупка не выдаётся')
                continue
            try:
                payload = json.loads(sale['source_payload'] or '{}')
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            fields = {key: [] for key in ALIASES}
            for key, names in {'invoice_id': ('invoice_id', 'source_invoice_id'),
                               'sale_id': ('sale_id',), 'operation_id': ('operation_id',),
                               'provider_payment_id': ('provider_payment_id',)}.items():
                for name in names:
                    if payload.get(name):
                        fields[key].append({'field': 'source_payload.'+name, 'value': str(payload[name]).strip()})
            record = {'store': 'erp_single_sales', 'source_kind': 'subs', 'source_id': sale['source_id'],
                      'file_id': None, 'row_num': None, 'state': 'native', 'fields': fields,
                      'owner_matches': True, 'target_table': 'subscriptions', 'target_id': sale['subscription_id']}
            record['source_signature'] = _hash([sale['source_id'], sale['subscription_id'], payload])
            record['evidence_id'] = 'erp-single:'+sale['source_id']
            record['evidence'] = [item for values in fields.values() for item in values]
            records.append(record)
    return records, list(dict.fromkeys(issues))


def _candidate(record):
    result = {key: record.get(key) for key in ('store', 'source_kind', 'source_id', 'file_id', 'row_num', 'state', 'evidence_id', 'source_signature')}
    result.update(evidence=record['evidence'], invoice_id=_one(record, 'invoice_id'),
                  source_invoice_id=_one(record, 'invoice_id'),
                  source_price=_amount(_one(record, 'price')),
                  amount=_amount(_one(record, 'amount')), paid_at=_date(_one(record, 'payment_at')),
                  source_method=_one(record, 'method'),
                  sale_id=_one(record, 'sale_id'), operation_id=_one(record, 'operation_id'),
                  subscription_id=record['target_id'] if record['target_table'] == 'subscriptions' else None,
                  payment_id=record['target_id'] if record['target_table'] == 'payments' else None)
    return result


def _invoice(con, sid, invoice_id, records):
    invoice_id = str(invoice_id).strip()
    matching = [record for record in records if invoice_id in _values(record, 'invoice_id')]
    candidates = [_candidate(record) for record in matching]
    sub_ids = sorted({row['subscription_id'] for row in candidates if row['subscription_id'] is not None})
    payment_ids = sorted({row['payment_id'] for row in candidates if row['payment_id'] is not None})
    sale_ids = {_one(record, 'sale_id') for record in matching if _one(record, 'sale_id')}
    inverse_invoices = {value for record in records if record['target_table'] == 'subscriptions' and record['target_id'] in sub_ids for value in _values(record, 'invoice_id')}
    # Several partial payments or refunds may legitimately belong to one
    # invoice. Their operation identities are checked separately; they do not
    # make the invoice's purchase identity ambiguous.
    ambiguous = len(sub_ids) > 1 or len(sale_ids) > 1 or len(inverse_invoices) > 1 or any(len(_values(record, 'invoice_id')) != 1 or not record['owner_matches'] for record in matching)
    issues = []
    if not matching:
        issues.append('Нет сохранённого явного ID счёта или заказа ERP '+invoice_id+'; сумма, дата, комментарий и счёт PayKeeper не используются для поиска')
    elif not sub_ids:
        issues.append('ID счёта ERP найден в источнике, но точная связь с покупкой CRM не сохранена')
    if ambiguous:
        issues.append('Счёт ERP связан с несколькими строками состава или конфликтующими записями; автоматический выбор покупки недоступен')
    return {'source_namespace': 'erp', 'invoice_id': invoice_id, 'subscription_ids': sub_ids,
            'payment_ids': payment_ids, 'candidates': candidates, 'ambiguous': ambiguous,
            'inverse_invoice_ids': sorted(inverse_invoices), 'exact': len(sub_ids) == 1 and not ambiguous, 'issues': issues}


def resolve_invoice(con, sid, invoice_id):
    records, issues = _records(con, sid)
    result = _invoice(con, sid, invoice_id, records)
    result['issues'].extend(issues)
    return result


def _operation(con, sid, operation_id, records):
    operation_id = str(operation_id).strip()
    matching = [record for record in records if operation_id in _values(record, 'operation_id')]
    candidates = [_candidate(record) for record in matching]
    mapped, foreign = set(), False
    if _exists(con, 'erp_full_operations'):
        for row in con.execute('''SELECT o.*,p.student_id FROM erp_full_operations o
                JOIN payments p ON p.id=o.payment_id WHERE o.operation_id=?''', (operation_id,)):
            if row['student_id'] != sid:
                foreign = True
                continue
            mapped.add(row['payment_id'])
            candidates.append({'store': 'erp_full_operations', 'operation_id': operation_id,
                'payment_id': row['payment_id'], 'subscription_id': None, 'state': 'native',
                'file_id': None, 'row_num': None, 'evidence_id': 'erp-operation:'+operation_id,
                'source_signature': _hash([operation_id, row['payment_id'], row['signature']]),
                'evidence': [{'field': 'erp_full_operations.operation_id', 'value': operation_id}]})
    raw_native = {row['payment_id'] for row in candidates if row.get('payment_id') is not None}
    # Native IDs from source rows remain candidates; the explicit ERP operation
    # crosswalk is required before the operation is declared resolved.
    ambiguous = foreign or len(mapped) > 1 or bool(mapped and raw_native - mapped) or any(not row['owner_matches'] for row in matching)
    invoice_ids = {_one(record, 'invoice_id') for record in matching if _one(record, 'invoice_id')}
    raw_facts = {(_one(record, 'invoice_id'), _one(record, 'amount'), _one(record, 'payment_at'), _one(record, 'method'))
                 for record in matching if record['source_kind'] == 'payments'}
    ambiguous = ambiguous or len(invoice_ids) > 1 or len(raw_facts) > 1
    related_subs = {row['subscription_id'] for row in candidates if row.get('subscription_id') is not None}
    for invoice_id in invoice_ids:
        invoice = _invoice(con, sid, invoice_id, records)
        related_subs.update(invoice['subscription_ids'])
        ambiguous = ambiguous or invoice['ambiguous']
    # The raw operation, source invoice, original gross and original date stay
    # on one candidate. Joining independent date/amount matches is forbidden.
    if len(mapped) == 1 and not ambiguous:
        for candidate in candidates:
            if candidate.get('store') == 'erp_full_files' and candidate.get('source_kind') == 'payments' and candidate.get('payment_id') is None:
                candidate['payment_id'] = next(iter(mapped))
    issues = []
    if not mapped:
        issues.append('Нет точной сохранённой связи ID операции ERP с оплатой этой карточки')
    raw_complete = [record for record in matching if record['source_kind'] == 'payments' and
                    _one(record, 'invoice_id') and _amount(_one(record, 'amount')) is not None and
                    _date(_one(record, 'payment_at')) and _one(record, 'method')]
    source_relation_confirmed = bool(len(mapped) == 1 and not ambiguous and raw_complete)
    if mapped and not source_relation_confirmed:
        issues.append('Связь операции с оплатой сохранена, но исходные ID счёта, сумма, дата или способ оплаты ERP не подтверждены одной строкой')
    if ambiguous:
        issues.append('ID операции ERP имеет конфликтующую связь; чужие оплаты не выдаются')
    return {'source_namespace': 'erp', 'operation_id': operation_id, 'payment_ids': sorted(mapped),
            'subscription_ids': sorted(related_subs), 'source_invoice_ids': sorted(invoice_ids),
            'candidates': candidates, 'ambiguous': ambiguous,
            'exact': len(mapped) == 1 and not ambiguous, 'source_relation_confirmed': source_relation_confirmed, 'issues': issues}


def resolve_payment_operation(con, sid, operation_id):
    records, issues = _records(con, sid)
    result = _operation(con, sid, operation_id, records)
    result['issues'].extend(issues)
    return result


def resolve_refund_operation(con, sid, operation_id):
    records, issues = _records(con, sid)
    operation_id = str(operation_id).strip()
    matching = [row for row in records if operation_id in _values(row, 'operation_id')]
    candidates = []
    for record in matching:
        if record['source_kind'] != 'payments':
            continue
        signed = _amount(_one(record, 'amount'))
        operation_type = ei._norm(_one(record, 'payment_type') or '')
        status = ei._norm(_one(record, 'execution_status') or '')
        explicit_date = _one(record, 'executed_at')
        executed_at = _date(explicit_date or _one(record, 'payment_at'))
        invoice_id = _one(record, 'invoice_id')
        confirmed = bool(record['owner_matches'] and signed is not None and signed < 0 and
                         operation_type in REFUND_TYPES and executed_at and status in EXECUTED and invoice_id)
        row = _candidate(record)
        row.update(amount=abs(signed) if signed is not None and signed < 0 else None,
                   source_signed_amount=signed, executed_at=executed_at,
                   source_method=_one(record, 'method'), source_invoice_id=invoice_id,
                   execution_confirmed=confirmed,
                   fn=_one(record, 'fn'), fd=_one(record, 'fd'), fp=_one(record, 'fp'),
                   date_precision='datetime' if executed_at and len(executed_at) > 10 else 'date' if executed_at else None)
        candidates.append(row)
    identities = {(row['amount'], row['executed_at'], row['source_method'], row['source_invoice_id'], row['execution_confirmed'], row['fn'], row['fd'], row['fp']) for row in candidates}
    ambiguous = len(identities) > 1 or any(not row['owner_matches'] or any(len(_values(row, key)) > 1 for key in ('fn', 'fd', 'fp')) for row in matching)
    confirmed = bool(candidates and not ambiguous and all(row['execution_confirmed'] for row in candidates))
    if not confirmed:
        issues.append('Исполненный возврат не подтверждён исходной строкой: нужны отрицательная операция «Возврат», ID счёта, дата исполнения и явный исполненный статус; запрос и чек не заменяют факт возврата')
    return {'source_namespace': 'erp', 'operation_id': operation_id, 'candidates': candidates,
            'ambiguous': ambiguous, 'execution_confirmed': confirmed,
            'exact': confirmed, 'issues': list(dict.fromkeys(issues))}


def _allocation(con, sid):
    try:
        from . import paymentallocation
    except ImportError:
        from . import money
        obligations = money.obligations(con, sid)
        payments = _dicts(con, 'SELECT * FROM payments WHERE student_id=? AND cancelled=0 ORDER BY paid_at,id', (sid,))
        budget = max(0, sum(row['amount'] for row in payments))
        sources = [[row['id'], min(max(0, row['amount']), budget)] for row in payments if row['amount'] > 0]
        remaining = budget
        for source in sources:
            source[1] = min(source[1], remaining)
            remaining -= source[1]
        allocation, covered, index = {}, {}, 0
        for obligation in obligations:
            need, parts = obligation['price'], []
            while need > 0 and index < len(sources):
                pid, available = sources[index]
                take = min(need, available)
                if take:
                    parts.append({'payment_id': pid, 'amount': take})
                    need -= take
                    sources[index][1] -= take
                if sources[index][1] <= 0:
                    index += 1
            allocation[obligation['id']] = parts
            covered[obligation['id']] = obligation['price'] - need
        return {'kind': 'legacy_fifo', 'obligations': obligations, 'sources': allocation,
                'covered': covered, 'paid_total': sum(row['amount'] for row in payments),
                'deposit': sum(row[1] for row in sources), 'conflicts': []}
    return {'kind': 'shared_allocation', **paymentallocation.allocation(con, sid)}


def trace(con, erp_id):
    student, issues = _student(con, erp_id)
    result = {'read_only': True, 'provenance': 'current_connection', 'source_namespace': 'erp',
              'erp_id': str(erp_id or '').strip(), 'student': student, 'payments': [], 'purchases': [],
              'retained_charges': [], 'source_rows': [], 'invoice_resolutions': [],
              'operation_resolutions': [], 'receipt_candidates': [], 'issues': issues}
    if not student:
        return result
    sid = student['id']
    records, source_issues = _records(con, sid)
    result['source_rows'] = records
    result['issues'].extend(source_issues)
    invoices = sorted({value for record in records for value in _values(record, 'invoice_id')})
    operations = {value for record in records for value in _values(record, 'operation_id')}
    if _exists(con, 'erp_full_operations'):
        operations.update(row[0] for row in con.execute('SELECT operation_id FROM erp_full_operations WHERE payment_id IN (SELECT id FROM payments WHERE student_id=?)', (sid,)))
    result['invoice_resolutions'] = [_invoice(con, sid, invoice, records) for invoice in invoices]
    result['operation_resolutions'] = [_operation(con, sid, operation, records) for operation in sorted(operations)]
    for resolution in result['invoice_resolutions'] + result['operation_resolutions']:
        result['issues'].extend(resolution['issues'])
    if not invoices:
        result['issues'].append('В сохранённых источниках этой карточки нет явных ID счетов ERP; поиск по сумме, дате или фамилии не выполняется')
    result['payments'] = _dicts(con, 'SELECT * FROM payments WHERE student_id=? ORDER BY paid_at,id', (sid,))
    result['purchases'] = _dicts(con, 'SELECT * FROM subscriptions WHERE student_id=? ORDER BY month,id', (sid,))
    for payment in result['payments']:
        payment['source_operations'] = [row['operation_id'] for row in result['operation_resolutions'] if payment['id'] in row['payment_ids']]
        payment['source_invoice_candidates'] = [row for resolution in result['invoice_resolutions'] for row in resolution['candidates'] if row['payment_id'] == payment['id']]
        payment['system_marker'] = payment.get('user_name') == 'Система' and payment.get('comment') == 'Оплата до реестра платежей'
        payment['source_evidence_status'] = 'erp_operation_mapped' if payment['source_operations'] else 'no_erp_operation_mapping'
        payment['source_relation_confirmed'] = any(row['source_relation_confirmed'] for row in result['operation_resolutions'] if payment['id'] in row['payment_ids'])
        payment['duplicate_status'] = 'unverified'
    for purchase in result['purchases']:
        purchase['source_invoice_candidates'] = [row for resolution in result['invoice_resolutions'] for row in resolution['candidates'] if row['subscription_id'] == purchase['id']]
    if _exists(con, 'cancelled_service_charges'):
        result['retained_charges'] = _dicts(con, 'SELECT * FROM cancelled_service_charges WHERE student_id=? ORDER BY month,subscription_id', (sid,))
    result['allocation'] = _allocation(con, sid)
    result['issues'].extend(result['allocation'].get('conflicts', []))
    # Receipt lookup is restricted to explicit provider IDs already belonging
    # to this child. Amount/date scans across other clients are forbidden.
    provider_ids = {value for record in records if record['owner_matches'] for value in _values(record, 'provider_payment_id')}
    if _exists(con, 'pay_links'):
        provider_ids.update(row[0] for row in con.execute("SELECT pk_payment_id FROM pay_links WHERE student_id=? AND pk_payment_id!=''", (sid,)))
    if _exists(con, 'fx_receipts'):
        receipts = {}
        for provider_id in sorted(provider_ids):
            for row in con.execute('SELECT * FROM fx_receipts WHERE pk_payment=? ORDER BY id', (provider_id,)):
                receipts[row['id']] = dict(row)
        columns = {row[1] for row in con.execute('PRAGMA table_info(fx_receipts)')}
        if {'fiscal_fn', 'fiscal_fd', 'fiscal_fp'} <= columns:
            for record in records:
                if not record['owner_matches']:
                    continue
                fn, fd, fp = (_one(record, key) for key in ('fn', 'fd', 'fp'))
                if not fn or not fd:
                    continue
                for row in con.execute('SELECT * FROM fx_receipts WHERE fiscal_fn=? AND fiscal_fd=? ORDER BY id', (fn, fd)):
                    if fp and row['fiscal_fp'] != fp:
                        continue
                    receipts[row['id']] = dict(row)
        for receipt in receipts.values():
            receipt.update(fn=receipt.get('fiscal_fn', ''), fd=receipt.get('fiscal_fd', ''),
                           fp=receipt.get('fiscal_fp', ''), issued_at=receipt.get('ts'),
                           total_minor=receipt['amount'], cashless_minor=receipt['ecash'],
                           operation=receipt['op'])
        result['receipt_candidates'] = list(receipts.values())
    result['issues'] = list(dict.fromkeys(result['issues']))
    return result


context = trace
