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


def years(birth: str, on: date | None = None) -> int | None:
    try:
        b = date.fromisoformat(birth)
    except (TypeError, ValueError):
        return None
    on = on or date.today()
    return on.year - b.year - ((on.month, on.day) < (b.month, b.day))


def slots(con, age: int, branch_id: int | None = None, limit: int = 4, days: int = 14) -> list[dict]:
    from . import capacity, closures
    now = datetime.now()
    out, fill = [], {}
    for g in con.execute("""SELECT g.*, b.name AS branch, b.address, b.lesson_limit FROM groups g JOIN branches b ON b.id=g.branch_id
                            WHERE g.active=1 AND b.active=1 AND g.is_makeup=0 AND g.program_id IS NULL AND g.trial_ok=1"""
                         + (" AND g.branch_id=?" if branch_id else ""), (branch_id,) if branch_id else ()).fetchall():
        r = age_range(g["age"])
        if not r or not r[0] <= age <= r[1] or "взросл" in (g["age"] or "").lower() or "пап" in g["name"].lower() \
                or "ОФП" in g["name"]:  # пробное — на футбол, не на ОФП
            continue
        wd = logic.parse_weekdays(g["weekdays"])
        for i in range(days):
            d = now.date() + timedelta(days=i)
            if d.weekday() not in wd or closures.is_closed(con, g["id"], d):
                continue
            t = logic.group_time(dict(g), d.weekday())
            if not t or datetime.combine(d, datetime.strptime(t, "%H:%M").time()) < now + timedelta(hours=2):
                continue
            o = capacity.occupancy(con, g["id"], d.isoformat())
            cap = o["limit"] or g["capacity"]
            free = cap - o["count"]
            if free <= 0:
                continue
            out.append({"group_id": g["id"], "group": g["name"], "age": g["age"], "branch_id": g["branch_id"], "branch": g["branch"],
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
    sl = slots(con, age, branch_id, n)
    many_branches = len({s["branch_id"] for s in sl}) > 1
    lines = []
    for s in sl:
        place = s["branch"] + (f", {s['address']}" if s["address"] and not many_branches else "")
        lines.append(f"📅 {s['label']}, {place} (группа {s['age']} лет)")
    tpl = comms.get(con, "msg_trial_offer") or DEFAULT
    child_txt = (child.split()[0] if child and "заявка" not in child.lower() and "ребёнок" not in child.lower() else "ребёнка")
    text = tpl.replace("{child}", child_txt).replace("{slots}", "\n".join(lines)).replace("{age}", str(age))
    return {"slots": sl, "text": text if sl else "", "age": age,
            "error": "" if sl else "Нет свободных мест для этого возраста на ближайшие 2 недели"}


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
        ages = age_range(group['age'])
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
    if not s or s['archived'] or s['funnel']!='sales':
        raise sv.ApiError('Для записи выберите действующую карточку лида')
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
        sv.update_student(con,sid,{'group_id':gid,'trial_at':day+'T'+time},user)
        con.execute('RELEASE inline_trial_booking')
    except BaseException as error:
        con.execute('ROLLBACK TO inline_trial_booking');con.execute('RELEASE inline_trial_booking')
        if isinstance(error,sv.ApiError) and getattr(error,'extra',{}).get('need'):
            raise sv.ApiError(error.message,422)
        raise
    return {'ok':True,'slot':chosen}
