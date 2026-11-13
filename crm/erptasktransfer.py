"""Task-register-only migration for any date, with preview, stable ERP IDs and audit.

Does not infer task workflow from event history or touch payments/attendance.
"""
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from . import erpfull, erpimport, tasks
from .services import ApiError


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def init(con):
    con.executescript('''CREATE TABLE IF NOT EXISTS erp_task_transfer_runs (
        id INTEGER PRIMARY KEY, created_at TEXT NOT NULL,user_name TEXT NOT NULL,
        backup TEXT NOT NULL,report TEXT NOT NULL,sources TEXT NOT NULL);
    ''')


def _parse(file,row):
    hs={erpimport._norm(k):str(row[i] if i<len(row) else '').strip() for i,k in enumerate(file['headers'])}
    def val(*keys):
        return next((hs[k] for k in keys if k in hs),'')
    if 'событие' in hs:
        raise ApiError('Это история событий задач. Нужен актуальный реестр задач, а не журнал событий')
    source=val('id задачи','№ задачи','номер задачи','id')
    client=val('id посетителя','id клиента','id заявки','erp id клиента')
    namespace='lead' if val('id заявки') else 'student'
    if not source or not client:
        raise ApiError('Нужны отдельные ID задачи и ID клиента ERP; совпадение имён не используется')
    title=val('задача','название задачи','тип задачи')
    due=erpfull._date(val('дедлайн','срок','дата выполнения','дата задачи'))
    raw_state=val('состояние задачи','статус выполнения','статус задачи')
    state=erpimport._norm(raw_state)
    states={'open':'open','открыта':'open','новая':'open','не выполнено':'open','не выполнена':'open',
            'done':'done','выполнена':'done','выполнено':'done',
            'cancelled':'cancelled','архив':'cancelled','в архиве':'cancelled','удалена':'cancelled'}
    if not title or not due or state not in states:
        raise ApiError('Нужны название, корректная дата и явное состояние: открыта / выполнена / архив. Неизвестный статус не подменяется')
    completed=erpfull._date(val('выполнено','дата фактического выполнения','дата завершения')) or ''
    return {'erp_task_id':source,'client_erp_id':client,'namespace':namespace,'title':title,'due_at':due[:10]+'T23:59',
            'status':states[state],'note':val('комментарий','описание'),'done_at':completed if states[state]=='done' else None,
            'assignee':val('ответственный','исполнитель'),'author':val('создатель','автор'),'source_status':raw_state}


def prepare(con,ids):
    files=erpfull._files(con,ids)
    if any(f['kind']!='tasks' for f in files):
        raise ApiError('Здесь можно переносить только файлы задач; оплаты, абонементы и посещения не допускаются')
    issues=[]; grouped=defaultdict(list); source_rows=0
    for f in files:
        for n,row in enumerate(f['rows'],2):
            if not any(str(x).strip() for x in row):continue
            source_rows+=1
            location={'file':f['filename'],'row':n}
            try:
                rec=_parse(f,row);grouped[rec['erp_task_id']].append((rec,location))
            except ApiError as e:issues.append({**location,'reason':e.message})
    actions=[]; duplicates=0; by_date=Counter(); by_state=Counter()
    for erpid,entries in grouped.items():
        rec,where=entries[0]
        if len({_json(r) for r,_ in entries})!=1:
            issues.extend({**w,'erp_task_id':erpid,'reason':'В выбранных файлах разные версии одной задачи ERP'} for _,w in entries)
            continue
        duplicates+=len(entries)-1
        sid_rows=con.execute('SELECT student_id FROM erp_full_identities WHERE namespace=? AND source_id=?',
                             (rec['namespace'],rec['client_erp_id'])).fetchall()
        if not sid_rows and rec['namespace']=='student':
            sid_rows=con.execute('SELECT id FROM students WHERE erp_id=?',(rec['client_erp_id'],)).fetchall()
        if len(sid_rows)!=1:
            issues.append({**where,'erp_task_id':erpid,'reason':'Не найдена однозначная связь клиента по ID ERP; сначала свяжите клиента'})
            continue
        sid=sid_rows[0][0]
        kind=tasks.ERP_STATUSES.get(rec['title'],'manual')
        translated=tasks.KINDS.get(kind,('',))[0] if kind!='manual' else rec['title']
        old=con.execute('SELECT t.* FROM tasks t JOIN erp_full_task_links l ON l.task_id=t.id WHERE l.source_id=?',(erpid,)).fetchone()
        if not old:old=con.execute('SELECT * FROM tasks WHERE auto_key=?',('erp-full-task:'+erpid,)).fetchone()
        if old and (old['student_id']!=sid or old['source']!='erp' or (str(old['auto_key'] or '').startswith('erp-full-task:') and old['auto_key']!='erp-full-task:'+erpid)):
            issues.append({**where,'erp_task_id':erpid,'reason':'Конфликт существующей связи ERP; ручная задача не изменяется'})
            continue
        if not old:
            candidates=con.execute("""SELECT t.* FROM tasks t LEFT JOIN erp_full_task_links l ON l.task_id=t.id
                WHERE t.student_id=? AND t.source='erp' AND t.title IN (?,?) AND substr(t.due_at,1,10)=?
                AND l.task_id IS NULL AND COALESCE(t.auto_key,'') NOT LIKE 'erp-full-task:%'""",(sid,rec['title'],translated,rec['due_at'][:10])).fetchall()
            if len(candidates)>1:
                issues.append({**where,'erp_task_id':erpid,'reason':'Несколько старых задач CRM без ID ERP; нужна сверка'})
                continue
            if candidates:old=candidates[0]
        kind=tasks.ERP_STATUSES.get(rec['title'],'manual')
        if not old:
            auto=con.execute("""SELECT id FROM tasks WHERE student_id=? AND source NOT IN ('erp','manual')
                AND status='open' AND substr(due_at,1,10)=? AND (title=? OR kind=?)""",(sid,rec['due_at'][:10],rec['title'],kind)).fetchall() if kind!='manual' else []
            if auto:
                issues.append({**where,'erp_task_id':erpid,'reason':'Есть автоматическая задача CRM на этот день; сначала сверьте дубль','crm_task_ids':[x[0] for x in auto]})
                continue
        after={'kind':kind,'title':rec['title'],'student_id':sid,'assignee_id':None,'due_at':rec['due_at'],
               'status':rec['status'],'note':rec['note'],'source':'erp','auto_key':'erp-full-task:'+erpid,
               'done_at':rec['done_at'],'done_by':old['done_by'] if old and rec['status']!='open' else '', 'result':old['result'] if old else ''}
        before=dict(old) if old else None
        changed=not before or any(before[k]!=v for k,v in after.items())
        actions.append({'erp_task_id':erpid,'client_erp_id':rec['client_erp_id'],'namespace':rec['namespace'],
                        'action':'add' if not old else 'update' if changed else 'same','before':before,'after':after,
                        'source_fields':rec,'locations':[w for _,w in entries]})
        by_date[rec['due_at'][:10]]+=1;by_state[rec['status']]+=1
    targets=Counter(a['before']['id'] for a in actions if a['before'])
    ambiguous={tid for tid,n in targets.items() if n>1}
    if ambiguous:
        keep=[]
        for a in actions:
            if a['before'] and a['before']['id'] in ambiguous:
                issues.extend({**w,'erp_task_id':a['erp_task_id'],'reason':'Несколько ID ERP претендуют на одну старую задачу CRM; нужна точная сверка'} for w in a['locations'])
            else:keep.append(a)
        actions=keep
        by_date=Counter(a['after']['due_at'][:10] for a in actions)
        by_state=Counter(a['after']['status'] for a in actions)
    fingerprint=[dict(r) for r in con.execute('SELECT * FROM tasks ORDER BY id')]
    links=[list(r) for r in con.execute('SELECT * FROM erp_full_task_links ORDER BY source_id')]
    identities=[list(r) for r in con.execute('SELECT * FROM erp_full_identities ORDER BY namespace,source_id')]
    not_in_export=[dict(r) for r in con.execute("SELECT l.source_id AS erp_task_id,t.id AS crm_task_id,t.title,t.due_at,t.status FROM erp_full_task_links l JOIN tasks t ON t.id=l.task_id WHERE t.source='erp' ORDER BY l.source_id") if r['erp_task_id'] not in grouped]
    report={'not_in_selected_export':not_in_export,'source_rows':source_rows,'unique_tasks':len(actions),'duplicates_in_files':duplicates,
            'counts':dict(Counter(a['action'] for a in actions)),'by_date':dict(sorted(by_date.items())),
            'by_state':dict(by_state),'issues':issues,'actions':actions}
    report['token']=hashlib.sha256(_json([files,report,fingerprint,links,identities]).encode()).hexdigest()
    return report


def apply(con,ids,token,user,backup):
    if not backup:raise ApiError('Нет резервной копии. Задачи не перенесены')
    report=prepare(con,ids)
    if not token or token!=report['token']:raise ApiError('Данные изменились после проверки. Повторите сверку')
    now=datetime.now().isoformat(timespec='seconds')
    con.execute('SAVEPOINT task_transfer')
    try:
        for a in report['actions']:
            after=a['after'];fields=list(after)
            if a['action']=='add':
                tid=con.execute('INSERT INTO tasks('+','.join(fields)+',created_at) VALUES ('+','.join('?' for _ in fields)+',?)',
                                tuple(after[k] for k in fields)+(now,)).lastrowid
            else:
                tid=a['before']['id']
                if a['action']=='update':
                    con.execute('UPDATE tasks SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',tuple(after[k] for k in fields)+(tid,))
            con.execute('INSERT OR IGNORE INTO erp_full_task_links(source_id,task_id) VALUES (?,?)',(a['erp_task_id'],tid))
            link=con.execute('SELECT task_id FROM erp_full_task_links WHERE source_id=?',(a['erp_task_id'],)).fetchone()
            if not link or link[0]!=tid:raise ApiError('Связь ID задачи конфликтует; пакет отменён')
            # Verify every accepted row, including unchanged ones, against the actual stored result.
            actual=con.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
            if any(actual[k]!=v for k,v in after.items()):raise ApiError('Проверка перенесённой задачи не прошла; пакет отменён')
            a['crm_task_id']=tid
        report['verified_tasks']=len(report['actions']);report['at']=now
        con.execute('INSERT INTO erp_task_transfer_runs(created_at,user_name,backup,report,sources) VALUES (?,?,?,?,?)',
                    (now,user.get('name',''),backup,_json(report),_json(erpfull._files(con,ids))))
        con.execute('RELEASE task_transfer')
    except BaseException:
        con.execute('ROLLBACK TO task_transfer');con.execute('RELEASE task_transfer');raise
    return report


def latest(con):
    row=con.execute('SELECT report FROM erp_task_transfer_runs ORDER BY id DESC LIMIT 1').fetchone()
    return json.loads(row[0]) if row else {'source_rows':0,'counts':{},'by_date':{},'by_state':{},'issues':[],'actions':[]}
