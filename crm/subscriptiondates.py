"""Индивидуальные исключения дат при продаже абонемента."""
import json
from datetime import date


def validate(raw, available):
    from .services import ApiError
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, list) or any(not isinstance(d, str) for d in raw):
        raise ApiError("Передайте список исключённых дат")
    dates = sorted(set(raw))
    try:
        for d in dates:
            if date.fromisoformat(d).isoformat() != d:
                raise ValueError()
    except ValueError:
        raise ApiError("Неверная дата занятия")
    if not set(dates).issubset(set(available)):
        raise ApiError("Исключать можно только занятия выбранного месяца и группы")
    if dates and not set(available) - set(dates):
        raise ApiError("В абонементе должно остаться хотя бы одно занятие")
    return dates


def excluded(con, sid, gid, day):
    rows = con.execute("""SELECT x.excluded_dates,x.lesson_schedule,x.historical_sale,x.composition_corrected FROM subscriptions x JOIN enrollments e ON e.id=x.enrollment_id
        JOIN groups g ON g.id=e.group_id JOIN groups target ON target.id=?
        WHERE x.student_id=? AND x.month=? AND x.status!='cancelled'
          AND (e.group_id=? OR (g.program_id IS NOT NULL AND g.program_id=target.program_id))""",
        (gid, sid, day[:7], gid))
    excluded_flags=[]
    for row in rows:
        if (row['historical_sale'] or row['composition_corrected']) and row['lesson_schedule']:
            components=json.loads(row['lesson_schedule'])
            excluded_flags.append(not any(c.get('group_id')==gid and day in c.get('dates',[]) for c in components))
        else:
            excluded_flags.append(day in json.loads(row['excluded_dates']))
    return bool(excluded_flags) and all(excluded_flags)


def outside_period(con, sid, gid, day):
    """Учитываем точный срок, если для этой группы он перенесён из ERP."""
    periods = con.execute("""SELECT x.valid_from, x.valid_until
        FROM subscriptions x JOIN enrollments e ON e.id=x.enrollment_id
        WHERE x.student_id=? AND e.group_id=? AND x.kind='month' AND x.status!='cancelled'
          AND (x.month=? OR (x.valid_from<=? AND x.valid_until>=?))""",
        (sid, gid, day[:7], day, day)).fetchall()
    return bool(periods) and all(
        (start and day < start) or (end and day > end)
        for start, end in periods
    )


def correct_period(con, sub_id, data, user):
    from . import services as sv
    row = con.execute("SELECT * FROM subscriptions WHERE id=? AND kind='month'", (sub_id,)).fetchone()
    if not row:
        raise sv.ApiError("Абонемент не найден", 404)
    start, end = data.get("valid_from"), data.get("valid_until")
    try:
        if date.fromisoformat(start).isoformat() != start or (end and date.fromisoformat(end).isoformat() != end):
            raise ValueError()
    except (ValueError, TypeError):
        raise sv.ApiError("Укажите даты действия абонемента")
    if start[:7] != row["month"] or (end and end < start):
        raise sv.ApiError("Дата начала должна быть в месяце абонемента, окончание не раньше начала")
    end = end or None
    previous = row["valid_from"], row["valid_until"]
    if previous == (start, end):
        return
    if previous != (data.get("expected_from"), data.get("expected_until")):
        raise sv.ApiError("Срок уже изменился. Обновите карточку", 409)
    con.execute("UPDATE subscriptions SET valid_from=?,valid_until=? WHERE id=?", (start, end, sub_id))
    sv.log(con, row["student_id"], f"Срок абонемента #{sub_id}: {previous[0] or 'не указан'} / {previous[1] or 'не указан'} -> {start} / {end or 'не указано'}", user, "system")
    sv.audit(con, user, "subscription.period", str(sub_id))


def guard_duplicate(con, sid, gid, ym, chosen=None):
    """Не допускаем повторную продажу через другую запись той же группы."""
    from .services import ApiError
    rows=con.execute("""SELECT x.id,x.month,x.lesson_schedule FROM subscriptions x
        LEFT JOIN enrollments e ON e.id=x.enrollment_id JOIN students s ON s.id=x.student_id
        WHERE x.student_id=? AND x.status!='cancelled' AND COALESCE(x.kind,'month')='month'
        AND COALESCE(e.group_id,s.group_id)=?""",(sid,gid))
    for row in rows:
        saved=json.loads(row['lesson_schedule'] or '[]')
        overlap=bool(chosen and isinstance(saved,list) and any((c['group_id'],day) in chosen for c in saved for day in c.get('dates',[])))
        if row['month']==ym or overlap:
            raise ApiError(f"На этот период уже есть абонемент №{row['id']}. Откройте существующий абонемент — повторно вносить его не нужно")


def calendar_entitlements(con, sid, ym):
    """Read the sale mode without turning a single purchase into recurring dates.

    Only retained ERP rows establish a legacy aggregate's original kind. A
    price, quantity, sale timestamp or group timetable cannot establish dates.
    Past single-only groups stay single-only until a real monthly sale exists.
    """
    from . import erpsingles, erpimport as ei
    evidence=erpsingles.subscription_evidence(con,sid)
    history=[dict(r) for r in con.execute("""SELECT x.*,e.group_id FROM subscriptions x
        LEFT JOIN enrollments e ON e.id=x.enrollment_id
        WHERE x.student_id=? AND x.month<=? AND x.status!='cancelled'""",(sid,ym))]
    rows=[r for r in history if r['month']==ym]
    history_single_ids={r['id'] for r in history if r['kind']=='single' or evidence.get(r['id'],{}).get('kind')=='single'}
    single_ids={r['id'] for r in rows if r['id'] in history_single_ids}
    monthly_history=[r for r in history if r['kind']=='month' and r['id'] not in history_single_ids]
    monthly=[r for r in monthly_history if r['month']==ym]
    recorded_visits=[dict(r) for r in con.execute('SELECT * FROM single_visits WHERE student_id=?',(sid,))]
    # The recurring-mode history is bounded by the displayed month. A sale's
    # exact linked booking, however, can legitimately move into another month.
    all_visits=[v for v in recorded_visits if v['date'][:7]<=ym]
    visits=[v for v in all_visits if v['date'][:7]==ym]

    def monthly_groups(subs):
        groups={r['group_id'] for r in subs if r['group_id']}
        if any(not r['enrollment_id'] for r in subs):
            student=con.execute('SELECT group_id FROM students WHERE id=?',(sid,)).fetchone()
            if student and student[0]:groups.add(student[0])
        for r in subs:
            if not r['lesson_schedule']:continue
            try:parts=json.loads(r['lesson_schedule'])
            except (TypeError,ValueError):continue
            if isinstance(parts,list):groups.update(c['group_id'] for c in parts if isinstance(c,dict) and type(c.get('group_id')) is int)
        programmes={r[0] for gid in tuple(groups) for r in con.execute('SELECT program_id FROM groups WHERE id=? AND program_id IS NOT NULL',(gid,))}
        for programme in programmes:
            groups.update(r[0] for r in con.execute('SELECT id FROM groups WHERE program_id=?',(programme,)))
        return groups

    current_monthly_groups=monthly_groups(monthly)
    historical_monthly_groups=monthly_groups(monthly_history)
    single_groups={r['group_id'] for r in history if r['id'] in history_single_ids and r['group_id']}
    single_groups.update(v['group_id'] for v in all_visits if v['status']=='booked')
    source_sales={}
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='erp_single_sales'").fetchone():
        source_sales={r['source_id']:dict(r) for r in con.execute('SELECT * FROM erp_single_sales WHERE student_id=?',(sid,))}
        single_groups.update(r['group_id'] for r in source_sales.values() if r['subscription_id'] in history_single_ids)

    # Owner-reviewed recovery replaces a false monthly aggregate with singles.
    # Preserve that explicit mode despite genuine earlier monthly purchases.
    # The marker is available inside preview, before a recovery run is saved.
    recovered_single_groups=set()
    native_singles={r['id']:r for r in history if r['kind']=='single'}
    for sale in source_sales.values():
        native=native_singles.get(sale['subscription_id'])
        if not native:continue
        try:payload=json.loads(sale.get('source_payload') or '{}')
        except (TypeError,ValueError):continue
        aggregate_id=payload.get('recovery_aggregate_id') if isinstance(payload,dict) else None
        if type(aggregate_id) is not int or aggregate_id<=0:continue
        aggregate=con.execute("""SELECT x.student_id,x.month,x.kind,x.status,e.group_id
            FROM subscriptions x LEFT JOIN enrollments e ON e.id=x.enrollment_id AND e.student_id=x.student_id
            WHERE x.id=?""",(aggregate_id,)).fetchone()
        if (not aggregate or aggregate['student_id']!=sid or aggregate['kind']!='month'
                or aggregate['status']!='cancelled' or aggregate['month']!=native['month']
                or aggregate['month']>ym):continue
        recovered_single_groups.add(sale['group_id'])
        if aggregate['group_id']:recovered_single_groups.add(aggregate['group_id'])

    reviews=[];review_identities=set();review_history=False
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='erp_single_reviews'").fetchone():
        for review in con.execute('SELECT id,group_id,source_id,payload FROM erp_single_reviews WHERE student_id=?',(sid,)):
            try:payload=json.loads(review['payload'])
            except (TypeError,ValueError):continue
            if not isinstance(payload,dict) or ei.sub_kind(payload.get('tariff',''))!='single':continue
            source_identity=str(review['source_id'] or payload.get('sale_id') or '').strip()
            # Native singles already supply their own date warning. A retained
            # review is source history, not an additional purchase.
            if source_identity and source_identity in source_sales:continue
            identity=source_identity or 'review:'+str(review['id'])
            if identity in review_identities:continue
            source_key=str(payload.get('_source_key') or '')
            if source_key.startswith(('payments:','visits:','tasks:','contacts:')) or payload.get('_record_kind') not in (None,'','subs') or payload.get('payment_type'):continue
            if 'amount' in payload and 'price' not in payload:continue
            month=ei.parse_month(payload.get('month',''))
            if not month and payload.get('lesson_date'):
                exact=ei.parse_date(payload['lesson_date']);month=exact[:7] if exact else None
            if not month or month>ym:continue
            quantity=ei.sub_lessons(payload)
            if quantity<=0:continue
            review_identities.add(identity);review_history=True
            if review['group_id'] and con.execute('SELECT 1 FROM groups WHERE id=?',(review['group_id'],)).fetchone():
                single_groups.add(review['group_id'])
            if month==ym:reviews.append({'id':review['id'],'lessons':quantity})

    # A normal monthly client's occasional single purchase does not erase the
    # regular plan. Current monthly coverage also overrides a past single mode.
    single_groups-=historical_monthly_groups
    single_groups.update(recovered_single_groups)
    single_only=bool(history_single_ids or any(v['status']=='booked' for v in all_visits) or review_history) and not monthly_history
    unknown=0;unknown_ids=[]
    for r in rows:
        source=evidence.get(r['id'],{})
        if r['id'] in single_ids:count=max(0,int(r['lessons']))
        elif source.get('kind')=='mixed':count=max(0,int(source.get('single_lessons') or 0))
        else:continue
        # Only a booking explicitly linked to this sale establishes its date.
        # An attendance mark or payment timestamp cannot establish that link.
        known={v['date'] for v in recorded_visits if v['subscription_id']==r['id']}
        missing=max(0,count-len(known))
        if missing:unknown+=missing;unknown_ids.append(r['id'])
    warnings=[]
    if unknown:
        warnings.append({'kind':'single_dates_unknown','lessons':unknown,'subscription_ids':unknown_ids,
            'message':f'Разовые занятия без записанных дат: {unknown}. В календаре показаны только подтверждённые даты; расписание группы не подставляется вместо них.'})
    if reviews:
        review_lessons=sum(r['lessons'] for r in reviews)
        warnings.append({'kind':'single_source_review','lessons':review_lessons,'review_ids':[r['id'] for r in reviews],
            'message':f'Разовые занятия из ERP требуют сверки: {review_lessons}. Нет подтверждённых данных для записей на даты; расписание группы не заменяет их.'})
    if single_only and not visits and not warnings:
        warnings.append({'kind':'single_dates_not_booked','lessons':0,'message':'Разовые занятия: на этот месяц отдельные даты пока не записаны.'})
    return {'single_ids':single_ids,'monthly_groups':current_monthly_groups,'monthly_ids':{r['id'] for r in monthly},
        'single_groups':single_groups,'single_only':single_only,'date_warnings':warnings}


def recurring_calendar_allowed(gid, entitlements):
    """Single purchases never grant every date of an enrollment's schedule."""
    if gid in entitlements['monthly_groups']:return True
    return not entitlements['single_only'] and gid not in entitlements['single_groups']
