"""Explicit retrospective entry, with factual dates/amounts and no external payment or message calls."""
import math
import hashlib
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from . import services as sv, smart, money, logic, schedule, closures, program, pricing


def init(con):
    cols={r[1] for r in con.execute('PRAGMA table_info(students)')}
    for col in ('trial_group_id INTEGER REFERENCES groups(id)','historical_trial INTEGER NOT NULL DEFAULT 0'):
        if col.split()[0] not in cols:con.execute('ALTER TABLE students ADD COLUMN '+col)
    if 'historical_sale' not in {r[1] for r in con.execute('PRAGMA table_info(subscriptions)')}:
        con.execute('ALTER TABLE subscriptions ADD COLUMN historical_sale INTEGER NOT NULL DEFAULT 0')
    con.executescript('''CREATE TABLE IF NOT EXISTS historical_entries (
        request_id TEXT PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES students(id),
        signature TEXT NOT NULL, result TEXT NOT NULL, recorded_at TEXT NOT NULL, recorded_by TEXT NOT NULL);
    ''')


def now():
    return datetime.now(ZoneInfo('Europe/Moscow')).replace(tzinfo=None)


def options(con,sid,group_id=None,ym=None,tariff='fixed',ofp_group_id=None,start_date=None, paid_date=None, trial_date=None):
    student=sv.get_student_row(con,sid)
    groups=[dict(r) for r in con.execute('''SELECT g.id,g.name,g.branch_id,g.weekdays,g.time,g.day_times,g.program_id,g.lesson_kind,g.active,
        b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id WHERE g.is_makeup=0 ORDER BY b.name,g.name''')]
    result={'groups':groups,'components':[]}
    if not group_id or not ym:return result
    try:ms,me=logic.month_bounds(ym)
    except (ValueError,TypeError):raise sv.ApiError('Выберите месяц абонемента')
    g=next((g for g in groups if g['id']==int(group_id)),None)
    if not g or g['lesson_kind']=='ofp':raise sv.ApiError('Выберите основную группу абонемента')
    pr=pricing.prices_for(con,ym)
    selected=[(g,pr.get(tariff,pr['fixed']))]
    if g['program_id']:
        if tariff not in ('arena_only','pkg'):raise sv.ApiError('Выберите тариф сборной')
        rates=program.prices_for(con,g['program_id'],ym)
        selected=[(g,rates['arena_only'] if tariff=='arena_only' else rates['arena'])]
        if tariff=='pkg':
            ofp=next((x for x in groups if x['id']==int(ofp_group_id or 0) and x['program_id']==g['program_id'] and x['lesson_kind']=='ofp'),None)
            if not ofp:raise sv.ApiError('Выберите группу ОФП')
            selected.append((ofp,rates['ofp']))
    elif tariff not in ('fixed','flex'):raise sv.ApiError('Выберите тариф абонемента')
    on=_day(paid_date,'дату оплаты') if paid_date else now().date()
    had=con.execute("SELECT 1 FROM subscriptions WHERE student_id=? AND status!='cancelled' AND kind='month'",(sid,)).fetchone()
    trial_day=trial_date or (student['trial_at'] or '')[:10]
    newcomer=not had and trial_day[:7]==ym and on.strftime('%Y-%m')==ym
    deadline=pricing.deadline(con,ym)
    late_pct=pr['late_pct']
    if g['program_id']:
        prog=program.program_of_group(con,g['id'])
        deadline=program._deadline(ym,prog['deadline_day'])
        late_pct=rates['late_pct']
    late=on>deadline and not newcomer
    discounts=money.auto_discounts(con,sid,on)
    if trial_day==on.isoformat() and not had and not g['program_id']:
        first=con.execute("SELECT * FROM discounts WHERE rule='first_on_trial_day' AND active=1").fetchone()
        if first:discounts.append(dict(first))
    if g['program_id']:discounts=[d for d in discounts if d['rule']=='sibling']
    discount=None if late else money.best_discount(discounts)
    result['calculation']={'discount_percent':int(discount['percent']) if discount else 0,
        'discount_name':discount['name'] if discount else '', 'late_pct':float(late_pct) if late else 0,
        'unit_late':bool(g['program_id']), 'deadline':deadline.isoformat()}
    for group,rate in selected:
        closed=closures.closed_dates(con,group['id'],ms,me)
        days=[d.isoformat() for d in schedule.month_dates(con,group,'',ym)
              if d.isoformat() not in closed and (not start_date or d.isoformat()>=start_date)]
        result['components'].append({'group_id':group['id'],'group':group['name'],'rate':rate,'dates':days})
    return result


def calculated_total(quoted, chosen):
    rules=quoted['calculation'];base=0
    for component in quoted['components']:
        rate=component['rate']
        if rules['unit_late']:rate=math.ceil(rate*(1+rules['late_pct']/100)-1e-9)
        base+=sum((component['group_id'],day) in chosen for day in component['dates'])*rate
    if not rules['unit_late']:base+=pricing.late_fee(base,rules['late_pct'])
    return base-math.floor(base*rules['discount_percent']/100+1e-9)


def _day(raw,label):
    try:
        value=date.fromisoformat(str(raw or ''))
    except (ValueError,TypeError):raise sv.ApiError('Укажите '+label)
    if value>now().date():raise sv.ApiError(label.capitalize()+' не может быть в будущем')
    return value


def record(con,sid,data,user):
    s=sv.get_student_row(con,sid)
    if s['archived']:raise sv.ApiError('Сначала восстановите карточку из архива')
    key=str(data.get('request_id') or '')
    if not 8<=len(key)<=100:raise sv.ApiError('Обновите форму внесения данных')
    signature=hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    old=con.execute('SELECT * FROM historical_entries WHERE request_id=?',(key,)).fetchone()
    if old:
        if old['student_id']!=sid or old['signature']!=signature:raise sv.ApiError('Этот запрос уже использован с другими данными')
        return json.loads(old['result'])
    trial=data.get('record_trial') is True;sold=data.get('sold') is True
    if not trial and not sold:raise sv.ApiError('Выберите прошедшее ПУ или состоявшуюся покупку')
    at=None;trial_gid=None
    if trial:
        day=_day(data.get('trial_date'),'дату прошедшего ПУ')
        try:at=datetime.fromisoformat(day.isoformat()+'T'+str(data.get('trial_time') or ''))
        except ValueError:raise sv.ApiError('Укажите время прошедшего ПУ')
        if at>=now():raise sv.ApiError('В этом режиме можно внести только уже прошедшее ПУ')
        trial_gid=int(data.get('trial_group_id') or 0)
        g=con.execute('SELECT * FROM groups WHERE id=? AND is_makeup=0 AND program_id IS NULL',(trial_gid,)).fetchone()
        if not g or g['lesson_kind']=='ofp':raise sv.ApiError('Выберите группу прошедшего ПУ')
        if s['trial_at'] and (s['trial_at'][:16]!=at.isoformat(timespec='minutes') or (s['trial_group_id'] or s['group_id'])!=trial_gid):
            raise sv.ApiError('В карточке уже есть другое ПУ. Не заменяйте его без отдельной сверки')
        if con.execute('SELECT 1 FROM attendance WHERE student_id=? AND group_id=? AND date=?',(sid,trial_gid,day.isoformat())).fetchone():
            raise sv.ApiError('На эту дату уже есть отметка занятия. Сначала сверьте её, чтобы не создать второй факт посещения')
    con.execute('SAVEPOINT historical_entry')
    try:
        fields={k:data[k] for k in ('child_name','birth','parent_name','phone') if k in data}
        if data.get('email'):fields['email']=data['email']
        smart.fill_required(con,sid,fields,user);smart.require(con,sid,'sale' if sold else 'trial')
        if trial:
            con.execute('''UPDATE students SET trial_at=?,trial_group_id=?,trial_came=1,historical_trial=1,
                trial_marked_at=?,
                group_id=CASE WHEN funnel='sales' THEN ? ELSE COALESCE(group_id,?) END,stage=CASE WHEN funnel='sales' THEN 'how_was' ELSE stage END,
                stage_changed_at=CASE WHEN funnel='sales' THEN ? ELSE stage_changed_at END WHERE id=?''',
                (at.isoformat(timespec='minutes'),trial_gid,sv.now_iso(),trial_gid,trial_gid,at.isoformat(timespec='minutes'),sid))
            sv.log(con,sid,f"Внесено прошедшее ПУ: {at.strftime('%d.%m.%Y %H:%M')}, группа #{trial_gid}, ребёнок пришёл. Дата внесения: {sv.now_iso()}",user,'attendance')
        result={'ok':True,'trial_at':at.isoformat(timespec='minutes') if at else None}
        if sold:
            sale_day=_day(data.get('sale_date'),'дату продажи');paid_day=_day(data.get('paid_date') or sale_day.isoformat(),'дату оплаты')
            if trial and sale_day<at.date():raise sv.ApiError('Дата продажи раньше указанного ПУ. Проверьте последовательность событий')
            ym=str(data.get('month') or '');gid=int(data.get('group_id') or 0);tariff=str(data.get('tariff') or 'fixed')
            amount=int(data.get('amount') or 0);method=str(data.get('method') or '')
            if amount and paid_day<sale_day:raise sv.ApiError('Дата оплаты раньше продажи. Предоплату учтите отдельным существующим платежом')
            if amount and method not in ('cash','card','sbp','transfer','card_branch','sber_qr_branch','tochka_qr_link'):raise sv.ApiError('Онлайн-оплату нельзя дублировать вручную: используйте уже учтённый платёж')
            quoted=options(con,sid,gid,ym,tariff,data.get('ofp_group_id'),paid_date=paid_day.isoformat(),trial_date=at.date().isoformat() if at else None)
            available={(c['group_id'],d) for c in quoted['components'] for d in c['dates']}
            chosen=set()
            for item in data.get('lessons') or []:
                chosen.add((int(item['group_id']),str(item['date'])))
            if not chosen or not chosen<=available:raise sv.ApiError('Отметьте фактически проданные занятия по расписанию')
            if trial and (trial_gid,at.date().isoformat()) in chosen:raise sv.ApiError('Пробное не включается в оплаченные занятия абонемента')
            price=calculated_total(quoted,chosen)
            if price<=0 or amount<0 or amount>price:raise sv.ApiError('Оплата не должна превышать рассчитанную стоимость абонемента')
            if data.get('price') is not None and int(data['price'])!=price:raise sv.ApiError('Стоимость изменилась. Обновите расчёт по календарю')
            from . import subscriptiondates
            subscriptiondates.guard_duplicate(con,sid,gid,ym,chosen)
            g=con.execute('SELECT * FROM groups WHERE id=?',(gid,)).fetchone()
            eid=con.execute('SELECT id FROM enrollments WHERE student_id=? AND group_id=? AND active=1',(sid,gid)).fetchone()
            if not eid:
                eid=con.execute('''INSERT INTO enrollments(student_id,group_id,tariff,weekdays,created_at,start_date,active,end_date)
                    VALUES (?,?,?,'',?,?,?,?)''',(sid,gid,tariff,sv.now_iso(),min(d for _,d in chosen),int(ym>=now().strftime('%Y-%m')),
                    '' if ym>=now().strftime('%Y-%m') else logic.shift_month(ym,1)+'-01')).lastrowid
            else:eid=eid[0]
            if g['program_id'] and tariff=='pkg':
                ofp=int(data.get('ofp_group_id') or 0)
                if not con.execute('SELECT 1 FROM enrollments WHERE student_id=? AND group_id=? AND active=1',(sid,ofp)).fetchone():
                    con.execute("INSERT INTO enrollments(student_id,group_id,tariff,created_at,start_date,active,end_date) VALUES (?,?,'pkg',?,?,?,?)",
                                (sid,ofp,sv.now_iso(),min(d for _,d in chosen),int(ym>=now().strftime('%Y-%m')),'' if ym>=now().strftime('%Y-%m') else logic.shift_month(ym,1)+'-01'))
            if con.execute("SELECT 1 FROM subscriptions WHERE student_id=? AND enrollment_id=? AND month=? AND status!='cancelled'",(sid,eid,ym)).fetchone():
                raise sv.ApiError('Абонемент на этот месяц уже внесён. Откройте его, не создавайте повторный')
            if amount and con.execute("SELECT 1 FROM payments WHERE student_id=? AND amount=? AND substr(paid_at,1,10)=? AND cancelled=0",(sid,amount,paid_day.isoformat())).fetchone():
                raise sv.ApiError('Похожая оплата уже есть в CRM. Выберите «Оплата уже учтена», чтобы не задвоить деньги')
            components=[{**c,'dates':[d for d in c['dates'] if (c['group_id'],d) in chosen]} for c in quoted['components']]
            subid=con.execute('''INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,status,
                created_at,corrected,ledger_managed,lesson_schedule,historical_sale) VALUES (?,?,?,?,?,?,?,'invoiced',?,1,1,?,1)''',
                (sid,eid,ym,tariff,len(chosen),price//len(chosen),price,sale_day.isoformat(),json.dumps(components))).lastrowid
            all_dates={d for c in quoted['components'] for d in c['dates']}
            excluded=sorted(all_dates-{d for _,d in chosen})
            con.execute('UPDATE subscriptions SET excluded_dates=? WHERE id=?',(json.dumps(excluded),subid))
            con.execute("UPDATE students SET funnel='members',stage='active',stage_changed_at=? WHERE id=?",(sale_day.isoformat(),sid))
            if amount:
                money.add_payment(con,sid,amount,method,user,subscription_id=subid,paid_at=paid_day.isoformat(),comment='Оплата состоявшейся покупки, внесено задним числом')
            else:money.reconcile(con,sid)
            covered=max(0,money.balance(con,sid)['covered'].get(subid,0))
            if covered>=price:
                if not s['converted_at']:con.execute('UPDATE students SET converted_at=? WHERE id=?',(paid_day.isoformat(),sid))
                con.execute('UPDATE subscriptions SET paid_at=? WHERE id=?',(paid_day.isoformat(),subid))
            sv.sync_primary(con,sid)
            sv.log(con,sid,f"Внесён проданный абонемент за {logic.month_label(ym)}: дата продажи {sale_day.isoformat()}, стоимость {price} ₽, занятий {len(chosen)}, внесена оплата {amount} ₽. Дата внесения: {sv.now_iso()}",user,'payment')
            if ym>=now().strftime('%Y-%m'):
                from . import tasks
                tasks.schedule_renewal(con,sid)
            result.update(subscription_id=subid,price=price,paid=covered,remaining=max(0,price-covered))
        con.execute('INSERT INTO historical_entries(request_id,student_id,signature,result,recorded_at,recorded_by) VALUES (?,?,?,?,?,?)',
                    (key,sid,signature,json.dumps(result),sv.now_iso(),user.get('name','')))
        con.execute('RELEASE historical_entry');return result
    except BaseException as error:
        con.execute('ROLLBACK TO historical_entry');con.execute('RELEASE historical_entry')
        if isinstance(error,sv.ApiError) and getattr(error,'extra',{}).get('need'):raise sv.ApiError(error.message,422)
        raise
