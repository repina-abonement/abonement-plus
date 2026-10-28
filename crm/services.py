"""Операции CRM поверх базы: воронки, карточка, абонементы, журнал, отработки."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import logic
from .db import normalize_phone, now_iso, settings


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


SALES_STAGES = [
    ("lead", "Новый лид"),
    ("no_answer", "Недозвон, отправили СМС"),
    ("trial_booked", "Записан на пробное"),
    ("trial_reminder", "Напоминание о пробном"),
    ("on_trial", "Сейчас на пробном"),
    ("trial_done", "Пробное закончилось"),
    ("missed_trial", "Не дошёл до ПУ"),
    ("how_was", "Как прошло пробное?"),
    ("bought", "Купил абонемент"),
    ("refused", "Отказ"),
    ("later", "Пригласим позже"),
]
AUTO_SALES = {"on_trial", "trial_done", "missed_trial", "later"}
SALES_LABELS = dict(SALES_STAGES)


def today() -> date:
    return date.today()


def now() -> datetime:
    return datetime.now().replace(second=0, microsecond=0)


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


def log(con, student_id: int, text: str, user: dict | None, kind: str = "note"):
    con.execute("INSERT INTO history(student_id, ts, user_name, kind, text) VALUES (?,?,?,?,?)",
                (student_id, now_iso(), (user or {}).get("name", "Система"), kind, text))


def audit(con, user: dict | None, action: str, detail: str = ""):
    con.execute("INSERT INTO audit(ts, user_id, action, detail) VALUES (?,?,?,?)",
                (now_iso(), (user or {}).get("id"), action, detail))


def get_student_row(con, sid: int) -> dict:
    r = con.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()
    if not r:
        raise ApiError("Ученик не найден", 404)
    return dict(r)


def vacations_of(con, sid: int) -> list[tuple[date, date]]:
    return [(date.fromisoformat(r["start"]), date.fromisoformat(r["end"]))
            for r in con.execute("SELECT start, end FROM vacations WHERE student_id = ?", (sid,))]


# ---------- пропуски и отработки ----------

def absence_counters(con, sid: int, ref: date | None = None) -> dict:
    ref = ref or today()
    cur = logic.month_of(ref)
    prev = logic.shift_month(cur, -1)
    w_start, w_end = logic.makeup_window(ref)
    rows = _rows(con.execute(
        """SELECT a.id, a.date, a.resolved,
                  (SELECT m.id FROM makeups m WHERE m.absence_id = a.id AND m.status = 'booked') AS booked_id
           FROM attendance a WHERE a.student_id = ? AND a.status = 'absent' AND a.date BETWEEN ? AND ?
           ORDER BY a.date""", (sid, w_start.isoformat(), w_end.isoformat())))
    c = {"absent_cur": 0, "absent_prev": 0, "made_up": 0, "credited": 0, "booked": 0, "available": 0,
         "available_ids": [], "month_cur": cur, "month_prev": prev}
    for r in rows:
        c["absent_cur" if r["date"].startswith(cur) else "absent_prev"] += 1
        if r["resolved"] == "makeup":
            c["made_up"] += 1
        elif r["resolved"] == "credit":
            c["credited"] += 1
        elif r["booked_id"]:
            c["booked"] += 1
        else:
            c["available"] += 1
            c["available_ids"].append({"id": r["id"], "date": r["date"]})
    return c


def unresolved_absences_in_month(con, sid: int, ym: str, group_id: int | None = None) -> list[dict]:
    start, end = logic.month_bounds(ym)
    extra, args = ("AND a.group_id = ?", (group_id,)) if group_id else ("", ())
    return _rows(con.execute(
        f"""SELECT a.id, a.date FROM attendance a
           WHERE a.student_id = ? AND a.status = 'absent' AND a.resolved IS NULL
             AND a.date BETWEEN ? AND ? {extra}
             AND NOT EXISTS (SELECT 1 FROM makeups m WHERE m.absence_id = a.id AND m.status = 'booked')
           ORDER BY a.date""", (sid, start.isoformat(), end.isoformat(), *args)))


# ---------- группы ученика ----------

def enrollments_of(con, sid: int, active_only: bool = True) -> list[dict]:
    q = """SELECT e.*, g.name AS group_name, g.weekdays AS group_weekdays, g.time AS group_time, g.age AS group_age,
                  g.program_id, g.lesson_kind
           FROM enrollments e JOIN groups g ON g.id = e.group_id WHERE e.student_id = ?"""
    if active_only:
        q += " AND e.active = 1"
    return _rows(con.execute(q + " ORDER BY e.id", (sid,)))


def enrollment_days(e: dict) -> list[int]:
    group_days = logic.parse_weekdays(e["group_weekdays"])
    own = logic.parse_weekdays(e["weekdays"] or "")
    return [d for d in group_days if d in own] if own else group_days


def sync_primary(con, sid: int):
    """students.group_id/tariff = первая активная группа (для поиска, карточек, тренера)."""
    e = con.execute("SELECT group_id, tariff FROM enrollments WHERE student_id=? AND active=1 ORDER BY id LIMIT 1",
                    (sid,)).fetchone()
    if e:
        con.execute("UPDATE students SET group_id=?, tariff=? WHERE id=?", (e[0], e[1], sid))


def add_enrollment(con, sid: int, group_id: int, weekdays: list[int] | None, tariff: str, user) -> int:
    get_student_row(con, sid)
    g = con.execute("SELECT * FROM groups WHERE id=? AND is_makeup=0", (group_id,)).fetchone()
    if not g:
        raise ApiError("Группа не найдена")
    if con.execute("SELECT 1 FROM enrollments WHERE student_id=? AND group_id=? AND active=1", (sid, group_id)).fetchone():
        raise ApiError("Ученик уже ходит в эту группу")
    days = _clean_days(weekdays, g["weekdays"])
    eid = con.execute("INSERT INTO enrollments(student_id, group_id, weekdays, tariff, created_at) VALUES (?,?,?,?,?)",
                      (sid, group_id, days, "flex" if tariff == "flex" else "fixed", now_iso())).lastrowid
    sync_primary(con, sid)
    from . import capacity
    capacity.ensure_roster(con, group_id)
    log(con, sid, f"Добавлена группа {g['name']}" + (f" ({_days_label(days)})" if days else ""), user, "system")
    return eid


def _clean_days(weekdays, group_weekdays: str) -> str:
    if not weekdays:
        return ""
    allowed = logic.parse_weekdays(group_weekdays)
    days = sorted({int(d) for d in weekdays if int(d) in allowed})
    if not days:
        raise ApiError("Выберите хотя бы один день группы")
    return "" if days == sorted(allowed) else ",".join(map(str, days))


def _days_label(days: str) -> str:
    return ", ".join(logic.WEEKDAY_NAMES[d] for d in logic.parse_weekdays(days))


def update_enrollment(con, eid: int, data: dict, user) -> None:
    e = con.execute("SELECT e.*, g.name, g.weekdays AS gw FROM enrollments e JOIN groups g ON g.id=e.group_id WHERE e.id=?",
                    (eid,)).fetchone()
    if not e:
        raise ApiError("Группа ученика не найдена", 404)
    if "weekdays" in data:
        days = _clean_days(data["weekdays"], e["gw"])
        con.execute("UPDATE enrollments SET weekdays=? WHERE id=?", (days, eid))
        log(con, e["student_id"], f"{e['name']}: дни — {_days_label(days) if days else 'все дни группы'}", user, "system")
    if "tariff" in data:
        if data["tariff"] not in ("fixed", "flex"):
            raise ApiError("Неизвестный тариф")
        con.execute("UPDATE enrollments SET tariff=? WHERE id=?", (data["tariff"], eid))
        log(con, e["student_id"], f"{e['name']}: тариф {'гибкий' if data['tariff'] == 'flex' else 'негибкий'}", user, "system")
    if "active" in data and not data["active"]:
        con.execute("UPDATE enrollments SET active=0 WHERE id=?", (eid,))
        log(con, e["student_id"], f"Больше не ходит в группу {e['name']}", user, "system")
    sync_primary(con, e["student_id"])


def book_makeup(con, sid: int, group_id: int, day: str, absence_id: int | None, user) -> dict:
    g = con.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
    if not g:
        raise ApiError("Группа не найдена")
    d = date.fromisoformat(day)
    if d < today():
        raise ApiError("Дата отработки уже прошла")
    if d.weekday() not in logic.parse_weekdays(g["weekdays"]):
        raise ApiError("В этот день у группы нет занятия")
    if not g["is_makeup"] and con.execute("""SELECT 1 FROM enrollments WHERE student_id=? AND group_id=? AND active=1""",
                                          (sid, group_id)).fetchone():
        raise ApiError("Ребёнок и так ходит в эту группу")
    from . import program
    program.check_makeup(con, sid, group_id, day)
    counters = absence_counters(con, sid)
    ids = [a["id"] for a in counters["available_ids"]]
    if not ids:
        raise ApiError("Нет пропусков, которые можно отработать")
    if absence_id is None:
        absence_id = ids[0]
    elif absence_id not in ids:
        raise ApiError("Этот пропуск нельзя отработать")
    dup = con.execute("SELECT 1 FROM makeups WHERE student_id=? AND date=? AND group_id=? AND status='booked'",
                      (sid, day, group_id)).fetchone()
    if dup:
        raise ApiError("Ученик уже записан на эту отработку")
    mid = con.execute("INSERT INTO makeups(student_id, absence_id, group_id, date) VALUES (?,?,?,?)",
                      (sid, absence_id, group_id, day)).lastrowid
    if not g["is_makeup"]:
        from . import capacity
        capacity.ensure(con, group_id, day, "записать на отработку")
    absence_date = con.execute("SELECT date FROM attendance WHERE id=?", (absence_id,)).fetchone()[0]
    con.execute("UPDATE students SET skip_stage='none' WHERE id=?", (sid,))
    log(con, sid, f"Записан на отработку {fmt_date(day)} ({g['name']}) за пропуск {fmt_date(absence_date)}", user, "makeup")
    return {"id": mid}


def set_makeup_status(con, mid: int, status: str, user) -> None:
    if status not in ("done", "missed", "cancelled", "booked"):
        raise ApiError("Неизвестный статус")
    m = con.execute("SELECT * FROM makeups WHERE id = ?", (mid,)).fetchone()
    if not m:
        raise ApiError("Отработка не найдена", 404)
    con.execute("UPDATE makeups SET status = ? WHERE id = ?", (status, mid))
    from . import pu
    pu.record_lesson_mark(con, m["group_id"], m["date"])
    if status == "done":
        con.execute("UPDATE attendance SET resolved = 'makeup' WHERE id = ?", (m["absence_id"],))
    elif m["status"] == "done":
        con.execute("UPDATE attendance SET resolved = NULL WHERE id = ? AND resolved = 'makeup'", (m["absence_id"],))
    labels = {"done": "Отработал", "missed": "Не пришёл на отработку", "cancelled": "Отработка отменена",
              "booked": "Отработка снова в записи"}
    log(con, m["student_id"], f"{labels[status]}: {fmt_date(m['date'])}", user, "makeup")


# ---------- абонементы ----------

def _pick_enrollment(con, sid: int, enrollment_id) -> dict:
    ens = enrollments_of(con, sid)
    if enrollment_id:
        ens = [e for e in ens if e["id"] == int(enrollment_id)]
    if not ens:
        raise ApiError("У ученика не выбрана группа")
    return ens[0]


def quote_for(con, sid: int, ym: str, enrollment_id=None, discount_id=None) -> dict:
    get_student_row(con, sid)
    e = _pick_enrollment(con, sid, enrollment_id)
    st = settings(con)
    prices = {"fixed": st["price_fixed"], "flex": st["price_flex"]}
    prev_abs = unresolved_absences_in_month(con, sid, logic.shift_month(ym, -1), e["group_id"])
    from . import closures, program
    if program.program_of_group(con, e["group_id"]):
        return program.quote(con, sid, ym, e, discount_id=discount_id)
    ms, me = logic.month_bounds(ym)
    q = logic.quote(weekdays=enrollment_days(e), ym=ym, tariff=e["tariff"], prices=prices,
                    vacations=vacations_of(con, sid), prev_unresolved_absences=len(prev_abs),
                    closed=closures.closed_dates(con, e["group_id"], ms, me))
    q["credit_absence_ids"] = [a["id"] for a in prev_abs[:q["credit_lessons"]]]
    q["credit_absence_dates"] = [a["date"] for a in prev_abs[:q["credit_lessons"]]]
    total, fee = logic.apply_late_fee(q["total"], enabled=st["late_fee_enabled"] == "1",
                                      percent=float(st["late_fee_percent"]), today=today(), target_month=ym,
                                      deadline_day=int(st["renewal_deadline_day"]))
    q["late_fee"] = fee
    from . import money
    manual = None
    if discount_id:
        r = con.execute("SELECT * FROM discounts WHERE id=? AND active=1", (int(discount_id),)).fetchone()
        manual = dict(r) if r else None
    disc = money.best_discount([manual, *money.auto_discounts(con, sid)])  # скидки не суммируются, берём большую
    total, off = money.apply_discount(total, disc)
    q["discount"] = {"id": disc["id"], "name": disc["name"], "percent": disc["percent"], "amount": off,
                     "auto": disc["rule"] != "manual"} if disc else None
    q["total"] = total
    q["waitlist"] = con.execute("SELECT COUNT(*) FROM waitlist WHERE group_id=? AND status IN ('queued','offered')",
                                (e["group_id"],)).fetchone()[0] if fee else 0
    q["tariff"] = e["tariff"]
    q["group"] = e["group_name"]
    q["enrollment_id"] = e["id"]
    q["days_label"] = _days_label(e["weekdays"]) if e["weekdays"] else ""
    return q


def create_invoice(con, sid: int, ym: str, user, enrollment_id=None, discount_id=None) -> dict:
    e = _pick_enrollment(con, sid, enrollment_id)
    existing = con.execute("SELECT 1 FROM subscriptions WHERE enrollment_id=? AND month=? AND status != 'cancelled'",
                           (e["id"], ym)).fetchone()
    if existing:
        raise ApiError(f"На {logic.month_label(ym)} в группу {e['group_name']} уже есть абонемент")
    q = quote_for(con, sid, ym, e["id"], discount_id)
    con.execute("DELETE FROM subscriptions WHERE enrollment_id=? AND month=? AND status='cancelled'", (e["id"], ym))
    d = q.get("discount") or {}
    sub_id = con.execute(
        """INSERT INTO subscriptions(student_id, enrollment_id, month, tariff, lessons, per_lesson, credit_lessons,
           late_fee, price, status, created_at, discount_name, discount_amount) VALUES (?,?,?,?,?,?,?,?,?,'invoiced',?,?,?)""",
        (sid, e["id"], ym, q["tariff"], q["lessons_paid"], q["per_lesson"], q["credit_lessons"], q["late_fee"],
         q["total"], now_iso(), d.get("name", ""), d.get("amount", 0))).lastrowid
    from . import money
    money.reconcile(con, sid)  # депозит сам закрывает новый счёт
    for aid in q["credit_absence_ids"]:
        con.execute("UPDATE attendance SET resolved='credit', resolved_month=? WHERE id=?", (ym, aid))
    extra = f", перерасчёт {q['credit_lessons']} пропуск(а) −{q['credit']} ₽" if q["credit_lessons"] else ""
    if d:
        extra += f", скидка «{d['name']}» −{d['amount']} ₽"
    log(con, sid, f"Выставлен счёт на {q['month_label']}, {e['group_name']}: {q['total']} ₽ ({q['lessons_paid']} занятий{extra})",
        user, "payment")
    return {"id": sub_id, "quote": q}


def pay_subscription(con, sub_id: int, user, method: str = "cash", amount=None) -> None:
    from . import money
    try:
        money.pay_subscription(con, sub_id, method, user, int(amount) if amount else None)
    except ValueError as e:
        raise ApiError(str(e))


def cancel_subscription(con, sub_id: int, user) -> None:
    sub = con.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
    if not sub:
        raise ApiError("Абонемент не найден", 404)
    con.execute("UPDATE subscriptions SET status='cancelled' WHERE id=?", (sub_id,))
    con.execute("""UPDATE attendance SET resolved=NULL, resolved_month=NULL
                   WHERE student_id=? AND resolved='credit' AND resolved_month=?
                     AND (? IS NULL OR group_id = (SELECT group_id FROM enrollments WHERE id = ?))""",
                (sub["student_id"], sub["month"], sub["enrollment_id"], sub["enrollment_id"]))
    from . import money
    money.reconcile(con, sub["student_id"])
    log(con, sub["student_id"], f"Счёт на {logic.month_label(sub['month'])} отменён, пропуски возвращены"
        + (" (оплата осталась на депозите)" if sub["status"] == "paid" else ""), user, "payment")


# ---------- карточки ----------

def fmt_date(iso: str) -> str:
    d = date.fromisoformat(iso[:10])
    return f"{d.day:02d}.{d.month:02d}"


def card_summary(con, s: dict, st: dict) -> dict:
    fam = con.execute("SELECT name FROM families WHERE id=?", (s["family_id"],)).fetchone()
    parent = con.execute("SELECT name, phone FROM parents WHERE family_id=? ORDER BY id LIMIT 1",
                         (s["family_id"],)).fetchone()
    ens = con.execute("""SELECT g.name FROM enrollments e JOIN groups g ON g.id=e.group_id
                         WHERE e.student_id=? AND e.active=1 ORDER BY e.id""", (s["id"],)).fetchall()
    grp = {"name": " + ".join(r[0] for r in ens)} if ens else (
        con.execute("SELECT name FROM groups WHERE id=?", (s["group_id"],)).fetchone() if s["group_id"] else None)
    sib = con.execute("SELECT COUNT(*) FROM students WHERE family_id=? AND id != ?",
                      (s["family_id"], s["id"])).fetchone()[0]
    return {
        "id": s["id"], "name": s["name"], "family": fam["name"] if fam else "", "family_id": s["family_id"],
        "parent": parent["name"] if parent else "", "phone": parent["phone"] if parent else "",
        "group": grp["name"] if grp else "", "tariff": s["tariff"], "siblings": sib,
        "stage": s["stage"], "stage_changed_at": s["stage_changed_at"], "trial_at": s["trial_at"],
        "priority": s.get("priority", 0),
    }


def effective_sales_stage(s: dict, st: dict, ref: datetime) -> tuple[str, str | None, int | None]:
    if s["stage"] in ("trial_booked", "trial_reminder") and s["trial_at"]:
        ts = logic.trial_state(datetime.fromisoformat(s["trial_at"]), bool(s["contacted_after_trial"]), ref,
                               int(st["trial_minutes"]))
        if ts["state"] in AUTO_SALES:
            return ts["state"], ts["color"], ts["minutes_since_end"]
    return s["stage"], None, None


def board(con, funnel: str) -> dict:
    st = settings(con)
    ref = now()
    if funnel == "sales":
        from .comms import missed_by_family
        missed = missed_by_family(con)
        cols = {k: {"key": k, "title": t, "cards": [], "auto": k in AUTO_SALES} for k, t in SALES_STAGES}
        for s in _rows(con.execute("SELECT * FROM students WHERE funnel='sales' AND archived=0 ORDER BY stage_changed_at DESC")):
            stage, color, mins = effective_sales_stage(s, st, ref)
            card = card_summary(con, s, st)
            card["color"] = color
            card["minutes_since_end"] = mins
            if stage == "trial_done" and mins is not None:
                card["badge"] = f"{mins} мин после пробного"
            elif s["trial_at"] and stage in ("trial_booked", "trial_reminder", "on_trial"):
                card["badge"] = "Пробное " + datetime.fromisoformat(s["trial_at"]).strftime("%d.%m %H:%M")
            if s["family_id"] in missed:
                card["color"] = "red"
                card["missed_call"] = True
                card["badge"] = "📞 Пропущенный звонок " + missed[s["family_id"]][11:16]
            if s.get("priority"):
                card["color"] = "red"
                mins = int((ref - datetime.fromisoformat(s["stage_changed_at"][:16])).total_seconds() // 60)
                card["badge"] = "Заявка с сайта · " + (f"{mins} мин назад" if mins < 120 else f"{mins // 60} ч назад")
            # дедлайн: ближайшая открытая задача по ребёнку или семье
            t = con.execute("""SELECT due_at, title FROM tasks WHERE status='open' AND (student_id=? OR (kind='rebook' AND family_id=?))
                               ORDER BY due_at LIMIT 1""", (s["id"], s["family_id"])).fetchone()
            if t:
                card["due"] = t["due_at"]
                card["due_title"] = t["title"]
                from . import pu as _pu
                card["due_color"] = _pu.overdue_color(t["due_at"], ref)
                if stage in ("missed_trial", "later") and card["due_color"]:
                    card["color"] = card["due_color"]
            if s["trial_at"] and s["trial_came"] is None and stage == "trial_done" and (mins or 0) >= 30:
                card["no_mark"] = True
                card["color"] = "red"
                card["badge"] = "Нет отметки тренера"
            cols.get(stage, cols["lead"])["cards"].append(card)
        for c in cols.values():
            c["cards"].sort(key=lambda k: (not k.get("priority"), not k.get("missed_call"), k.get("due") or "9999"))
        return {"funnel": "sales", "columns": list(cols.values()), "draggable": True, "today_end": today().isoformat() + "T23:59"}

    if funnel == "members":
        from .comms import missed_by_family
        missed = missed_by_family(con)
        cur = logic.month_of(today())
        nxt = logic.shift_month(cur, 1)
        deadline_day = int(st["renewal_deadline_day"])
        late = today().day > deadline_day
        cols = [
            {"key": "unpaid_cur", "title": f"Не оплачен {logic.month_label(cur)}", "cards": []},
            {"key": "renew", "title": f"Нужно продлить на {logic.month_label(nxt)}", "cards": []},
            {"key": "invoiced", "title": f"Счёт на {logic.month_label(nxt)} выставлен", "cards": []},
            {"key": "paid_next", "title": f"Оплачен {logic.month_label(nxt)}", "cards": []},
            {"key": "left", "title": "Ушёл", "cards": []},
        ]
        by = {c["key"]: c for c in cols}
        for s in _rows(con.execute("SELECT * FROM students WHERE funnel='members' AND archived=0 ORDER BY name")):
            card = card_summary(con, s, st)
            ens = [e["id"] for e in enrollments_of(con, s["id"])]
            st_by = {(r["enrollment_id"], r["month"]): r["status"] for r in con.execute(
                "SELECT enrollment_id, month, status FROM subscriptions WHERE student_id=? AND status != 'cancelled'",
                (s["id"],))}
            month_state = lambda m: ("paid" if ens and all(st_by.get((e, m)) == "paid" for e in ens) else
                                     "invoiced" if ens and all(st_by.get((e, m)) for e in ens) else None)
            if s["stage"] == "left":
                key = "left"
            elif month_state(cur) != "paid":
                key = "unpaid_cur"
                card["color"] = "red"
            elif month_state(nxt) == "paid":
                key = "paid_next"
            elif month_state(nxt) == "invoiced":
                key = "invoiced"
                card["color"] = "yellow" if late else None
            else:
                key = "renew"
                card["color"] = "red" if late else None
                card["badge"] = f"Продлить до {deadline_day}.{today().month:02d}"
            if s["family_id"] in missed:
                card["color"] = "red"
                card["badge"] = "📞 Пропущенный звонок " + missed[s["family_id"]][11:16]
            by[key]["cards"].append(card)
        return {"funnel": "members", "columns": cols, "draggable": False,
                "note": f"Этапы ставятся сами по оплатам. Продление — до {deadline_day} числа."}

    if funnel == "skips":
        cols = [
            {"key": "none", "title": "Есть пропуски", "cards": []},
            {"key": "offered", "title": "Предложили отработку", "cards": []},
            {"key": "booked", "title": "Записан на отработку", "cards": []},
            {"key": "refused", "title": "Отказ от отработки", "cards": []},
        ]
        by = {c["key"]: c for c in cols}
        for s in _rows(con.execute("SELECT * FROM students WHERE funnel='members' AND stage != 'left' AND archived=0 ORDER BY name")):
            c = absence_counters(con, s["id"])
            if not c["available"] and not c["booked"]:
                continue
            card = card_summary(con, s, st)
            card["badge"] = f"Можно отработать: {c['available']}" + (f" · записан: {c['booked']}" if c["booked"] else "")
            key = "booked" if c["booked"] else s["skip_stage"]
            by.get(key, by["none"])["cards"].append(card)
        return {"funnel": "skips", "columns": cols, "draggable": True,
                "note": "Сюда попадают ученики с неотработанными пропусками за этот и прошлый месяц."}

    raise ApiError("Неизвестная воронка", 404)


def move_card(con, sid: int, funnel: str, stage: str, user) -> None:
    s = get_student_row(con, sid)
    if funnel == "sales":
        if stage not in SALES_LABELS or stage in AUTO_SALES:
            raise ApiError("В этот этап карточка попадает сама")
        if stage == "bought":
            con.execute("""UPDATE students SET funnel='members', stage='active', stage_changed_at=?, priority=0,
                           converted_at=COALESCE(converted_at, ?) WHERE id=?""", (now_iso(), now_iso(), sid))
            if s["group_id"] and not enrollments_of(con, sid):
                con.execute("INSERT INTO enrollments(student_id, group_id, weekdays, tariff, created_at) VALUES (?,?,?,?,?)",
                            (sid, s["group_id"], "", s["tariff"], now_iso()))
                from . import capacity
                capacity.ensure_roster(con, s["group_id"])
            log(con, sid, "Купил абонемент — переведён в учеников", user, "stage")
            return
        contacted = 1 if stage in ("how_was", "refused") else s["contacted_after_trial"]
        con.execute("UPDATE students SET stage=?, stage_changed_at=?, contacted_after_trial=?, priority=0 WHERE id=?",
                    (stage, now_iso(), contacted, sid))
        log(con, sid, f"Этап: {SALES_LABELS[stage]}", user, "stage")
    elif funnel == "skips":
        if stage not in ("none", "offered", "refused"):
            raise ApiError("В этот этап карточка попадает сама, после записи на отработку")
        con.execute("UPDATE students SET skip_stage=? WHERE id=?", (stage, sid))
        labels = {"none": "Есть пропуски", "offered": "Предложили отработку", "refused": "Отказ от отработки"}
        log(con, sid, f"Отработки: {labels[stage]}", user, "stage")
    elif funnel == "members":
        if stage not in ("left", "active"):
            raise ApiError("Этапы учеников ставятся сами по оплатам")
        con.execute("UPDATE students SET stage=?, stage_changed_at=? WHERE id=?", (stage, now_iso(), sid))
        log(con, sid, "Ушёл" if stage == "left" else "Вернулся", user, "stage")
    else:
        raise ApiError("Неизвестная воронка")


def student_detail(con, sid: int) -> dict:
    s = get_student_row(con, sid)
    st = settings(con)
    fam = dict(con.execute("SELECT * FROM families WHERE id=?", (s["family_id"],)).fetchone())
    parents = _rows(con.execute("SELECT id, name, phone, messenger FROM parents WHERE family_id=?", (s["family_id"],)))
    siblings = _rows(con.execute("SELECT id, name, birth FROM students WHERE family_id=? AND id != ?",
                                 (s["family_id"], sid)))
    grp = dict(con.execute("SELECT * FROM groups WHERE id=?", (s["group_id"],)).fetchone()) if s["group_id"] else None
    subs = _rows(con.execute("""SELECT x.*, g.name AS group_name FROM subscriptions x
                                LEFT JOIN enrollments e ON e.id = x.enrollment_id LEFT JOIN groups g ON g.id = e.group_id
                                WHERE x.student_id=? ORDER BY x.month DESC, x.id""", (sid,)))
    vacs = _rows(con.execute("SELECT * FROM vacations WHERE student_id=? ORDER BY start DESC", (sid,)))
    makeups = _rows(con.execute(
        """SELECT m.*, g.name AS group_name, a.date AS absence_date FROM makeups m
           JOIN groups g ON g.id = m.group_id JOIN attendance a ON a.id = m.absence_id
           WHERE m.student_id=? ORDER BY m.date DESC LIMIT 20""", (sid,)))
    hist = _rows(con.execute("SELECT ts, user_name, kind, text FROM history WHERE student_id=? ORDER BY id DESC LIMIT 100",
                             (sid,)))
    stage_label = SALES_LABELS.get(s["stage"], s["stage"])
    if s["funnel"] == "sales":
        eff, color, _ = effective_sales_stage(s, st, now())
        stage_label = SALES_LABELS.get(eff, eff)
    else:
        stage_label = "Ушёл" if s["stage"] == "left" else "Занимается"
    cur = logic.month_of(today())
    return {
        "student": s, "family": fam, "parents": parents, "siblings": siblings, "group": grp,
        "subscriptions": subs, "vacations": vacs, "enrollments": enrollments_of(con, sid), "makeups": makeups, "history": hist,
        "counters": absence_counters(con, sid) if s["funnel"] == "members" else None,
        "balance": _balance_out(con, sid),
        "payments": _rows(con.execute("SELECT * FROM payments WHERE student_id=? ORDER BY paid_at DESC, id DESC LIMIT 30", (sid,))),
        "discounts": _rows(con.execute("SELECT id, name, percent, rule FROM discounts WHERE active=1 AND rule='manual' ORDER BY name")),
        "stage_label": stage_label,
        "next_month": logic.shift_month(cur, 1), "cur_month": cur,
        "program": _program_out(con, sid),
        "pu": __import__("crm.pu", fromlist=["card_info"]).card_info(con, sid),
        "waitlist": __import__("crm.waitlist", fromlist=["for_student"]).for_student(con, sid),
    }


def _program_out(con, sid: int) -> dict:
    from . import program
    progs = []
    for p in con.execute("SELECT id, name FROM programs WHERE active=1").fetchall():
        progs.append({"id": p["id"], "name": p["name"], "tariffs": program.TARIFFS,
                      "ofp_groups": [{"id": g["id"], "name": g["name"], "branch": g["branch"]} for g in program.groups(con, p["id"], "ofp")],
                      "plan": program.plan_of(con, sid, p["id"])})
    return {"programs": progs}


def _balance_out(con, sid: int) -> dict:
    from . import money
    b = money.balance(con, sid)
    b["covered"] = {str(k): v for k, v in b["covered"].items()}
    return b


def find_families_by_phone(con, phone: str) -> list[dict]:
    p = normalize_phone(phone)
    if len(p) < 10:
        return []
    out = []
    for r in con.execute("""SELECT DISTINCT f.id, f.name FROM families f JOIN parents p ON p.family_id=f.id
                            WHERE p.phone_norm = ?""", (p,)):
        kids = [k["name"] for k in con.execute("SELECT name FROM students WHERE family_id=?", (r["id"],))]
        out.append({"id": r["id"], "name": r["name"], "children": kids})
    return out


def create_student(con, data: dict, user) -> int:
    name = (data.get("name") or "").strip()
    if not name:
        raise ApiError("Укажите имя ребёнка")
    family_id = data.get("family_id")
    if family_id:
        if not con.execute("SELECT 1 FROM families WHERE id=?", (family_id,)).fetchone():
            raise ApiError("Семья не найдена")
    else:
        pname = (data.get("parent_name") or "").strip()
        phone = (data.get("phone") or "").strip()
        if not pname or not phone:
            raise ApiError("Укажите родителя и телефон")
        if find_families_by_phone(con, phone) and not data.get("force_new_family"):
            raise ApiError("Семья с этим телефоном уже есть — добавьте ребёнка в неё", 409)
        fam_name = (data.get("family_name") or "").strip() or pname.split()[-1]
        family_id = con.execute("INSERT INTO families(name) VALUES (?)", (fam_name,)).lastrowid
        con.execute("INSERT INTO parents(family_id, name, phone, phone_norm, messenger) VALUES (?,?,?,?,?)",
                    (family_id, pname, phone, normalize_phone(phone), data.get("messenger") or ""))
    funnel = "members" if data.get("funnel") == "members" else "sales"
    tariff = "flex" if data.get("tariff") == "flex" else "fixed"
    stage = "active" if funnel == "members" else "lead"
    from . import sources as _src
    source_id = int(data["source_id"]) if data.get("source_id") else None
    source_txt = _src.label(con, source_id) if source_id else (data.get("source") or "")
    sid = con.execute(
        """INSERT INTO students(family_id, name, birth, group_id, tariff, funnel, stage, stage_changed_at,
           source, source_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (family_id, name, data.get("birth") or "", data.get("group_id") or None, tariff, funnel, stage,
         now_iso(), source_txt, source_id, now_iso())).lastrowid
    if funnel == "members" and data.get("group_id"):
        con.execute("INSERT INTO enrollments(student_id, group_id, weekdays, tariff, created_at) VALUES (?,?,?,?,?)",
                    (sid, data["group_id"], "", tariff, now_iso()))
        from . import capacity
        capacity.ensure_roster(con, int(data["group_id"]))
    log(con, sid, "Карточка создана", user, "system")
    audit(con, user, "student.create", str(sid))
    return sid


UPDATABLE = {"name", "birth", "group_id", "tariff", "trial_at", "source", "source_id", "refusal_reason"}


def update_student(con, sid: int, data: dict, user) -> None:
    s = get_student_row(con, sid)
    changes = []
    for k, v in data.items():
        if k not in UPDATABLE:
            continue
        if k == "tariff" and v not in ("fixed", "flex"):
            raise ApiError("Неизвестный тариф")
        if k == "trial_at" and v:
            try:
                datetime.fromisoformat(v)
            except ValueError:
                raise ApiError("Неверная дата пробного")
        if v == "":
            v = None if k in ("group_id", "trial_at") else ""
        if s.get(k) != v:
            con.execute(f"UPDATE students SET {k} = ? WHERE id = ?", (v, sid))
            changes.append(k)
    if "source_id" in changes:
        from . import sources as _src
        con.execute("UPDATE students SET source=? WHERE id=?", (_src.label(con, data.get("source_id")), sid))
    if "trial_at" in changes:
        con.execute("UPDATE students SET contacted_after_trial = 0, trial_came = NULL WHERE id=?", (sid,))
        if data.get("trial_at"):
            con.execute("UPDATE students SET trial_booked_at = COALESCE(trial_booked_at, ?) WHERE id=?", (now_iso(), sid))
        if data.get("trial_at") and s["stage"] in ("lead", "no_answer"):
            con.execute("UPDATE students SET stage='trial_booked', stage_changed_at=? WHERE id=?", (now_iso(), sid))
    if ("trial_at" in changes or "group_id" in changes) and s["funnel"] == "sales":
        cur = get_student_row(con, sid)
        if cur["trial_at"] and cur["group_id"] and cur["trial_at"][:10] >= today().isoformat():
            from . import capacity
            capacity.ensure(con, cur["group_id"], cur["trial_at"][:10], "записать на пробное")
    names = {"name": "имя", "birth": "дата рождения", "group_id": "группа", "tariff": "тариф",
             "trial_at": "пробное", "source": "источник", "source_id": "источник", "refusal_reason": "причина отказа"}
    if changes:
        log(con, sid, "Изменено: " + ", ".join(names[c] for c in changes), user, "system")


def add_vacation(con, sid: int, start: str, end: str, note: str, user) -> None:
    get_student_row(con, sid)
    try:
        ds, de = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError):
        raise ApiError("Укажите даты отпуска")
    if de < ds:
        raise ApiError("Конец отпуска раньше начала")
    con.execute("INSERT INTO vacations(student_id, start, end, note) VALUES (?,?,?,?)", (sid, start, end, note or ""))
    paid = con.execute("""SELECT month FROM subscriptions WHERE student_id=? AND status != 'cancelled'
                          AND month BETWEEN ? AND ?""", (sid, start[:7], end[:7])).fetchall()
    warn = " — внимание: на этот месяц абонемент уже выставлен" if paid else ""
    log(con, sid, f"Отпуск {fmt_date(start)}–{fmt_date(end)}{warn}", user, "vacation")


# ---------- журнал ----------

def journal(con, group_id: int, day: str) -> dict:
    g = con.execute("SELECT g.*, b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id WHERE g.id=?",
                    (group_id,)).fetchone()
    if not g:
        raise ApiError("Группа не найдена", 404)
    d = date.fromisoformat(day)
    from . import closures
    closed = closures.is_closed(con, group_id, d)
    has_lesson = d.weekday() in logic.parse_weekdays(g["weekdays"]) and not closed
    rows = []
    if g["is_makeup"]:
        for m in con.execute(
                """SELECT m.id AS makeup_id, m.status, s.id, s.name, a.date AS absence_date,
                          (SELECT g2.name FROM groups g2 WHERE g2.id = s.group_id) AS home_group
                   FROM makeups m JOIN students s ON s.id=m.student_id JOIN attendance a ON a.id=m.absence_id
                   WHERE m.group_id=? AND m.date=? AND m.status != 'cancelled' ORDER BY s.name""",
                (group_id, day)):
            rows.append(dict(m))
    else:
        for s in con.execute("""SELECT s.id, s.name, e.weekdays FROM enrollments e JOIN students s ON s.id = e.student_id
                                WHERE e.group_id=? AND e.active=1 AND s.funnel='members' AND s.stage != 'left' AND s.archived=0
                                ORDER BY s.name""", (group_id,)).fetchall():
            if s["weekdays"] and d.weekday() not in logic.parse_weekdays(s["weekdays"]):
                continue
            a = con.execute("SELECT status, resolved, warned, note FROM attendance WHERE student_id=? AND date=? AND group_id=?",
                            (s["id"], day, group_id)).fetchone()
            vac = logic.in_vacation(d, vacations_of(con, s["id"]))
            unpaid = bool(g["program_id"]) and not con.execute(
                "SELECT 1 FROM subscriptions WHERE student_id=? AND month=? AND status='paid' AND kind='month'",
                (s["id"], day[:7])).fetchone()
            rows.append({"id": s["id"], "name": s["name"], "unpaid": unpaid, "status": "vacation" if vac else (a["status"] if a else None),
                         "resolved": a["resolved"] if a else None, "warned": bool(a and a["warned"]) and not vac,
                         "note": a["note"] if a else ""})
        # отработки в обычной группе (на место того, кто предупредил)
        for m in con.execute(
                """SELECT m.id AS makeup_id, m.status AS mk_status, s.id, s.name, a.date AS absence_date,
                          (SELECT g2.name FROM groups g2 WHERE g2.id = s.group_id) AS home_group
                   FROM makeups m JOIN students s ON s.id=m.student_id JOIN attendance a ON a.id=m.absence_id
                   WHERE m.group_id=? AND m.date=? AND m.status != 'cancelled' ORDER BY s.name""", (group_id, day)):
            rows.append(dict(m) | {"status": "makeup"})
        # пробные на этот день
        for s in con.execute("""SELECT id, name, trial_at FROM students WHERE funnel='sales' AND group_id=? AND archived=0
                                AND substr(trial_at,1,10)=?""", (group_id, day)):
            came = con.execute("SELECT trial_came FROM students WHERE id=?", (s["id"],)).fetchone()[0]
            rows.append({"id": s["id"], "name": s["name"], "status": "trial", "trial_at": s["trial_at"], "trial_came": came})
    return {"group": dict(g), "date": day, "has_lesson": has_lesson, "closed": closed, "rows": rows}


def mark_trial(con, sid: int, came: bool, user) -> None:
    s = get_student_row(con, sid)
    if not s["trial_at"]:
        raise ApiError("У ученика нет пробного занятия")
    con.execute("UPDATE students SET trial_came=? WHERE id=?", (1 if came else 0, sid))
    log(con, sid, "Пришёл на пробное" if came else "Не пришёл на пробное", user, "attendance")
    from . import pu
    pu.on_trial_mark(con, sid, came, user)


def mark_attendance(con, sid: int, group_id: int, day: str, status: str, user) -> None:
    if status not in ("present", "absent", "clear"):
        raise ApiError("Неизвестная отметка")
    s = get_student_row(con, sid)
    d = date.fromisoformat(day)
    if d > today():
        raise ApiError("Нельзя отметить будущее занятие")
    if logic.in_vacation(d, vacations_of(con, sid)):
        raise ApiError("У ученика в этот день отпуск")
    a = con.execute("SELECT * FROM attendance WHERE student_id=? AND date=? AND group_id=?",
                    (sid, day, group_id)).fetchone()
    if a and a["resolved"] == "credit" and status != "absent":
        raise ApiError("Этот пропуск уже учтён в перерасчёте абонемента")
    if a and a["resolved"] == "makeup" and status != "absent":
        raise ApiError("Этот пропуск уже отработан")
    if status == "clear":
        if a:
            con.execute("UPDATE makeups SET status='cancelled' WHERE absence_id=? AND status='booked'", (a["id"],))
            con.execute("DELETE FROM attendance WHERE id=?", (a["id"],))
        return
    if a:
        if a["status"] == status:
            return
        if status == "present":
            con.execute("UPDATE makeups SET status='cancelled' WHERE absence_id=? AND status='booked'", (a["id"],))
        con.execute("UPDATE attendance SET status=?, warned=CASE WHEN ?='present' THEN 0 ELSE warned END WHERE id=?",
                    (status, status, a["id"]))
    else:
        con.execute("INSERT INTO attendance(student_id, group_id, date, status) VALUES (?,?,?,?)",
                    (sid, group_id, day, status))
    con.execute("UPDATE attendance SET marked_at=? WHERE student_id=? AND date=? AND group_id=?", (now_iso(), sid, day, group_id))
    from . import pu
    pu.record_lesson_mark(con, group_id, day)
    if status == "absent":
        if s["skip_stage"] == "refused":
            con.execute("UPDATE students SET skip_stage='none' WHERE id=?", (sid,))
        log(con, sid, f"Пропуск {fmt_date(day)}", user, "attendance")


def meta(con) -> dict:
    return {
        "branches": _rows(con.execute("SELECT * FROM branches WHERE active=1 ORDER BY id")),
        "groups": _rows(con.execute("""SELECT g.*, b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id
                                      WHERE g.active=1 ORDER BY g.is_makeup, b.id, g.name""")),
        "sales_stages": [{"key": k, "title": t, "auto": k in AUTO_SALES} for k, t in SALES_STAGES],
        "ad": __import__("crm.sources", fromlist=["listing"]).listing(con),
        "settings": settings(con),
        "today": today().isoformat(),
    }


def search_students(con, q: str, archived: bool = False) -> list[dict]:
    st = settings(con)
    like = f"%{q.strip().lower()}%"
    p = normalize_phone(q)
    rows = _rows(con.execute(
        """SELECT DISTINCT s.* FROM students s JOIN families f ON f.id=s.family_id
           LEFT JOIN parents p ON p.family_id=f.id
           WHERE (ulower(s.name) LIKE ? OR ulower(f.name) LIKE ? OR ulower(p.name) LIKE ?
              OR (? != '' AND p.phone_norm LIKE ?)) AND s.archived = ?
           ORDER BY s.name LIMIT 100""", (like, like, like, p, f"%{p}%", 1 if archived else 0)))
    return [card_summary(con, s, st) | {"funnel": s["funnel"], "archived": s.get("archived", 0)} for s in rows]


# ---------- дашборд ----------

def _pct(a, b):
    return round(100 * a / b) if b else None


def _change(cur, old):
    """Изменение в процентах к прошлому периоду (None — сравнивать не с чем)."""
    if not old:
        return None
    return round(100 * (cur - old) / old)


def dashboard(con, ym: str | None = None, branch_id: int | None = None) -> dict:
    cur = ym or logic.month_of(today())
    br_sql = " AND s.group_id IN (SELECT id FROM groups WHERE branch_id = ?)" if branch_id else ""
    br_arg = (branch_id,) if branch_id else ()

    def holders(m):
        out = {}
        for sid_, price in con.execute(
                "SELECT s.id, x.price FROM subscriptions x JOIN students s ON s.id = x.student_id "
                "LEFT JOIN enrollments e ON e.id = x.enrollment_id "
                "WHERE x.month = ? AND x.status = 'paid'"
                + (" AND COALESCE(e.group_id, s.group_id) IN (SELECT id FROM groups WHERE branch_id = ?)" if branch_id else ""),
                (m, *br_arg)):
            out.setdefault(sid_, []).append(price)
        return out

    first_paid = {r[0]: r[1] for r in con.execute(
        "SELECT student_id, MIN(month) FROM subscriptions WHERE status='paid' GROUP BY student_id")}
    cache = {}

    def month_stats(m):
        if m in cache:
            return cache[m]
        h = holders(m)
        prev = holders(logic.shift_month(m, -1))
        revenue = sum(sum(v) for v in h.values())
        n_subs = sum(len(v) for v in h.values())
        st = {"revenue": revenue, "holders": len(h), "subs": n_subs, "avg": round(revenue / n_subs) if n_subs else 0,
              "conversion": _pct(len(set(h) & set(prev)), len(prev)), "ids": set(h),
              "prices": {k: sum(v) for k, v in h.items()}}
        cache[m] = st
        return st

    seats = con.execute("SELECT COALESCE(SUM(capacity), 0) FROM groups WHERE is_makeup = 0 AND active = 1"
                        + (" AND branch_id = ?" if branch_id else ""), br_arg).fetchone()[0]
    c, m1, y1 = (month_stats(cur), month_stats(logic.shift_month(cur, -1)), month_stats(logic.shift_month(cur, -12)))

    def kpi(key):
        return {"value": c[key], "vs_month": _change(c[key] or 0, m1[key] or 0),
                "vs_year": _change(c[key] or 0, y1[key] or 0)}

    fill = _pct(c["holders"], seats)
    fill_m1 = _pct(m1["holders"], seats)
    fill_subs = _pct(c["subs"], seats)

    # новые / постоянные / вернувшиеся
    prev_ids = month_stats(logic.shift_month(cur, -1))["ids"]
    seg = {"new": [0, 0], "regular": [0, 0], "returned": [0, 0]}
    for sid, price in c["prices"].items():
        k = "new" if first_paid.get(sid) == cur else ("regular" if sid in prev_ids else "returned")
        seg[k][0] += 1
        seg[k][1] += price

    # лиды и пробные
    start, end = logic.month_bounds(cur)
    s_iso, e_iso = start.isoformat(), end.isoformat() + "T23:59"

    def cnt(where, *args):
        return con.execute("SELECT COUNT(*) FROM students s WHERE " + where + br_sql, (*args, *br_arg)).fetchone()[0]

    lead_where = "s.created_at BETWEEN ? AND ? AND (s.funnel = 'sales' OR s.converted_at IS NOT NULL)"
    leads = cnt(lead_where, s_iso, e_iso)
    leads_trial = cnt(lead_where + " AND s.trial_booked_at IS NOT NULL", s_iso, e_iso)
    leads_came = cnt(lead_where + " AND s.trial_came = 1", s_iso, e_iso)
    leads_bought = cnt(lead_where + " AND s.converted_at IS NOT NULL", s_iso, e_iso)
    tr_where = "s.trial_at BETWEEN ? AND ?"
    trials = cnt(tr_where, s_iso, e_iso)
    trials_came = cnt(tr_where + " AND s.trial_came = 1", s_iso, e_iso)
    trials_bought = cnt(tr_where + " AND s.converted_at IS NOT NULL", s_iso, e_iso)

    def day_stats(d):
        a, b = d.isoformat(), d.isoformat() + "T23:59"
        return {"leads": cnt("s.created_at BETWEEN ? AND ? AND (s.funnel='sales' OR s.converted_at IS NOT NULL)", a, b),
                "trial_booked": cnt("s.trial_booked_at BETWEEN ? AND ?", a, b),
                "bought_first": cnt("s.converted_at BETWEEN ? AND ?", a, b)}

    t = today()
    future_trials = cnt("s.funnel = 'sales' AND s.trial_at > ?", now().isoformat(timespec="minutes"))

    # графики: 12 месяцев, текущий и прошлый год
    months = [logic.shift_month(cur, -i) for i in range(11, -1, -1)]
    series = {}
    for key in ("revenue", "holders", "avg", "conversion"):
        val = lambda m: month_stats(m)[key] if month_stats(m)["holders"] else None
        series[key] = {"cur": [val(x) for x in months],
                       "prev": [val(logic.shift_month(x, -12)) for x in months]}

    # отработки (своё, нет в ERP)
    can_make_up, at_risk = 0, 0
    per = int(settings(con)["price_flex"])
    for r in con.execute("SELECT s.id, s.tariff FROM students s WHERE s.funnel='members' AND s.stage != 'left'"
                         + br_sql, br_arg).fetchall():
        a = absence_counters(con, r[0])["available"]
        can_make_up += a
        if r[1] == "flex":
            at_risk += min(logic.MAX_FLEX_CREDIT, a) * per

    return {
        "month": cur, "month_label": logic.month_label(cur),
        "kpi": {"revenue": kpi("revenue"), "holders": kpi("holders"), "avg": kpi("avg"),
                "conversion": kpi("conversion"),
                "fill": {"value": fill, "subs": fill_subs, "subs_count": c["subs"],
                         "vs_month": (fill - fill_m1) if fill is not None and fill_m1 is not None else None},
                "seats": seats},
        "days": {"yesterday": day_stats(t - timedelta(days=1)), "today": day_stats(t)},
        "future_trials": future_trials,
        "months": [logic.month_label(x).split()[0].capitalize() for x in months],
        "series": series,
        "funnel_leads": [["Новый лид", leads], ["Записаны на пробное", leads_trial],
                         ["Пришли на пробное", leads_came], ["Купили абонемент", leads_bought]],
        "funnel_trials": [["Записаны на пробное", trials], ["Пришли на пробное", trials_came],
                          ["Купили абонемент", trials_bought]],
        "segments": {"count": {k: v[0] for k, v in seg.items()}, "revenue": {k: v[1] for k, v in seg.items()}},
        "makeups": {"can_make_up": can_make_up, "at_risk_rub": at_risk},
    }


# ---------- дни рождения ----------

def _bday_this_year(birth: str, year: int) -> date | None:
    try:
        b = date.fromisoformat(birth[:10])
    except (TypeError, ValueError):
        return None
    try:
        return b.replace(year=year)
    except ValueError:  # 29 февраля в невисокосный год — празднуем 28-го
        return date(year, 2, 28)


def birthday_info(birth: str, ref: date) -> dict | None:
    """Сегодня или вчера был день рождения. Возвращает сколько исполнилось."""
    for delta, when in ((0, "today"), (1, "yesterday")):
        d = ref - timedelta(days=delta)
        bd = _bday_this_year(birth, d.year)
        if bd == d:
            return {"when": when, "age": d.year - int(birth[:4])}
    return None


def birthdays(con, ref: date | None = None, days_ahead: int = 0) -> list[dict]:
    """Ученики, у кого день рождения сегодня (и вчера), для рабочего стола админа."""
    ref = ref or today()
    out = []
    for s in con.execute("""SELECT s.id, s.name, s.birth, s.group_id, s.family_id, g.name AS group_name FROM students s
                            LEFT JOIN groups g ON g.id=s.group_id
                            WHERE s.funnel='members' AND s.stage != 'left' AND s.archived=0 AND s.birth != ''"""):
        info = birthday_info(s["birth"], ref)
        if info:
            out.append({"id": s["id"], "name": s["name"], "group": s["group_name"] or "", **info})
    return sorted(out, key=lambda x: (x["when"] != "today", x["name"]))
