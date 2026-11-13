"""Редактирование шапки и семейных контактов без переноса/удаления диалогов."""
import hashlib,json,re
from datetime import date
from . import services as sv,smart
from .db import normalize_phone


def init(con):
    if 'is_primary' not in {r[1] for r in con.execute('PRAGMA table_info(parents)')}:
        con.execute('ALTER TABLE parents ADD COLUMN is_primary INTEGER NOT NULL DEFAULT 0')


def signature(con,sid):
    s=sv.get_student_row(con,sid)
    fields={k:s[k] for k in ('id','family_id','name','birth','stage','funnel','group_id')}
    fields['note']=con.execute('SELECT note FROM families WHERE id=?',(s['family_id'],)).fetchone()[0]
    fields['parents']=[dict(r) for r in con.execute('SELECT id,name,phone,phone_norm,email,messenger,relation,is_primary FROM parents WHERE family_id=? ORDER BY id',(s['family_id'],))]
    return hashlib.sha256(json.dumps(fields,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def save(con,sid,data,user):
    con.execute('SAVEPOINT client_profile')
    try:
        s=sv.get_student_row(con,sid);fid=s['family_id']
        if data.get('expected')!=signature(con,sid):raise sv.ApiError('Карточка или контакты изменились. Откройте форму заново',409)
        name=' '.join(str(data.get('name') or '').split())
        if not name:raise sv.ApiError('Укажите имя ребёнка')
        birth=str(data.get('birth') or '')
        if birth:
            try:b=date.fromisoformat(birth)
            except ValueError:raise sv.ApiError('Проверьте дату рождения')
            if not date(2000,1,1)<b<sv.today():raise sv.ApiError('Проверьте дату рождения')
        rows=data.get('parents')
        if not isinstance(rows,list) or not rows or len(rows)>20:raise sv.ApiError('Укажите контакты родителей')
        existing={r['id']:dict(r) for r in con.execute('SELECT * FROM parents WHERE family_id=?',(fid,))}
        selected=[i for i,p in enumerate(rows) if p.get('is_primary') is True]
        if len(selected)!=1:raise sv.ApiError('Отметьте одного основного родителя для общения')
        seen=set();phones=set();clean=[]
        for row in rows:
            if not isinstance(row,dict):raise sv.ApiError('Неверные данные контакта')
            try:pid=int(row.get('id') or 0)
            except (TypeError,ValueError):raise sv.ApiError('Неверный ID контакта')
            if pid and (pid not in existing or pid in seen):raise sv.ApiError('Контакт не принадлежит этой семье или указан дважды')
            if pid:seen.add(pid)
            pname=' '.join(str(row.get('name') or '').split());phone=str(row.get('phone') or '').strip();norm=normalize_phone(phone);email=str(row.get('email') or '').strip()
            if not pname:raise sv.ApiError('Укажите имя родителя')
            if phone and len(norm)!=11:raise sv.ApiError('Телефон родителя должен содержать 11 цифр')
            if norm and (norm in phones or con.execute('SELECT 1 FROM parents WHERE phone_norm=? AND family_id!=?',(norm,fid)).fetchone()):raise sv.ApiError('Этот телефон уже указан у другого контакта/семьи. Сначала сверьте карточки')
            if email and not smart.EMAIL_RE.match(email):raise sv.ApiError('Проверьте почту родителя')
            if norm:phones.add(norm)
            clean.append((pid,pname,phone,norm,email,str(row.get('messenger') or '').strip()[:200],str(row.get('relation') or '').strip()[:100],bool(row.get('is_primary'))))
        if seen!=set(existing):raise sv.ApiError('Все существующие контакты должны остаться в форме; удаление не предусмотрено')
        con.execute('UPDATE students SET name=?,birth=? WHERE id=?',(name,birth,sid))
        con.execute('UPDATE families SET note=? WHERE id=?',(str(data.get('note') or '').strip()[:500],fid))
        con.execute('UPDATE parents SET is_primary=0 WHERE family_id=?',(fid,))
        for pid,pname,phone,norm,email,messenger,relation,primary in clean:
            if pid:con.execute('UPDATE parents SET name=?,phone=?,phone_norm=?,email=?,messenger=?,relation=?,is_primary=? WHERE id=?',(pname,phone,norm,email,messenger,relation,int(primary),pid))
            else:con.execute('INSERT INTO parents(family_id,name,phone,phone_norm,email,messenger,relation,is_primary) VALUES (?,?,?,?,?,?,?,?)',(fid,pname,phone,norm,email,messenger,relation,int(primary)))
        if s['funnel']=='sales' and 'trial_at' in data and data['trial_at']!=(s['trial_at'] or ''):
            sv.update_student(con,sid,{'trial_at':data['trial_at']},user)
        stage=str(data.get('stage') or s['stage'])
        if s.get('erp_status') and stage!=s['stage']:raise sv.ApiError('У клиента сохранён статус ERP. Изменение этапа CRM не заменяет исходный статус ERP; выполните сверку источника')
        if stage!=s['stage']:sv.move_card(con,sid,s['funnel'],stage,user,reason=str(data.get('stage_reason') or ''),comment=str(data.get('stage_comment') or ''),due=str(data.get('stage_due') or ''))
        sv.log(con,sid,'Изменены данные ребёнка и контакты семьи в шапке карточки; основной контакт для общения обновлён',user,'system')
        sv.audit(con,user,'client.profile',str(sid))
        con.execute('RELEASE client_profile');return {'ok':True}
    except BaseException:
        con.execute('ROLLBACK TO client_profile');con.execute('RELEASE client_profile');raise
