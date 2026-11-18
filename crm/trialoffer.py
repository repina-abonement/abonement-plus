"""Предложение ближайших пробных одним нажатием рядом с перепиской.

Подбираем группы по возрасту ребёнка (4 года → группы «3–4» и «4–5»), берём ближайшие тренировки, где есть места
(в филиалах с лимитом считаем лимит, иначе места в группе), без праздников и без сборной (туда ПУ нет).
Цель — чтобы ребёнок пришёл на пробное как можно раньше.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from . import logic

WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
DEFAULT = ("Здравствуйте! Вот ближайшие пробные занятия по футболу, на которые можно прийти ⚽\n\n{slots}\n\n"
           "Какой вариант вам удобнее? Запишем и пришлём напоминание 🙌")


def age_range(txt: str) -> tuple[int, int] | None:
    nums = [int(x) for x in re.findall(r"\d+", txt or "")]
    if not nums:
        return None
    if "+" in (txt or "") and len(nums) == 1:
        return nums[0], 99
    return nums[0], nums[-1]


def group_age_range(group):
    explicit=(group['age'] or '').strip()
    if explicit:
        return age_range(explicit)
    # Legacy ERP groups can have their explicit age range only in the name.
    match=re.search(r'(?<![\d:])(\d{1,2})\s*[–—-]\s*(\d{1,2})(?![\d:])',(dict(group).get('source_name') or group['name']) or '')
    return (int(match[1]),int(match[2])) if match else None


def years(birth: str, on: date | None = None) -> int | None:
    try:
        b = date.fromisoformat(birth)
    except (TypeError, ValueError):
        return None
    on = on or date.today()
    return on.year - b.year - ((on.month, on.day) < (b.month, b.day))


def slots(con, age: int, branch_id: int | None = None, limit: int = 4, days: int = 14, diagnostics=None) -> list[dict]:
    from . import capacity, closures, schedule
    now = datetime.now()
    out, fill = [], {}
    for g in con.execute("""SELECT g.*, b.name AS branch, b.address, b.lesson_limit, b.active AS branch_active FROM groups g JOIN branches b ON b.id=g.branch_id
                            WHERE g.active=1 AND b.active=1"""
                         + (" AND g.branch_id=?" if branch_id else ""), (branch_id,) if branch_id else ()).fetchall():
        r = group_age_range(g)
        reason = ('Группа в архиве' if not g['active'] else
                  'Филиал в архиве' if not g['branch_active'] else
                  'Это группа отработок' if g['is_makeup'] else
                  'Это сборная — пробное не предусмотрено' if g['program_id'] else
                  'В настройках группы выключена запись на пробное' if not g['trial_ok'] else
                  'Не указан возраст группы' if not r else
                  f'Группа для возраста {g["age"] or str(r[0])+"–"+str(r[1])}' if not r[0]<=age<=r[1] else
                  'Это не детская футбольная группа' if 'взросл' in (g['age'] or '').lower() or 'пап' in g['name'].lower() or 'офп' in g['name'].lower() else '')
        diag={'group':g['name'],'reason':reason,'scheduled':0,'closed':0,'full':0,'missing_time':0,'too_soon':0,'available':0}
        if diagnostics is not None:diagnostics.append(diag)
        if reason:continue
        for i in range(days):
            d = now.date() + timedelta(days=i)
            actual = schedule.on(con,g,d)
            if d.weekday() not in logic.parse_weekdays(actual["weekdays"]):continue
            diag['scheduled']+=1
            if closures.is_closed(con,g['id'],d):diag['closed']+=1;continue
            t = logic.group_time(actual,d.weekday())
            if not t:diag['missing_time']+=1;continue
            if datetime.combine(d,datetime.strptime(t,'%H:%M').time())<now+timedelta(hours=2):diag['too_soon']+=1;continue
            o = capacity.occupancy(con, g["id"], d.isoformat())
            cap = o["limit"] or g["capacity"]
            free = cap - o["count"]
            if free <= 0:
                diag['full']+=1;continue
            diag['available']+=1
            out.append({"group_id": g["id"], "group": g["name"], "age": g["age"] or f"{r[0]}–{r[1]}", "branch_id": g["branch_id"], "branch": g["branch"],
                        "address": g["address"] or "", "date": d.isoformat(), "time": t, "free": free,
                        "fill": fill.setdefault(g["id"], _fill(con, g)),
                        "label": f"{WD[d.weekday()]} {d.strftime('%d.%m')} в {t}"})
    # сначала группы, где детей меньше (Юлия, 01.11: чтобы дети распределялись по группам равномерно), потом ближайшие даты
    out.sort(key=lambda x: (x["fill"], x["date"], x["time"]))
    # не больше двух вариантов в один день и в одну группу, чтобы предложить разные дни
    res, per_day, per_group = [], {}, {}
    for s in out:
        if per_day.get(s["date"], 0) >= 2 or per_group.get(s["group_id"], 0) >= 2:
            continue
        per_day[s["date"]] = per_day.get(s["date"], 0) + 1
        per_group[s["group_id"]] = per_group.get(s["group_id"], 0) + 1
        res.append(s)
        if len(res) >= limit:
            break
    return res


def _fill(con, g) -> float:
    """Насколько заполнена группа постоянными учениками: доля занятых мест."""
    kids = con.execute("SELECT COUNT(DISTINCT student_id) FROM enrollments WHERE group_id=? AND active=1", (g["id"],)).fetchone()[0]
    return round(kids / max(1, g["capacity"] or 12), 3)


def offer(con, age: int | None, child: str = "", branch_id: int | None = None, n: int = 4) -> dict:
    from . import comms
    if age is None:
        return {"slots": [], "text": "", "error": "Укажите возраст ребёнка"}
    diagnostics=[]
    sl = slots(con, age, branch_id, n, diagnostics=diagnostics)
    many_branches = len({s["branch_id"] for s in sl}) > 1
    lines = []
    for s in sl:
        place = s["branch"] + (f", {s['address']}" if s["address"] and not many_branches else "")
        lines.append(f"📅 {s['label']}, {place} (группа {s['age']} лет)")
    tpl = comms.get(con, "msg_trial_offer") or DEFAULT
    child_txt = (child.split()[0] if child and "заявка" not in child.lower() and "ребёнок" not in child.lower() else "ребёнка")
    text = tpl.replace("{child}", child_txt).replace("{slots}", "\n".join(lines)).replace("{age}", str(age))
    for item in diagnostics:
        if not item['reason'] and not item['available']:
            item['reason']=('Нет занятий по расписанию в ближайшие 2 недели' if not item['scheduled'] else
                            'Все ближайшие занятия отменены' if item['closed']==item['scheduled'] else
                            'Нет свободных мест на доступные даты' if item['full'] else
                            'Не указано время занятий' if item['missing_time'] else 'Ближайшее занятие уже начинается — запись закрыта')
    details=[f"{item['group']}: {item['reason']}" for item in diagnostics if item['reason']]
    error='Не найдено доступных пробных на ближайшие 2 недели'
    if not sl:error+='\n'+('\n'.join(details) if details else 'В выбранном филиале нет групп')
    return {"slots":sl,"text":text if sl else "","age":age,"error":"" if sl else error,"diagnostics":diagnostics if not sl else []}


def booking_options(con, sid: int, birth: str = '', day: str = '', branch_id: int | None = None) -> dict:
    """Scheduled, age-appropriate trial slots for the inline booking form."""
    from . import smart, schedule, closures, capacity
    from .services import ApiError
    info = smart.required_info(con, sid, 'trial')
    birth = birth or info['values']['birth']
    if not birth:
        return {'slots': [], 'error': 'Укажите дату рождения ребёнка, чтобы выбрать подходящие группы'}
    try:
        born = date.fromisoformat(birth)
        start = date.fromisoformat(day) if day else date.today()
    except (ValueError,TypeError):
        raise ApiError('Проверьте дату рождения и дату занятия')
    now = datetime.now()
    if born >= now.date() or born <= date(2000,1,1):
        raise ApiError('Проверьте дату рождения ребёнка')
    if start < now.date():
        raise ApiError('Для пробного выберите сегодня или будущую дату')
    student=con.execute('SELECT group_id,trial_at FROM students WHERE id=?',(sid,)).fetchone()
    slots = []
    for group in con.execute('''SELECT g.*,b.name AS branch,b.address FROM groups g JOIN branches b ON b.id=g.branch_id
        WHERE g.active=1 AND b.active=1 AND g.is_makeup=0 AND g.program_id IS NULL AND g.trial_ok=1'''
        + (' AND g.branch_id=?' if branch_id else ''),(branch_id,) if branch_id else ()):
        ages = group_age_range(group)
        if not ages or 'взросл' in group['age'].lower() or 'пап' in group['name'].lower() or 'ОФП' in group['name']:
            continue
        for offset in range(1 if day else 28):
            d = start + timedelta(days=offset)
            age = years(birth,d)
            actual = schedule.on(con,group,d)
            if not ages[0] <= age <= ages[1] or d.weekday() not in logic.parse_weekdays(actual['weekdays']):
                continue
            if closures.is_closed(con,group['id'],d):
                continue
            time = logic.group_time(actual,d.weekday())
            if not time:
                continue
            at = datetime.combine(d,datetime.strptime(time,'%H:%M').time())
            if at < now + timedelta(hours=2):
                continue
            occupancy = capacity.occupancy(con,group['id'],d.isoformat())
            limit = occupancy['limit'] or group['capacity']
            own_booking=student['group_id']==group['id'] and (student['trial_at'] or '')[:10]==d.isoformat()
            own_hold=any(h['student_id']==sid for h in capacity.active_holds(con,group['id'],d.isoformat()))
            free = limit - occupancy['count'] + int(own_booking or own_hold)
            if free <= 0:
                continue
            slots.append({'group_id':group['id'],'group':group['name'],'branch_id':group['branch_id'],
                          'branch':group['branch'],'date':d.isoformat(),'time':time,'free':free})
    slots.sort(key=lambda s:(s['date'],s['time'],s['branch'],s['group'],s['group_id']))
    return {'slots':slots,'error':'' if slots else 'На выбранные даты нет подходящих свободных мест. Выберите другой день или филиал'}


def book_inline(con,sid: int,data: dict,user: dict) -> dict:
    """Save required data and the selected slot together; failed bookings leave no partial edits."""
    from . import smart, services as sv
    s = con.execute('SELECT * FROM students WHERE id=?',(sid,)).fetchone()
    if not s or s['archived']:
        raise sv.ApiError('Для записи выберите действующую карточку клиента')
    try:
        gid = int(data.get('group_id') or 0)
    except (ValueError,TypeError):
        raise sv.ApiError('Выберите группу и время занятия')
    day = str(data.get('date') or '')
    time = str(data.get('time') or '')
    if not day or not time or not gid:
        raise sv.ApiError('Выберите дату, группу и время занятия')
    options = booking_options(con,sid,str(data.get('birth') or ''),day)
    chosen = next((x for x in options['slots'] if x['group_id']==gid and x['time']==time),None)
    if not chosen:
        raise sv.ApiError('Выбранное время недоступно. Обновите варианты и выберите свободное занятие')
    con.execute('SAVEPOINT inline_trial_booking')
    try:
        fields = {k:data[k] for k in ('child_name','birth','parent_name','phone') if k in data}
        if str(data.get('email') or '').strip():
            fields['email'] = data['email']
        smart.fill_required(con,sid,fields,user)
        smart.require(con,sid,'trial')
        changes={'trial_at':day+'T'+time}
        if s['funnel']=='sales':
            changes['group_id']=gid
        sv.update_student(con,sid,changes,user)
        con.execute('UPDATE students SET trial_group_id=? WHERE id=?',(gid,sid))
        con.execute('RELEASE inline_trial_booking')
    except BaseException as error:
        con.execute('ROLLBACK TO inline_trial_booking');con.execute('RELEASE inline_trial_booking')
        if isinstance(error,sv.ApiError) and getattr(error,'extra',{}).get('need'):
            raise sv.ApiError(error.message,422)
        raise
    return {'ok':True,'slot':chosen}
