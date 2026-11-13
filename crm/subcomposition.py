"""Восстановление компонентов ERP-абонемента без изменения финансов/отметок."""
import hashlib
import json
import re
from datetime import date
from . import logic, program, schedule, closures, services as sv


def init(con):
    if 'composition_corrected' not in {r[1] for r in con.execute('PRAGMA table_info(subscriptions)')}:
        con.execute('ALTER TABLE subscriptions ADD COLUMN composition_corrected INTEGER NOT NULL DEFAULT 0')
    con.execute('''CREATE TABLE IF NOT EXISTS subscription_composition_reviews (id INTEGER PRIMARY KEY, subscription_id INTEGER NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL, reason TEXT NOT NULL, user_name TEXT NOT NULL, created_at TEXT NOT NULL)''')


def exact_dates(raw,month):
    values=re.split(r'[\s,;]+',raw.strip()) if isinstance(raw,str) else raw
    if not isinstance(values,list) or not values:raise sv.ApiError('Укажите точные даты каждой части из ERP')
    result=[]
    for value in values:
        try:
            if re.fullmatch(r'\d{2}\.\d{2}(?:\.\d{4})?',str(value)):
                pieces=value.split('.');day=date(int(pieces[2]) if len(pieces)==3 else int(month[:4]),int(pieces[1]),int(pieces[0]))
            else:day=date.fromisoformat(value)
        except (ValueError,TypeError):raise sv.ApiError('Даты вводятся как ДД.ММ.ГГГГ или ГГГГ-ММ-ДД, через запятую или новую строку')
        if day.isoformat()[:7]!=month:raise sv.ApiError('Все даты должны быть в месяце исправляемого абонемента')
        if day.isoformat() in result:raise sv.ApiError('В одной части указана повторная дата')
        result.append(day.isoformat())
    return sorted(result)


def _amount(value):
    if isinstance(value,bool):raise sv.ApiError('Укажите фактическую сумму части в целых рублях')
    try:
        text=str(value).replace(' ','').replace('\u00a0','').replace('\u202f','')
        if not re.fullmatch(r'\d+',text):raise ValueError()
        amount=int(text)
    except (ValueError,TypeError):raise sv.ApiError('Укажите фактическую сумму части в целых рублях')
    if amount<=0:raise sv.ApiError('Сумма каждой части должна быть положительной')
    return amount


def unit_rate(component):
    """Не выдумываем ставки отдельных дат при известной только общей сумме."""
    if 'amount' in component:
        amount=component['amount'];count=len(component['dates'])
        if count and amount%count:
            raise sv.ApiError('Сумма индивидуальной части не делится поровну на занятия. Перед возвратом или переводом сверьте стоимость оставшихся дат с ERP')
        return amount//count if count else 0
    return int(component['rate'])


def preview(con,subid,ofp_gid=None,components=None):
    sub=con.execute("SELECT x.*,e.group_id FROM subscriptions x JOIN enrollments e ON e.id=x.enrollment_id WHERE x.id=? AND x.status!='cancelled' AND x.kind='month'",(subid,)).fetchone()
    if not sub:raise sv.ApiError('Действующий месячный абонемент не найден')
    arena=con.execute("SELECT * FROM groups WHERE id=? AND lesson_kind='arena' AND program_id IS NOT NULL",(sub['group_id'],)).fetchone()
    if not arena:raise sv.ApiError('Выберите абонемент сборной Арктики')
    result=[]
    if components is not None:
        if not isinstance(components,list) or len(components)!=2:raise sv.ApiError('Укажите две части: Арктика и ОФП')
        for raw in components:
            if not isinstance(raw,dict):raise sv.ApiError('Неверные данные части абонемента')
            try:gid=int(raw.get('group_id') or 0)
            except (ValueError,TypeError):raise sv.ApiError('Выберите группу каждой части')
            group=con.execute('SELECT g.*,b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id WHERE g.id=? AND g.program_id=?',(gid,arena['program_id'])).fetchone()
            if not group or group['lesson_kind'] not in ('arena','ofp'):raise sv.ApiError('Выберите группы Арктики и ОФП одного направления')
            dates=exact_dates(raw.get('dates'),sub['month']);amount=_amount(raw.get('amount'))
            source=str(raw.get('erp_subscription_id') or '').strip().lstrip('#№').strip()
            if not re.fullmatch(r'\d{1,30}',source):raise sv.ApiError('Укажите номер исходного абонемента ERP для каждой части')
            result.append({'group_id':gid,'group':group['name'],'branch':group['branch'],'time':group['time'],'kind':group['lesson_kind'],'dates':dates,'amount':amount,'rate':amount//len(dates) if amount%len(dates)==0 else None,'erp_subscription_id':source})
        if {c['kind'] for c in result}!={'arena','ofp'}:raise sv.ApiError('Нужна одна часть Арктики и одна часть ОФП')
        result.sort(key=lambda c:0 if c['kind']=='arena' else 1)
    else:
        ofp=con.execute("SELECT g.*,b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id WHERE g.id=? AND g.lesson_kind='ofp' AND g.program_id=?",(ofp_gid,arena['program_id'])).fetchone()
        if not ofp:raise sv.ApiError('Выберите группу ОФП этого направления')
        rates=program.prices_for(con,arena['program_id'],sub['month']);start,end=logic.month_bounds(sub['month'])
        for group,rate in ((arena,rates['arena']),(ofp,rates['ofp'])):
            dates=[day.isoformat() for day in schedule.month_dates(con,group,'',sub['month']) if not closures.is_closed(con,group['id'],day)]
            result.append({'group_id':group['id'],'group':group['name'],'kind':group['lesson_kind'],'time':group['time'],'rate':rate,'amount':len(dates)*rate,'dates':dates})
    lessons=sum(len(c['dates']) for c in result);price=sum(c['amount'] for c in result)
    if not lessons:raise sv.ApiError('В выбранном месяце нет занятий')
    if price!=sub['price']:raise sv.ApiError(f'Суммы частей дают {price} ₽, стоимость абонемента CRM — {sub["price"]} ₽. Они должны совпасть в точности. Для скидки/индивидуальных дат выберите режим «Точные данные ERP»')
    # Не создаём пересекающееся право по уже существующему отдельному счёту.
    for component in result:
        other=con.execute("SELECT x.id FROM subscriptions x JOIN enrollments e ON e.id=x.enrollment_id WHERE x.student_id=? AND x.id!=? AND x.month=? AND x.kind='month' AND x.status!='cancelled' AND e.group_id=? LIMIT 1",(sub['student_id'],subid,sub['month'],component['group_id'])).fetchone()
        if other:raise sv.ApiError(f'В этой группе уже есть отдельный абонемент №{other[0]}. Сначала сверьте состав счетов')
    evidence={'subscription':dict(sub),'components':result,'mode':'exact' if components is not None else 'full'}
    token=hashlib.sha256(json.dumps(evidence,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return {'subscription_id':subid,'month':sub['month'],'old_lessons':sub['lessons'],'lessons':lessons,'price':price,'status':sub['status'],'mode':evidence['mode'],'components':result,'ofp_group_id':next(c['group_id'] for c in result if c['kind']=='ofp'),'token':token}


def apply(con,subid,data,user):
    reason=str(data.get('reason') or '').strip()
    if len(reason)<5:raise sv.ApiError('Укажите основание сверки и ID абонементов ERP')
    con.execute('SAVEPOINT sub_composition')
    try:
        q=preview(con,subid,data.get('ofp_group_id'),data.get('components'))
        if data.get('token')!=q['token']:raise sv.ApiError('Абонемент или состав изменились. Повторите предварительную сверку',409)
        sub=con.execute('SELECT * FROM subscriptions WHERE id=?',(subid,)).fetchone()
        start,end=logic.month_bounds(q['month']);next_month=logic.shift_month(q['month'],1)+'-01'
        main_eid=sub['enrollment_id']
        for component in q['components']:
            gid=component['group_id']
            enrollment=con.execute("SELECT id FROM enrollments WHERE student_id=? AND group_id=? AND (start_date='' OR start_date<=?) AND (end_date='' OR end_date>=?) ORDER BY id LIMIT 1",(sub['student_id'],gid,start.isoformat(),next_month)).fetchone()
            if not enrollment:
                eid=con.execute("INSERT INTO enrollments(student_id,group_id,tariff,active,created_at,start_date,end_date) VALUES (?,?,?,0,?,?,?)",(sub['student_id'],gid,'pkg' if component['kind']=='arena' else 'included',sv.now_iso(),start.isoformat(),next_month)).lastrowid
            else:eid=enrollment[0]
            if component['kind']=='arena':main_eid=eid
        con.execute("UPDATE subscriptions SET enrollment_id=?,tariff='pkg',lessons=?,per_lesson=?,lesson_schedule=?,excluded_dates='[]',composition_corrected=1,calculation_reason=? WHERE id=?",(main_eid,q['lessons'],q['price']//q['lessons'],json.dumps(q['components']), 'Состав восстановлен по ERP: '+reason[:1000]+'. Администратор: '+user.get('name',''),subid))
        after=dict(con.execute('SELECT * FROM subscriptions WHERE id=?',(subid,)).fetchone())
        con.execute('INSERT INTO subscription_composition_reviews(subscription_id,before_json,after_json,reason,user_name,created_at) VALUES (?,?,?,?,?,?)',(subid,json.dumps(dict(sub)),json.dumps(after),reason[:1000],user.get('name',''),sv.now_iso()))
        sv.log(con,sub['student_id'],f"Состав абонемента #{subid} за {q['month']}: {q['old_lessons']} → {q['lessons']} занятий. Сумма {q['price']} ₽, статус и платежи сохранены. Основание: {reason}",user,'erp_subscription_reconciliation')
        sv.audit(con,user,'subscription.composition',str(subid))
        con.execute('RELEASE sub_composition');return q
    except BaseException:
        con.execute('ROLLBACK TO sub_composition');con.execute('RELEASE sub_composition');raise


def planned(con,group_id,day):
    """Подтверждённые даты видим в журнале независимо от текущего расписания/активности записи."""
    rows=[]
    for sub in con.execute("SELECT x.*,s.name,s.birth FROM subscriptions x JOIN students s ON s.id=x.student_id WHERE x.month=? AND x.composition_corrected=1 AND x.status!='cancelled'",(day[:7],)):
        if any(c['group_id']==group_id and day in c['dates'] for c in json.loads(sub['lesson_schedule'] or '[]')):
            rows.append(dict(sub))
    return rows


def group_dates(con,group_id,month):
    return {day for row in con.execute("SELECT lesson_schedule FROM subscriptions WHERE month=? AND composition_corrected=1 AND status!='cancelled'",(month,)) for c in json.loads(row[0] or '[]') if c['group_id']==group_id for day in c['dates']}
