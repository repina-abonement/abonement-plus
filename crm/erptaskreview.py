"""Reviewed updates of existing ERP tasks, with optimistic concurrency and an audit trail."""
import hashlib
import json
import re
from datetime import datetime
from .services import ApiError

FIELDS = ('id','student_id','kind','title','due_at','status','note','source','auto_key','assignee_id','priority','done_at','done_by','result')

def enc(x):
    return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))

def prepare(con, rows):
    if not isinstance(rows,list) or not rows or len(rows)>500:
        raise ApiError('Нужен список проверенных задач')
    ids=set(); targets=set(); changes=[]
    for item in rows:
        source=str(item.get('erp_task_id',''))
        tid=item.get('task_id'); sid=item.get('student_id'); day=item.get('day','')
        if not source.isdigit() or source in ids or type(tid)!=int or tid in targets:
            raise ApiError('Повторный или неверный ID задачи')
        ids.add(source); targets.add(tid)
        if not re.fullmatch(r'2026-11-(09|1[0-5])',day):
            raise ApiError('Разрешены только даты 9-15 ноября 2026')
        r=con.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
        if not r: raise ApiError('Задача не найдена')
        before={k:r[k] for k in FIELDS}
        link=con.execute('SELECT source_id,task_id FROM erp_full_task_links WHERE source_id=? OR task_id=?',(source,tid)).fetchall()
        if any(str(x[0])!=source or x[1]!=tid for x in link):
            raise ApiError('Конфликт существующей связи ERP')
        identity=con.execute('SELECT student_id FROM erp_full_identities WHERE namespace=? AND source_id=?',(item.get('namespace'),str(item.get('client_erp_id')))).fetchone()
        if not identity or identity[0]!=sid or r['student_id']!=sid or r['source']!='erp':
            raise ApiError('Связь клиента не подтверждена или задача ручная')
        if not '2026-11-09'<=r['due_at'][:10]<='2026-11-15':
            raise ApiError('Исходная задача вне разрешённого диапазона')
        title=item.get('title'); state=item.get('status')
        if not isinstance(title,str) or not title.strip() or state not in ('open','done','cancelled'):
            raise ApiError('Неверное название или состояние')
        if r['auto_key'] and r['auto_key'].startswith('erp-full-task:') and r['auto_key']!='erp-full-task:'+source:
            raise ApiError('Конфликт уникального ключа ERP')
        note=str(item.get('erp_note') or '')
        block=f'[ERP задача {source}]\nСрок ERP: {day}\n{note}'.strip()
        after=dict(before,title=title,due_at=day+'T23:59',status=state,assignee_id=None)
        after['note']=r['note'] if block in (r['note'] or '') else ((r['note'] or '').rstrip()+'\n\n'+block).strip()
        if state=='open': after.update(done_at=None,done_by='',result='')
        # Preserve the existing unique key and all staff notes. The link table is authoritative.
        if before!=after or not link:
            expected=item.get('expected')
            if not isinstance(expected,dict) or any(expected.get(k)!=before[k] for k in FIELDS):
                raise ApiError(f'Задача CRM {tid} изменилась. Повторите чтение и сверку')
        changes.append({'erp_task_id':source,'before':before,'after':after,'linked':bool(link),'changed':before!=after or not link})
    fingerprint=[dict(r) for r in con.execute('SELECT * FROM tasks ORDER BY id')]
    links=[list(r) for r in con.execute('SELECT * FROM erp_full_task_links ORDER BY source_id')]
    identities=[list(r) for r in con.execute('SELECT * FROM erp_full_identities ORDER BY namespace,source_id')]
    token=hashlib.sha256(enc([rows,fingerprint,links,identities]).encode()).hexdigest()
    return {'token':token,'updated':sum(x['changed'] for x in changes),'added':0,'tasks':changes}

def apply(con,rows,token,user,backup):
    plan=prepare(con,rows)
    if token!=plan['token']:raise ApiError('Данные изменились после предварительной сверки. Повторите её')
    con.execute('''CREATE TABLE IF NOT EXISTS erp_task_review_audit (id INTEGER PRIMARY KEY,at TEXT NOT NULL,user_name TEXT NOT NULL,backup TEXT NOT NULL,report TEXT NOT NULL)''')
    for x in plan['tasks']:
        if not x['changed']:continue
        a=x['after']; cols=[k for k in FIELDS if k!='id']
        con.execute('UPDATE tasks SET '+','.join(k+'=?' for k in cols)+' WHERE id=?',tuple(a[k] for k in cols)+(a['id'],))
        con.execute('INSERT OR IGNORE INTO erp_full_task_links(source_id,task_id) VALUES (?,?)',(x['erp_task_id'],a['id']))
    con.execute('INSERT INTO erp_task_review_audit(at,user_name,backup,report) VALUES (?,?,?,?)',(datetime.now().isoformat(),user['name'],backup,enc(plan)))
    return plan
