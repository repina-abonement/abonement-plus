"""Disposable, owner-requested review of the unresolved CRM-175 card.

The native IDs are deliberately not configured here. A unique name, group and
purchase must be resolved in the server's full database copy. A matching shape
permits a provisional copy calculation, never a production accounting change.
"""
from datetime import date
import re

from . import erpbalancerecovery as recovery, erpimport, historicalrefunds
from . import money, paymentallocation, paylinkguard
from .services import ApiError


AS_OF = '2026-11-20'
MONTH = '2026-12'
PRICE = 9450
LESSONS = 7
SOURCE_PARTS = [1385, 4015, 4050]


def _tokens(value):
    return set(re.findall(r'[а-яa-z0-9]+', erpimport._norm(str(value or ''))))


def _table(con, name):
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _links(con, sid):
    if not _table(con, 'pay_links'):
        return []
    result = []
    fields = ('id', 'student_id', 'amount', 'orderid', 'invoice_id', 'url', 'invoice_url',
        'purpose', 'status', 'created_at', 'paid_at', 'payment_id', 'pk_payment_id',
        'subscription_id', 'full_payment', 'checkout_state')
    for raw in con.execute('SELECT * FROM pay_links WHERE student_id=? ORDER BY id', (sid,)):
        row = dict(raw)
        item = {key: row.get(key) for key in fields}
        item['parts'] = [dict(r) for r in con.execute(
            'SELECT subscription_id,amount FROM pay_link_subscriptions WHERE link_id=? ORDER BY subscription_id',
            (row['id'],))] if _table(con, 'pay_link_subscriptions') else []
        try:
            item['inspection'] = paylinkguard.inspect(con, row, student_id=sid)
        except (ValueError, ApiError) as exc:
            item['inspection'] = {'valid': False, 'read_only': True, 'reason': str(exc)}
        result.append(item)
    return result


def _matching_group(row):
    try:
        weekdays = sorted(int(value.strip()) for value in row['weekdays'].split(',') if value.strip())
    except (TypeError, ValueError, AttributeError):
        return False
    branch = _tokens(row['branch'])
    return ('небо' in branch and branch <= {'небо', 'тц', 'трц'} and weekdays == [0, 3]
            and str(row['time']).strip() == '19:45' and row['active'] == 1)


def review(con):
    """Read source evidence first; run any provisional change on a full clone."""
    snapshot = historicalrefunds._clone(con)
    try:
        return _review_snapshot(snapshot)
    finally:
        snapshot.close()


def _review_snapshot(con):
    result = {'case_id': 'CRM-175', 'read_only': True, 'snapshot_on_copy': True,
        'copy_scope': 'full_server_database', 'external_actions': False, 'live_provider_requests': False,
        'can_apply': False, 'production_apply_available': False, 'production_apply': False,
        'provisional': True, 'independent_source_verified': False, 'plan_ready': False,
        'already_correct': False, 'correction_required': None,
        'student': None, 'erp_id': None, 'group': None, 'invoice': None,
        'source_context': None, 'payment_links': [], 'projection': None,
        'native_financial_history': [], 'native_invoice_balance_uses': [], 'native_invoice_payment_targets': [],
        'before': None, 'after': None,
        'source_context_captured_before_projection': False, 'issues': [],
        'selection': {'student_count': 0, 'group_count': 0, 'purchase_count': 0}}
    students = [dict(row) for row in con.execute('SELECT id,name,erp_id,archived FROM students ORDER BY id')
                if {'даниил', 'мамедов'} <= _tokens(row['name'])]
    result['selection']['student_count'] = len(students)
    if len(students) != 1:
        result['issues'].append('Даниил Мамедов не найден как единственная карточка CRM; выбор по первой строке не выполняется')
        return result
    student = students[0]
    sid, erp_id = student['id'], str(student['erp_id'] or '').strip()
    result.update(student=student, erp_id=erp_id or None)
    if not erp_id or student['archived']:
        result['issues'].append('Нужны точный ID клиента ERP и действующая карточка; сверка источника пока заблокирована')
        return result
    source = historicalrefunds.context(con, erp_id)
    if not source.get('student') or source['student']['id'] != sid:
        result['issues'].append('ID клиента ERP не связан однозначно с найденной карточкой CRM')
        return result
    # Capture everything below before the projection protects or reconciles
    # the target. Link inspection is local and never opens checkout.
    result['source_context'] = source
    result['payment_links'] = _links(con, sid)
    result['native_financial_history'] = [dict(row) for row in con.execute('''SELECT * FROM history
        WHERE student_id=? AND kind IN ('payment','subscription','cancel','refund','balance','invoice')
        ORDER BY ts,id''', (sid,))] if _table(con, 'history') else []
    result['native_invoice_balance_uses'] = [dict(row) for row in con.execute('''SELECT u.*
        FROM invoice_balance_uses u JOIN subscriptions x ON x.id=u.subscription_id
        WHERE x.student_id=? ORDER BY u.subscription_id''', (sid,))] if _table(con, 'invoice_balance_uses') else []
    result['native_invoice_payment_targets'] = [dict(row) for row in con.execute('''SELECT t.*
        FROM invoice_payment_targets t JOIN payments p ON p.id=t.payment_id
        JOIN subscriptions x ON x.id=t.subscription_id
        WHERE p.student_id=? AND x.student_id=? ORDER BY t.payment_id,t.subscription_id''',
        (sid, sid))] if _table(con, 'invoice_payment_targets') else []
    result['source_context_captured_before_projection'] = True
    allocation = paymentallocation.allocation(con, sid)
    observed = money.balance(con, sid, date.fromisoformat(AS_OF))
    result['native_allocation'] = allocation
    result['native_balance'] = observed
    result['current_fingerprint'] = recovery.financial_fingerprint(con, sid, erp_id)
    candidates = [dict(row) for row in con.execute('''SELECT DISTINCT g.*,b.name AS branch
        FROM subscriptions x JOIN enrollments e ON e.id=x.enrollment_id AND e.student_id=x.student_id
        JOIN groups g ON g.id=e.group_id JOIN branches b ON b.id=g.branch_id
        WHERE x.student_id=? AND x.month=? AND x.kind='month' AND x.status!='cancelled'
        ORDER BY g.id''', (sid, MONTH)) if _matching_group(row)]
    result['selection']['group_count'] = len(candidates)
    if len(candidates) != 1:
        result['issues'].append('Не найдена единственная группа декабрьского абонемента: Небо, понедельник/четверг, 19:45')
        return result
    group = candidates[0]
    result['group'] = group
    purchases = [dict(row) for row in con.execute('''SELECT x.* FROM subscriptions x
        JOIN enrollments e ON e.id=x.enrollment_id AND e.student_id=x.student_id
        WHERE x.student_id=? AND e.group_id=? AND x.month=? AND x.kind='month'
        AND x.status!='cancelled' ORDER BY x.id''', (sid, group['id'], MONTH))]
    result['selection']['purchase_count'] = len(purchases)
    if len(purchases) != 1:
        result['issues'].append('Декабрьская покупка не сопоставлена однозначно; исходные записи сохранены')
        return result
    target = purchases[0]
    subid = target['id']
    payments = {row['id']: row for row in source['payments']}
    parts = []
    for part in allocation['sources'].get(subid, []):
        payment = payments.get(part['payment_id'], {})
        parts.append(dict(part, payment_amount=payment.get('amount'), paid_at=payment.get('paid_at'),
            method=payment.get('method'), source_operation_ids=payment.get('source_operations', []),
            source_relation_confirmed=payment.get('source_relation_confirmed', False)))
    covered = observed['covered'].get(subid, 0)
    result['invoice'] = dict(target, covered=covered, payable=max(0, target['price']-covered),
        sources=parts, source_parts_are_income=False,
        payment_remaining=allocation['payment_remaining'])
    if target['price'] != PRICE or target['lessons'] != LESSONS:
        result['issues'].append('Покупка отличается от подтверждённых 7 занятий за 9 450 ₽; принудительная проекция не выполняется')
        return result
    if allocation['conflicts']:
        result['issues'].append('В текущих распределениях есть противоречия; сначала требуется сверка фактических связей')
        return result
    mistaken = (covered == PRICE and sorted(part['amount'] for part in parts) == SOURCE_PARTS
                and observed['deposit'] == 12115 and observed.get('free_deposit', observed['deposit']) == 12115)
    corrected = (covered == 0 and result['invoice']['payable'] == PRICE
                 and observed['deposit'] == 0 and not parts)
    result['observed_shape'] = 'reported_fifo_shape' if mistaken else 'already_correct' if corrected else 'changed_or_unrecognised'
    if not mistaken and not corrected:
        result['issues'].append('Фактические суммы или распределение изменились относительно обращения; возвращены исходные данные без проекции')
        return result
    if any(not payment['cancelled'] and str(payment.get('paid_at') or '')[:10] > AS_OF for payment in payments.values()):
        result['issues'].append('Есть операции после 20.11.2026; нулевой снимок не должен скрывать новые оплаты или возвраты')
        return result
    if corrected:
        unchanged = {'balance': observed, 'invoice': result['invoice']}
        result.update(already_correct=True, correction_required=False, plan_ready=True,
            before=unchanged, after=unchanged)
        return result
    for table in ('erp_payment_allocations', 'invoice_payment_targets'):
        if _table(con, table) and con.execute('SELECT 1 FROM '+table+
                ' WHERE subscription_id=? AND amount>0 LIMIT 1', (subid,)).fetchone():
            result['issues'].append('У декабря есть сохранённые прямые связи с исходными оплатами; их нельзя обнулить по совпадению сумм')
            return result
    case = {'erp_id': erp_id, 'subscription_id': subid, 'as_of_date': AS_OF, 'cutoff_date': AS_OF,
        'base_amount': 0, 'existing_credit': 0, 'credit_label': 'Аванса не было',
        'credit_ref': 'owner:crm175:existing-credit:none:2026-11-20',
        'evidence_id': 'owner:crm175:zero-balance:2026-11-20:provisional',
        'reviewed_fingerprint': result['current_fingerprint']}
    result['projection'] = recovery._preview_snapshot(con, erp_id, case=case)
    result['projection'].update(provisional=True, independent_source_verified=False,
        can_apply=False, production_apply_available=False, production_apply=False)
    result['plan_ready'] = result['projection']['plan_ready']
    result['correction_required'] = True
    result['issues'].extend(result['projection']['issues'])
    return result
