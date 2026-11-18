"""Delete only explicitly erroneous clean cards, preserving shared family contacts and audit."""
import hashlib
import json
import re
import sqlite3
from . import services as sv,subcancel

CATEGORIES={'duplicate':'Дубликат','test':'Тестовая карточка','import_error':'Ошибка переноса','entry_error':'Ошибочное создание'}
PROTECTED={'subscriptions','payments','attendance','single_visits','erp_trial_visits','pay_links','historical_entries'}


def preview(con,sid,user):
    if not subcancel.can_cancel_error(con,user):raise sv.ApiError('Удаление ошибочных карточек доступно руководителю и Варе',403)
    student=dict(sv.get_student_row(con,sid));relations=[];blockers=[]
    for table, in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',table) or table=='deleted_client_cards':continue
        columns={r['name'] for r in con.execute(f'PRAGMA table_info("{table}")')}
        refs={r['from']:r['on_delete'] for r in con.execute(f'PRAGMA foreign_key_list("{table}")') if r['table']=='students'}
        if 'student_id' in columns:refs.setdefault('student_id','NO ACTION')
        for column,action in refs.items():
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',column):continue
            rows=[dict(r) for r in con.execute(f'SELECT * FROM "{table}" WHERE "{column}"=?',(sid,))]
            if not rows:continue
            relations.append({'table':table,'column':column,'rows':rows})
            if table in PROTECTED or action!='CASCADE':blockers.append({'table':table,'count':len(rows)})
    state={'student':student,'relations':relations}
    token=hashlib.sha256(json.dumps(state,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
    return {'student_id':sid,'name':student['name'],'token':token,'allowed':not blockers,'blockers':blockers,'state':state}


def delete(con,sid,user,data):
    if not subcancel.can_cancel_error(con,user):raise sv.ApiError('Удаление ошибочных карточек доступно руководителю и Варе',403)
    category=data.get('category');reason=str(data.get('reason') or '').strip()
    if category not in CATEGORIES or len(reason)<5 or data.get('confirmed_error') is not True:
        raise sv.ApiError('Удалять можно только ошибочные, тестовые или задвоенные карточки. Отказ и прекращение занятий не являются основанием удаления')
    q=preview(con,sid,user)
    if not q['allowed']:raise sv.ApiError('Есть оплаты, абонементы, посещения или связанные данные переноса. Сначала нужна сверка или объединение дубля; удаление заблокировано')
    if q['token']!=data.get('token'):raise sv.ApiError('Карточка изменилась — обновите проверку',409)
    con.execute('''CREATE TABLE IF NOT EXISTS deleted_client_cards (
        id INTEGER PRIMARY KEY,former_student_id INTEGER NOT NULL,category TEXT NOT NULL,reason TEXT NOT NULL,
        snapshot TEXT NOT NULL,deleted_at TEXT NOT NULL,deleted_by TEXT NOT NULL)''')
    con.execute('SAVEPOINT delete_erroneous_card')
    try:
        con.execute('INSERT INTO deleted_client_cards(former_student_id,category,reason,snapshot,deleted_at,deleted_by) VALUES (?,?,?,?,?,?)',
            (sid,category,reason[:1000],json.dumps(q['state'],ensure_ascii=False,default=str),sv.now_iso(),user.get('name','')))
        con.execute('DELETE FROM students WHERE id=?',(sid,))
        sv.audit(con,user,'student.delete_error',json.dumps({'former_id':sid,'category':category,'reason':reason[:1000]},ensure_ascii=False))
        con.execute('RELEASE delete_erroneous_card')
    except BaseException as error:
        con.execute('ROLLBACK TO delete_erroneous_card');con.execute('RELEASE delete_erroneous_card')
        if isinstance(error,sqlite3.IntegrityError):raise sv.ApiError('Карточка связана с другими записями — сначала нужна сверка связей') from None
        raise
    return {'ok':True,'deleted_id':sid}
