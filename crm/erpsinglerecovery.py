"""Explicit, reversible recovery of ERP single sales collapsed into one bill.

No financial operation is created. Existing payment identities are supplied by
the ERP operation register or reviewed individually by the owner.
"""
from contextlib import contextmanager
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import sqlite3

from . import erpimport as ei, erpsingles, money


class RecoveryError(ei.ImportError_):
    pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS erp_single_recovery_runs (
 id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES students(id),
 aggregate_id INTEGER NOT NULL, request_json TEXT NOT NULL,
 before_json TEXT NOT NULL, after_json TEXT NOT NULL,
 before_fingerprint TEXT NOT NULL, after_fingerprint TEXT NOT NULL,
 before_summary TEXT NOT NULL, after_summary TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'applied', created_at TEXT NOT NULL,
 user_name TEXT NOT NULL, backup_path TEXT NOT NULL,
 rolled_back_at TEXT, rolled_back_by TEXT, rollback_backup_path TEXT);
"""


def init(con):
    con.executescript(SCHEMA)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _exists(con, table):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _dicts(con, sql, args=()):
    return [dict(row) for row in con.execute(sql, args)]


def _integer(value):
    if isinstance(value, bool):
        return None
    try:
        converted = int(value)
        return converted if str(converted) == str(value).strip() else None
    except (TypeError, ValueError):
        return None


def _day(value):
    try:
        return value if date.fromisoformat(value).isoformat() == value else None
    except (TypeError, ValueError):
        return None


def context(con, erp_id):
    students = _dicts(con, 'SELECT id,name,erp_id FROM students WHERE erp_id=? AND archived=0', (str(erp_id).strip(),))
    result = {'student': students[0] if len(students) == 1 else None, 'groups': [], 'aggregates': [], 'payments': [], 'issues': []}
    if len(students) != 1:
        result['issues'].append('Нужна единственная действующая карточка с указанным ID клиента ERP')
        return result
    sid = students[0]['id']
    result['groups'] = _dicts(con, '''SELECT g.*,b.name AS branch_name FROM groups g
        JOIN branches b ON b.id=g.branch_id WHERE g.active=1 ORDER BY b.name,g.name,g.id''')
    result['aggregates'] = _dicts(con, '''SELECT x.*,e.group_id FROM subscriptions x
        LEFT JOIN enrollments e ON e.id=x.enrollment_id
        WHERE x.student_id=? AND x.kind='month' AND x.status!='cancelled' ORDER BY x.month DESC,x.id''', (sid,))
    result['payments'] = _dicts(con, 'SELECT * FROM payments WHERE student_id=? ORDER BY paid_at,id', (sid,))
    for payment in result['payments']:
        operation = con.execute('SELECT operation_id FROM erp_full_operations WHERE payment_id=? ORDER BY operation_id', (payment['id'],)).fetchall()
        single = con.execute('SELECT source_sale_id FROM erp_single_payments WHERE payment_id=?', (payment['id'],)).fetchone()
        payment['operation_id'] = operation[0][0] if len(operation) == 1 else ''
        payment['source_sale_id'] = single[0] if single else ''
    return result


def _snapshot(con, sid):
    """Fingerprint every potentially affected row and later references.

    This deliberately refuses a rollback after any later change in the child's
    working data. It never restores an entire database over later work.
    """
    result = {}
    direct = ('enrollments', 'subscriptions', 'payments', 'attendance', 'single_visits',
              'tasks', 'history', 'erp_single_sales', 'erp_single_reviews',
              'erp_single_reschedules', 'erp_full_rows')
    result['students'] = _dicts(con, 'SELECT * FROM students WHERE id=? ORDER BY id', (sid,))
    for table in direct:
        if _exists(con, table):
            result[table] = _dicts(con, 'SELECT * FROM '+table+' WHERE student_id=? ORDER BY 1', (sid,))
    for table in ('erp_full_operations', 'erp_single_payments'):
        if _exists(con, table):
            result[table] = _dicts(con, 'SELECT * FROM '+table+' WHERE payment_id IN (SELECT id FROM payments WHERE student_id=?) ORDER BY 1', (sid,))
    for table in ('price_corrections', 'cancelled_service_charges', 'erp_full_subscription_corrections'):
        if _exists(con, table):
            result[table] = _dicts(con, 'SELECT * FROM '+table+' WHERE subscription_id IN (SELECT id FROM subscriptions WHERE student_id=?) ORDER BY 1', (sid,))
    # A later cross-client reference to one of these bills must also prevent
    # rollback, even when its owner is not this child.
    result['_external_payments'] = _dicts(con, '''SELECT * FROM payments WHERE student_id!=?
        AND subscription_id IN (SELECT id FROM subscriptions WHERE student_id=?) ORDER BY id''', (sid, sid))
    references = {}
    for table in (row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")):
        if not table.replace('_', '').isalnum():
            continue
        if 'subscription_id' in {row['name'] for row in con.execute('PRAGMA table_info('+table+')')}:
            references[table] = _dicts(con, 'SELECT * FROM '+table+' WHERE subscription_id IN (SELECT id FROM subscriptions WHERE student_id=?) ORDER BY 1', (sid,))
    result['_subscription_references'] = references
    return result


def _group(con, group_id, sales):
    row = con.execute('SELECT g.*,b.name AS branch_name FROM groups g JOIN branches b ON b.id=g.branch_id WHERE g.id=?', (group_id,)).fetchone()
    if not row:
        return None, 'Выберите подтверждённую группу продаж ERP: Небо, среда 18:45'
    group = dict(row)
    if not group['active'] or group.get('is_makeup') or 'небо' not in ei._norm(group['branch_name']):
        return group, 'Для восстановления нужна действующая группа филиала Небо'
    from . import logic, schedule
    for sale in sales:
        for key in ('source_date', 'effective_date'):
            day = _day(sale.get(key))
            if not day:
                continue
            actual = date.fromisoformat(day)
            effective = schedule.on(con, group, actual)
            weekdays = {int(value) for value in str(effective.get('weekdays', '')).split(',') if value.strip().isdigit()}
            if actual.weekday() != 2 or 2 not in weekdays or logic.group_time(effective, 2) != '18:45':
                return group, 'Группа должна соответствовать подтверждённым продажам: среда 18:45; время из реестра оплат не используется'
    return group, ''


def _calendar(con, sid, report):
    from . import clientattendance
    group_ids = {report['group']['id']} if report.get('group') else set()
    enrollment = (report.get('aggregate') or {}).get('enrollment_id')
    if enrollment:
        row = con.execute('SELECT group_id FROM enrollments WHERE id=?', (enrollment,)).fetchone()
        if row:
            group_ids.add(row[0])
    months = sorted({sale[key][:7] for sale in report['sales'] for key in ('source_date', 'effective_date') if _day(sale.get(key))})
    events = []
    for month in months:
        events.extend({key: event.get(key) for key in ('date', 'group_id', 'kind', 'status', 'time', 'single_id', 'attendance_id')}
                      for event in clientattendance.calendar(con, sid, month)['events'] if event['group_id'] in group_ids)
    events.sort(key=lambda event: (event['date'], event['group_id'], event['kind']))
    return events


def _real_month_coverage(con, sid, gid, day):
    for row in con.execute("""SELECT x.* FROM subscriptions x JOIN enrollments e ON e.id=x.enrollment_id
            WHERE x.student_id=? AND e.group_id=? AND x.kind='month' AND x.status!='cancelled'""", (sid, gid)):
        if row['valid_from'] and day < row['valid_from'] or row['valid_until'] and day > row['valid_until']:
            continue
        if row['month'] == day[:7] or row['valid_from'] and row['valid_until'] and row['valid_from'] <= day <= row['valid_until']:
            return True
        if row['historical_sale'] or row['composition_corrected']:
            if any(component.get('group_id') == gid and day in component.get('dates', []) for component in json.loads(row['lesson_schedule'] or '[]')):
                return True
    return False


def _verify_calendar(con, report):
    from . import services
    sid, gid = report['student']['id'], report['group']['id']
    events = _calendar(con, sid, report)
    for sale in report['sales']:
        matching = [event for event in events if event['date'] == sale['effective_date'] and event['group_id'] == gid and event['kind'] == 'single']
        if len(matching) != 1 or matching[0]['time'] != '18:45':
            raise RecoveryError('Календарь не подтвердил отдельное занятие '+sale['sale_id']+' на согласованную дату в 18:45')
        rows = [row for row in services.journal(con, gid, sale['effective_date'])['rows'] if row['id'] == sid]
        if len(rows) != 1 or rows[0]['unpaid']:
            raise RecoveryError('Журнал не подтвердил учтённую оплату разового занятия '+sale['sale_id'])
    for event in events:
        if event['kind'] == 'subscription' and not _real_month_coverage(con, sid, event['group_id'], event['date']):
            raise RecoveryError('Календарь добавил дату из расписания без действующей месячной покупки: '+event['date']+'; восстановление заблокировано')
    return events


def _summary(con, sid, report):
    active = con.execute("SELECT COUNT(*),COALESCE(SUM(lessons),0),COALESCE(SUM(price),0) FROM subscriptions WHERE student_id=? AND status!='cancelled'", (sid,)).fetchone()
    payments = con.execute('SELECT COUNT(*),COALESCE(SUM(amount),0) FROM payments WHERE student_id=? AND cancelled=0', (sid,)).fetchone()
    balance = money.balance(con, sid)
    events = _calendar(con, sid, report)
    return {'sales_count': active[0], 'lessons_count': active[1], 'sales_total': active[2],
            'payment_count': payments[0], 'payment_total': payments[1], 'debt': balance['debt'],
            'deposit': balance['deposit'], 'calendar_dates': sorted({event['date'] for event in events}),
            'calendar_events': events}


def _validate(con, data):
    issues = []
    info = context(con, data.get('erp_id', ''))
    student = info['student']
    if not student:
        return {'can_apply': False, 'issues': info['issues'], 'student': None, 'sales': data.get('sales', []), 'payment_mapping': {}}
    sid = student['id']
    sales = data.get('sales')
    if not isinstance(sales, list) or len(sales) != 4 or any(not isinstance(row, dict) for row in sales):
        return {'can_apply': False, 'issues': ['Для сверки нужны четыре подтверждённые строки продаж ERP'], 'student': student, 'sales': [], 'payment_mapping': {}}
    sales = [dict(row) for row in sales]
    for row in sales:
        for key in ('sale_id', 'invoice_id', 'operation_id'):
            row[key] = str(row.get(key) or '').strip()
        row['amount'] = _integer(row.get('amount'))
        for key in ('source_date', 'effective_date'):
            if not isinstance(row.get(key), str):
                row[key] = ''
        if not all(row[key] for key in ('sale_id', 'invoice_id', 'operation_id')):
            issues.append('У каждой продажи нужны подтверждённые ID продажи, счёта и операции ERP')
        if row['amount'] != 2500 or not _day(row.get('source_date')) or not _day(row.get('effective_date')):
            issues.append('Нужны точные даты занятий и стоимость 2 500 ₽ для каждой из четырёх продаж')
    for key in ('sale_id', 'invoice_id', 'operation_id'):
        if len({row[key] for row in sales}) != 4:
            issues.append('ID продаж, счетов и операций ERP должны различаться у четырёх строк')
    months = {str(row.get('source_date') or '')[:7] for row in sales}
    if months != {'2026-11'}:
        issues.append('Исходные занятия этого восстановления должны относиться к ноябрю 2026; фактический перенос хранится отдельно')
    group, group_issue = _group(con, _integer(data.get('group_id')), sales)
    if group_issue:
        issues.append(group_issue)
    confirmed = data.get('confirmed_group')
    if confirmed is not None and (not isinstance(confirmed, dict) or
            'небо' not in ei._norm(confirmed.get('branch', '')) or
            confirmed.get('weekday') != 2 or confirmed.get('time') != '18:45'):
        issues.append('Подтверждённая группа в сведениях восстановления должна быть Небо, среда 18:45')
    aggregate_id = _integer(data.get('aggregate_id'))
    aggregate = con.execute('SELECT * FROM subscriptions WHERE id=? AND student_id=?', (aggregate_id, sid)).fetchone()
    if not aggregate or (aggregate['kind'], aggregate['month'], aggregate['lessons'], aggregate['price'], aggregate['status']) != ('month', '2026-11', 4, 10000, 'paid'):
        issues.append('Нужен существующий оплаченный месячный счёт ноября: 4 занятия, 10 000 ₽; отличающиеся данные автоматически не заменяются')
    elif any(aggregate[key] for key in ('credit_lessons', 'late_fee', 'composition_corrected', 'discount_amount')):
        issues.append('У старого счёта есть дополнительные изменения состава или стоимости; нужна отдельная проверка')
    if aggregate:
        for table in ('price_corrections', 'cancelled_service_charges', 'erp_full_subscription_corrections',
                      'subscription_cancellations', 'refund_requests', 'subscription_composition_reviews'):
            if _exists(con, table) and con.execute('SELECT 1 FROM '+table+' WHERE subscription_id=?', (aggregate_id,)).fetchone():
                issues.append('У старого счёта есть коррекция или удержанная стоимость; восстановление требует отдельной проверки')
        if _exists(con, 'pay_links') and con.execute("SELECT 1 FROM pay_links WHERE subscription_id=? AND status='open'", (aggregate_id,)).fetchone():
            issues.append('У старого счёта есть действующая ссылка на оплату; сначала нужно проверить её состояние')
    if not isinstance(data.get('reason'), str) or not data['reason'].strip():
        issues.append('Укажите основание восстановления и сверки четырёх продаж')
    mapping = data.get('payment_mapping') or {}
    if not isinstance(mapping, dict):
        mapping = {}
        issues.append('Для связи оплат укажите ID существующего платежа отдельно у каждой продажи')
    resolved, selected = {}, []
    for row in sales:
        sale_id, op = row['sale_id'], row['operation_id']
        chosen = mapping.get(sale_id)
        if isinstance(chosen, list):
            chosen = chosen[0] if len(chosen) == 1 else None
        chosen = _integer(chosen)
        exact = con.execute('SELECT payment_id FROM erp_full_operations WHERE operation_id=?', (op,)).fetchone()
        if not exact:
            exact = con.execute('SELECT payment_id FROM erp_single_payments WHERE operation_id=?', (op,)).fetchone()
        if exact:
            if chosen and chosen != exact['payment_id']:
                issues.append('Ручной выбор не может заменить существующую связь операции ERP '+op)
            chosen = exact['payment_id']
        elif not chosen or data.get('mapping_reviewed') is not True:
            issues.append('Продажа '+sale_id+': нет точной связи операции с оплатой; выберите существующий платёж и подтвердите проверку каждого соответствия')
        payment = con.execute('SELECT * FROM payments WHERE id=?', (chosen,)).fetchone() if chosen else None
        if payment:
            if (payment['student_id'] != sid or payment['amount'] != row['amount'] or payment['cancelled'] or not payment['confirmed'] or payment['paid_at'][:10] != str(data.get('payment_date') or '2026-10-28')):
                issues.append('Продажа '+sale_id+': выбранная оплата отличается от подтверждённых сведений ERP за 28 октября 2026')
            expected_method = data.get('payment_method')
            if expected_method and payment['method'] != expected_method:
                issues.append('Продажа '+sale_id+': способ выбранной оплаты отличается от подтверждённого карточного платежа ERP; исходная оплата не изменяется')
            if payment['subscription_id'] not in (None, aggregate_id):
                issues.append('Продажа '+sale_id+': оплата уже связана с другим счётом; связь сохранена')
            other = con.execute('SELECT operation_id FROM erp_full_operations WHERE payment_id=? AND operation_id!=?', (chosen, op)).fetchone()
            other_single = con.execute('SELECT operation_id FROM erp_single_payments WHERE payment_id=? AND operation_id!=?', (chosen, op)).fetchone()
            if other or other_single:
                issues.append('Продажа '+sale_id+': платёж уже относится к другой операции ERP')
            resolved[sale_id] = chosen
            selected.append(chosen)
        elif chosen:
            issues.append('Продажа '+sale_id+': выбранный платёж CRM не найден')
        if sale_id and con.execute('SELECT 1 FROM erp_single_sales WHERE source_id=?', (sale_id,)).fetchone():
            issues.append('Продажа '+sale_id+' уже перенесена отдельно; повторное восстановление не создаёт новую покупку')
        if group:
            if con.execute('SELECT 1 FROM single_visits WHERE student_id=? AND date IN (?,?)', (sid, row.get('source_date'), row.get('effective_date'))).fetchone():
                issues.append('Продажа '+sale_id+': на исходную или новую дату уже есть разовая запись CRM; нужна сверка её происхождения')
            if row.get('source_date') != row.get('effective_date') and con.execute('SELECT 1 FROM attendance WHERE student_id=? AND group_id=? AND date IN (?,?)', (sid, group['id'], row.get('source_date'), row.get('effective_date'))).fetchone():
                issues.append('Продажа '+sale_id+': у переносимого занятия есть фактическая отметка посещения; сначала нужно её сверить')
    if len(selected) != len(set(selected)):
        issues.append('Один платёж нельзя выбрать для нескольких разных продаж')
    if len(resolved) != 4:
        issues.append('Не все четыре оплаты однозначно связаны с продажами ERP')
    if aggregate and any(row[0] not in selected for row in con.execute('SELECT id FROM payments WHERE subscription_id=? AND cancelled=0', (aggregate_id,))):
        issues.append('Со старым счётом связаны другие оплаты; нельзя автоматически распределить только часть его состава')
    if aggregate and group and con.execute("SELECT 1 FROM subscriptions x JOIN enrollments e ON e.id=x.enrollment_id WHERE x.student_id=? AND x.id!=? AND x.kind='month' AND x.status!='cancelled' AND x.month='2026-11' AND e.group_id=?", (sid, aggregate_id, group['id'])).fetchone():
        issues.append('В подтверждённой группе есть другой действующий месячный счёт; нужно проверить состав покупок')
    return {'can_apply': not issues, 'issues': list(dict.fromkeys(issues)), 'student': student,
            'group': group, 'aggregate': dict(aggregate) if aggregate else None,
            'sales': sales, 'payment_mapping': resolved}


def _clone(con):
    clone = sqlite3.connect(':memory:', isolation_level=None)
    clone.row_factory = sqlite3.Row
    clone.create_function('ulower', 1, lambda value: value.lower() if isinstance(value, str) else value)
    snapshot = bytearray(con.serialize())
    snapshot[18:20] = b'\x01\x01'
    clone.deserialize(bytes(snapshot))
    clone.execute('PRAGMA foreign_keys=ON')
    return clone


def _execute(con, data, report, user, now):
    sid, gid, old_id = report['student']['id'], report['group']['id'], report['aggregate']['id']
    con.execute("UPDATE subscriptions SET status='cancelled' WHERE id=? AND status='paid'", (old_id,))
    # The only modified columns of pre-existing payments are their explicit
    # bill links. All IDs, sums, methods, dates and fiscal evidence stay intact.
    for row in report['sales']:
        payment_id = report['payment_mapping'][row['sale_id']]
        payment = con.execute('SELECT * FROM payments WHERE id=?', (payment_id,)).fetchone()
        con.execute('UPDATE payments SET subscription_id=NULL WHERE id=?', (payment_id,))
        record = {'sale_id': row['sale_id'], 'invoice_id': row['invoice_id'],
                  'operation_id': row['operation_id'], 'erp_id': str(data['erp_id']),
                  'tariff': 'Разовое занятие', 'month': '2026-11', 'price': str(row['amount']),
                  'lessons': '1', 'status': 'Оплачен', 'lesson_date': row['source_date'],
                  'sale_at': row.get('sale_at', ''), '_record_kind': 'subs',
                  '_source_key': 'single-recovery:'+row['sale_id'],
                  'source_invoice_id': row['invoice_id'], 'source_payment_date': payment['paid_at'],
                  'source_payment_method': payment['method'], 'recovery_reason': data['reason'],
                  'group': report['group']['name'], 'branch': report['group']['branch_name'],
                  'confirmed_weekday': 2, 'confirmed_time': '18:45', 'recovery_aggregate_id': old_id}
        imported = erpsingles.import_sale(con, record, sid, gid, now)
        if imported['reason'] or not imported['target_id']:
            raise RecoveryError(imported['reason'] or 'Разовая покупка не создана')
        if not con.execute('SELECT 1 FROM erp_full_operations WHERE operation_id=?', (row['operation_id'],)).fetchone():
            signature = _json([sid, payment['paid_at'][:10], payment['amount'], payment['method']])
            con.execute('INSERT INTO erp_full_operations(operation_id,payment_id,signature) VALUES (?,?,?)', (row['operation_id'], payment_id, signature))
        linked = erpsingles.register_payment(con, payment_id, row['operation_id'], row['sale_id'], now)
        if linked['reason']:
            raise RecoveryError(linked['reason'])
        # This reviewed mapping is local to the four selected payments. Do not
        # call the global import reconciler and mutate unrelated clients.
        changed = con.execute('UPDATE payments SET subscription_id=? WHERE id=? AND subscription_id IS NULL',
                              (imported['target_id'], payment_id)).rowcount
        if changed != 1:
            raise RecoveryError('Связь исходной оплаты изменилась во время восстановления')
        if row['source_date'] != row['effective_date']:
            erpsingles.reschedule_sale(con, row['sale_id'], row['source_date'], row['effective_date'], user,
                                      row.get('move_reason') or data['reason'])
    _verify_calendar(con, report)


def plan(con, data):
    if not isinstance(data, dict):
        raise RecoveryError('Передайте сведения для сверки ERP')
    report = _validate(con, data)
    if report['student']:
        sid = report['student']['id']
        report['before_summary'] = _summary(con, sid, report)
        report['after_summary'] = None
        report['state_fingerprint'] = _hash(_snapshot(con, sid))
        if report['can_apply']:
            clone = _clone(con)
            try:
                _execute(clone, data, report, {'name': 'Проверка восстановления ERP'}, '2000-01-01T00:00:00')
                report['after_summary'] = _summary(clone, sid, report)
                report['calendar_verified'] = True
                if any(report['after_summary'][key] != report['before_summary'][key] for key in ('sales_total', 'payment_count', 'payment_total', 'debt', 'deposit')):
                    report['can_apply'] = False
                    report['issues'].append('Составление разовых покупок изменило бы суммы, долг или остаток; требуется отдельная сверка')
            except (ei.ImportError_, sqlite3.Error, ValueError) as exc:
                report['can_apply'] = False
                report['issues'].append(str(exc))
            finally:
                clone.close()
    report['token'] = _hash({'data': data, 'report': report})
    return report


@contextmanager
def _write(con, name):
    outer = con.in_transaction
    if not outer:
        con.execute('BEGIN IMMEDIATE')
    con.execute('SAVEPOINT '+name)
    try:
        # Acquire a write reservation even in a caller's deferred transaction.
        con.execute('UPDATE settings SET value=value WHERE 0')
        yield
        con.execute('RELEASE '+name)
        if not outer:
            con.execute('COMMIT')
    except BaseException:
        con.execute('ROLLBACK TO '+name)
        con.execute('RELEASE '+name)
        if not outer:
            con.execute('ROLLBACK')
        raise


def _verify_backup(con, path, sid):
    backup = Path(str(path or ''))
    if not backup.is_file() or backup.stat().st_size == 0:
        raise RecoveryError('Нужна проверенная резервная копия текущей базы перед изменениями')
    try:
        check = sqlite3.connect(backup.resolve().as_uri()+'?mode=ro', uri=True)
        check.row_factory = sqlite3.Row
        try:
            if check.execute('PRAGMA quick_check').fetchone()[0] != 'ok' or _hash(_snapshot(check, sid)) != _hash(_snapshot(con, sid)):
                raise RecoveryError('Резервная копия не соответствует текущим данным; создайте новую перед подтверждением')
        finally:
            check.close()
    except sqlite3.Error as exc:
        raise RecoveryError('Не удалось проверить резервную копию базы') from exc


def apply(con, data, token, user, backup_path):
    if not (user or {}).get('name'):
        raise RecoveryError('Укажите, кто подтвердил восстановление')
    with _write(con, 'erp_single_recovery_apply'):
        report = plan(con, data)
        if not token or token != report['token']:
            raise RecoveryError('Данные изменились после предварительной проверки; выполните её заново')
        if not report['can_apply']:
            raise RecoveryError('; '.join(report['issues']))
        sid = report['student']['id']
        _verify_backup(con, backup_path, sid)
        before = _snapshot(con, sid)
        now = datetime.now().isoformat(timespec='seconds')
        _execute(con, data, report, user, now)
        after_summary = _summary(con, sid, report)
        if after_summary != report['after_summary']:
            raise RecoveryError('Результат отличается от проверенной копии базы; изменения отменены')
        after = _snapshot(con, sid)
        run_id = con.execute('''INSERT INTO erp_single_recovery_runs(student_id,aggregate_id,request_json,
            before_json,after_json,before_fingerprint,after_fingerprint,before_summary,after_summary,
            state,created_at,user_name,backup_path) VALUES (?,?,?,?,?,?,?,?,?,'applied',?,?,?)''',
            (sid, report['aggregate']['id'], _json(data), _json(before), _json(after), _hash(before),
             _hash(after), _json(report['before_summary']), _json(after_summary), now, user['name'], str(backup_path))).lastrowid
        report.update({'run_id': run_id, 'state': 'applied', 'rollback_token': _hash([run_id, _hash(after)]), 'after_summary': after_summary})
        return report


def _primary_keys(con, table):
    return [row['name'] for row in sorted(con.execute('PRAGMA table_info('+table+')'), key=lambda row: row['pk']) if row['pk']]


def rollback(con, run_id, user, backup_path, token=None):
    if not (user or {}).get('name'):
        raise RecoveryError('Укажите, кто подтвердил отмену восстановления')
    with _write(con, 'erp_single_recovery_rollback'):
        run = con.execute('SELECT * FROM erp_single_recovery_runs WHERE id=?', (_integer(run_id),)).fetchone()
        if not run or run['state'] != 'applied':
            raise RecoveryError('Восстановление не найдено или уже отменено')
        if token is not None and token != _hash([run['id'], run['after_fingerprint']]):
            raise RecoveryError('Подтверждение отмены устарело')
        if _hash(_snapshot(con, run['student_id'])) != run['after_fingerprint']:
            raise RecoveryError('После восстановления данные изменились; автоматическая отмена не затронет новые правки')
        _verify_backup(con, backup_path, run['student_id'])
        before, after = json.loads(run['before_json']), json.loads(run['after_json'])
        # Delete only rows demonstrably created by this run, in FK-safe order.
        order = ('erp_single_reschedules', 'erp_single_reviews', 'erp_full_rows', 'history',
                 'erp_single_payments', 'erp_full_operations', 'erp_single_sales',
                 'single_visits', 'subscriptions', 'tasks', 'payments', 'enrollments')
        for table in order:
            keys = _primary_keys(con, table)
            old_ids = {tuple(row[key] for key in keys) for row in before.get(table, [])}
            for row in after.get(table, []):
                if tuple(row[key] for key in keys) not in old_ids:
                    con.execute('DELETE FROM '+table+' WHERE '+' AND '.join(key+'=?' for key in keys), tuple(row[key] for key in keys))
        # Restore exact previous values only for pre-existing rows changed by
        # this run. The unchanged-after fingerprint protects every restoration.
        for table in ('subscriptions', 'payments', 'erp_full_operations', 'erp_single_payments'):
            keys = _primary_keys(con, table)
            now_rows = {tuple(row[key] for key in keys): row for row in after.get(table, [])}
            for row in before.get(table, []):
                if now_rows.get(tuple(row[key] for key in keys)) != row:
                    columns = [key for key in row if key not in keys]
                    con.execute('UPDATE '+table+' SET '+','.join(key+'=?' for key in columns)+' WHERE '+' AND '.join(key+'=?' for key in keys),
                                tuple(row[key] for key in columns+keys))
        if _hash(_snapshot(con, run['student_id'])) != run['before_fingerprint']:
            raise RecoveryError('Точечная отмена не восстановила исходное состояние; изменения отменены')
        at = datetime.now().isoformat(timespec='seconds')
        con.execute("UPDATE erp_single_recovery_runs SET state='rolled_back',rolled_back_at=?,rolled_back_by=?,rollback_backup_path=? WHERE id=?", (at, user['name'], str(backup_path), run['id']))
        return {'run_id': run['id'], 'state': 'rolled_back', 'before_summary': json.loads(run['after_summary']),
                'after_summary': json.loads(run['before_summary'])}
