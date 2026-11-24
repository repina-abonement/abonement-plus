"""Idempotent, non-financial transfer of reviewed client facts from work-chat exports."""
from __future__ import annotations

import json
import re
import calendar
from datetime import datetime, timedelta

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
    'костя':'константин',
}


def init(con):
    con.executescript(SCHEMA)
    # Repair cards created before booking began clearing the urgent site-lead
    # flag, and remove temporary chat-import tasks already superseded by a
    # real trial booking. This is deliberately idempotent on every startup.
    con.execute("""UPDATE students SET priority=0 WHERE priority=1 AND trial_at IS NOT NULL
        AND substr(trial_at,1,10)>=?""",(sv.today().isoformat(),))
    for row in con.execute("SELECT student_id,payload,event_kind FROM chat_day_transfer_events WHERE event_kind IN ('trial_booking','single_sale','subscription_sale','trial_result')").fetchall():
        try:
            event=json.loads(row['payload'])
            _reconcile_trial_booking(con,row['student_id'],event)
            _reconcile_single_sale(con,row['student_id'],event)
            _reconcile_subscription_sale(con,row['student_id'],event,{'id':0,'role':'admin','name':'CRM'})
            _reconcile_trial_outcome(con,row['student_id'],event)
            _reconcile_confirmed_sale(con,row['student_id'],event)
        except (TypeError,ValueError,KeyError):continue


def _words(value):
    words=re.findall(r'[а-яa-z]+',str(value or '').casefold().replace('ё','е'))
    return [ALIASES.get(word,word) for word in words]


def _match(con,event):
    erp_id=str(event.get('erp_id') or '').strip()
    if erp_id:
        row=con.execute('SELECT id,name,erp_id,family_id FROM students WHERE archived=0 AND erp_id=?',(erp_id,)).fetchone()
        if row:return [dict(row)]
    phone=comms.normalize_phone(str(event.get('phone') or ''))
    name=_words(event.get('student_name'))
    candidates=[]
    for row in con.execute('''SELECT s.id,s.name,s.erp_id,f.id family_id FROM students s JOIN families f ON f.id=s.family_id
                              WHERE s.archived=0'''):
        row_words=_words(row['name'])
        exact=name and set(name)==set(row_words)
        care_surname=(len(name)==1 and str(event.get('source_chat') or '').casefold().startswith('служба заботы')
                      and name[0] in row_words)
        if exact or care_surname:candidates.append(dict(row))
    if phone:
        by_phone={r['student_id'] for r in con.execute('''SELECT s.id student_id FROM students s JOIN parents p ON p.family_id=s.family_id
             WHERE s.archived=0 AND (phone_match(p.phone_norm,?) OR phone_match(p.phone,?))''',(phone,phone))}
        candidates=[row for row in candidates if row['id'] in by_phone] or ([dict(con.execute('SELECT id,name,erp_id,family_id FROM students WHERE id=?',(next(iter(by_phone)),)).fetchone())] if len(by_phone)==1 and not name else [])
    unique={row['id']:row for row in candidates}
    return list(unique.values())


def _current_tasks(con,sid):
    return [dict(r) for r in con.execute("SELECT id,title,due_at,note FROM tasks WHERE student_id=? AND status='open' ORDER BY due_at,id",(sid,))]


def batch_is_complete(result, extra_error=False):
    """A card that is missing today must remain eligible for a later retry."""
    return not extra_error and not result.get('errors') and not result.get('ambiguous') and not result.get('unmatched')


def saved_batch_can_skip(saved):
    return isinstance(saved,dict) and saved.get('state')=='complete' and not saved.get('unmatched')


def _staged_erp_profile(event,profiles):
    """Use a unique staged ERP identity; for namesakes prefer the one that still looks active."""
    if not str(event.get('source_chat') or '').casefold().startswith('служба заботы'):
        return None
    wanted=_words(event.get('student_name'))
    if len(wanted)<2:return None
    exact=[row for row in profiles if set(_words(row.get('name')))==set(wanted)]
    phone=comms.normalize_phone(str(event.get('phone') or ''))
    if phone:
        by_phone=[row for row in exact if comms.normalize_phone(str(row.get('phone') or ''))==phone]
        if len(by_phone)==1:return by_phone[0]
    if len(exact)==1:return exact[0]
    active=[row for row in exact if re.search(r'постоян|разов|оплачен|пробн|посетител',str(row.get('status') or ''),re.I)
            and not re.search(r'архив|отказ|неактив',str(row.get('status') or ''),re.I)]
    return active[0] if len(active)==1 else None


def _erp_group_for_event(profile,event):
    groups=[part.strip() for part in str(profile.get('group') or '').split(';') if part.strip()]
    tm=str(event.get('lesson_time') or event.get('trial_time') or '').strip()
    if tm:
        timed=[group for group in groups if tm in group]
        if len(timed)==1:return timed[0]
    return groups[0] if len(groups)==1 else ''


def _family_for_phone(con, phone):
    """Reuse one existing family when the verified parent phone already belongs to it."""
    normalized=comms.normalize_phone(str(phone or ''))
    if not normalized:return None
    rows=con.execute('''SELECT DISTINCT family_id FROM parents
        WHERE phone_match(phone_norm,?) OR phone_match(phone,?) ORDER BY family_id''',
        (normalized,normalized)).fetchall()
    return rows[0]['family_id'] if len(rows)==1 else None


def _reconcile_clarification_task(con,sid,event):
    resolved=str(event.get('resolved_clarification_note') or '').strip()
    if resolved:
        con.execute("""UPDATE tasks SET status='cancelled',done_at=?,done_by='CRM',result=?
            WHERE student_id=? AND status='open' AND auto_key=?""",
            (sv.now_iso(),resolved[:1000],sid,'chat-clarify:'+str(event.get('source_id') or '')))
        return None
    title=str(event.get('clarification_task_title') or '').strip()
    if not title:return None
    due=str(event.get('clarification_task_due') or (sv.today().isoformat()+'T23:59'))[:16]
    return tasks.add_task(con,'manual',sid,due,auto_key='chat-clarify:'+str(event.get('source_id') or ''),
                          title=title[:300],note=str(event.get('clarification_task_note') or event.get('note') or '')[:4000],source='manual')


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
    tasks.ensure_trial_confirm_task(con,sid)
    return True


def _reconcile_single_sale(con,sid,event):
    """Remove a manual reminder once the same dated single visit is paid."""
    if event.get('event_kind')!='single_sale':return False
    day=str(event.get('event_date') or '')[:10]
    if len(day)!=10:return False
    paid=con.execute("""SELECT 1 FROM single_visits v JOIN subscriptions s ON s.id=v.subscription_id
        WHERE v.student_id=? AND v.date=? AND v.status='booked' AND s.status='paid' LIMIT 1""",(sid,day)).fetchone()
    if not paid:return False
    label=day[8:10]+'.'+day[5:7]
    con.execute("""UPDATE tasks SET status='cancelled',done_at=?,done_by='CRM',
        result='Заменено автоматическими задачами оплаченного разового'
        WHERE student_id=? AND status='open' AND kind='manual'
        AND title LIKE ?""",(sv.now_iso(),sid,'%разовом занятии '+label+'%'))
    return True


def _reconcile_subscription_sale(con,sid,event,user):
    """Create the exact subscriptions and one payment stated in the care chat."""
    if event.get('event_kind')!='subscription_sale':return None
    from . import historyentry, money
    components=event.get('components')
    if not isinstance(components,list) or not components:raise sv.ApiError('В продаже не указаны месяцы и даты занятий')
    student=con.execute('SELECT group_id,trial_group_id FROM students WHERE id=?',(sid,)).fetchone()
    gid=int(event.get('group_id') or student['trial_group_id'] or student['group_id'] or 0)
    current=con.execute('SELECT weekdays,time FROM groups WHERE id=? AND active=1 AND is_makeup=0',(gid,)).fetchone()
    exact_current=bool(current)
    if exact_current and event.get('lesson_time'):
        exact_current=current['time']==event['lesson_time']
    if exact_current and event.get('weekdays') is not None:
        exact_current={int(x) for x in re.findall(r'\d',str(current['weekdays'] or ''))}=={int(x) for x in event['weekdays']}
    if (not exact_current) and event.get('lesson_time') and event.get('weekdays') is not None:
        branch_hint=str(event.get('branch_hint') or '').strip()
        wanted={int(x) for x in event['weekdays']}
        candidates=[]
        for row in con.execute('''SELECT g.id,g.weekdays FROM groups g JOIN branches b ON b.id=g.branch_id
            WHERE g.active=1 AND g.is_makeup=0 AND g.time=?
              AND (?='' OR lower(b.name) LIKE lower(?)) ORDER BY g.id''',
            (str(event['lesson_time']),branch_hint,'%'+branch_hint+'%' if branch_hint else '')):
            actual={int(x) for x in re.findall(r'\d',str(row['weekdays'] or ''))}
            if actual==wanted:candidates.append(row['id'])
        if len(candidates)==1:
            gid=int(candidates[0])
            con.execute('''UPDATE students SET group_id=?,
                trial_group_id=CASE WHEN COALESCE(trial_group_id,0)=0 THEN ? ELSE trial_group_id END WHERE id=?''',(gid,gid,sid))
    group=con.execute('SELECT weekdays,time FROM groups WHERE id=? AND active=1 AND is_makeup=0',(gid,)).fetchone()
    if not group:raise sv.ApiError('Не определена действующая группа подтверждённой продажи')
    if event.get('lesson_time') and group['time']!=event['lesson_time']:
        raise sv.ApiError('В карточке выбрана группа с другим временем занятия')
    if event.get('weekdays') is not None:
        actual={int(x) for x in re.findall(r'\d',str(group['weekdays'] or ''))}
        if actual!={int(x) for x in event['weekdays']}:
            raise sv.ApiError('В карточке выбрана группа с другими днями занятий')
    paid_at=str(event.get('paid_at') or event.get('source_at') or '')[:19]
    sale_day=str(event.get('sale_date') or paid_at)[:10]
    method=str(event.get('payment_method') or '')
    total=int(event.get('amount') or 0)
    if not total or not method:raise sv.ApiError('В подтверждённой продаже не указаны сумма или способ оплаты')
    discount_id=None
    if event.get('discount_percent'):
        rule=str(event.get('discount_rule') or 'first_on_trial_day')
        if rule=='large_family':
            discount=next((row for row in con.execute("SELECT * FROM discounts WHERE active=1 ORDER BY id")
                           if money.large_family_discount(dict(row)) and int(row['percent'])==int(event['discount_percent'])),None)
        else:
            discount=con.execute("SELECT id,percent FROM discounts WHERE rule=? AND active=1 ORDER BY id LIMIT 1",(rule,)).fetchone()
        if not discount or int(discount['percent'])!=int(event['discount_percent']):
            raise sv.ApiError('Подтверждённая скидка не совпадает с действующим правилом CRM')
        discount_id=discount['id']
    created=[];parts=[]
    for item in components:
        month=str(item.get('month') or '')
        price=int(item.get('price') or 0)
        dates=[str(x) for x in item.get('dates') or []]
        existing=con.execute("""SELECT s.id,s.price,s.status FROM subscriptions s
            LEFT JOIN enrollments e ON e.id=s.enrollment_id
            WHERE s.student_id=? AND s.month=? AND s.status!='cancelled'
              AND (e.group_id=? OR s.enrollment_id IS NULL) ORDER BY s.id LIMIT 1""",(sid,month,gid)).fetchone()
        if existing:
            if existing['price']!=price:raise sv.ApiError('Стоимость уже созданного абонемента не совпадает с подтверждённой продажей')
            subid=existing['id']
        else:
            recorded=historyentry.record(con,sid,{
                'request_id':f"chat-sale:{event.get('source_id')}:{month}",
                'record_trial':False,'sold':True,'sale_date':sale_day,'paid_date':sale_day,
                'month':month,'group_id':gid,'tariff':str(event.get('tariff') or 'fixed'),
                'price':price,'amount':0,'method':'','expected_balance':max(0,money.balance(con,sid)['deposit']),
                'discount_id':discount_id,
                'discount_percent':int(event.get('discount_percent') or 0),
                'lessons':[{'group_id':gid,'date':day} for day in dates],
                'dates_reason':str(item.get('dates_reason') or 'Состав занятий подтверждён сообщением СЗ')
            },user,trusted_confirmed=True)
            subid=recorded['subscription_id'];created.append(subid)
        parts.append({'subscription_id':subid,'amount':price})
    if sum(x['amount'] for x in parts)!=total:raise sv.ApiError('Сумма по месяцам не равна подтверждённой оплате')
    covered=money.balance(con,sid)['covered']
    if not all(covered.get(x['subscription_id'],0)>=x['amount'] for x in parts):
        duplicate=con.execute("""SELECT id FROM payments WHERE student_id=? AND amount=? AND method=?
            AND substr(paid_at,1,10)=? AND cancelled=0""",(sid,total,method,sale_day)).fetchone()
        if duplicate:raise sv.ApiError('Такая оплата уже есть, но не распределена по подтверждённым абонементам')
        money.add_payment(con,sid,total,method,user,paid_at=paid_at,invoice_parts=parts,
                          comment='Продажа подтверждена СЗ, сообщение '+str(event.get('source_id') or ''))
    if event.get('payment_method_needs_confirmation'):
        con.execute("""UPDATE payments SET method='online' WHERE student_id=? AND cancelled=0 AND amount=?
            AND comment=? AND method IN ('sbp','online')""",
            (sid,total,'Продажа подтверждена СЗ, сообщение '+str(event.get('source_id') or '')))
    tasks.schedule_renewal(con,sid)
    return {'subscription_ids':[x['subscription_id'] for x in parts],'created':created,'amount':total}


def _reconcile_trial_outcome(con,sid,event):
    """Turn an already known 'grow up and return later' answer into the right stage and one task."""
    if event.get('event_kind')!='trial_result':return False
    note=str(event.get('note') or '')
    later=event.get('outcome')=='later' or re.search(r'пусть подраст|секци[яи] в сад|подождать до весны|весной попробуют|пробовать пока не готовы',note,re.I)
    reinvite=event.get('outcome')=='reinvite'
    if not later and not reinvite:return False
    day=str(event.get('event_date') or '')[:10]
    student=con.execute('SELECT stage,refusal_reason,birth FROM students WHERE id=?',(sid,)).fetchone()
    spring=bool(re.search(r'весн|апрел',note,re.I))
    try:
        base=datetime.fromisoformat(day).date()
        if spring and student and student['birth']:
            born=datetime.fromisoformat(str(student['birth'])[:10]).date()
            month_index=born.month-1+35
            year=born.year+month_index//12
            month=month_index%12+1
            due=datetime(year,month,min(born.day,calendar.monthrange(year,month)[1]),11)
        elif event.get('followup_due'):due=datetime.fromisoformat(str(event['followup_due'])[:16])
        elif spring:due=datetime(base.year+1,3,1,11)
        else:due=datetime.combine(base+timedelta(days=7 if reinvite else 91),datetime.min.time()).replace(hour=11)
    except ValueError:return False
    if event.get('refusal_reason'):reason=str(event['refusal_reason'])
    elif re.search(r'весн|апрел',note,re.I):reason='Ребёнок пока не готов по возрасту, семья хочет вернуться весной'
    elif re.search(r'не смог|не заш[её]л|не готов',note,re.I):reason='Ребёнок пока не готов войти в тренировку, семья не готова повторять попытку сейчас'
    else:reason='Ребёнок пока не готов: занимается в саду, семья изначально не планировала покупку'
    before=student
    stage='reinvite_pu' if reinvite else 'later'
    con.execute("""UPDATE students SET stage=?,refusal_reason=?,contacted_after_trial=1,
        priority=0,stage_changed_at=? WHERE id=?""",(stage,reason,sv.now_iso(),sid))
    con.execute("""UPDATE tasks SET status='cancelled',done_at=?,done_by='CRM',
        result='Решение семьи уже известно: пригласить позже'
        WHERE student_id=? AND status='open' AND (
          (kind='manual' AND auto_key=?) OR kind='why_not_bought' OR trim(title)='Почему не купили'
        )""",(sv.now_iso(),sid,'chat-day:'+str(event.get('source_id') or '').strip()))
    from . import pu
    if event.get('followup_title'):title=str(event['followup_title'])
    elif reinvite:title='Предложить адаптационное повторное ПУ с родителем'
    elif re.search(r'весн|апрел',note,re.I):title='Связаться с семьёй весной и предложить подходящую группу'
    elif re.search(r'не смог|не заш[её]л|не готов',note,re.I):title='Пригласить снова на ПУ: ребёнок будет готов'
    else:title='Пригласить снова на ПУ: ребёнок подрос'
    pu.ensure_task(con,sid,due,datetime.fromisoformat(str(event.get('source_at') or day+'T12:00')[:19]),
                   title=title)
    if not before or before['stage']!=stage or before['refusal_reason']!=reason:
        action='Предложим адаптационное повторное ПУ' if reinvite else 'Пригласим снова'
        sv.log(con,sid,f'{reason}. {action} {due:%d.%m.%Y}',
               {'name':'Перенос СЗ'},'stage')
    return True


def _reconcile_confirmed_sale(con,sid,event):
    """Once a confirmed sale is present, keep only the automatic renewal chain."""
    if event.get('event_kind')!='trial_result':return False
    note=str(event.get('note') or '')
    if event.get('outcome')!='purchased' and not re.search(r'ПРОДАЖА НОЯ\+ДЕК|\bКупил\b',note,re.I):return False
    source='chat-day:'+str(event.get('source_id') or '').strip()
    required=set(event.get('subscription_months') or ['2026-11','2026-12'])
    paid={r[0] for r in con.execute("""SELECT month FROM subscriptions WHERE student_id=? AND status='paid'
        AND COALESCE(kind,'month')='month'""",(sid,))}
    if required.issubset(paid):
        con.execute("""UPDATE tasks SET status='cancelled',done_at=?,done_by='CRM',
            result='Продажа оформлена, дальше действует автоматическая цепочка продления'
            WHERE student_id=? AND status='open' AND (
              (kind='manual' AND auto_key=?) OR kind='why_not_bought' OR trim(title)='Почему не купили'
            )""",(sv.now_iso(),sid,source))
        from . import erpstatus
        erpstatus.sync_one(con,sid)
        return True
    return False


def apply(con,data,user):
    events=data.get('events') if isinstance(data,dict) else None
    if not isinstance(events,list) or not events:raise sv.ApiError('В файле нет событий переноса')
    # A reviewed day may mention a sale before the later line containing the
    # full trial result. Create-capable identity events must run first so the
    # remaining facts attach to that same card during one upload.
    events=sorted(events,key=lambda event:0 if event.get('create_if_missing') is True else 1)
    result={'applied':[],'already':[],'unmatched':[],'ambiguous':[],'errors':[]}
    erp_profiles=None
    for event in events:
        source=str(event.get('source_id') or '').strip()
        if not source:result['errors'].append({'name':event.get('student_name'),'error':'Нет ID сообщения источника'});continue
        old=con.execute('SELECT student_id FROM chat_day_transfer_events WHERE source_id=?',(source,)).fetchone()
        if old:
            con.execute('SAVEPOINT chat_day_existing')
            try:
                _reconcile_trial_booking(con,old['student_id'],event)
                _reconcile_single_sale(con,old['student_id'],event)
                _reconcile_subscription_sale(con,old['student_id'],event,user)
                _reconcile_trial_outcome(con,old['student_id'],event)
                _reconcile_confirmed_sale(con,old['student_id'],event)
                _reconcile_clarification_task(con,old['student_id'],event)
                con.execute('RELEASE chat_day_existing')
            except Exception as exc:
                con.execute('ROLLBACK TO chat_day_existing');con.execute('RELEASE chat_day_existing')
                result['errors'].append({'name':event.get('student_name'),'error':str(exc)})
                continue
            student=con.execute('SELECT id,name FROM students WHERE id=?',(old['student_id'],)).fetchone()
            result['already'].append({'source_id':source,'student_id':old['student_id'],'name':student['name'] if student else '', 'tasks':_current_tasks(con,old['student_id'])});continue
        matches=_match(con,event)
        created=False
        if not matches:
            if erp_profiles is None:
                from . import chatbulk
                erp_profiles=chatbulk._erp_profiles(con)
            profile=_staged_erp_profile(event,erp_profiles)
            if profile:
                event=dict(event,erp_create_if_missing=True,erp_id=profile['erp_id'],erp_profile=profile,
                           erp_group_name=_erp_group_for_event(profile,event))
            erp_allowed=(event.get('erp_create_if_missing') is True and str(event.get('erp_id') or '').strip()
                         and str(event.get('student_name') or '').strip())
            if erp_allowed:
                con.execute('SAVEPOINT chat_day_event')
                try:
                    profile=event.get('erp_profile') if isinstance(event.get('erp_profile'),dict) else {}
                    name=str(profile.get('name') or event['student_name']).strip()
                    group=con.execute('SELECT id FROM groups WHERE name=? ORDER BY active DESC,id LIMIT 1',(str(event.get('erp_group_name') or ''),)).fetchone()
                    if not group and event.get('erp_group_time'):
                        rows=con.execute("""SELECT g.id FROM groups g JOIN branches b ON b.id=g.branch_id
                            WHERE g.active=1 AND g.is_makeup=0 AND g.time=? AND b.name LIKE ? ORDER BY g.id""",
                            (str(event['erp_group_time']),'%'+str(event.get('erp_branch_hint') or '')+'%')).fetchall()
                        if len(rows)==1:group=rows[0]
                    gid=group['id'] if group else None
                    create_data={'name':name,'birth':str(profile.get('birth') or ''),
                        'parent_name':str(profile.get('parent_name') or ''),'phone':str(profile.get('phone') or ''),
                        'email':str(profile.get('email') or ''),'funnel':'members' if gid else 'sales',
                        'group_id':gid,'source':str(event.get('source') or 'ERP')}
                    family_id=_family_for_phone(con,create_data['phone'])
                    if family_id:create_data['family_id']=family_id
                    if not create_data['parent_name'] or not create_data['phone']:
                        create_data['family_id']=con.execute('INSERT INTO families(name) VALUES (?)',(name.split()[0],)).lastrowid
                    sid=sv.create_student(con,create_data,user)
                    if profile.get('email'):
                        con.execute("UPDATE parents SET email=? WHERE family_id=(SELECT family_id FROM students WHERE id=?)",
                                    (str(profile['email']).strip(),sid))
                    con.execute('UPDATE students SET erp_id=?,erp_status=? WHERE id=?',
                                (str(event['erp_id']).strip(),str(profile.get('erp_status') or ''),sid))
                    for item in profile.get('history',[]) if isinstance(profile.get('history'),list) else []:
                        if not isinstance(item,dict):continue
                        text='ERP с 01.11: '+str(item.get('text') or '').strip()
                        if text!='ERP с 01.11: ':
                            con.execute('INSERT INTO history(student_id,ts,user_name,kind,text) VALUES (?,?,?,?,?)',
                                (sid,str(item.get('at') or '2026-11-01T00:00')[:19],'Перенос ERP','erp',text))
                    matches=[{'id':sid,'name':name}];created=True
                except Exception as exc:
                    con.execute('ROLLBACK TO chat_day_event');con.execute('RELEASE chat_day_event')
                    result['errors'].append({'source_id':source,'name':event.get('student_name'),'error':str(exc)});continue
        if not matches:
            allowed=(event.get('create_if_missing') is True
                     and str(event.get('source_chat') or '').casefold().startswith('служба заботы'))
            required=(str(event.get('student_name') or '').strip(),str(event.get('birth') or '').strip(),
                      str(event.get('parent_name') or '').strip(),str(event.get('phone') or '').strip())
            if not allowed or not all(required):
                result['unmatched'].append({'source_id':source,'name':event.get('student_name'),'phone':event.get('phone','')});continue
            try:datetime.fromisoformat(required[1])
            except ValueError:
                result['errors'].append({'source_id':source,'name':event.get('student_name'),'error':'Неверная дата рождения новой карточки'});continue
            con.execute('SAVEPOINT chat_day_event')
            try:
                create_data={'name':required[0],'birth':required[1],'parent_name':required[2],
                    'phone':required[3],'funnel':'sales','source':str(event.get('source') or '')}
                family_id=_family_for_phone(con,required[3])
                if family_id:create_data['family_id']=family_id
                sid=sv.create_student(con,create_data,user)
                if event.get('event_kind') in ('trial_booking','trial_result'):
                    trial_day=str(event.get('event_date') or '')[:10]
                    trial_time=str(event.get('trial_time') or '')[:5]
                    datetime.fromisoformat(trial_day+'T'+trial_time)
                    historical=int(trial_day < sv.today().isoformat())
                    con.execute("UPDATE students SET trial_at=?,historical_trial=?,stage='trial_booked',stage_changed_at=? WHERE id=?",
                                (trial_day+'T'+trial_time,historical,sv.now_iso(),sid))
                matches=[{'id':sid,'name':required[0]}];created=True
            except Exception as exc:
                con.execute('ROLLBACK TO chat_day_event');con.execute('RELEASE chat_day_event')
                result['errors'].append({'source_id':source,'name':event.get('student_name'),'error':str(exc)});continue
        if len(matches)!=1:
            result['ambiguous'].append({'source_id':source,'name':event.get('student_name'),'candidates':[x['name'] for x in matches]});continue
        sid=matches[0]['id']
        if not created:con.execute('SAVEPOINT chat_day_event')
        try:
            at=str(event.get('source_at') or '')[:19]
            datetime.fromisoformat(at)
            note=str(event.get('note') or '').strip()
            if not note:raise sv.ApiError('Пустое описание события')
            if str(event.get('source') or '').strip():
                con.execute("UPDATE students SET source=? WHERE id=? AND trim(COALESCE(source,''))=''",
                            (str(event['source']).strip()[:300],sid))
            single_sale=None
            subscription_sale=None
            balance_credit=None
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
                _reconcile_single_sale(con,sid,event)
                single_sale={'subscription_id':subid,'date':day,'group_id':gid}
            if event.get('event_kind')=='subscription_sale':
                subscription_sale=_reconcile_subscription_sale(con,sid,event,user)
            if event.get('event_kind')=='balance_credit':
                from . import balancecredit
                amount=int(event.get('amount') or 0)
                if amount<=0:raise sv.ApiError('В подтверждённом бонусе не указана сумма')
                ref='chat-credit:'+source
                fundid=balancecredit.add_existing_fund(con,sid,amount,source_ref=ref,
                    label=str(event.get('credit_label') or note)[:300],evidence_id=source,
                    source_kind='existing_carry',user=user)
                balance_credit={'fund_id':fundid,'amount':amount}
            con.execute('INSERT INTO history(student_id,ts,user_name,kind,text) VALUES (?,?,?,?,?)',(sid,at,'Перенос СЗ','chat_transfer',note))
            marked=False
            if event.get('event_kind')=='trial_result' and isinstance(event.get('came'),bool):
                student=con.execute('SELECT trial_at FROM students WHERE id=?',(sid,)).fetchone()
                if student and str(student['trial_at'] or '')[:10]==str(event.get('event_date') or '')[:10]:
                    sv.mark_trial(con,sid,event['came'],user);marked=True
                if marked:
                    con.execute("""UPDATE tasks SET status='cancelled',done_at=?,result='Результат ПУ подтверждён СЗ и тренером'
                        WHERE student_id=? AND status='open' AND kind='manual' AND due_at<=?
                        AND (title LIKE 'После ПУ%' OR title LIKE '%пришёл ли%' OR title LIKE '%результат%ПУ%')""",
                        (sv.now_iso(),sid,at[:10]+'T23:59'))
            booked=_reconcile_trial_booking(con,sid,event)
            outcome=_reconcile_trial_outcome(con,sid,event)
            sold=_reconcile_confirmed_sale(con,sid,event)
            _reconcile_clarification_task(con,sid,event)
            if event.get('task_title') and event.get('task_due') and not booked and not outcome and not sold:
                tasks.add_task(con,'manual',sid,str(event['task_due'])[:16],auto_key='chat-day:'+source,
                               title=str(event['task_title'])[:300],note=str(event.get('task_note') or note)[:4000],source='erp')
                _reconcile_confirmed_sale(con,sid,event)
            con.execute('INSERT INTO chat_day_transfer_events VALUES (?,?,?,?,?,?,?,?)',(source,sid,str(event.get('source_chat') or ''),at,str(event.get('event_kind') or 'note'),json.dumps(event,ensure_ascii=False),sv.now_iso(),user.get('name','')))
            tasks.ensure_client_task_coverage(con)
            con.execute('RELEASE chat_day_event')
            result['applied'].append({'source_id':source,'student_id':sid,'name':matches[0]['name'],'created':created,'trial_marked':marked,'single_sale':single_sale,'subscription_sale':subscription_sale,'balance_credit':balance_credit,'added':note,'tasks':_current_tasks(con,sid)})
        except BaseException as error:
            con.execute('ROLLBACK TO chat_day_event');con.execute('RELEASE chat_day_event')
            result['errors'].append({'source_id':source,'name':event.get('student_name'),'error':str(error)})
    sv.audit(con,user,'chat_day_transfer',json.dumps({k:len(v) for k,v in result.items()},ensure_ascii=False))
    return result
