"""Проверяемое восстановление календарных компонентов ERP-абонемента без изменения денег."""
import hashlib
import json
from . import logic, program, schedule, closures, services as sv


def init(con):
    if 'composition_corrected' not in {r[1] for r in con.execute('PRAGMA table_info(subscriptions)')}:
        con.execute('ALTER TABLE subscriptions ADD COLUMN composition_corrected INTEGER NOT NULL DEFAULT 0')
    con.execute('''CREATE TABLE IF NOT EXISTS subscription_composition_reviews (id INTEGER PRIMARY KEY, subscription_id INTEGER NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL, reason TEXT NOT NULL, user_name TEXT NOT NULL, created_at TEXT NOT NULL)''')


def preview(con, subid, ofp_gid):
    sub=con.execute("SELECT x.*,e.group_id FROM subscriptions x JOIN enrollments e ON e.id=x.enrollment_id WHERE x.id=? AND x.status!='cancelled' AND x.kind='month'",(subid,)).fetchone()
    if not sub:raise sv.ApiError('Действующий месячный абонемент не найден')
    arena=con.execute("SELECT * FROM groups WHERE id=? AND lesson_kind='arena' AND program_id IS NOT NULL",(sub['group_id'],)).fetchone()
    if not arena:raise sv.ApiError('Выберите абонемент сборной Арктики')
    ofp=con.execute("SELECT g.*,b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id WHERE g.id=? AND g.lesson_kind='ofp' AND g.program_id=?",(ofp_gid,arena['program_id'])).fetchone()
    if not ofp:raise sv.ApiError('Выберите группу ОФП этого направления')
    rates=program.prices_for(con,arena['program_id'],sub['month'])
    start,end=logic.month_bounds(sub['month']);components=[]
    for group,rate in ((arena,rates['arena']),(ofp,rates['ofp'])):
        dates=[day.isoformat() for day in schedule.month_dates(con,group,'',sub['month']) if not closures.is_closed(con,group['id'],day)]
        components.append({'group_id':group['id'],'group':group['name'],'rate':rate,'dates':dates})
    price=sum(len(c['dates'])*c['rate'] for c in components)
    if not sum(len(c['dates']) for c in components):raise sv.ApiError('В выбранном месяце нет занятий')
    if price!=sub['price']:raise sv.ApiError(f'Состав по прайсу даёт {price} ₽, в абонементе {sub["price"]} ₽. Нужна отдельная сверка скидок/суммы, автоматическое исправление запрещено')
    evidence={'subscription':dict(sub),'components':components}
    token=hashlib.sha256(json.dumps(evidence,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return {'subscription_id':subid,'month':sub['month'],'old_lessons':sub['lessons'],'lessons':sum(len(c['dates']) for c in components),'price':price,'status':sub['status'],'components':components,'ofp_group_id':ofp['id'],'token':token}


def apply(con,subid,data,user):
    reason=str(data.get('reason') or '').strip()
    if len(reason)<5:raise sv.ApiError('Укажите основание сверки и ID абонементов ERP')
    con.execute('SAVEPOINT sub_composition')
    try:
        q=preview(con,subid,int(data.get('ofp_group_id') or 0))
        if data.get('token')!=q['token']:raise sv.ApiError('Абонемент или расписание изменились. Повторите предварительную сверку',409)
        sub=con.execute('SELECT * FROM subscriptions WHERE id=?',(subid,)).fetchone()
        start,end=logic.month_bounds(q['month'])
        # Ограниченная запись ОФП даёт групповой журнал только на оплаченный месяц.
        ofp=q['ofp_group_id'];next_month=logic.shift_month(q['month'],1)+'-01'
        if not con.execute("SELECT 1 FROM enrollments WHERE student_id=? AND group_id=? AND (start_date='' OR start_date<=?) AND (end_date='' OR end_date>=?)",(sub['student_id'],ofp,start.isoformat(),next_month)).fetchone():
            con.execute("INSERT INTO enrollments(student_id,group_id,tariff,active,created_at,start_date,end_date) VALUES (?,?,'included',0,?,?,?)",(sub['student_id'],ofp,sv.now_iso(),start.isoformat(),next_month))
        con.execute("UPDATE subscriptions SET tariff='pkg',lessons=?,per_lesson=?,lesson_schedule=?,excluded_dates='[]',composition_corrected=1,calculation_reason=? WHERE id=?",(q['lessons'],q['price']//q['lessons'],json.dumps(q['components']), 'Состав восстановлен по ERP: '+reason[:1000]+'. Администратор: '+user.get('name',''),subid))
        after=dict(con.execute('SELECT * FROM subscriptions WHERE id=?',(subid,)).fetchone())
        con.execute('INSERT INTO subscription_composition_reviews(subscription_id,before_json,after_json,reason,user_name,created_at) VALUES (?,?,?,?,?,?)',(subid,json.dumps(dict(sub)),json.dumps(after),reason[:1000],user.get('name',''),sv.now_iso()))
        sv.log(con,sub['student_id'],f"Состав абонемента #{subid} за {q['month']}: {q['old_lessons']} → {q['lessons']} занятий. Сумма {q['price']} ₽, статус и платежи сохранены. Основание: {reason}",user,'erp_subscription_reconciliation')
        sv.audit(con,user,'subscription.composition',str(subid))
        con.execute('RELEASE sub_composition');return q
    except BaseException:
        con.execute('ROLLBACK TO sub_composition');con.execute('RELEASE sub_composition');raise
