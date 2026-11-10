"""Перевод ученика в другую группу без удаления абонемента.

С даты перевода ребёнок ходит в новую группу, до неё остаётся в журнале старой (посещения и зарплата не сдвигаются).
Абонементы старой группы с месяца перевода переезжают в новую: та же цена одного занятия (со скидкой, наценкой
и перерасчётом, как продали), число занятий по новому расписанию. Если перевод с середины месяца, абонемент делится:
занятия до даты остаются старой группе, после неё переходят в новую. Разница по деньгам сама уходит на депозит
или в долг: платежи ученика общие и раскладываются по абонементам заново (money.reconcile).
"""
from __future__ import annotations

import math
import json
from datetime import date

from . import logic
from .db import now_iso
from .services import ApiError, _clean_days, _days_label, log, sync_primary, vacations_of


def init(con):
    cols = {r[1] for r in con.execute("PRAGMA table_info(enrollments)")}
    if "start_date" not in cols:  # с какой даты ребёнок в группе и до какой (не включая): нужно для журнала при переводе
        con.execute("ALTER TABLE enrollments ADD COLUMN start_date TEXT NOT NULL DEFAULT ''")
        con.execute("ALTER TABLE enrollments ADD COLUMN end_date TEXT NOT NULL DEFAULT ''")


def _dates(con, group_id: int, days: list[int], ym: str, vacations) -> list[date]:
    from . import closures
    ms, me = logic.month_bounds(ym)
    closed = closures.closed_dates(con, group_id, ms, me)
    return [d for d in logic.lesson_dates(days, ym) if d.isoformat() not in closed and not logic.in_vacation(d, vacations)]


def _load(con, eid: int, new_gid: int, start: str, weekdays):
    e = con.execute("""SELECT e.*, g.name AS group_name, g.weekdays AS gw, g.program_id, g.lesson_kind FROM enrollments e
                       JOIN groups g ON g.id=e.group_id WHERE e.id=?""", (eid,)).fetchone()
    if not e or not e["active"]:
        raise ApiError("Группа ученика не найдена", 404)
    g = con.execute("SELECT * FROM groups WHERE id=? AND active=1 AND is_makeup=0", (new_gid,)).fetchone()
    if not g:
        raise ApiError("Выберите группу, куда переводим")
    if g["id"] == e["group_id"]:
        raise ApiError("Ученик уже в этой группе")
    if g["program_id"] or e["program_id"] and e["lesson_kind"] != "arena":
        raise ApiError("Для перевода из сборной выберите основной абонемент Арктики")
    if con.execute("SELECT 1 FROM enrollments WHERE student_id=? AND group_id=? AND active=1", (e["student_id"], g["id"])).fetchone():
        raise ApiError("Ученик уже ходит в эту группу")
    try:
        d0 = date.fromisoformat(start or "")
    except ValueError:
        raise ApiError("Укажите дату, с которой ребёнок ходит в новую группу")
    if d0 < date.today().replace(day=1):
        raise ApiError("Перевести можно с начала текущего месяца или позже")
    marks = con.execute("SELECT COUNT(*) FROM attendance WHERE student_id=? AND group_id=? AND date>=?",
                        (e["student_id"], e["group_id"], d0.isoformat())).fetchone()[0]
    if marks:
        raise ApiError(f"В старой группе уже есть отметки с {d0:%d.%m}. Выберите дату после последней отметки")
    new_days = _clean_days(weekdays, g["weekdays"]) if weekdays else ""
    return dict(e), dict(g), d0, new_days


def plan(con, eid: int, new_gid: int, start: str, weekdays=None, tariff="fixed") -> dict:
    """Что произойдёт при переводе: по каждому абонементу с месяца перевода."""
    e, g, d0, new_days = _load(con, eid, new_gid, start, weekdays)
    if e["program_id"]:
        return _program_plan(con,e,g,d0,new_days,tariff)
    sid = e["student_id"]
    vac = vacations_of(con, sid)
    old_days = logic.parse_weekdays(e["weekdays"]) or logic.parse_weekdays(e["gw"])
    nd = logic.parse_weekdays(new_days) or logic.parse_weekdays(g["weekdays"])
    months = []
    for x in con.execute("""SELECT * FROM subscriptions WHERE enrollment_id=? AND month>=? AND status!='cancelled'
                            ORDER BY month""", (eid, logic.month_of(d0))).fetchall():
        x = dict(x)
        unit = x["price"] / x["lessons"] if x["lessons"] else x["per_lesson"]
        keep = [d for d in _dates(con, e["group_id"], old_days, x["month"], vac) if d < d0]
        new = [d for d in _dates(con, g["id"], nd, x["month"], vac) if d >= d0]
        keep_price = round(unit * len(keep)) if keep else 0
        new_price = math.ceil(unit * len(new) - 1e-9)
        months.append({"sub_id": x["id"], "month": x["month"], "label": logic.month_label(x["month"]), "status": x["status"],
                       "old_lessons": x["lessons"], "old_price": x["price"],
                       "keep_lessons": len(keep), "keep_price": keep_price,
                       "new_lessons": len(new), "new_price": new_price, "new_dates": [d.isoformat() for d in new]})
    diff = sum(m["keep_price"] + m["new_price"] - m["old_price"] for m in months)
    return {"student_id": sid, "from_group": e["group_name"], "to_group": g["name"], "start": d0.isoformat(),
            "days_label": _days_label(new_days) if new_days else "", "months": months, "diff": diff}


def _apply(con, eid: int, new_gid: int, start: str, user, weekdays=None, tariff="fixed") -> dict:
    p = plan(con, eid, new_gid, start, weekdays,tariff)
    e, g, d0, new_days = _load(con, eid, new_gid, start, weekdays)
    if e["program_id"]:
        return _program_apply(con,e,g,d0,new_days,p,user,tariff)
    sid = e["student_id"]
    con.execute("UPDATE enrollments SET active=0, end_date=? WHERE id=?", (d0.isoformat(), eid))
    new_eid = con.execute("""INSERT INTO enrollments(student_id, group_id, weekdays, tariff, created_at, start_date)
                             VALUES (?,?,?,?,?,?)""", (sid, g["id"], new_days, e["tariff"], now_iso(), d0.isoformat())).lastrowid
    cols = [r[1] for r in con.execute("PRAGMA table_info(subscriptions)") if r[1] != "id"]
    notes = []
    for m in p["months"]:
        x = dict(con.execute("SELECT * FROM subscriptions WHERE id=?", (m["sub_id"],)).fetchone())
        part = lambda v, n: round((v or 0) * n / x["lessons"]) if x["lessons"] else 0  # скидку и наценку делим по занятиям
        share = lambda v: part(v, m["new_lessons"])
        if m["keep_lessons"]:  # с середины месяца: начало месяца остаётся старой группе, остальное новым абонементом
            con.execute("UPDATE subscriptions SET lessons=?, price=?, late_fee=?, discount_amount=? WHERE id=?",
                        (m["keep_lessons"], m["keep_price"], part(x.get("late_fee"), m["keep_lessons"]),
                         part(x.get("discount_amount"), m["keep_lessons"]), x["id"]))
            row = x | {"enrollment_id": new_eid, "lessons": m["new_lessons"], "price": m["new_price"], "credit_lessons": 0,
                       "late_fee": share(x.get("late_fee")), "discount_amount": share(x.get("discount_amount")),
                       "status": "invoiced", "paid_at": None, "created_at": now_iso()}
            con.execute(f"INSERT INTO subscriptions({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [row[c] for c in cols])
        else:
            con.execute("UPDATE subscriptions SET enrollment_id=?, lessons=?, price=?, late_fee=?, discount_amount=? WHERE id=?",
                        (new_eid, m["new_lessons"], m["new_price"], share(x.get("late_fee")), share(x.get("discount_amount")), x["id"]))
        notes.append(f"{m['label']}: {m['old_lessons']} зан. {m['old_price']} ₽ → "
                     + (f"{m['keep_lessons']} зан. в старой группе {m['keep_price']} ₽ и " if m["keep_lessons"] else "")
                     + f"{m['new_lessons']} зан. в новой {m['new_price']} ₽")
    sync_primary(con, sid)
    from . import capacity, money
    money.reconcile(con, sid)
    capacity.ensure_roster(con, g["id"])
    log(con, sid, f"Переведён из группы {e['group_name']} в {g['name']} с {d0:%d.%m.%Y}"
        + (f" ({_days_label(new_days)})" if new_days else "") + (". Абонементы: " + "; ".join(notes) if notes else ""), user, "system")
    return p | {"enrollment_id": new_eid}


def _program_plan(con,e,g,d0,new_days,tariff):
    """Остатки Арктики и ОФП вместе оплачивают новый обычный абонемент."""
    from . import program, services as sv, money
    if tariff not in ('fixed','flex'):
        raise ApiError('Выберите тариф новой группы')
    sid=e['student_id'];start=d0.isoformat()
    members=con.execute('''SELECT en.*, gr.lesson_kind FROM enrollments en JOIN groups gr ON gr.id=en.group_id
        WHERE en.student_id=? AND gr.program_id=? AND en.active=1''',(sid,e['program_id'])).fetchall()
    for member in members:
        if con.execute('SELECT 1 FROM attendance WHERE student_id=? AND group_id=? AND date>=? LIMIT 1',
                       (sid,member['group_id'],start)).fetchone():
            raise ApiError('В Арктике или ОФП уже есть отметки после даты перевода. Выберите более поздний день')
    source=con.execute("SELECT * FROM subscriptions WHERE enrollment_id=? AND month>=? AND status!='cancelled' AND kind='month' ORDER BY month",(e['id'],logic.month_of(d0))).fetchall()
    months=[];covered=money.balance(con,sid)['covered']
    for row in source:
        x=dict(row);saved=json.loads(x['program_selection'] or '{}');components=saved.get('contract',{}).get('components')
        if not components:
            quote=program.quote(con,sid,x['month'],e,excluded_dates=json.loads(x['excluded_dates']),selection=saved or {'tariff':x['tariff']})
            components=quote['components']
        if sum(len(c['dates']) for c in components)!=x['lessons']:
            raise ApiError(f"Для старого абонемента на {logic.month_label(x['month'])} нужно уточнить проданные даты Арктики и ОФП перед переводом")
        weight=sum(c['rate']*len(c['dates']) for c in components)
        remaining={kind:sum(c['rate']*sum(day>=start for day in c['dates']) for c in components if c['kind']==kind) for kind in ('arena','ofp')}
        credit=round(x['price']*sum(remaining.values())/weight) if weight else 0
        arena=round(x['price']*remaining['arena']/weight) if weight else 0
        keep=x['price']-credit
        # Изолированный расчёт в новой группе: ни записи, ни история не сохраняются.
        con.execute('SAVEPOINT transfer_quote')
        try:
            # Перевод действующего ученика не является первой покупкой после пробного.
            con.execute("UPDATE students SET converted_at='' WHERE id=?",(sid,))
            temp=con.execute('''INSERT INTO enrollments(student_id,group_id,weekdays,tariff,created_at,start_date)
                VALUES (?,?,?,?,?,?)''',(sid,g['id'],new_days,tariff,now_iso(),start)).lastrowid
            q=sv.quote_for(con,sid,x['month'],temp)
            q=sv.quote_for(con,sid,x['month'],temp,excluded_dates=[day for day in q['calendar_dates'] if day<start])
        finally:
            con.execute('ROLLBACK TO transfer_quote');con.execute('RELEASE transfer_quote')
        months.append({'sub_id':x['id'],'month':x['month'],'label':logic.month_label(x['month']),'old_lessons':x['lessons'],'old_price':x['price'],
            'keep_lessons':sum(day<start for c in components for day in c['dates']),'keep_price':keep,
            'new_lessons':q['lessons_paid'],'new_price':q['total'],'new_dates':q['dates'],
            'arena_credit':arena,'ofp_credit':credit-arena,'credit':credit,'paid_credit':max(0,covered.get(x['id'],0)-keep),'quote':q,
            'components':[{**c,'dates':[day for day in c['dates'] if day<start]} for c in components]})
    b=money.balance(con,sid);net=b['debt']+b['upcoming']-b['deposit']+sum(m['new_price']-m['credit'] for m in months)
    return {'projected_due':max(0,net),'projected_deposit':max(0,-net),'program_transfer':True,'student_id':sid,'from_group':'Сборная Арктика','to_group':g['name'],'start':start,'months':months,
            'diff':sum(m['new_price']-m['credit'] for m in months),'credit_total':sum(m['credit'] for m in months),
            'paid_credit':sum(m['paid_credit'] for m in months),'tariff':tariff}


def _program_apply(con,e,g,d0,new_days,p,user,tariff):
    from . import capacity,money
    sid=e['student_id'];start=d0.isoformat()
    con.execute('''UPDATE enrollments SET active=0,end_date=?,next_plan='' WHERE student_id=? AND active=1 AND group_id IN
        (SELECT id FROM groups WHERE program_id=?)''',(start,sid,e['program_id']))
    new_eid=con.execute('''INSERT INTO enrollments(student_id,group_id,weekdays,tariff,created_at,start_date)
        VALUES (?,?,?,?,?,?)''',(sid,g['id'],new_days,tariff,now_iso(),start)).lastrowid
    for m in p['months']:
        # Старая стоимость уменьшается только на остаток; реальные платежи остаются в общем балансе.
        old=con.execute('SELECT * FROM subscriptions WHERE id=?',(m['sub_id'],)).fetchone()
        snap=json.loads(old['program_selection'] or '{}');snap['contract']={'components':m['components']}
        con.execute('''UPDATE subscriptions SET lessons=?,price=?,corrected=1,program_selection=?,late_fee=0,discount_amount=0 WHERE id=?''',
                    (m['keep_lessons'],m['keep_price'],json.dumps(snap),m['sub_id']))
        q=m['quote'];disc=q.get('discount') or {}
        con.execute('''INSERT INTO subscriptions(student_id,enrollment_id,month,tariff,lessons,per_lesson,price,late_fee,
            discount_name,discount_amount,excluded_dates,corrected,status,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,1,'invoiced',?)''',
            (sid,new_eid,m['month'],tariff,q['lessons_paid'],q['per_lesson'],q['total'],q['late_fee'],disc.get('name',''),disc.get('amount',0),json.dumps(q.get('excluded_dates',[])),now_iso()))
    sync_primary(con,sid);money.reconcile(con,sid);capacity.ensure_roster(con,g['id'])
    detail='; '.join(f"{m['label']}: остаток Арктики {m['arena_credit']} ₽ + ОФП {m['ofp_credit']} ₽, новая группа {m['new_price']} ₽" for m in p['months'])
    log(con,sid,f"Перевод из сборной в {g['name']} с {start}. {detail}. Разница {p['diff']} ₽; реальные платежи сохранены",user,'payment')
    return p|{'enrollment_id':new_eid,'balance':money.balance(con,sid)}


def confirmation(p):
    return {'diff':p['diff'],'credit_total':p.get('credit_total'),'months':[[m['sub_id'],m['old_price'],m['keep_price'],m['new_price'],m['new_lessons']] for m in p['months']]}


def apply(con,eid,new_gid,start,user,weekdays=None,tariff='fixed',expected=None):
    con.execute('SAVEPOINT group_transfer')
    try:
        if expected is not None and confirmation(plan(con,eid,new_gid,start,weekdays,tariff))!=expected:
            raise ApiError('Расчёт перевода изменился. Обновите предварительный расчёт')
        result=_apply(con,eid,new_gid,start,user,weekdays,tariff)
        con.execute('RELEASE group_transfer')
        return result
    except BaseException:
        con.execute('ROLLBACK TO group_transfer');con.execute('RELEASE group_transfer')
        raise
