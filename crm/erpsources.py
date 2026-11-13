"""Reconcile advertising sources for cards already linked to ERP."""
import csv
import hashlib
import io
import json
from collections import defaultdict
from datetime import datetime

from . import sources
from .services import ApiError


def plan(con, text):
    rows = csv.reader(io.StringIO(text), delimiter=';')
    if next(rows, None) != ['Тип', 'ID', 'Канал', 'Источник']:
        raise ApiError('Первая строка должна быть: Тип;ID;Канал;Источник')
    pairs = defaultdict(set)
    issues = []
    for line, row in enumerate(rows, 2):
        if not any(row):
            continue
        if len(row) != 4:
            issues.append(f'Строка {line}: ожидаются четыре столбца')
            continue
        kind, erp_id, channel, name = (value.strip() for value in row)
        if kind not in ('lead', 'student') or not erp_id.isascii() or not erp_id.isdigit():
            issues.append(f'Строка {line}: неверный тип или ID ERP')
            continue
        pairs[kind, erp_id].add((channel, name))
    if not pairs:
        raise ApiError('Нет строк с ID клиентов ERP')

    identities = defaultdict(set)
    for row in con.execute('SELECT namespace, source_id, student_id FROM erp_full_identities'):
        identities[row[0], row[1]].add(row[2])
    for row in con.execute("SELECT erp_id, id FROM students WHERE erp_id != ''"):
        identities['student', row[0]].add(row[1])

    by_student = defaultdict(set)
    for (kind, erp_id), values in pairs.items():
        ids = identities[kind, erp_id]
        if len(ids) != 1:
            issues.append(f'{kind} {erp_id}: нет однозначной связи с карточкой CRM')
            continue
        by_student[next(iter(ids))].update(values)

    current = {row['id']: row for row in con.execute("""
        SELECT s.id, s.source, s.source_id, a.name, c.name AS channel
        FROM students s LEFT JOIN ad_sources a ON a.id=s.source_id
        LEFT JOIN ad_channels c ON c.id=a.channel_id
    """)}
    changes = []
    matched = empty = 0
    for sid, values in sorted(by_student.items()):
        if len(values) != 1:
            issues.append(f'CRM {sid}: в ERP указаны разные значения')
            continue
        channel, name = next(iter(values))
        if not name or name == '-':
            empty += 1
            continue
        old = current[sid]
        channel = channel if channel and channel != '-' else 'Другое'
        if (old['channel'], old['name']) == (channel, name):
            matched += 1
            continue
        changes.append(dict(id=sid, before=old['name'] or old['source'], before_id=old['source_id'],
                            before_channel=old['channel'], channel=channel, name=name))
    report = dict(changes=changes, matched=matched, empty=empty, issues=issues)
    report['token'] = hashlib.sha256(json.dumps([text, report], sort_keys=True).encode()).hexdigest()
    return report


def apply(con, text, token, user):
    report = plan(con, text)
    if token != report['token']:
        raise ApiError('Данные изменились. Повторите сверку', 409)
    for change in report['changes']:
        source_id = sources.ensure_source(con, change['channel'], change['name'])
        con.execute('UPDATE students SET source_id=?, source=? WHERE id=?',
                    (source_id, change['name'], change['id']))
    if report['changes']:
        con.execute('INSERT INTO audit(ts, user_id, action, detail) VALUES (?,?,?,?)',
                    (datetime.now().isoformat(), user.get('id'), 'erp.sources',
                     json.dumps(report['changes'], ensure_ascii=False)))
    return report
