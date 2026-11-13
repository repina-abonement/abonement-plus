"""Отработки дополняют оплаченный абонемент: окно двух месяцев и две тренировки за неделю."""
from datetime import date,timedelta
import json
from . import services as sv,clientattendance,subscriptiondates


def paid(con,sid,month):
    return [dict(r) for r in con.execute("""SELECT x.*,e.group_id,g.program_id FROM subscriptions x
        LEFT JOIN enrollments e ON e.id=x.enrollment_id LEFT JOIN groups g ON g.id=e.group_id
        WHERE x.student_id=? AND x.month=? AND x.status='paid' AND COALESCE(x.kind,'month')='month'""",(sid,month))]


def covered(con,subs,event):
    if not subs:return False
    sid=subs[0]['student_id']
    if subscriptiondates.excluded(con,sid,event['group_id'],event['date']) or subscriptiondates.outside_period(con,sid,event['group_id'],event['date']):return False
    for sub in subs:
        if sub['valid_from'] and event['date']<sub['valid_from'] or sub['valid_until'] and event['date']>sub['valid_until']:continue
        if sub['lesson_schedule'] and (sub['historical_sale'] or sub['composition_corrected']):
            if any(c.get('group_id')==event['group_id'] and event['date'] in c.get('dates',[]) for c in json.loads(sub['lesson_schedule'])):return True
            continue
        group=con.execute('SELECT program_id FROM groups WHERE id=?',(event['group_id'],)).fetchone()
        if sub['group_id']==event['group_id'] or sub['program_id'] and group and sub['program_id']==group[0]:return True
        # Старые записи без enrollment_id: только основная группа ученика.
        if not sub['enrollment_id'] and con.execute('SELECT 1 FROM students WHERE id=? AND group_id=?',(sub['student_id'],event['group_id'])).fetchone():return True
    return False


def check(con,sid,day,cache=None):
    d=date.fromisoformat(day);cache=cache if cache is not None else {}
    month=day[:7]
    def subs(ym):
        key=('paid',ym)
        if key not in cache:cache[key]=paid(con,sid,ym)
        return cache[key]
    if not subs(month):raise sv.ApiError('Для отработки нужен оплаченный абонемент на месяц отработки')
    start=d-timedelta(days=d.weekday());end=start+timedelta(days=6)
    key=('week',start.isoformat())
    if key not in cache:
        lessons=set()
        for ym in {start.isoformat()[:7],end.isoformat()[:7]}:
            if not subs(ym):continue
            ck=('calendar',ym)
            if ck not in cache:cache[ck]=clientattendance.calendar(con,sid,ym)['events']
            for e in cache[ck]:
                if start.isoformat()<=e['date']<=end.isoformat() and e['kind']=='subscription' and covered(con,subs(ym),e):lessons.add((e['date'],e['group_id']))
        cache[key]=len(lessons)
    if cache[key]<2:raise sv.ApiError('Отработка возможна только дополнительно к минимум двум оплаченным занятиям по абонементу на этой неделе (понедельник–воскресенье)')
    return cache[key]


def absences(con,sid,day,cache=None):
    """Срок пропуска оценивается на дату отработки, а не на день записи."""
    cache=cache if cache is not None else {};ym=day[:7];key=('absences',ym)
    if key not in cache:cache[key]=sv.absence_counters(con,sid,date.fromisoformat(day))['available_ids']
    return cache[key]
