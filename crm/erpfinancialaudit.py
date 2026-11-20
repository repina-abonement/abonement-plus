"""Read-only audit of imported money, technical backfills and event carry.

The report prepares evidence for review. It never applies a correction, creates
cash, invokes a provider, or treats a user's expected amount as an observed fact.
Any proposed after-state is calculated only in an isolated in-memory copy.
"""
from datetime import date
import sqlite3

from . import erpimport, historicalrefundtrace as trace, money, paymentallocation


OWNER_SNAPSHOT = {'erp_id': '117559', 'amount': 0, 'currency': 'RUB', 'as_of_date': '2026-11-20',
    'date_precision': 'day', 'cutoff_date': '2026-11-02',
    'provenance': 'owner_confirmation', 'confirmed': True, 'history_complete': False,
    'live_erp_read': False, 'evidence_id': 'owner:erp117559:balance:2026-11-20'}


def resolve_balance_snapshot(erp_id, evidence=None):
    """Validate explicit balance evidence; absence is never interpreted as zero.

    This pure helper does not discover, save or activate an import policy. An
    export must actually supply the balance field/value and an explicit as-of
    date. A known owner's later confirmation outranks an older supplied export.
    """
    erp_id = str(erp_id).strip()
    baseline = dict(OWNER_SNAPSHOT) if erp_id == OWNER_SNAPSHOT['erp_id'] else None
    if evidence is None:
        return baseline
    if not isinstance(evidence, dict):
        raise ValueError('Нужен явный снимок баланса с источником и датой')
    if (str(evidence.get('erp_id', '')).strip() != erp_id or type(evidence.get('amount')) is not int
            or evidence['amount'] < 0 or evidence.get('currency') != 'RUB'
            or evidence.get('confirmed') is not True
            or evidence.get('provenance') not in ('owner_confirmation', 'erp_export')
            or not isinstance(evidence.get('evidence_id'), str) or not evidence['evidence_id'].strip()):
        raise ValueError('Не подтверждены ID клиента, сумма, валюта или источник снимка баланса')
    for key in ('as_of_date', 'cutoff_date'):
        try:
            value = evidence[key]
            if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise ValueError('Для снимка нужны точные даты баланса и границы истории')
    if evidence['cutoff_date'] > evidence['as_of_date']:
        raise ValueError('Граница истории не может быть позже снимка баланса')
    if evidence['provenance'] == 'erp_export' and (
            not isinstance(evidence.get('source_field'), str) or not evidence['source_field'].strip()
            or 'source_value' not in evidence or trace._amount(evidence['source_value']) != evidence['amount']):
        raise ValueError('В исходной выгрузке должно явно присутствовать поле баланса с этой суммой')
    result = {k: evidence[k] for k in ('amount', 'currency', 'as_of_date', 'cutoff_date', 'provenance', 'evidence_id')}
    result.update(erp_id=erp_id, confirmed=True, history_complete=False, date_precision='day', live_erp_read=False)
    if evidence['provenance'] == 'erp_export':
        result.update(source_field=evidence['source_field'], source_value=evidence['source_value'])
    if baseline and baseline['as_of_date'] > result['as_of_date']:
        return dict(baseline, ignored_older_evidence_id=result['evidence_id'])
    if baseline and baseline['as_of_date'] == result['as_of_date'] and baseline['amount'] != result['amount']:
        raise ValueError('Сумма снимка противоречит подтверждению владельца на ту же дату')
    return result


def _one(record, key):
    return trace._one(record, key)


def _amount(record, *keys):
    values = {trace._amount(value) for key in keys for value in trace._values(record, key)}
    return next(iter(values)) if len(values) == 1 and None not in values else None


def _purchase_amount_proved(source, subid, invoice_id):
    purchase = next((r for r in source['purchases'] if r['id'] == subid), None)
    rows = [r for r in source['source_rows'] if r.get('source_kind') == 'subs'
            and r.get('target_table') == 'subscriptions' and r.get('target_id') == subid
            and _one(r, 'invoice_id') == invoice_id and r.get('owner_matches')]
    amounts = {_amount(r, 'price') for r in rows}
    return bool(purchase and amounts == {purchase['price']})


def _summary(con, sid):
    return {'balance': money.balance(con, sid), 'allocation': paymentallocation.allocation(con, sid)}


def _verified_links(source):
    """Resolve only a complete, internally consistent operation/invoice row."""
    payments = {p['id']: p for p in source['payments']}
    invoices = {r['invoice_id']: r for r in source['invoice_resolutions']}
    links, issues = [], []
    for operation in source['operation_resolutions']:
        if not operation.get('source_relation_confirmed') or not operation.get('exact'):
            continue
        ids = operation['payment_ids']
        invoice_ids = operation.get('source_invoice_ids', [])
        if len(ids) != 1 or len(invoice_ids) != 1:
            continue
        payment = payments.get(ids[0])
        invoice = invoices.get(invoice_ids[0])
        if (not payment or payment['cancelled'] or payment['amount'] <= 0
                or not invoice or not invoice.get('exact')):
            continue
        if not _purchase_amount_proved(source, invoice['subscription_ids'][0], invoice_ids[0]):
            issues.append('Стоимость исходной покупки ERP не подтверждает текущее начисление')
            continue
        candidates = [r for r in operation['candidates'] if r.get('source_kind') == 'payments'
                      and r.get('source_invoice_id') == invoice_ids[0]
                      and r.get('operation_id') == operation['operation_id']]
        exact = [r for r in candidates if r.get('amount') == payment['amount']
                 and r.get('paid_at') and payment['paid_at'].startswith(r['paid_at'])
                 and r.get('source_method') and erpimport.pay_method(r['source_method']) == payment['method']]
        if not exact or any(r not in exact for r in candidates):
            issues.append('Сумма, дата или способ исходной операции не совпадают с сохранённой оплатой')
            continue
        links.append({'payment_id': payment['id'], 'subscription_id': invoice['subscription_ids'][0],
                      'amount': payment['amount'], 'source_operation_id': operation['operation_id'],
                      'source_invoice_id': invoice_ids[0],
                      'evidence_id': sorted(r['evidence_id'] for r in exact)[0],
                      'evidence': [r for r in exact]})
    # A second operation identity is not a second copy of the same cash row.
    for pid in {r['payment_id'] for r in links}:
        own = [r for r in links if r['payment_id'] == pid]
        if len(own) > 1:
            links = [r for r in links if r['payment_id'] != pid]
            issues.append('Одна оплата связана с несколькими ID операций; нужен разбор источника')
    return links, list(dict.fromkeys(issues))


def _synthetic_candidates(source, links):
    purchases = {r['id']: r for r in source['purchases']}
    candidates = []
    for p in source['payments']:
        if not (p['user_name'] == 'Система' and p['comment'] == 'Оплата до реестра платежей'
                and p['method'] == 'unknown' and p['subscription_id']):
            continue
        sub = purchases.get(p['subscription_id'])
        related = [r for r in links if r['subscription_id'] == p['subscription_id'] and r['payment_id'] != p['id']]
        blockers = []
        if p['cancelled']:
            blockers.append('Техническая строка уже отменена; повторное исключение не требуется')
        if not sub or sub['status'] == 'cancelled' or sub['price'] != p['amount'] or p['amount'] <= 0:
            blockers.append('Сумма технической строки не подтверждена действующей исходной покупкой')
        if p.get('source_operations') or any(r['payment_id'] == p['id'] for r in links):
            blockers.append('Эта строка уже представляет операцию источника; исключать исходное поступление нельзя')
        invoices = {r['source_invoice_id'] for r in related}
        if len(invoices) != 1 or not sub or sum(r['amount'] for r in related) != sub['price']:
            blockers.append('Отдельные реальные поступления по тому же ID счёта не покрывают покупку однозначно')
        candidates.append({'payment_id': p['id'], 'subscription_id': p['subscription_id'],
            'amount': p['amount'], 'paid_at': p['paid_at'], 'created_at': p['created_at'],
            'classification': 'source_supported_duplicate' if not blockers else 'unproven_legacy',
            'fingerprint': {'method': p['method'], 'comment': p['comment'], 'user_name': p['user_name']},
            'related_payment_ids': sorted({r['payment_id'] for r in related}),
            'source_operation_ids': sorted({r['source_operation_id'] for r in related}),
            'source_invoice_ids': sorted(invoices),
            'evidence': [item for r in related for item in r['evidence']], 'blockers': blockers})
    return candidates


def _cross_period(source, month, links):
    payments = {p['id']: p for p in source['payments']}
    purchases = {r['id']: r for r in source['purchases']}
    allocated = source['allocation']
    locked = set(allocated.get('payment_locks', []))
    verified = {(r['payment_id'], r['subscription_id']) for r in links}
    result = []
    for key, parts in allocated.get('sources', {}).items():
        subid = int(key)
        sub = purchases.get(subid)
        if not sub or month and sub['month'] != month:
            continue
        for part in parts:
            payment = payments.get(part['payment_id'])
            if not payment or payment['paid_at'][:7] == sub['month']:
                continue
            result.append({'subscription_id': subid, 'month': sub['month'], 'payment_id': payment['id'],
                'source_month': payment['paid_at'][:7], 'amount': part['amount'],
                'kind': 'explicit' if payment['id'] in locked else 'fifo',
                'proof_status': 'source_confirmed' if (payment['id'], subid) in verified else 'unverified',
                'note': 'Ранняя дата поступления сама по себе не доказывает ошибку зачёта'})
    return result


def _event_carry(source):
    events = {}
    for record in source['source_rows']:
        event_type = _one(record, 'event_type') or _one(record, 'sale_type') or ''
        if 'турнир' not in erpimport._norm(event_type):
            continue
        ids = {key: _one(record, key) for key in ('event_id', 'sale_id', 'invoice_id', 'operation_id')}
        identity = next(((key, ids[key]) for key in ('event_id', 'sale_id', 'invoice_id') if ids[key]), None)
        if not identity:
            continue
        amount = _amount(record, 'credit_amount')
        if amount is None:
            amount = _amount(record, 'price', 'amount')
        day = trace._date(_one(record, 'event_at'))
        target = _one(record, 'credit_target_invoice_id')
        fact = (amount, day, target)
        item = events.setdefault(identity, {'source_identity': dict([identity]), 'source_ids': {},
            'facts': set(), 'evidence_ids': set(), 'event_statuses': set(), 'credit_operation_ids': set()})
        item['facts'].add(fact)
        item['evidence_ids'].add(record['evidence_id'])
        for key, value in ids.items():
            if value:
                item['source_ids'].setdefault(key, set()).add(value)
        for key, target_key in (('event_status', 'event_statuses'), ('credit_operation_id', 'credit_operation_ids')):
            value = _one(record, key)
            if value:
                item[target_key].add(value)
    candidates = []
    for identity, item in sorted(events.items()):
        conflicts = []
        if len(item['facts']) != 1:
            conflicts.append('У одного ID мероприятия различаются сумма, дата или целевой счёт')
        fact = next(iter(item['facts'])) if len(item['facts']) == 1 else (None, None, None)
        if fact[0] is None or fact[0] <= 0 or not fact[1]:
            conflicts.append('Не подтверждены точная сумма и дата мероприятия')
        # No persisted exactly-once event-carry accounting model exists yet.
        # Generic flex absence discounts are a different contract entirely.
        conflicts.append('Нет сохранённой связи однократного переноса: исходная оплата, мероприятие и целевой счёт требуют сверки')
        candidates.append({'source_identity': item['source_identity'],
            'source_ids': {k: sorted(v) for k, v in item['source_ids'].items()},
            'amount': fact[0], 'event_at': fact[1], 'target_invoice_id': fact[2],
            'event_statuses': sorted(item['event_statuses']),
            'credit_operation_ids': sorted(item['credit_operation_ids']),
            'evidence_ids': sorted(item['evidence_ids']), 'state': 'blocked', 'blockers': conflicts})
    return {'expected': [], 'source_candidates': candidates,
            'plans': [{'action': 'verify_one_time_carry', 'source_identity': r['source_identity'],
                       'amount': r['amount'], 'state': 'blocked', 'financial_effect_applied': False,
                       'blockers': r['blockers']} for r in candidates],
            'state': 'blocked' if candidates else 'not_observed',
            'blockers': list(dict.fromkeys(reason for r in candidates for reason in r['blockers']))}


def _post_cutoff(con, source, links, snapshot):
    """Keep receipts/refunds separate from a free-balance snapshot, never add them twice."""
    verified = {r['payment_id']: r for r in links}
    operations = {r['operation_id']: r for r in source['operation_resolutions']}
    rows, seen = [], set()
    for payment in source['payments']:
        day = payment['paid_at'][:10]
        if day <= snapshot['cutoff_date']:
            continue
        ids = payment.get('source_operations', [])
        opid = ids[0] if len(ids) == 1 else None
        operation = operations.get(opid, {})
        confirmed = bool(payment['confirmed'] and not payment['cancelled'] and payment['id'] in verified)
        direction = 'income' if payment['amount'] > 0 else 'refund' if payment['amount'] < 0 else 'zero'
        if direction == 'refund' and opid and operation.get('exact'):
            proof = trace.resolve_refund_operation(con, source['student']['id'], opid)
            candidates = proof['candidates']
            confirmed = bool(payment['confirmed'] and not payment['cancelled'] and proof['execution_confirmed']
                and candidates and all(r['amount'] == -payment['amount'] and r['executed_at']
                    and payment['paid_at'].startswith(r['executed_at']) for r in candidates))
        if payment['cancelled']:
            confirmed = False
        relation = ('before_snapshot_date' if day < snapshot['as_of_date'] else
                    'requires_same_day_reconciliation' if day == snapshot['as_of_date'] else 'after_snapshot')
        rows.append({'operation_id': opid, 'source_operation_ids': ids, 'payment_id': payment['id'],
            'amount': payment['amount'], 'paid_at': payment['paid_at'], 'direction': direction,
            'confirmation': 'source_confirmed' if confirmed else 'unverified',
            'included_in_snapshot': True if day < snapshot['as_of_date'] else False if day > snapshot['as_of_date'] else None,
            'snapshot_inclusion': relation, 'snapshot_coverage_confirmed': False,
            'cancelled': bool(payment['cancelled']), 'native_present': True,
            'origin': 'technical_backfill' if payment['user_name'] == 'Система'
                and payment['comment'] == 'Оплата до реестра платежей' else 'ledger',
            'apply_as_new_income': False,
            'issues': [] if confirmed else ['Исходная операция и её исполнение не подтверждены полностью по идентификаторам']})
        seen.update(ids)
    # Retained operations absent from the native ledger remain visible for
    # review. Merely finding a raw row is not permission to create cash.
    for opid, operation in operations.items():
        if opid in seen or operation.get('payment_ids'):
            continue
        candidates = [r for r in operation['candidates'] if r.get('source_kind') == 'payments']
        facts = {(r.get('amount'), r.get('paid_at')) for r in candidates}
        if len(facts) != 1:
            continue
        amount, stamp = next(iter(facts))
        if amount is None or not stamp or stamp[:10] <= snapshot['cutoff_date']:
            continue
        relation = ('before_snapshot_date' if stamp[:10] < snapshot['as_of_date'] else
                    'requires_same_day_reconciliation' if stamp[:10] == snapshot['as_of_date'] else 'after_snapshot')
        rows.append({'operation_id': opid, 'source_operation_ids': [opid], 'payment_id': None,
            'amount': amount, 'paid_at': stamp, 'direction': 'income' if amount > 0 else 'refund',
            'confirmation': 'unverified', 'included_in_snapshot': True if stamp[:10] < snapshot['as_of_date']
                else False if stamp[:10] > snapshot['as_of_date'] else None,
            'snapshot_inclusion': relation, 'snapshot_coverage_confirmed': False,
            'cancelled': False, 'native_present': False, 'origin': 'unposted_source',
            'apply_as_new_income': False, 'issues': ['Операция источника не связана с записью CRM; требуется сверка']})
    totals = {'confirmed_income': 0, 'confirmed_refunds': 0, 'unverified_income': 0, 'unverified_refunds': 0}
    for row in rows:
        if row['cancelled'] or row['direction'] == 'zero':
            continue
        prefix = 'confirmed' if row['confirmation'] == 'source_confirmed' else 'unverified'
        totals[prefix + ('_income' if row['direction'] == 'income' else '_refunds')] += abs(row['amount'])
    newer = [r for r in rows if not r['cancelled'] and r['snapshot_inclusion'] == 'after_snapshot']
    same_day = [r for r in rows if not r['cancelled'] and r['snapshot_inclusion'] == 'requires_same_day_reconciliation'
                and r['origin'] != 'technical_backfill']
    return {'after_date': snapshot['cutoff_date'], 'start_exclusive': True,
        'as_of_date': snapshot['as_of_date'], 'operations': rows, 'totals': totals,
        'apply_as_new_income': False, 'newer_operations_present': bool(newer),
        'requires_new_snapshot': bool(newer), 'operations_after_snapshot': newer,
        'requires_same_day_reconciliation': bool(same_day), 'same_day_operations': same_day}


def _owner_snapshot_plan(con, source, before, links, post, snapshot):
    payments = {p['id']: p for p in source['payments']}
    confirmations = {r['payment_id']: r for r in post['operations'] if r['payment_id'] is not None}
    refunded, coverage_blockers = {}, []
    for debit in post['operations']:
        if debit['cancelled'] or debit['direction'] != 'refund':
            continue
        parts = list(con.execute('SELECT * FROM erp_refund_allocations WHERE refund_payment_id=?',
                                 (debit['payment_id'],))) if debit['payment_id'] is not None else []
        if (debit['confirmation'] != 'source_confirmed' or not parts
                or sum(r['amount'] for r in parts) != -debit['amount']
                or before['allocation']['conflicts']):
            coverage_blockers.append('Источник возврата после границы истории не распределён однозначно; оплата будущих покупок не подтверждается общей суммой')
            continue
        for part in parts:
            key = (part['original_payment_id'], part['subscription_id'])
            refunded[key] = refunded.get(key, 0) + part['amount']
    protected = []
    for sub in source['purchases']:
        if sub['status'] == 'cancelled' or sub['month'] <= snapshot['as_of_date'][:7]:
            continue
        parts = before['allocation']['sources'].get(sub['id'], [])
        old = sum(r['amount'] for r in parts if payments[r['payment_id']]['paid_at'][:10] <= snapshot['cutoff_date']
            or payments[r['payment_id']]['user_name'] == 'Система'
            and payments[r['payment_id']]['comment'] == 'Оплата до реестра платежей')
        confirmed = sum(max(0, r['amount'] - refunded.get((r['payment_id'], sub['id']), 0))
            for r in links if r['subscription_id'] == sub['id']
            and r['payment_id'] in confirmations
            and confirmations[r['payment_id']]['confirmation'] == 'source_confirmed'
            and confirmations[r['payment_id']]['origin'] != 'technical_backfill'
            and confirmations[r['payment_id']]['included_in_snapshot'])
        if coverage_blockers:
            confirmed = 0
        confirmed = min(sub['price'], confirmed)
        protected.append({'subscription_id': sub['id'], 'month': sub['month'], 'price': sub['price'],
            'crm_covered': before['balance']['covered'].get(sub['id'], 0),
            'historical_covered': old, 'historical_funds_allowed': 0,
            'confirmed_post_cutoff_covered': confirmed, 'planned_covered': confirmed,
            'planned_unpaid': None, 'coverage_unverified': sub['price'] - confirmed,
            'debt_created': 0, 'plan_only': True, 'proof': 'post_cutoff_source_ids_only',
            'status': 'source_confirmed' if confirmed == sub['price'] else 'requires_source_reconciliation',
            'coverage_blockers': list(dict.fromkeys(coverage_blockers)),
            'note': 'Неподтверждённая часть требует сверки; снимок свободного баланса не создаёт новый долг'})
    return {'plan_only': True, 'applied': False, 'provenance': snapshot['provenance'],
        'snapshot_evidence_id': snapshot['evidence_id'], 'free_balance': snapshot['amount'],
        'free_balance_as_of_date': snapshot['as_of_date'], 'current_free_balance': None,
        'historical_funds_available_for_future': 0, 'history_recalculation_allowed': False,
        'snapshot_funds_available_for_future': snapshot['amount'],
        'post_cutoff_income_already_in_snapshot': None, 'apply_post_cutoff_as_new_income': False,
        'post_cutoff_income_application_rule': 'never_add_again_without_reconciliation',
        'protected_invoices': protected, 'preserve_subscription_ids': [r['id'] for r in source['purchases']],
        'attendance_rows_preserved': True, 'debt_created': 0,
        'coverage_blockers': list(dict.fromkeys(coverage_blockers)),
        'requires_new_snapshot': post['requires_new_snapshot'],
        'requires_same_day_reconciliation': post['requires_same_day_reconciliation'],
        'newer_operations_present': post['newer_operations_present']}


def _proposed(con, sid, source, candidates, links):
    blockers = []
    known = {r['payment_id'] for r in links}
    excluded = {r['payment_id'] for r in candidates if r['classification'] == 'source_supported_duplicate'}
    for payment in source['payments']:
        if payment['cancelled']:
            continue
        if payment['amount'] < 0:
            blockers.append('Есть возвраты или переносы; полный состав корректировок требует отдельной сверки')
        elif payment['amount'] > 0 and payment['id'] not in known | excluded:
            blockers.append('Не у всех поступлений подтверждены ID исходной операции и счёта ERP')
    exact_subs = {r['subscription_ids'][0] for r in source['invoice_resolutions'] if r.get('exact')}
    if any(p['status'] != 'cancelled' and p['id'] not in exact_subs for p in source['purchases']):
        blockers.append('Состав покупок подтверждён не полностью; закрытие счета по старым суммам пока не исправляется')
    if source['retained_charges']:
        blockers.append('Есть сохранённая стоимость отменённых покупок; нужны точные зачёты по этим начислениям')
    if source['issues'] or source['allocation'].get('conflicts'):
        blockers.append('В исходных связях или текущих зачётах есть противоречия')
    if any(r['classification'] != 'source_supported_duplicate' and not any(
            p['id'] == r['payment_id'] and p['cancelled'] for p in source['payments']) for r in candidates):
        blockers.append('Не все технические строки доказаны как дубли; существующие суммы сохранены')
    if not links:
        blockers.append('Нет полного подтверждённого состава оплат по идентификаторам источника')
    if blockers:
        return None, [], list(dict.fromkeys(blockers))
    clone = sqlite3.connect(':memory:', isolation_level=None)
    clone.row_factory = sqlite3.Row
    try:
        snapshot = bytearray(con.serialize())
        snapshot[18:20] = b'\x01\x01'
        clone.deserialize(bytes(snapshot))
        clone.execute('PRAGMA foreign_keys=ON')
        paymentallocation.init(clone)
        for pid in excluded:
            clone.execute('UPDATE payments SET cancelled=1 WHERE id=?', (pid,))
        changes = [{'action': 'exclude_proved_technical_duplicate', 'payment_id': pid,
                    'financial_effect_applied': False} for pid in sorted(excluded)]
        for link in links:
            old = clone.execute('SELECT * FROM erp_payment_allocations WHERE payment_id=? AND subscription_id=?',
                                (link['payment_id'], link['subscription_id'])).fetchone()
            if old:
                if (old['amount'], old['source_invoice_id'], old['source_operation_id']) != (
                        link['amount'], link['source_invoice_id'], link['source_operation_id']):
                    raise ValueError('Сохранённый зачёт расходится с подтверждённым источником')
                continue
            paymentallocation.bind_payment(clone, link['payment_id'], link['subscription_id'], link['amount'],
                source_namespace='erp', source_invoice_id=link['source_invoice_id'],
                source_operation_id=link['source_operation_id'], evidence_id=link['evidence_id'])
            changes.append({'action': 'bind_source_payment', 'payment_id': link['payment_id'],
                'subscription_id': link['subscription_id'], 'amount': link['amount'],
                'source_invoice_id': link['source_invoice_id'], 'source_operation_id': link['source_operation_id'],
                'financial_effect_applied': False})
        after = _summary(clone, sid)
        if after['allocation']['conflicts'] or after['allocation']['restricted_deposit']:
            return None, [], ['Предлагаемые связи оставляют конфликт или нераспределённые суммы; нужен полный состав зачётов']
        return after, changes, []
    except (ValueError, sqlite3.Error):
        return None, [], ['Изолированная проверка связей не завершена; существующие финансовые записи сохранены']
    finally:
        clone.close()


def audit(con, erp_id, target_month=None, *, balance_snapshot=None):
    if target_month:
        try:
            if date.fromisoformat(str(target_month)+'-01').strftime('%Y-%m') != target_month:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('Укажите месяц в формате ГГГГ-ММ')
    source = trace.trace(con, str(erp_id).strip())
    result = {'read_only': True, 'apply_allowed': False, 'provenance': 'current_connection',
        'erp_id': str(erp_id).strip(), 'student': source['student'], 'target_month': target_month,
        'before': None, 'after': None, 'proposed_after': None, 'synthetic_candidates': [],
        'after_scope': None, 'plan_complete': False,
        'cross_period_allocations': [], 'event_carry': {'expected': [], 'source_candidates': [],
            'plans': [], 'state': 'not_observed', 'blockers': []}, 'expectations': [],
        'proposed_changes': [], 'blockers': [], 'issues': [], 'source_trace': source}
    result.update(erp_balance_snapshot=resolve_balance_snapshot(erp_id, balance_snapshot), crm_observed=None,
                  authoritative_balance_plan=None, post_cutoff=None)
    if str(erp_id).strip() == '117559':
        result['expectations'] = [
            {'kind': 'previously_reported_crm_free_balance', 'amount': 39300, 'verified': False},
            {'kind': 'reported_invoice', 'month': '2026-12', 'amount': 12150, 'verified': False},
            {'kind': 'reported_fifo_split', 'month': '2026-12', 'parts': [
                {'source_month': '2026-08', 'amount': 2100}, {'source_month': '2026-09', 'amount': 10050}], 'verified': False},
            ]
        requirement = {'requirement_id': 'owner:erp117559:tournament:2026-11-14:next',
            'amount': 2700, 'event_date': '2026-11-14', 'destination': 'next_tournament',
            'once': True, 'provenance': 'owner_confirmation', 'confirmed': True,
            'income_effect': 0, 'free_balance_effect': 0, 'source_verified': False}
        result['event_carry']['owner_requirement'] = requirement
        result['event_carry']['expected'] = [dict(requirement, verified=True, confirmation_type='requirement')]
    if not source['student']:
        result['blockers'] = source['issues']
        return result
    sid = source['student']['id']
    result['before'] = _summary(con, sid)
    result['crm_observed'] = result['before']
    links, link_issues = _verified_links(source)
    result['synthetic_candidates'] = _synthetic_candidates(source, links)
    result['cross_period_allocations'] = _cross_period(source, target_month, links)
    carry = _event_carry(source)
    carry['expected'] = result['event_carry']['expected']
    if result['event_carry'].get('owner_requirement'):
        carry['owner_requirement'] = result['event_carry']['owner_requirement']
        carry['plans'] = [{'action': 'verify_one_time_carry',
            'requirement_id': carry['owner_requirement']['requirement_id'], 'amount': 2700,
            'destination': 'next_tournament', 'once': True, 'state': 'blocked',
            'financial_effect_applied': False, 'income_effect': 0, 'free_balance_effect': 0,
            'blockers': ['Для однократного целевого переноса нужны исходная операция и ID следующего турнира']}]
        carry['state'] = 'blocked'
        carry['blockers'] = list(dict.fromkeys(carry['blockers'] + carry['plans'][0]['blockers']))
    if carry['expected'] and not carry['source_candidates']:
        carry['state'] = 'blocked'
        carry['blockers'] = list(dict.fromkeys(carry['blockers'] + [
            'Перенос подтверждён владельцем; исходная операция мероприятия пока не найдена']))
    result['event_carry'] = carry
    result['issues'] = list(dict.fromkeys(source['issues'] + link_issues))
    if result['erp_balance_snapshot']:
        snapshot = result['erp_balance_snapshot']
        result['post_cutoff'] = _post_cutoff(con, source, links, snapshot)
        result['authoritative_balance_plan'] = _owner_snapshot_plan(
            con, source, result['before'], links, result['post_cutoff'], snapshot)
        result['after_scope'] = ('owner_confirmed_balance_snapshot_plan' if snapshot['provenance'] == 'owner_confirmation'
                                 else 'confirmed_erp_export_balance_snapshot_plan')
        result['proposed_changes'] = [
            {'action': 'use_confirmed_erp_balance_snapshot', 'evidence_id': snapshot['evidence_id'],
             'amount': snapshot['amount'], 'as_of_date': snapshot['as_of_date'],
             'provenance': snapshot['provenance'], 'financial_effect_applied': False},
            {'action': 'exclude_old_history_from_free_and_future_funding', 'cutoff_date': snapshot['cutoff_date'],
             'future_funds': 0, 'financial_effect_applied': False},
            {'action': 'preserve_sold_purchases_and_attendance', 'financial_effect_applied': False}]
        result['blockers'] = list(dict.fromkeys(result['issues'] + carry['blockers']
            + result['authoritative_balance_plan']['coverage_blockers']))
        if result['post_cutoff']['requires_new_snapshot']:
            result['blockers'].append('Есть операции после даты подтверждённого снимка; нужна новая сверка баланса')
        if result['post_cutoff']['requires_same_day_reconciliation']:
            result['blockers'].append('У снимка нет точного времени: операции за тот же день требуют отдельной сверки охвата')
        if any(r['confirmation'] != 'source_confirmed' and not r['cancelled']
               for r in result['post_cutoff']['operations']):
            result['blockers'].append('Не все операции после '+snapshot['cutoff_date']+' подтверждены по исходным идентификаторам')
        # The incomplete historical ledger cannot determine a corrected net
        # balance. Keep actual CRM values intact and the snapshot policy apart.
        return result
    result['after'], result['proposed_changes'], result['blockers'] = _proposed(
        con, sid, source, result['synthetic_candidates'], links)
    if link_issues:
        result.update(after=None, proposed_changes=[])
        result['blockers'] = list(dict.fromkeys(result['blockers'] + link_issues))
    # Proven source corrections can still be useful when a tournament carry
    # remains unresolved. Preserve that partial clone, never call it a complete
    # after-state or silently credit the amount reported by the user.
    result['proposed_after'] = result['after']
    if result['proposed_after'] is not None:
        result['after_scope'] = 'verified_source_and_technical_duplicates_only'
    if carry['state'] == 'blocked':
        result['blockers'] = list(dict.fromkeys(result['blockers'] + carry['blockers']))
    if result['blockers']:
        result['after'] = None
    result['plan_complete'] = result['after'] is not None and not result['blockers']
    return result
