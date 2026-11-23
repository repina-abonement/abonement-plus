"""Idempotent, non-financial transfer of reviewed client facts from work-chat exports."""
from __future__ import annotations

import json
import re
from datetime import datetime

from . import comms, services as sv, tasks


SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_day_transfer_events (
 source_id TEXT PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES students(id),
 source_chat TEXT NOT NULL, source_at TEXT NOT NULL, event_kind TEXT NOT NULL,
 payload TEXT NOT NULL, applied_at TEXT NOT NULL, applied_by TEXT NOT NULL DEFAULT ''
);
"""


ALIASES = {
    'миша':'михаил','женя':'евгений','даня':'даниил','дима':'дмитрий','саша':'александр',
    'вова':'владимир','леша':'алексей','лёша':'алексей','паша':'павел','ваня':'иван',
}


def init(con):
    con.executescript(SCHEMA)


def _words(value):
    words=re.findall(r'[а-яa-z]+',str(value or '').casefold().replace('ё','е'))
    return [ALIASES.get(word,word) for word in words]


def _match(con,event):
    phone=comms.normalize_phone(str(event.get('phone') or ''))
    name=_words(event.get('student_name'))
    candidates=[]
    for row in con.execute('''SELECT s.id,s.name,s.erp_id,f.id family_id FROM students s JOIN families f ON f.id=s.family_id
                              WHERE s.archived=0'''):
        if name and set(name)==set(_words(row['name'])):candidates.append(dict(row))
    if phone:
        by_phone={r['student_id'] for r in con.execute('''SELECT s.id student_id FROM students s JOIN parents p ON p.family_id=s.family_id
             WHERE s.archived=0 AND (phone_match(p.phone_norm,?) OR phone_match(p.phone,?))''',(phone,phone))}
        candidates=[row for row in candidates if row['id'] in by_phone] or ([dict(con.execute('SELECT id,name,erp_id,family_id FROM students WHERE id=?',(next(iter(by_phone)),)).fetchone())] if len(by_phone)==1 and not name else [])
    unique={row['id']:row for row in candidates}
    return list(unique.values())


def _current_tasks(con,sid):
    return [dict(r) for r in con.execute("SELECT id,title,due_at,note FROM tasks WHERE student_id=? AND status='open' ORDER BY due_at,id",(sid,))]


def _reconcile_trial_booking(con,sid,event):
    """A confirmed CRM booking supersedes the temporary imported follow-up."""
    if event.get('event_kind')!='trial_booking':return False
    student=con.execute('SELECT trial_at FROM students WHERE id=?',(sid,)).fetchone()
    day=str(event.get('event_date') or '')[:10]
    if not student or not day or str(student['trial_at'] or '')[:10]!=day:return False
    con.execute('UPDATE students SET priority=0 WHERE id=?',(sid,))
    con.execute("""UPDATE tasks SET status='cancelled',done_at=?,done_by='CRM',
        result='Пробное уже записано, действует автоматическое напоминание'
        WHERE student_id=? AND status='open' AND auto_key=?""",
        (sv.now_iso(),sid,'chat-day:'+str(event.get('source_id') or '').strip()))
    return True


def apply(con,data,user):
    events=data.get('events') if isinstance(data,dict) else None
    if not isinstance(events,list) or not events:raise sv.ApiError('В файле нет событий переноса')
    result={'applied':[],'already':[],'unmatched':[],'ambiguous':[],'errors':[]}
    for event in events:
        source=str(event.get('source_id') or '').strip()
        if not source:result['errors'].append({'name':event.get('student_name'),'error':'Нет ID сообщения источника'});continue
        old=con.execute('SELECT student_id FROM chat_day_transfer_events WHERE source_id=?',(source,)).fetchone()
        if old:
            _reconcile_trial_booking(con,old['student_id'],event)
            student=con.execute('SELECT id,name FROM students WHERE id=?',(old['student_id'],)).fetchone()
            result['already'].append({'source_id':source,'student_id':old['student_id'],'name':student['name'] if student else '', 'tasks':_current_tasks(con,old['student_id'])});continue
        matches=_match(con,event)
        if not matches:
            result['unmatched'].append({'source_id':source,'name':event.get('student_name'),'phone':event.get('phone','')});continue
        if len(matches)!=1:
            result['ambiguous'].append({'source_id':source,'name':event.get('student_name'),'candidates':[x['name'] for x in matches]});continue
        sid=matches[0]['id']; con.execute('SAVEPOINT chat_day_event')
        try:
            at=str(event.get('source_at') or '')[:19]
            datetime.fromisoformat(at)
            note=str(event.get('note') or '').strip()
            if not note:raise sv.ApiError('Пустое описание события')
            single_sale=None
            if event.get('event_kind')=='single_sale':
                from . import money, singles
                day=str(event.get('event_date') or '')[:10]
                student=con.execute('SELECT group_id,trial_group_id FROM students WHERE id=?',(sid,)).fetchone()
                gid=int(event.get('group_id') or student['trial_group_id'] or student['group_id'] or 0)
                if not gid:raise sv.ApiError('Не определена группа разового занятия')
                if singles.lesson_time(con,gid,day)!=str(event.get('lesson_time') or ''):
                    raise sv.ApiError('В подтверждённой группе на эту дату другое время занятия')
                visit=con.execute("SELECT * FROM single_visits WHERE student_id=? AND date=? AND status='booked'",(sid,day)).fetchone()
                subid=visit['subscription_id'] if visit else None
                if not subid:
                    sold=singles.sell_visit(con,sid,gid,day,user);subid=sold['id']
                sub=con.execute('SELECT status,price FROM subscriptions WHERE id=?',(subid,)).fetchone()
                amount=int(event.get('amount') or 0)
                if not sub or sub['price']!=amount:raise sv.ApiError('Стоимость разового в CRM не совпадает с подтверждённой суммой')
                if sub['status']!='paid':
                    money.add_payment(con,sid,amount,str(event.get('payment_method') or ''),user,subscription_id=subid,
                                      comment='Подтверждено СЗ, сообщение '+source,paid_at=str(event.get('paid_at') or at)[:19])
                singles.sync_paid_tasks(con,datetime.now(),sid)
                con.execute("""UPDATE tasks SET status='cancelled',done_at=?,result='Заменено автоматическими задачами оплаченного разового'
                    WHERE student_id=? AND status='open' AND source='manual' AND title LIKE ?""",
                    (sv.now_iso(),sid,'%разовом занятии '+day[8:10]+'.'+day[5:7]+'%'))
                single_sale={'subscription_id':subid,'date':day,'group_id':gid}
            con.execute('INSERT INTO history(student_id,ts,user_name,kind,text) VALUES (?,?,?,?,?)',(sid,at,'Перенос СЗ','chat_transfer',note))
            marked=False
            if event.get('event_kind')=='trial_result' and isinstance(event.get('came'),bool):
                student=con.execute('SELECT trial_at FROM students WHERE id=?',(sid,)).fetchone()
                if student and str(student['trial_at'] or '')[:10]==str(event.get('event_date') or '')[:10]:
                    sv.mark_trial(con,sid,event['came'],user);marked=True
                if marked:
                    con.execute("""UPDATE tasks SET status='cancelled',done_at=?,result='Результат ПУ подтверждён СЗ и тренером'
                        WHERE student_id=? AND status='open' AND source='manual' AND due_at<=?
                        AND (title LIKE 'После ПУ%' OR title LIKE '%пришёл ли%' OR title LIKE '%результат%ПУ%')""",
                        (sv.now_iso(),sid,at[:10]+'T23:59'))
            booked=_reconcile_trial_booking(con,sid,event)
            if event.get('task_title') and event.get('task_due') and not booked:
                tasks.add_task(con,'manual',sid,str(event['task_due'])[:16],auto_key='chat-day:'+source,
                               title=str(event['task_title'])[:300],note=str(event.get('task_note') or note)[:4000],source='erp')
            con.execute('INSERT INTO chat_day_transfer_events VALUES (?,?,?,?,?,?,?,?)',(source,sid,str(event.get('source_chat') or ''),at,str(event.get('event_kind') or 'note'),json.dumps(event,ensure_ascii=False),sv.now_iso(),user.get('name','')))
            tasks.ensure_client_task_coverage(con)
            con.execute('RELEASE chat_day_event')
            result['applied'].append({'source_id':source,'student_id':sid,'name':matches[0]['name'],'trial_marked':marked,'single_sale':single_sale,'added':note,'tasks':_current_tasks(con,sid)})
        except BaseException as error:
            con.execute('ROLLBACK TO chat_day_event');con.execute('RELEASE chat_day_event')
            result['errors'].append({'source_id':source,'name':event.get('student_name'),'error':str(error)})
    sv.audit(con,user,'chat_day_transfer',json.dumps({k:len(v) for k,v in result.items()},ensure_ascii=False))
    return result
