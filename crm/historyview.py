"""Понятная клиентская хронология поверх неизменяемого технического журнала."""
import json
import re
from datetime import datetime

_LABELS=re.compile(r'(?:^|\s)(Дата события|Дедлайн|Задача \(END\)|Задача \(NEW\)|Событие|Автор|Исполнитель|Фамилия и имя|Филиал|Группа|Комментарий|ID|№)\s*:\s*',re.I)
_ERP_ARCHIVE = re.compile(r'^Архив\s+ERP\s+([^·.\n]+)\s*·\s*([^\.\n]+)', re.I)


def present(row):
    out=dict(row);raw=out.get('text') or ''
    text = raw.strip()
    kind = out.get('kind')
    export = kind == 'note' and re.match(r'^Выгрузка ERP\s+[^·\n]+\s*·', text, re.I)
    profile_edit = kind == 'system' and text.startswith('Изменены данные ребёнка и контакты семьи в шапке карточки')
    task_rename = kind == 'note' and text.startswith('Задача «') and '» переименована: «' in text
    migration_task = kind == 'task' and (
        ('Следующий шаг ERP:' in text and 'перенос истории' in text.lower())
        or 'выполнена: ошибочно перенесена вручную' in text.lower()
    )
    if export or profile_edit or task_rename or migration_task:
        out.update(technical_only=True, technical_text=raw)
        return out
    if kind == 'system' and text.startswith('Статус по правилам ERP:'):
        out['display_text'] = 'Статус: ' + text.partition(':')[2].strip()
        return out
    archive = _ERP_ARCHIVE.match(raw.strip())
    if archive:
        out.update(display_text=archive.group(2).strip(), technical_text=raw,
                   technical_only=True, display_author='ERP')
        return out
    if not re.match(r'^ERP(?:,|\s)',raw):return out
    found=list(_LABELS.finditer(raw));fields={}
    for i,m in enumerate(found):fields[m.group(1).casefold()]=raw[m.end():found[i+1].start() if i+1<len(found) else len(raw)].strip()
    title=fields.get('задача (new)') or fields.get('задача (end)') or ''
    event=fields.get('событие','');deadline=fields.get('дедлайн','')
    if 'перенес' in event.casefold() or 'перенос' in event.casefold():summary='Перенесён срок'+(f' задачи «{title}»' if title else '')
    else:summary=event or 'Событие из ERP';summary+=(f': «{title}»' if title else '')
    if deadline:summary+=' — '+deadline.split()[0]
    old=fields.get('задача (end)')
    if old and title and old!=title:summary+=f' (ранее «{old}»)'
    comment=fields.get('комментарий','')
    comment=re.sub(r'(?:#|№)\s*\d+','',comment).strip()
    if comment:summary+='\n'+comment
    out.update(display_text=summary,technical_text=raw,
               display_author=fields.get('автор') or out.get('user_name',''),technical_only=False)
    return out


def _iso(value, fallback=''):
    raw=str(value or '')[:19]
    try:return datetime.fromisoformat(raw).isoformat(timespec='minutes')
    except ValueError:return fallback


def journey(con, student):
    """Return one concise business timeline shared by every client card."""
    sid=student['id'];rows=[]
    def add(key,at,title,text,author='CRM'):
        if at:rows.append({'key':key,'ts':_iso(at),'title':title,'text':text.strip(),'author':author})
    source=str(student['source'] or '').strip()
    lead_at=student['created_at']
    older=con.execute("""SELECT MIN(ts) FROM history WHERE student_id=? AND
        (lower(text) LIKE '%новый лид%' OR lower(text) LIKE '%промоутер%')""",(sid,)).fetchone()[0]
    if older and (not lead_at or older<lead_at):lead_at=older
    if source:
        promoter=source[len('Промоутер '):] if source.casefold().startswith('промоутер ') else ''
        genitive={'Иван Ивакин':'Ивана Ивакина','Никита Евсюнин':'Никиты Евсюнина'}.get(promoter,promoter)
        lead=('Лид от промоутера '+genitive) if promoter else 'Новый лид'
        add('lead',lead_at,lead,'Источник: '+source)
    else:add('lead',lead_at,'Новый лид','Карточка клиента создана')
    trial_at=student['trial_at'] or ''
    if trial_at:
        group=con.execute('SELECT name FROM groups WHERE id=?',(student['trial_group_id'] or student['group_id'],)).fetchone()
        add('trial_booked',trial_at,'Записан на ПУ',('Группа: '+group['name']) if group else 'Пробное занятие')
    trial_event=None
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_day_transfer_events'").fetchone():
        for row in con.execute("SELECT source_at,payload FROM chat_day_transfer_events WHERE student_id=? AND event_kind='trial_result' ORDER BY source_at DESC",(sid,)):
            try:event=json.loads(row['payload'])
            except (TypeError,ValueError):continue
            if not trial_at or not event.get('event_date') or str(event['event_date'])[:10]==trial_at[:10]:
                trial_event=(row,event);break
    if student['trial_came'] is not None and trial_at:
        add('trial_attendance',trial_at,'Пришёл на ПУ' if student['trial_came'] else 'Не пришёл на ПУ',
            'Посещение пробного подтверждено' if student['trial_came'] else 'Отсутствие на пробном подтверждено')
    if trial_event:
        row,event=trial_event
        add('trial_result',str(event.get('event_date'))[:10]+'T'+(trial_at[11:16] if len(trial_at)>=16 else '12:00'),
            'Результат ПУ',str(event.get('note') or 'Результат пробного зафиксирован'),'Служба заботы')
    subs=con.execute("""SELECT month,price,created_at,paid_at,status FROM subscriptions
        WHERE student_id=? AND status!='cancelled' AND COALESCE(kind,'month')='month' ORDER BY month,id""",(sid,)).fetchall()
    if subs:
        grouped={}
        for sub in subs:
            day=str(sub['paid_at'] or sub['created_at'] or '')[:10]
            grouped.setdefault(day,[]).append(sub)
        for day,items in grouped.items():
            months=', '.join(x['month'][5:7]+'.'+x['month'][:4] for x in items)
            total=sum(int(x['price'] or 0) for x in items)
            paid=all(x['status']=='paid' for x in items)
            payment=con.execute("""SELECT amount,method,paid_at FROM payments WHERE student_id=? AND cancelled=0
                AND substr(paid_at,1,10)=? ORDER BY id LIMIT 1""",(sid,day)).fetchone() if day else None
            methods={'card_branch':'картой в филиале','cash':'наличными','sbp':'по СБП','transfer':'переводом',
                     'paykeeper':'онлайн','sber_qr_branch':'по QR Сбера','tochka_qr_link':'по ссылке Точки'}
            payment_text=''
            if payment:
                payment_text=f" Оплата: {int(payment['amount']):,} ₽, {methods.get(payment['method'],payment['method'])}".replace(',',' ')
            elif paid:payment_text=' Оплата подтверждена'
            add('sale:'+day,day+'T23:00','Куплен абонемент' if paid else 'Оформлен абонемент',
                f'Период: {months}. Стоимость: {total:,} ₽'.replace(',',' ')+'.'+payment_text)
    task=con.execute("""SELECT title,note,due_at,created_at,kind FROM tasks WHERE student_id=? AND status='open'
        ORDER BY due_at,priority DESC,id LIMIT 1""",(sid,)).fetchone()
    if task:
        detail=task['title']+(('. '+task['note'].strip()) if str(task['note'] or '').strip() else '')
        detail+=' Срок: '+task['due_at'][8:10]+'.'+task['due_at'][5:7]+'.'+task['due_at'][:4]
        add('next_step',task['created_at'] or task['due_at'],'Следующий шаг',detail)
    order={'lead':0,'trial_booked':10,'trial_attendance':20,'trial_result':30,'next_step':90}
    return sorted(rows,key=lambda row:(row['ts'],order.get(row['key'],50),row['key']))


def readable_event(row):
    """Business event only: never send the raw import payload to a task window."""
    item = present(row)
    if item.get('technical_only') or item.get('kind') == 'archived_note':
        return None
    text = item.get('display_text') or item.get('text') or ''
    if re.match(r'^\s*(?:Импорт|Перенос истории|Служебн|Синхронизац|Выгрузка|Архив ERP)', text, re.I):
        return None
    from .services import SALES_LABELS
    for code, label in SALES_LABELS.items():
        text = re.sub(r'\b' + re.escape(code) + r'\b', label, text)
    text = re.sub(r'Текущий раздел:\s*\w+;\s*этап:\s*[^.\n]+[.]?', '', text)
    text = re.sub(r'\b(?:student_id|family_id|task_id|subscription_id|erp_id|source_key)\s*[:=]\s*[^\s;,]+', '', text)
    text = re.sub(r'(?:Следующий шаг ERP:)', 'Следующий шаг:', text)
    text = text.strip()
    if not text:
        return None
    return {'id': item.get('id'), 'ts': item.get('ts'),
            'text': text, 'user_name': item.get('display_author') or item.get('user_name') or 'CRM'}


def task_timeline(con, tid, user, offset=0):
    from . import planner
    from .services import ApiError
    task = con.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
    if not task:
        raise ApiError('Задача не найдена', 404)
    if not task['student_id']:
        if not planner.can_view_plan(con, user, task['assignee_id']):
            raise ApiError('Недостаточно прав', 403)
        return {'rows': [], 'next_offset': None}
    offset = max(0, int(offset))
    rows = con.execute('''SELECT id,ts,kind,text,user_name FROM history
        WHERE student_id=? AND kind!='archived_note'
        AND NOT EXISTS (SELECT 1 FROM erp_full_history_corrections c
            WHERE c.history_id=history.id AND c.assigned_student_id IS NULL)
        ORDER BY ts DESC,id DESC LIMIT 101 OFFSET ?''', (task['student_id'], offset)).fetchall()
    return {'rows': [event for row in rows[:100] if (event := readable_event(row))],
            'next_offset': offset + 100 if len(rows) > 100 else None}
