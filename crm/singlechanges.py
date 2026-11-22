"""Cancellation to client balance and rescheduling of an existing single visit."""
from datetime import date, datetime, time, timedelta
from . import balancecredit, capacity, closures, logic, money, paymentallocation, schedule, services as sv, singles, tasks


def _visit(con, vid):
    row = con.execute("SELECT * FROM single_visits WHERE id=?", (vid,)).fetchone()
    if not row:
        raise sv.ApiError("Запись не найдена", 404)
    return row


def _check_original(con, v, expected):
    if expected and (v['date'], v['group_id']) != expected:
        raise sv.ApiError("Запись уже перенесли. Обновите карточку", 409)
    if v['status'] != 'booked':
        raise sv.ApiError("Эта запись уже отменена", 409)
    if v['date'] < sv.today().isoformat() or con.execute(
        'SELECT 1 FROM attendance WHERE student_id=? AND date=? AND group_id=?',
        (v['student_id'], v['date'], v['group_id'])).fetchone():
        raise sv.ApiError("Можно изменить предстоящую запись без отметки посещения")
    if v['date'] == sv.today().isoformat():
        clock = singles.lesson_time(con,v['group_id'],v['date'])
        if clock and datetime.fromisoformat(f"{v['date']}T{clock}") <= singles._now():
            raise sv.ApiError("Это занятие уже началось. Сверьте посещение в журнале")
    if v['subscription_id']:
        sub = con.execute("SELECT * FROM subscriptions WHERE id=?", (v['subscription_id'],)).fetchone()
        if (not sub or sub['student_id'] != v['student_id'] or sub['kind'] != 'single' or sub['status'] == 'cancelled'
                or con.execute("SELECT COUNT(*) FROM single_visits WHERE subscription_id=? AND status='booked'", (sub['id'],)).fetchone()[0] != 1):
            raise sv.ApiError("Связь записи с разовой покупкой требует сверки")
        return sub


def _release(con, sub, user):
    sid, subid = sub['student_id'], sub['id']
    before = paymentallocation.allocation(con, sid)
    paid = before['covered'].get(subid, 0)
    if before['conflicts'] or (sub['status'] == 'paid' and paid != sub['price']):
        raise sv.ApiError("Сумма исходной оплаты требует сверки")
    # Keep cash used on other purchases in place, even in older FIFO accounts.
    # An explicit balance use already preserves its source. Do not bind it twice.
    meta = balancecredit.context(con, sid)
    fixed = {(r['payment_id'], r['subscription_id']) for r in meta['direct']}
    consumed = {}
    for use in meta['uses']:
        for part in use['source_parts']:
            if 'payment_id' in part:
                key = (part['payment_id'], use['subscription_id'])
                consumed[key] = consumed.get(key, 0) + part['amount']
    affected = {r['payment_id'] for r in before['sources'].get(subid, [])} - set(before['payment_locks'])
    amounts = {}
    for target, parts in before['sources'].items():
        for part in parts:
            pid = part['payment_id']
            if pid in affected and (pid, target) not in fixed and target > 0:
                amounts[pid,target] = amounts.get((pid,target), 0) + part['amount']
    for (pid,target), amount in amounts.items():
        if target != subid:
            amount -= consumed.get((pid,target), 0)
        if amount > 0:
            balancecredit.target_payment(con, pid, target, amount)
    con.execute("UPDATE subscriptions SET status='cancelled' WHERE id=?", (subid,))
    for part in before['sources'].get(subid, []):
        pid = part['payment_id']
        if pid not in before['payment_locks']:
            continue
        ref = f"cancel-single:{subid}:payment:{pid}"
        fund = con.execute("""INSERT INTO balance_credit_funds(student_id,amount,source_kind,source_ref,label,evidence_id,created_at,created_by)
            VALUES (?,?,'single_cancel',?,'Отмена разового занятия',?,?,?)""",
            (sid,part['amount'],ref,ref,sv.now_iso(),user.get('name',''))).lastrowid
        con.execute("INSERT INTO single_cancelled_payment_credits VALUES (?,?,?)",(fund,pid,subid))
    after = paymentallocation.allocation(con, sid)
    if (after['conflicts'] or after['free_deposit'] != before['free_deposit'] + paid or
            any(after['covered'].get(k) != val for k,val in before['covered'].items() if k != subid)):
        raise sv.ApiError("Не удалось вернуть сумму на свободный баланс с сохранением остальных оплат. Нужна сверка")
    con.execute("""INSERT INTO subscription_cancellations(subscription_id,student_id,mode,amount,reason,approval,created_at,
        created_by,effective_date,paid_before,balance_available,future_amount,period_verified)
        VALUES (?,?,'balance',?,'Отмена разового занятия','',?,?,?,?,?,?,1)""",
        (subid,sid,paid,sv.now_iso(),user.get('name',''),sv.today().isoformat(),paid,paid,sub['price']))
    money.reconcile(con, sid)
    return paid


def _continuation_exists(con, sid, vid):
    return bool(con.execute("""SELECT 1 FROM single_visits WHERE student_id=? AND id!=? AND status='booked' AND date>=?
            UNION ALL SELECT 1 FROM subscriptions WHERE student_id=? AND kind='month' AND status='paid' AND month>=? LIMIT 1""",
            (sid,vid,sv.today().isoformat(),sid,sv.today().isoformat()[:7])).fetchone())


def cancel_options(con, vid):
    v = _visit(con, vid)
    if _continuation_exists(con,v['student_id'],vid):
        return {'continuation':True,'due':None}
    group = con.execute("SELECT * FROM groups WHERE id=? AND active=1",(v['group_id'],)).fetchone()
    day = date.fromisoformat(v['date'])
    if group:
        for week in range(1,54):
            candidate = day + timedelta(weeks=week)
            if (candidate.weekday() in logic.parse_weekdays(schedule.on(con,group,candidate)['weekdays'])
                    and not closures.is_closed(con,group['id'],candidate)):
                return {'continuation':False,'due':(candidate-timedelta(days=1)).isoformat(),'lesson':candidate.isoformat()}
    return {'continuation':False,'due':None}


def _follow_up(con, v, user, mode, contact_date):
    options = cancel_options(con,v['id'])
    if options['continuation']:
        return
    sid = v['student_id']
    if mode=='lead':
        due = singles._now()
    elif mode in ('auto','date'):
        day = _day(contact_date if mode=='date' else options['due'])
        if day < sv.today():
            raise sv.ApiError("Выберите сегодняшний или будущий день для связи")
        due = max(singles._now(),datetime.combine(day,time(10)))
    else:
        raise sv.ApiError("Выберите следующий шаг после отмены")
    con.execute("""UPDATE tasks SET status='cancelled',done_at=?,result='Выбран новый срок связи'
        WHERE student_id=? AND status='open' AND (kind='single_next' OR auto_key LIKE 'single-recontact:%')""",
        (sv.now_iso(),sid))
    if mode=='lead':
        if not con.execute("SELECT 1 FROM tasks WHERE student_id=? AND status='open' AND kind='call_lead'",(sid,)).fetchone():
            tasks.add_task(con,'call_lead',sid,due,title='Новый лид',source='erp_rule',auto_key=f"single-recontact:{v['id']}",
                           note="Повторный контакт после отмены разового. Уточнить планы семьи, предложить подходящее занятие или абонемент")
        sv.log(con,sid,"После отмены разового выбрана цепочка повторной продажи по задаче «Новый лид»",user,"stage")
    else:
        tasks.add_task(con,'single_next',sid,due,title='Уточнить дату следующего разового',source='single',
                       auto_key=f"single-recontact:{v['id']}",note="Семья отменила разовое. Уточнить планы и предложить запись")
        sv.log(con,sid,f"После отмены разового связаться с семьёй {due:%d.%m.%Y в %H:%M}",user,"stage")


def cancel(con, vid, user, expected=None, mode="auto", contact_date=None):
    con.execute('SAVEPOINT cancel_single_to_balance')
    try:
        v = _visit(con, vid)
        if v['status'] == 'cancelled':
            con.execute('RELEASE cancel_single_to_balance')
            return {'ok': True, 'already_cancelled': True}
        sub = _check_original(con, v, expected)
        if not sub and con.execute("""SELECT 1 FROM subscriptions s WHERE s.student_id=? AND s.kind='single'
                AND s.status='paid' AND s.month=? AND NOT EXISTS
                (SELECT 1 FROM single_visits v WHERE v.subscription_id=s.id)""",
                (v['student_id'],v['date'][:7])).fetchone():
            raise sv.ApiError("У старой записи есть покупка без привязки. Сначала сверьте оплату")
        con.execute("UPDATE single_visits SET status='cancelled' WHERE id=?", (vid,))
        paid = _release(con, sub, user or {}) if sub else 0
        con.execute("UPDATE tasks SET status='cancelled',done_at=?,result='Разовое отменено' WHERE status='open' AND auto_key LIKE ?",
                    (sv.now_iso(), f"single:{vid}:%"))
        singles.sync_paid_tasks(con, singles._now(), v['student_id'])
        _follow_up(con,v,user,mode,contact_date)
        sv.log(con,v['student_id'],f"Отменено разовое {sv.fmt_date(v['date'])}. Возвращено на баланс: {paid} ₽",user,"stage")
        con.execute('RELEASE cancel_single_to_balance')
        return {'ok': True, 'balance_credit': paid}
    except BaseException:
        con.execute('ROLLBACK TO cancel_single_to_balance')
        con.execute('RELEASE cancel_single_to_balance')
        raise


def _day(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise sv.ApiError("Выберите дату занятия")


def slots(con, vid, day):
    v = _visit(con, vid)
    d = _day(day)
    now = singles._now()
    options = []
    if d < sv.today():
        return options
    for g in con.execute("""SELECT g.*,b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id
                            WHERE g.active=1 AND g.is_makeup=0 AND g.program_id IS NULL ORDER BY b.name,g.name"""):
        effective = schedule.on(con, g, d)
        clock = logic.group_time(effective, d.weekday())
        if (d.weekday() not in logic.parse_weekdays(effective['weekdays']) or not clock
                or datetime.fromisoformat(f"{day}T{clock}") <= now or closures.is_closed(con,g['id'],d)
                or (day,g['id']) == (v['date'],v['group_id'])):
            continue
        occ = capacity.occupancy(con, g['id'], day)
        if occ['has_lesson'] and occ['free'] != 0:
            options.append({'group_id':g['id'],'group':effective['name'],'branch':g['branch'],'time':clock})
    return sorted(options,key=lambda o:(o['time'],o['branch'],o['group']))


def move(con, vid, user, expected, group_id, day):
    con.execute('SAVEPOINT move_single')
    try:
        v = _visit(con, vid)
        if v['status']=='booked' and (day,group_id)==(v['date'],v['group_id']):
            con.execute('RELEASE move_single')
            return {'ok':True,'already_moved':True}
        _check_original(con,v,expected)
        dest = next((o for o in slots(con,vid,day) if o['group_id']==group_id), None)
        if not dest:
            raise sv.ApiError("Нет свободного занятия на выбранное время")
        sid = v['student_id']
        if con.execute("SELECT 1 FROM single_visits WHERE student_id=? AND date=? AND id!=? AND status='booked'",
                       (sid,day,vid)).fetchone() or con.execute("SELECT 1 FROM attendance WHERE student_id=? AND group_id=? AND date=?",
                       (sid,group_id,day)).fetchone():
            raise sv.ApiError("На новую дату уже есть разовая запись или отметка посещения")
        con.execute("UPDATE single_visits SET date=?,group_id=? WHERE id=?", (day,group_id,vid))
        capacity.ensure(con,group_id,day,"перенести занятие")
        con.execute("""UPDATE tasks SET auto_key='moved_single:'||id,source='single_history',
            done_at=CASE WHEN status='open' THEN ? ELSE done_at END,
            result=CASE WHEN status='open' THEN 'Разовое перенесено' ELSE result END,
            status=CASE WHEN status='open' THEN 'cancelled' ELSE status END
            WHERE auto_key LIKE ? AND source IN ('single','single_paid')""",(sv.now_iso(),f"single:{vid}:%"))
        new = _visit(con,vid)
        tasks.add_task(con,'single_remind',sid,singles._remind_due(day),auto_key=f"single:{vid}:{day}",
                       title=singles.unpaid_title(day),source='single',note=singles.message(con,new))
        singles.sync_paid_tasks(con,singles._now(),sid)
        old_group=con.execute("SELECT name FROM groups WHERE id=?", (v['group_id'],)).fetchone()[0]
        sv.log(con,sid,f"Разовое перенесено с {sv.fmt_date(v['date'])} ({old_group}) на {sv.fmt_date(day)} в {dest['time']} ({dest['group']})",user,'stage')
        con.execute('RELEASE move_single')
        return {'ok':True}
    except BaseException:
        con.execute('ROLLBACK TO move_single')
        con.execute('RELEASE move_single')
        raise


def register(route):
    @route("POST", "/api/singles/{vid}/cancel-with-followup", role="operator|client_ops:full")
    def singles_cancel(con, h, user, body, q, vid):
        expected = (body.get("expected_date"), body.get("expected_group_id")) if "expected_date" in body else None
        return cancel(con, int(vid), user, expected, body.get("follow_up","auto"),body.get("contact_date"))


    @route("GET", "/api/singles/{vid}/cancel-options", role="operator|client_ops:full")
    def single_cancel_options(con, h, user, body, q, vid):
        return cancel_options(con,int(vid))


    @route("GET", "/api/singles/{vid}/slots", role="operator|client_ops:full")
    def single_move_slots(con, h, user, body, q, vid):
        return {"slots": slots(con, int(vid), q.get("date"))}


    @route("POST", "/api/singles/{vid}/move", role="operator|client_ops:full")
    def single_move(con, h, user, body, q, vid):
        return move(con, int(vid), user, (body.get("expected_date"),body.get("expected_group_id")),
                                  body.get("group_id"),body.get("date"))
