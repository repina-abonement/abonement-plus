"""Additive ERP recovery with immutable source rows and explicit reconciliation.

This path never runs the legacy update/undo or triggers messages. Source storage
and operational reconciliation are reported separately. Unknown identities and
conflicting values remain review items, never implicit merges or overwrites.
"""
import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation

from . import erpimport as ei
from .db import normalize_phone

SCHEMA = """
CREATE TABLE IF NOT EXISTS erp_full_files (
 id INTEGER PRIMARY KEY, digest TEXT NOT NULL UNIQUE, filename TEXT NOT NULL,
 kind TEXT NOT NULL, headers TEXT NOT NULL, rows TEXT NOT NULL, mapping TEXT NOT NULL,
 imported_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS erp_full_rows (
 file_id INTEGER NOT NULL REFERENCES erp_full_files(id), row_num INTEGER NOT NULL,
 student_id INTEGER REFERENCES students(id), state TEXT NOT NULL, reason TEXT NOT NULL,
 target_table TEXT NOT NULL DEFAULT '', target_id INTEGER,
 PRIMARY KEY(file_id,row_num));
CREATE INDEX IF NOT EXISTS erp_full_student ON erp_full_rows(student_id);
CREATE INDEX IF NOT EXISTS erp_full_history_student ON history(student_id,kind);
CREATE INDEX IF NOT EXISTS erp_full_event_text ON history(text) WHERE kind='erp_task_event' AND user_name='ERP';
CREATE TABLE IF NOT EXISTS erp_full_identities (
 namespace TEXT NOT NULL, source_id TEXT NOT NULL,
 student_id INTEGER NOT NULL REFERENCES students(id),
 PRIMARY KEY(namespace,source_id));
CREATE TABLE IF NOT EXISTS erp_full_history_corrections (
 history_id INTEGER PRIMARY KEY REFERENCES history(id),
 previous_student_id INTEGER NOT NULL REFERENCES students(id),
 assigned_student_id INTEGER REFERENCES students(id),
 file_id INTEGER NOT NULL, row_num INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS erp_full_operations (
 operation_id TEXT PRIMARY KEY, payment_id INTEGER NOT NULL REFERENCES payments(id),
 signature TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS erp_full_task_links (
 source_id TEXT PRIMARY KEY, task_id INTEGER NOT NULL UNIQUE REFERENCES tasks(id));
CREATE TABLE IF NOT EXISTS erp_full_runs (
 id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, user_name TEXT NOT NULL,
 report TEXT NOT NULL, backup TEXT NOT NULL);
"""


def init(con):
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute('PRAGMA table_info(subscriptions)')}
    if 'erp_full_managed' not in cols:
        con.execute('ALTER TABLE subscriptions ADD COLUMN erp_full_managed INTEGER NOT NULL DEFAULT 0')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _date(value):
    try:
        d = ei.parse_date(value)
        if d:
            datetime.fromisoformat(d)
        return d
    except (ValueError, OverflowError):
        return None


def _money(value):
    # Native CRM amounts are whole rubles. Never silently round kopecks.
    try:
        n = Decimal(re.sub(r'[\s\u00a0₽]', '', str(value)).replace(',', '.'))
        return int(n) if n.is_finite() and n == n.to_integral_value() else None
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _files(con, ids):
    if not isinstance(ids, list) or not ids or any(type(i) is not int for i in ids):
        raise ei.ImportError_('Выберите файлы для сверки')
    result = []
    for uid in sorted(set(ids)):
        r = con.execute('SELECT * FROM erp_uploads WHERE id=?', (uid,)).fetchone()
        if not r:
            raise ei.ImportError_('Список файлов изменился. Повторите проверку')
        f = dict(r)
        for key in ('headers', 'rows', 'mapping'):
            f[key] = json.loads(f[key])
        f['digest'] = _hash([f[k] for k in ('kind', 'headers', 'rows', 'mapping')])
        result.append(f)
    return result


def _record(f, row):
    r = {k: row[v] if type(v) is int and 0 <= v < len(row) else '' for k, v in f['mapping'].items()}
    # Exact ERP headings take precedence over legacy fuzzy mappings. In the
    # task register ID is a task ID, never a student ID.
    hs = {ei._norm(h): str(row[i] if i < len(row) else '').strip() for i, h in enumerate(f['headers'])}
    aliases = {
        'erp_id': ['id посетителя', 'id клиента'], 'child_name': ['фио посетителя', 'посетитель', 'имя ребенка', 'фимилия и имя'],
        'phone': ['телефон посетителя', 'телефон'], 'parent_name': ['имя родителя'],
        'birth': ['дата рождения'], 'email': ['почта', 'email', 'e-mail'],
        'group': ['группа'], 'branch': ['филиал'], 'comment': ['комментарий'],
        'price': ['стоимость со скидкой, руб.(выручка для пиу)', 'стоимость со скидкой'], 'month': ['дата начала'], 'tariff': ['тип'],
        'date': ['дата оплаты'] if f['kind'] == 'payments' else ['дата занятия'],
        'date_alt': ['дата продажи'], 'amount': ['сумма'], 'method': ['вид оплаты'],
        'operation_id': ['id операции'], 'task_id': ['id задачи'], 'due': ['дедлайн'],
        'payment_type': ['тип платежа'],
        'event_at': ['дата события'], 'event': ['событие'], 'task_end': ['задача (end)'],
        'task_new': ['задача (new)'], 'assignee': ['исполнитель'], 'author': ['автор'],
        'start': ['дата начала'], 'end': ['дата окончания'], 'sale_at': ['дата продажи'],
        'discount': ['размер скидки'], 'phone2': ['второй телефон', 'телефон 2'],
        'balance': ['баланс'], 'task_status': ['состояние задачи', 'статус выполнения'],
        'done_at': ['дата выполнения'],
        'trial_at': ['дата пробного'],
    }
    for key, names in aliases.items():
        for name in names:
            if name in hs:
                r[key] = hs[name]
                break
    if f['kind'] == 'clients' and 'id' in hs:
        r['erp_id'] = hs['id']
    r['id_namespace'] = 'lead' if f['kind'] == 'clients' and 'имя ребенка' in hs and 'фио посетителя' not in hs else 'student'
    if hs.get('id заявки'):
        r['id_namespace'], r['erp_id'] = 'lead', hs['id заявки']
    if f['kind'] == 'clients' and 'дата пробного' not in hs:
        r.pop('trial_at', None)
    if f['kind'] == 'subs':
        for name in ('оплачено(руб.)', 'оплачено (руб.)', 'оплачено'):
            if name in hs:
                r['status'] = hs[name]
    if f['kind'] == 'tasks' and 'событие' in hs:
        # ERP appends a list separator even when there is only one visitor.
        # Keep internal separators: a multi-person event must not be guessed.
        r['child_name'] = r.get('child_name', '').rstrip('; ').strip()
        r['erp_id'] = ''
        r['task_id'] = hs.get('id', '')
        m = re.search(r'\bID\s*:\s*(\d+)', r.get('child_name', ''), re.I)
        if m:
            r['erp_id'] = m[1]
            r['id_namespace'] = 'lead'
    return r


class Matcher:
    def __init__(self, con):
        self.by_id, self.by_np, self.by_name = defaultdict(set), defaultdict(set), defaultdict(set)
        self.students = {}
        self.phones = defaultdict(set)
        self.identities = {(r['namespace'], r['source_id']): r['student_id']
                           for r in con.execute('SELECT * FROM erp_full_identities')}
        for row in con.execute('SELECT * FROM students'):
            s = dict(row)
            self.students[s['id']] = s
            if s.get('erp_id'):
                self.by_id[s['erp_id']].add(s['id'])
            self.by_name[ei._norm(s['name'])].add(s['id'])
        for s in con.execute('SELECT s.id,s.name,p.phone_norm FROM students s JOIN parents p ON p.family_id=s.family_id'):
            if s['phone_norm']:
                self.by_np[(ei._norm(s['name']), s['phone_norm'][-10:])].add(s['id'])
                self.phones[s['id']].add(s['phone_norm'][-10:])

    def find(self, r, kind):
        eid, name = r.get('erp_id', '').strip(), ei._norm(r.get('child_name', ''))
        phone = normalize_phone(r.get('phone', ''))[-10:]
        lead = r.get('id_namespace') == 'lead'
        # ERP leads and students have independent numeric sequences. A lead
        # must never inherit a student's identity just because IDs coincide.
        if lead and kind != 'clients':
            if ';' in name or len(re.findall(r'\bid\s*:', name)) != 1:
                return None, 'Событие связано с несколькими заявками или клиентами'
            sid = self.identities.get(('lead', eid))
            return (sid, '') if sid else (None, 'ID заявки ERP не сопоставлен с карточкой; ID ученика не используется')
        ids = self.by_id.get(eid, set()) if eid and not lead else set()
        if not ids and name and phone:
            ids = self.by_np.get((name, phone), set())
        if not ids and kind == 'clients' and not name and phone:
            # Legacy import uses this exact placeholder for an unnamed lead.
            # Match only another unnamed card, never a named sibling sharing
            # the parent's phone. Multiple placeholder cards remain ambiguous.
            ids = self.by_np.get((ei._norm(ei.NO_NAME), phone), set())
        # Task history omits phone and often ID. Unique full name is usable;
        # a first name alone must not be treated as a unique identity.
        if not ids and kind in ('tasks', 'visits') and len(name.split()) >= 2 and not eid and ';' not in name:
            ids = self.by_name.get(name, set())
        if len(ids) != 1:
            return None, 'Несколько карточек: ' + ', '.join(map(str, sorted(ids))) if ids else 'Не найдено однозначное совпадение'
        sid = next(iter(ids))
        s = self.students[sid]
        if lead and eid and self.identities.get(('lead', eid), sid) != sid:
            return None, 'ID заявки ERP связан с другой карточкой'
        if not lead and eid and s.get('erp_id') and s['erp_id'] != eid:
            return None, 'Совпали имя и телефон, но различаются ID ERP'
        # Reusing an ERP ID cannot silently change a known person's identity.
        if not lead and eid and name and name != ei._norm(s['name']):
            # A source payment may omit a patronymic or the appended parent's
            # name. Require both the exact ERP ID and the family's exact phone,
            # plus the same complete two-word name prefix in this case.
            known = ei._norm(s['name'])
            prefix = len(name.split()) >= 2 and known.startswith(name + ' ')
            verified_catalog = not phone and self.identities.get(('student', eid)) == sid
            if not (prefix and ((phone and phone in self.phones[sid]) or verified_catalog)):
                return None, 'ID ERP найден, имя отличается: ' + s['name']
        birth = _date(r.get('birth', ''))
        if birth and s['birth'] and birth[:10] != s['birth'][:10]:
            return None, 'Дата рождения не совпадает'
        return sid, ''


def _group(con, r):
    name = r.get('group', '').strip()
    if not name:
        return None
    for key in (ei._group_key(r.get('branch', ''), name), name):
        saved = con.execute('SELECT group_id FROM erp_group_map WHERE erp_name=?', (key,)).fetchone()
        if saved:
            return saved[0]
    # Explicit exact matches only. Old fuzzy matching can confuse branches.
    candidates = [g['id'] for g in con.execute('SELECT g.id,g.name,b.name branch FROM groups g JOIN branches b ON b.id=g.branch_id')
                  if ei._norm(name) in (ei._norm(g['name']), ei._norm(g['branch'] + ' · ' + g['name']))]
    return candidates[0] if len(candidates) == 1 else None


def _history(con, sid, ts, text, kind='erp_source'):
    old = con.execute('SELECT id FROM history WHERE student_id=? AND kind=? AND text=?', (sid, kind, text)).fetchone()
    return old[0] if old else con.execute('INSERT INTO history(student_id,ts,user_name,kind,text) VALUES (?,?,?,?,?)',
                                        (sid, ts, 'ERP', kind, text)).lastrowid


def _event_history(con, sid, ts, text, fid, index, counts, corrections):
    # Repair exact previously imported events in place. Unresolved identities
    # retain their original record and source evidence, but are quarantined
    # from the former client's timeline until an identity is established.
    old = list(con.execute("SELECT * FROM history WHERE kind='erp_task_event' AND user_name='ERP' AND text=?", (text,)))
    for h in old:
        correction = con.execute('SELECT * FROM erp_full_history_corrections WHERE history_id=?', (h['id'],)).fetchone()
        current_sid = correction['assigned_student_id'] if correction else h['student_id']
        if current_sid != sid:
            con.execute('INSERT INTO erp_full_history_corrections(history_id,previous_student_id,assigned_student_id,file_id,row_num) VALUES (?,?,?,?,?) ON CONFLICT(history_id) DO UPDATE SET assigned_student_id=excluded.assigned_student_id,file_id=excluded.file_id,row_num=excluded.row_num',
                        (h['id'], h['student_id'], sid, fid, index))
            if sid:
                con.execute('UPDATE history SET student_id=? WHERE id=?', (sid, h['id']))
            counts['corrected_task_links' if sid else 'quarantined_task_events'] += 1
            corrections.append({'history_id':h['id'], 'previous_student_id':current_sid, 'student_id':sid, 'file_id':fid, 'row_num':index})
    if old:
        return old[0]['id']
    return _history(con, sid, ts, text, 'erp_task_event') if sid else None


def _process(con, files, now):
    matcher = Matcher(con)
    report = {'files': [], 'counts': {}, 'issues': [], 'history_corrections': [], 'source_rows': 0, 'linked_rows': 0, 'native_rows': 0}
    counts = Counter()
    subs = defaultdict(list)
    used_legacy = set(r[0] for r in con.execute('SELECT payment_id FROM erp_full_operations'))
    # Resolve the client files first, regardless of upload order.
    for f in sorted(files, key=lambda x: (x['kind'] != 'clients', x['id'])):
        old = con.execute('SELECT id FROM erp_full_files WHERE digest=?', (f['digest'],)).fetchone()
        fid = old[0] if old else con.execute('INSERT INTO erp_full_files(digest,filename,kind,headers,rows,mapping,imported_at) VALUES (?,?,?,?,?,?,?)',
            (f['digest'], f['filename'], f['kind'], _json(f['headers']), _json(f['rows']), _json(f['mapping']), now)).lastrowid
        report['files'].append({'id': fid, 'filename': f['filename'], 'kind': f['kind'], 'rows': len(f['rows']), 'sha256': f['digest']})
        for index, row in enumerate(f['rows'], 2):
            report['source_rows'] += 1
            r = _record(f, row)
            sid, reason = matcher.find(r, f['kind'])
            state, tbl, target = 'unmatched', '', None
            if f['kind'] in ('payments','subs') and not any(str(v).strip() for v in row[:17]):
                state, reason = 'summary', 'Итоговая строка отчёта, не отдельная операция'
                counts['summary_rows'] += 1
            event_target = None
            if f['kind'] == 'tasks' and r.get('event') and _date(r.get('event_at', '')):
                event_text = 'ERP, задача '+r.get('task_id', '')+'\n'+'\n'.join(f'{h}: {v}' for h, v in zip(f['headers'], row) if v)
                event_target = _event_history(con, sid, _date(r['event_at']), event_text, fid, index, counts, report['history_corrections'])
                if event_target and not sid:
                    tbl, target = 'history_quarantine', event_target
            if sid:
                report['linked_rows'] += 1
                state = 'source'
                reason = 'Исходные поля сохранены в разделе ERP клиента'
                if f['kind'] == 'clients':
                    s = matcher.students[sid]
                    eid = r.get('erp_id', '').strip()
                    if eid:
                        key = (r['id_namespace'], eid)
                        if key not in matcher.identities:
                            con.execute('INSERT INTO erp_full_identities(namespace,source_id,student_id) VALUES (?,?,?)', (*key,sid))
                            matcher.identities[key] = sid
                            counts['linked_source_identities'] += 1
                    if eid and r['id_namespace'] == 'student' and not s.get('erp_id'):
                        con.execute('UPDATE students SET erp_id=? WHERE id=?', (eid, sid))
                        s['erp_id'] = eid
                        matcher.by_id[eid].add(sid)
                        counts['linked_ids'] += 1
                    if not s['birth'] and _date(r.get('birth', '')):
                        con.execute('UPDATE students SET birth=? WHERE id=?', (_date(r['birth'])[:10], sid))
                        counts['filled_births'] += 1
                    ps = list(con.execute('SELECT * FROM parents WHERE family_id=?', (s['family_id'],)))
                    ph = normalize_phone(r.get('phone', ''))[-10:]
                    matched = [p for p in ps if ph and p['phone_norm'][-10:] == ph]
                    if len(matched) == 1:
                        p = matched[0]
                        for field in ('email', 'name'):
                            value = r.get('parent_name' if field == 'name' else field, '').strip()
                            if value and (not p[field] or (field == 'name' and p[field] == 'Родитель')):
                                con.execute(f'UPDATE parents SET {field}=? WHERE id=?', (value, p['id']))
                                counts['filled_contacts'] += 1
                    # Full original contact row remains available even when a
                    # conflicting parent, balance or attachment cannot be mapped.
                    comment = r.get('comment', '').strip()
                    if comment:
                        _history(con, sid, _date(r.get('created', '')) or now, comment)
                    state, tbl, target = 'linked', 'students', sid
                    trial = _date(r.get('trial_at', ''))
                    if r.get('trial_at') and trial != (s.get('trial_at') or ''):
                        from . import smart, capacity
                        from .services import ApiError
                        try:
                            if s.get('trial_at') or not trial or len(trial) < 16:
                                raise ApiError('Дата пробного уже задана или в источнике нет точного времени')
                            if s['stage'] != 'trial_booked' or s['funnel'] != 'sales':
                                raise ApiError('В CRM нет подтверждённого этапа записи на пробное')
                            if not s['group_id'] or _group(con, r) != s['group_id']:
                                raise ApiError('Группа источника не совпадает с группой записи')
                            smart.require(con, sid, 'trial')
                            capacity.check_trial_group(con, s['group_id'])
                            capacity.ensure(con, s['group_id'], trial[:10], 'восстановить дату пробного')
                            con.execute('UPDATE students SET trial_at=?,trial_booked_at=COALESCE(trial_booked_at,?) WHERE id=?', (trial,now,sid))
                            _history(con, sid, now, 'Восстановлена дата подтверждённого пробного из источника: '+trial+'\n'+comment)
                            s['trial_at'] = trial
                            counts['restored_trial_dates'] += 1
                        except ApiError as exc:
                            state, reason = 'source', str(exc)
                elif f['kind'] == 'visits':
                    d, status, gid = _date(r.get('date', '')), ei.visit_status(r.get('status', '')), _group(con, r)
                    if not d or not status or not gid:
                        reason = 'Не определены дата, отметка или группа посещения'
                    else:
                        a = con.execute('SELECT * FROM attendance WHERE student_id=? AND group_id=? AND date=?', (sid, gid, d[:10])).fetchone()
                        if a and a['status'] != status:
                            reason = 'Отметка ERP отличается от CRM, отметка CRM сохранена'
                        else:
                            target = a['id'] if a else con.execute('INSERT INTO attendance(student_id,group_id,date,status,note) VALUES (?,?,?,?,?)',
                                (sid, gid, d[:10], status, r.get('comment', ''))).lastrowid
                            if not a:
                                counts['added_visits'] += 1
                            state, tbl = 'native', 'attendance'
                elif f['kind'] == 'payments':
                    d = _date(r.get('date', '')) or _date(r.get('date_alt', ''))
                    amount, op = _money(r.get('amount', '')), r.get('operation_id', '').strip()
                    method = ei.pay_method(r.get('method', ''))
                    refund = amount is not None and amount < 0 and ei._norm(r.get('payment_type','')) == 'возврат'
                    accepted_methods = {method, 'refund'} if refund else {method}
                    if amount is None or not d or not op:
                        reason = 'Для платежа нужны ID операции, точная сумма и дата'
                    elif amount == 0 or (amount < 0 and not refund):
                        reason = 'Нулевая сумма или отрицательная операция без типа Возврат требуют отдельной проверки'
                    else:
                        signature = _json([sid, d, amount, method])
                        linked = con.execute('SELECT * FROM erp_full_operations WHERE operation_id=?', (op,)).fetchone()
                        if linked:
                            p = con.execute('SELECT * FROM payments WHERE id=?', (linked['payment_id'],)).fetchone()
                            if p and linked['signature'] == signature and not p['cancelled'] and p['method'] in accepted_methods and (p['student_id'], p['amount'], p['paid_at'][:len(d)]) == (sid, amount, d):
                                state, tbl, target = 'native', 'payments', p['id']
                            else:
                                reason = 'Операция ERP уже связана с отличающимся платежом'
                        else:
                            candidates = [p for p in con.execute('SELECT * FROM payments WHERE student_id=? AND amount=? AND substr(paid_at,1,10)=? AND cancelled=0 AND user_name=? ORDER BY id',
                                (sid, amount, d[:10], 'ERP')) if p['id'] not in used_legacy and p['method'] in accepted_methods and (len(d) == 10 or p['paid_at'][:len(d)] == d)]
                            synthetic = con.execute("SELECT 1 FROM payments WHERE student_id=? AND cancelled=0 AND comment='Оплата до реестра платежей'", (sid,)).fetchone()
                            other = con.execute('SELECT 1 FROM payments WHERE student_id=? AND amount=? AND substr(paid_at,1,10)=? AND cancelled=0 AND user_name!=?', (sid, amount, d[:10], 'ERP')).fetchone()
                            if candidates:
                                target = candidates[0]['id']
                                used_legacy.add(target)
                                counts['matched_payments'] += 1
                            elif synthetic or other:
                                reason = 'Есть платёж CRM или техническая оплата, нужна сверка перед добавлением'
                            else:
                                target = con.execute('INSERT INTO payments(student_id,amount,method,paid_at,comment,user_name,confirmed,confirmed_by,confirmed_at,created_at) VALUES (?,?,?,?,?,?,1,?,?,?)',
                                    (sid, amount, 'refund' if refund else method, d if len(d) > 10 else d+'T12:00', 'ERP, операция '+op, 'ERP', 'Перенос ERP', now, now)).lastrowid
                                used_legacy.add(target)
                                counts['added_payments'] += 1
                            if target:
                                con.execute('INSERT INTO erp_full_operations(operation_id,payment_id,signature) VALUES (?,?,?)', (op, target, signature))
                                state, tbl = 'native', 'payments'
                elif f['kind'] == 'tasks':
                    if r.get('event'):
                        # Event log is history, not a new open task for every event.
                        ts = _date(r.get('event_at', ''))
                        if ts:
                            target = event_target
                            state, tbl = 'native', 'history'
                        else:
                            reason = 'Не определена дата события задачи'
                    else:
                        title, due = r.get('task', '').strip(), _date(r.get('due', ''))
                        status = ei._norm(r.get('task_status', ''))
                        state_task = 'done' if status in ('done', 'выполнена', 'выполнено', 'закрыта') else 'open' if status in ('open', 'открыта', 'новая') else None
                        if not title or not due or not state_task or not r.get('task_id'):
                            reason = 'Нужны ID, срок и явный статус задачи; неизвестный статус не превращён в открытую задачу'
                        else:
                            from . import tasks as task_service
                            key = 'erp-full-task:'+r['task_id']
                            t = con.execute('SELECT * FROM tasks WHERE auto_key=?', (key,)).fetchone()
                            if not t:
                                t = con.execute('SELECT t.* FROM tasks t JOIN erp_full_task_links l ON l.task_id=t.id WHERE l.source_id=?', (r['task_id'],)).fetchone()
                            kind = task_service.ERP_STATUSES.get(title, 'manual')
                            translated = task_service.KINDS[kind][0] if kind != 'manual' else title
                            if t:
                                if t['student_id'] == sid and t['status'] == state_task and t['due_at'][:10] == due[:10] and t['title'] in (title,translated):
                                    state, tbl, target = 'native', 'tasks', t['id']
                                else:
                                    reason = 'Существующая задача отличается от ERP'
                            else:
                                legacy = list(con.execute("SELECT t.* FROM tasks t LEFT JOIN erp_full_task_links l ON l.task_id=t.id WHERE t.student_id=? AND t.title IN (?,?) AND substr(t.due_at,1,10)=? AND t.status=? AND t.source='erp' AND l.task_id IS NULL AND COALESCE(t.auto_key,'') NOT LIKE 'erp-full-task:%'", (sid, title, translated, due[:10], state_task)))
                                uncertain = con.execute("SELECT t.id FROM tasks t LEFT JOIN erp_full_task_links l ON l.task_id=t.id WHERE t.student_id=? AND ((t.source='erp' AND l.task_id IS NULL AND COALESCE(t.auto_key,'') NOT LIKE 'erp-full-task:%') OR (t.title IN (?,?) AND substr(t.due_at,1,10)=?)) LIMIT 1", (sid,title,translated,due[:10])).fetchone()
                                if len(legacy) == 1:
                                    target = legacy[0]['id']
                                    con.execute('INSERT INTO erp_full_task_links(source_id,task_id) VALUES (?,?)', (r['task_id'],target))
                                    counts['matched_tasks'] += 1
                                    state, tbl = 'native', 'tasks'
                                elif uncertain:
                                    reason = 'В CRM есть задача без однозначной связи с ID ERP; повторная задача не создана'
                                else:
                                    assignees = list(con.execute('SELECT id FROM users WHERE name=?', (r.get('assignee', ''),)))
                                    target = con.execute('INSERT INTO tasks(kind,title,student_id,assignee_id,due_at,status,note,source,auto_key,created_at,done_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                                        (kind, title, sid, assignees[0][0] if len(assignees)==1 else task_service.owner(con), due if len(due)>10 else due+'T10:00', state_task,
                                         r.get('comment', '')+'\nИсполнитель ERP: '+r.get('assignee', ''), 'erp', key, now, _date(r.get('done_at', '')))).lastrowid
                                    counts['added_tasks'] += 1
                                    state, tbl = 'native', 'tasks'
                            if state == 'native':
                                old_note = con.execute('SELECT note FROM tasks WHERE id=?',(target,)).fetchone()[0]
                                evidence = '[ERP задача '+r['task_id']+']\nСрок ERP: '+due+'\nИсполнители ERP: '+r.get('assignee','')+'\n'+r.get('comment','')
                                if evidence not in old_note:
                                    con.execute('UPDATE tasks SET note=? WHERE id=?', ((old_note+'\n\n'+evidence).strip(),target))
                                    counts['supplemented_task_notes'] += 1
                elif f['kind'] == 'subs':
                    kind = ei.sub_kind(r.get('tariff', ''))
                    ym, gid, price = ei.parse_month(r.get('month', '')), _group(con, r), _money(r.get('price', ''))
                    if kind in ('trial', 'makeup'):
                        reason = 'Вводное занятие или отработка сохранены как исходная продажа ERP'
                    elif not ym or not gid or price is None:
                        reason = 'Не определены месяц, группа или точная стоимость абонемента'
                    else:
                        subs[(sid, gid, ym)].append((fid, index, r, price))
                        reason = 'Проверяется сумма продаж месяца'
                if state == 'native':
                    report['native_rows'] += 1
                    reason = 'Запись сверена с CRM; все исходные поля сохранены'
            con.execute('INSERT INTO erp_full_rows(file_id,row_num,student_id,state,reason,target_table,target_id) VALUES (?,?,?,?,?,?,?) ON CONFLICT(file_id,row_num) DO UPDATE SET student_id=excluded.student_id,state=excluded.state,reason=excluded.reason,target_table=excluded.target_table,target_id=excluded.target_id',
                        (fid, index, sid, state, reason, tbl, target))
    for (sid, gid, ym), rows in subs.items():
        # No sale ID exists in this ERP report. Identical rows from overlapping
        # files cannot safely be added twice or silently collapsed.
        fingerprints = [_hash(r) for _, _, r, _ in rows]
        reason = ''
        if len(set(fingerprints)) != len(fingerprints):
            reason = 'Повторяющиеся продажи без уникального ID: проверьте пересечение файлов'
        price = sum(p for _, _, _, p in rows)
        lessons = sum(ei.sub_lessons(r) for _, _, r, _ in rows)
        paid = all(ei.sub_status(r.get('status',''),p) == 'paid' for _,_,r,p in rows)
        status = 'paid' if paid else 'invoiced'
        tariff = 'flex' if any('гибк' in ei._norm(r.get('tariff','')) for _,_,r,_ in rows) else 'fixed'
        sale_dates = [_date(r.get('sale_at','')) for _,_,r,_ in rows]
        # The ERP sales report has no payment date. Do not invent one.
        native = list(con.execute("SELECT x.* FROM subscriptions x JOIN enrollments e ON e.id=x.enrollment_id WHERE x.student_id=? AND e.group_id=? AND x.month=? AND x.status!='cancelled'", (sid,gid,ym)))
        if not reason and not native and lessons > 0 and price >= 0:
            enrollments = list(con.execute('SELECT id FROM enrollments WHERE student_id=? AND group_id=?',(sid,gid)))
            if len(enrollments) > 1:
                reason = 'Несколько записей ученика в одной группе'
            else:
                eid = enrollments[0][0] if enrollments else con.execute('INSERT INTO enrollments(student_id,group_id,tariff,active,created_at) VALUES (?,?,?,0,?)',(sid,gid,tariff,now)).lastrowid
                subid = con.execute('INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,created_at,paid_at,erp_full_managed) VALUES (?,?,?,?,?,?,?,?,?,NULL,1)',
                    (sid,eid,ym,tariff,lessons,price//lessons,price,status,min((d for d in sale_dates if d),default=now))).lastrowid
                native = [con.execute('SELECT * FROM subscriptions WHERE id=?',(subid,)).fetchone()]
                counts['added_subscriptions'] += 1
        if not reason and len(native) == 1 and native[0]['price'] == price and native[0]['lessons'] == lessons and native[0]['status'] == status:
            for fid,index,_,_ in rows:
                con.execute("UPDATE erp_full_rows SET state='native',reason=?,target_table='subscriptions',target_id=? WHERE file_id=? AND row_num=?",
                    ('Сумма и число занятий сверены; остальные поля сохранены в исходных продажах',native[0]['id'],fid,index))
                report['native_rows'] += 1
        else:
            reason = reason or ('Стоимость, статус или число занятий отличаются от CRM' if native else 'Абонемент отсутствует в CRM, требуется сверка числа занятий, продаж месяца и платежей')
            for fid,index,_,_ in rows:
                con.execute('UPDATE erp_full_rows SET reason=? WHERE file_id=? AND row_num=?', (reason,fid,index))
    file_ids = sorted(set(f['id'] for f in report['files']))
    for fid in file_ids:
        f = dict(con.execute('SELECT filename,kind,headers,rows,mapping FROM erp_full_files WHERE id=?', (fid,)).fetchone())
        for key in ('headers', 'rows', 'mapping'):
            f[key] = json.loads(f[key])
        for row in con.execute("SELECT * FROM erp_full_rows WHERE file_id=? AND state NOT IN ('native','linked','summary')", (fid,)):
            rec = _record(f, f['rows'][row['row_num']-2])
            identity = {k: rec.get(k, '') for k in ('child_name','erp_id','id_namespace','phone','group','event_at','date','month','amount','price','task_id')}
            report['issues'].append({'file': f['filename'], 'kind': f['kind'], **dict(row), 'source_identity': identity})
    report['counts'] = dict(counts)
    report['issue_count'] = len(report['issues'])
    report['complete'] = not report['issues']
    return report


def plan(con, ids):
    files = _files(con, ids)
    # Serialize the current snapshot into a separate in-memory database. Dry run
    # cannot accidentally commit CRM changes, migrations or outbound messages.
    clone = sqlite3.connect(':memory:', isolation_level=None)
    clone.row_factory = sqlite3.Row
    clone.create_function('ulower', 1, lambda v: v.lower() if isinstance(v, str) else v)
    try:
        snapshot = bytearray(con.serialize())
        # serialize includes the current WAL contents, but deserialize cannot
        # open a WAL-mode image in memory. SQLite header bytes 18/19 are the
        # file read/write format versions: use rollback mode for this copy.
        snapshot[18:20] = b'\x01\x01'
        clone.deserialize(bytes(snapshot))
        clone.execute('PRAGMA foreign_keys=ON')
        report = _process(clone, files, '2000-01-01T00:00')
    finally:
        clone.close()
    report['token'] = _hash(report)
    return report


def apply(con, ids, token, user, backup):
    if not backup:
        raise ei.ImportError_('Нет резервной копии перед переносом')
    expected = plan(con, ids)
    if not token or token != expected['token']:
        raise ei.ImportError_('Данные изменились после проверки. Повторите сверку')
    con.execute('SAVEPOINT erp_full_apply')
    try:
        now = datetime.now().isoformat(timespec='seconds')
        report = _process(con, _files(con, ids), now)
        report['at'] = now
        run = con.execute('INSERT INTO erp_full_runs(created_at,user_name,report,backup) VALUES (?,?,?,?)',
                          (now, user.get('name',''), _json(report), backup)).lastrowid
        con.execute('INSERT INTO audit(ts,user_id,action,detail) VALUES (?,?,?,?)',
                    (now,user.get('id'),'erp.full',_json({'run':run,'source_rows':report['source_rows'],'issue_count':report['issue_count']})))
        con.execute('RELEASE erp_full_apply')
        return report
    except BaseException:
        con.execute('ROLLBACK TO erp_full_apply')
        con.execute('RELEASE erp_full_apply')
        raise


def rows(con, sid=None, issues=False, offset=0):
    where, args = [], []
    if sid is not None:
        where.append('r.student_id=?'); args.append(sid)
    if issues:
        where.append("r.state NOT IN ('native','linked','summary')")
    sql = ' FROM erp_full_rows r JOIN erp_full_files f ON f.id=r.file_id'+(' WHERE '+' AND '.join(where) if where else '')
    total = con.execute('SELECT COUNT(*)'+sql, args).fetchone()[0]
    result = []
    cache = {}
    for row in con.execute('SELECT r.*,f.filename,f.kind'+sql+' ORDER BY r.file_id,r.row_num LIMIT 100 OFFSET ?', (*args,offset)):
        if row['file_id'] not in cache:
            f = con.execute('SELECT headers,rows FROM erp_full_files WHERE id=?', (row['file_id'],)).fetchone()
            cache[row['file_id']] = (json.loads(f['headers']),json.loads(f['rows']))
        headers, body = cache[row['file_id']]
        result.append(dict(row)|{'fields': list(zip(headers,body[row['row_num']-2]))})
    return {'total':total,'offset':offset,'items':result}
