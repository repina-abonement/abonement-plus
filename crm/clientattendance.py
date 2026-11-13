"""Календарь клиента и подбор отработок по числу записанных детей."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import json


def calendar(con, sid, ym):
    from . import services as sv, logic, schedule, closures, subscriptiondates, program
    student=sv.get_student_row(con,sid)
    try: start,end=logic.month_bounds(ym)
    except (ValueError,TypeError): raise sv.ApiError('Выберите месяц')
    events={}
    def add(day,gid,kind,status='planned',**extra):
        key=(day,gid,kind)
        events[key]={'date':day,'group_id':gid,'kind':kind,'status':status,**extra}
    groups={r['id']:dict(r) for r in con.execute('SELECT g.*,b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id')}
    marks={(r['date'],r['group_id']):dict(r) for r in con.execute('SELECT * FROM attendance WHERE student_id=? AND date BETWEEN ? AND ?',(sid,start.isoformat(),end.isoformat()))}
    makeups=list(con.execute("SELECT * FROM makeups WHERE student_id=? AND date BETWEEN ? AND ? AND status!='cancelled'",(sid,start.isoformat(),end.isoformat())))
    mk={(r['date'],r['group_id']):r for r in makeups}
    singles={(r['date'],r['group_id']):r for r in con.execute("SELECT * FROM single_visits WHERE student_id=? AND date BETWEEN ? AND ? AND status='booked'",(sid,start.isoformat(),end.isoformat()))}
    billed={r[0] for r in con.execute("SELECT enrollment_id FROM subscriptions WHERE student_id=? AND month=? AND status!='cancelled' AND kind='month'",(sid,ym))}
    for e in con.execute("SELECT * FROM enrollments WHERE student_id=?",(sid,)).fetchall():
        g=groups.get(e['group_id'])
        if not g:continue
        if not e['active'] and not e['end_date']:continue
        for day in schedule.month_dates(con,g,e['weekdays'],ym):
            iso=day.isoformat()
            effective_start=e['start_date'] or (start.isoformat() if e['id'] in billed else e['created_at'][:10])
            if iso<effective_start or e['end_date'] and iso>=e['end_date']:continue
            if closures.is_closed(con,g['id'],day) or subscriptiondates.excluded(con,sid,g['id'],iso):continue
            if (iso,g['id']) not in singles and subscriptiondates.outside_period(con,sid,g['id'],iso):continue
            if logic.in_vacation(day,sv.vacations_of(con,sid)):continue
            add(iso,g['id'],'single' if (iso,g['id']) in singles else 'subscription')
    for (day,gid),a in marks.items():
        g=groups.get(gid,{})
        kind='makeup' if (day,gid) in mk or g.get('is_makeup') else 'single' if (day,gid) in singles else 'subscription'
        add(day,gid,kind,'warned' if a['warned'] and day>sv.today().isoformat() else a['status'],attendance_id=a['id'],warned=bool(a['warned']),note=a['note'] or '',resolved=a['resolved'])
    for m in makeups:
        a=marks.get((m['date'],m['group_id']))
        add(m['date'],m['group_id'],'makeup',a['status'] if a else {'done':'present','missed':'absent'}.get(m['status'],'planned'),makeup_id=m['id'])
        # Не дублируем отработку как обычное посещение в тот же день/группу.
        events.pop((m['date'],m['group_id'],'subscription'),None)
    for (day,gid),v in singles.items():
        a=marks.get((day,gid))
        add(day,gid,'single',a['status'] if a else 'planned',single_id=v['id'])
        events.pop((day,gid,'subscription'),None)
    trial_day=(student.get('trial_at') or '')[:10]
    if trial_day.startswith(ym):
        # Для завершённого ПУ показываем подтверждённый вид/дату; историческую группу не угадываем.
        gid=student.get('trial_group_id') or (student['group_id'] if student['funnel']=='sales' else None)
        add(trial_day,gid,'trial','present' if student['trial_came']==1 else 'absent' if student['trial_came']==0 else 'planned')
        events.pop((trial_day,gid,'subscription'),None)
    for m in con.execute('SELECT * FROM trial_misses WHERE student_id=? AND undone=0 AND substr(trial_at,1,7)=?',(sid,ym)):
        add(m['trial_at'][:10],m['group_id'],'trial','absent',note=m['reason'] or '')
    for trial in con.execute("""SELECT * FROM erp_trial_visits WHERE student_id=? AND visit_at>=? AND visit_at<?""",
                             (sid, start.isoformat(), (end+timedelta(days=1)).isoformat())):
        day, gid = trial['visit_at'][:10], trial['group_id']
        if day == trial_day and gid == (student.get('trial_group_id') or student['group_id']):
            events.pop((day, gid, 'trial'), None)
            events.pop((day, None, 'trial'), None)
        if (day, gid) not in marks:
            events.pop((day, gid, 'subscription'), None)
        events[(day, gid, 'trial', trial['id'])] = {'date':day,'group_id':gid,'kind':'trial',
            'status':'present' if trial['came'] else 'absent','trial_time':trial['visit_at'][11:16]}
    result=[]
    for event in events.values():
        g=groups.get(event['group_id'],{})
        day=date.fromisoformat(event['date'])
        event.update(group=g.get('name',''),branch=g.get('branch',''),time=logic.group_time(schedule.on(con,g,day),day.weekday()) if g else '',future=event['date']>sv.today().isoformat())
        result.append(event)
    for event in result:
        if event.get('trial_time'):
            event['time'] = event['trial_time']
    result.sort(key=lambda x:(x['date'],x['time'],x['kind']))
    return {'month':ym,'today':sv.today().isoformat(),'events':result,'counters':sv.absence_counters(con,sid),
            'attended':sum(x['status']=='present' for x in result),'missed':sum(x['status']=='absent' and x['kind']!='makeup' for x in result)}


def suggestions(con,sid,days=21):
    from . import services as sv, capacity, logic, schedule, closures, program
    student=sv.get_student_row(con,sid);counters=sv.absence_counters(con,sid)
    out=[];today=sv.today();now=datetime.now(ZoneInfo('Europe/Moscow')).strftime('%H:%M')
    own={e['group_id'] for e in sv.enrollments_of(con,sid)}
    sp=program.student_program(con,sid)
    commitments=set()
    if counters['available']:
        months={today.isoformat()[:7],(today+timedelta(days=20)).isoformat()[:7]}
        for month in months:
            commitments.update((e['date'],e['time']) for e in calendar(con,sid,month)['events'] if e['time'] and e['status'] not in ('absent','warned') and e['date']>=today.isoformat())
    if counters['available']:
        for row in con.execute('SELECT g.*,b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id WHERE g.active=1 AND b.active=1').fetchall():
            g=dict(row)
            if g['id'] in own:continue
            if g['program_id'] and (not sp or g['program_id']!=sp['program']['id'] or g['lesson_kind']!='ofp'):continue
            warning=logic.age_warning(student['birth'],g['age'],g['branch']) if student['birth'] and g['age'] else ''
            if warning and not sp:continue
            for i in range(min(max(int(days),1),31)):
                day=today+timedelta(days=i);iso=day.isoformat();version=schedule.on(con,g,day)
                if day.weekday() not in logic.parse_weekdays(version['weekdays']) or closures.is_closed(con,g['id'],day):continue
                time=logic.group_time(version,day.weekday())
                if not time or (iso,time) in commitments:continue
                if i==0 and time and time<=now:continue
                try:program.check_makeup(con,sid,g['id'],iso)
                except sv.ApiError:continue
                occupancy=capacity.occupancy(con,g['id'],iso);limit=occupancy['limit']
                if limit and occupancy['count']>=limit:continue
                if con.execute("SELECT 1 FROM makeups WHERE student_id=? AND date=? AND group_id=? AND status='booked'",(sid,iso,g['id'])).fetchone():continue
                out.append({'group_id':g['id'],'group':g['name'],'branch':g['branch'],'date':iso,'time':time,
                            'count':occupancy['count'],'limit':limit,'free':max(0,limit-occupancy['count']) if limit else None,
                            'age_warning':warning,'approval_required':bool(sp and day.weekday()==0)})
    out.sort(key=lambda x:(x['count'],x['date'],x['time'],x['group']))
    out=out[:60]
    n=counters['available'];name=student['name'].split()[0]
    word='занятие' if n%10==1 and n%100!=11 else 'занятия' if n%10 in (2,3,4) and n%100 not in (12,13,14) else 'занятий'
    text=f'{name} может посетить ещё {n} {word} в счёт пропусков. Давайте подберём удобное время, чтобы не терять занятия и сохранить тренировочный ритм ⚽'
    offers=[x for x in out if not x["age_warning"]]
    if offers:
        text+='\n\nМожно прийти:\n'+'\n'.join(f"• {x['date'][8:10]}.{x['date'][5:7]} в {x['time']} — {x['branch']}, {x['group']}" for x in offers[:3])
        text+='\n\nКакой вариант вам удобнее?'
    else:text+='\n\nНапишите, какие дни вам подходят — подберём время.'
    return {'counters':counters,'slots':out,'message':text if n else ''}
