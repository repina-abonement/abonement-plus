"""Операции CRM поверх базы: воронки, карточка, абонементы, журнал, отработки."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from . import logic
from .db import normalize_phone, now_iso, settings


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400, extra: dict | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.extra = extra or {}


SALES_STAGES = [
    ("lead", "Новый лид"),
    ("no_answer", "Недозвон, отправили СМС"),
    ("trial_booked", "Записан на пробное"),
    ("on_trial", "Сейчас на пробном"),
    ("trial_done", "Пробное закончилось"),
    ("missed_trial", "Не дошёл до ПУ"),
    ("how_was", "Как прошло пробное?"),
    ("reinvite_pu", "Приглашение на ПУ повторно"),  # Юлия, 04.11: был на пробном, не сложилось; дату следующего приглашения выбирает админ
    ("bought", "Купил абонемент"),
    ("refused", "Отказ"),
    ("later", "Пригласим позже"),
]
# Почему не купили после пробного (Юлия, 28.10.2026). Две последние — не отказ, а задача
TRIAL_REFUSALS = ["Дорого", "Неудобные дни или время", "Далеко ездить", "Ребёнку не понравилось",
                  "Ребёнок пока не готов (маленький, стеснялся)", "Выбрали другую секцию",
                  "Думают, посоветуются", "Заболели или уезжают", "Другое"]
REFUSAL_THINKING, REFUSAL_LATER = "Думают, посоветуются", "Заболели или уезжают"
AUTO_SALES = {"on_trial", "trial_done", "missed_trial", "later"}
RENEW_FROM_DAY = 20  # с этого числа ученик без оплаты следующего месяца попадает в «Нужно продлить»
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
        """SELECT a.id, a.date, a.group_id, a.resolved, a.cert,
                  (SELECT m.id FROM makeups m WHERE m.absence_id = a.id AND m.status = 'booked') AS booked_id
           FROM attendance a WHERE a.student_id = ? AND a.status = 'absent' AND a.date BETWEEN ? AND ?
           ORDER BY a.date""", (sid, w_start.isoformat(), w_end.isoformat())))
    c = {"absent_cur": 0, "absent_prev": 0, "made_up": 0, "credited": 0, "booked": 0, "available": 0,
         "available_ids": [], "month_cur": cur, "month_prev": prev}
    from . import subscriptiondates
    for r in rows:
        if subscriptiondates.excluded(con, sid, r["group_id"], r["date"]):
            continue
        c["absent_cur" if r["date"].startswith(cur) else "absent_prev"] += 1
        if r["resolved"] == "makeup":
            c["made_up"] += 1
        elif r["resolved"] == "credit":
            c["credited"] += 1
        elif r["booked_id"]:
            c["booked"] += 1
        else:
            c["available"] += 1
            c["available_ids"].append({"id": r["id"], "date": r["date"], "cert": r["cert"]})
    return c


def unresolved_absences_in_month(con, sid: int, ym: str, group_id: int | None = None, cert_only: bool = False) -> list[dict]:
    start, end = logic.month_bounds(ym)
    extra, args = ("AND a.group_id = ?", (group_id,)) if group_id else ("", ())
    if cert_only:
        extra += " AND a.cert = 1"
    rows = _rows(con.execute(
        f"""SELECT a.id, a.date, a.group_id FROM attendance a
           WHERE a.student_id = ? AND a.status = 'absent' AND a.resolved IS NULL
             AND a.date BETWEEN ? AND ? {extra}
             AND NOT EXISTS (SELECT 1 FROM makeups m WHERE m.absence_id = a.id AND m.status = 'booked')
           ORDER BY a.date""", (sid, start.isoformat(), end.isoformat(), *args)))
    from . import subscriptiondates
    return [r for r in rows if not subscriptiondates.excluded(con, sid, r["group_id"], r["date"])]


# ---------- группы ученика ----------

def enrollments_of(con, sid: int, active_only: bool = True) -> list[dict]:
    q = """SELECT e.*, g.name AS group_name, g.weekdays AS group_weekdays, g.time AS group_time, g.age AS group_age,
                  g.program_id, g.lesson_kind, b.name AS branch_name
           FROM enrollments e JOIN groups g ON g.id = e.group_id JOIN branches b ON b.id = g.branch_id WHERE e.student_id = ?"""
    if active_only:
        q += " AND e.active = 1"
    return _rows(con.execute(q + " ORDER BY e.id", (sid,)))


def unpaid_groups(con, sid: int, ym: str) -> list[str]:
    """Группы ребёнка, за которые месяц не оплачен (у ребёнка в двух группах может быть оплачена только одна)."""
    paid = {r[0] for r in con.execute("SELECT enrollment_id FROM subscriptions WHERE student_id=? AND month=? AND status='paid'", (sid, ym))}
    return [e["group_name"] for e in enrollments_of(con, sid) if e["id"] not in paid]


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
        log(con, e["student_id"], f"{e['name']}: дни: {_days_label(days) if days else 'все дни группы'}", user, "system")
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
    from . import schedule, closures
    if not g["active"] or d.weekday() not in logic.parse_weekdays(schedule.on(con,g,d)["weekdays"]) or closures.is_closed(con,group_id,d):
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
    from . import capacity
    if not g["is_makeup"]:
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


def sale_enrollment(con, sid, enrollment_id, group_id=None):
    """Другая доступная группа для расчёта без изменения записей клиента."""
    enrollments = enrollments_of(con, sid)
    source = _pick_enrollment(con, sid, enrollment_id) if enrollments or enrollment_id else None
    try:
        group_id = int(group_id) if group_id else None
    except (ValueError, TypeError):
        raise ApiError("Выберите группу")
    if source and (not group_id or group_id == source["group_id"]):
        return source
    if not group_id:
        raise ApiError("Выберите группу")
    own = next((e for e in enrollments_of(con, sid) if e["group_id"] == int(group_id)), None)
    if own:
        return own
    g = con.execute("""SELECT g.*, b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id
        WHERE g.id=? AND g.active=1 AND b.active=1 AND g.is_makeup=0 AND g.lesson_kind!='ofp'""", (int(group_id),)).fetchone()
    if not g:
        raise ApiError("Выберите действующую группу для абонемента")
    if source and (g["program_id"] or None) != (source["program_id"] or None):
        raise ApiError("Переход между обычной группой и сборной оформляется отдельно")
    return dict(source or {}, id=None, student_id=sid, group_id=g["id"], group_name=g["name"], group_weekdays=g["weekdays"],
                weekdays="", group_age=g["age"], branch_name=g["branch"], source_id=source["id"] if source else None,
                program_id=g["program_id"], lesson_kind=g["lesson_kind"], tariff=(source or {}).get("tariff") or "fixed")


def quote_for(con, sid: int, ym: str, enrollment_id=None, discount_id=None, tariff=None, excluded_dates=None, credit_requested=False, group_id=None, ofp_group_id=None) -> dict:
    get_student_row(con, sid)
    e = sale_enrollment(con, sid, enrollment_id, group_id)
    if tariff is not None:
        if e.get("program_id"):
            from . import program
            if tariff not in program.TARIFFS:
                raise ApiError("Выберите тариф сборной")
        elif tariff not in ("fixed", "flex"):
            raise ApiError("Неизвестный тариф")
        else:
            e = dict(e, tariff=tariff)
    cert_from = (con.execute("SELECT value FROM settings WHERE key='cert_required_from'").fetchone() or ["9999"])[0]
    # перерасчёт на «Гибком»: без справки (с 01.11.2026), не больше 3 и только при оплате следующего месяца
    prev_abs = unresolved_absences_in_month(con, sid, logic.shift_month(ym, -1), e["group_id"], cert_only=ym >= cert_from)
    prev_flex = con.execute("""SELECT 1 FROM subscriptions WHERE student_id=? AND enrollment_id=? AND month=?
        AND tariff='flex' AND status='paid'""", (sid, e["id"], logic.shift_month(ym, -1))).fetchone()
    eligible = prev_abs if prev_flex and e["tariff"] == "flex" else []
    requested = credit_requested is True or credit_requested == "1"
    from . import closures, pricing, program, subscriptiondates
    if program.program_of_group(con, e["group_id"]):
        return program.quote(con, sid, ym, e, discount_id=discount_id, excluded_dates=excluded_dates, selection={"tariff": tariff, "ofp_group_id": ofp_group_id} if tariff is not None else None, validate_selection=True)
    on = today()
    pr = pricing.prices_for(con, ym)  # прайс оплачиваемого месяца (pricing.py)
    ms, me = logic.month_bounds(ym)
    newcomer = pricing.is_newcomer(con, sid, ym, on)
    closed = closures.closed_dates(con, e["group_id"], ms, me)
    weekdays = enrollment_days(e)
    from . import schedule
    if con.execute("SELECT 1 FROM group_versions WHERE group_id=?", (e["group_id"],)).fetchone():  # расписание меняется с даты
        g = con.execute("SELECT * FROM groups WHERE id=?", (e["group_id"],)).fetchone()
        real = {d.isoformat() for d in schedule.month_dates(con, g, e["weekdays"], ym)}
        weekdays = sorted({date.fromisoformat(x).weekday() for x in real} | set(weekdays))
        closed = closed | {d.isoformat() for d in logic.lesson_dates(weekdays, ym) if d.isoformat() not in real}
    if newcomer:  # новичок в текущем месяце: только оставшиеся занятия
        start = pricing.newcomer_start(con, sid, on)
        closed = closed | {d.isoformat() for d in logic.lesson_dates(weekdays, ym) if d < start}
    available = [d.isoformat() for d in logic.lesson_dates(weekdays, ym)
                 if d.isoformat() not in closed and not logic.in_vacation(d, vacations_of(con, sid))]
    exceptions = subscriptiondates.validate(excluded_dates, available)
    closed = closed | set(exceptions)
    q = logic.quote(weekdays=weekdays, ym=ym, tariff=e["tariff"], prices={"fixed": pr["fixed"], "flex": pr["flex"]},
                    vacations=vacations_of(con, sid), prev_unresolved_absences=min(len(eligible), len(available) - len(exceptions)) if requested else 0, closed=closed)
    q["calendar_dates"] = available
    q["excluded_dates"] = exceptions
    q["credit_available"] = min(len(eligible), logic.MAX_FLEX_CREDIT)
    q["credit_requested"] = requested
    q["credit_absence_ids"] = [a["id"] for a in eligible[:q["credit_lessons"]]]
    q["credit_absence_dates"] = [a["date"] for a in eligible[:q["credit_lessons"]]]
    dl = pricing.deadline(con, ym)
    late = on > dl and not newcomer and pr["late_pct"] > 0
    total, fee = q["total"], 0
    if late:  # после срока: надбавка со всей суммы, скидок нет
        fee = pricing.late_fee(q["total"], pr["late_pct"])
        total += fee
    q["late_fee"] = fee
    q["pricing"] = {"late": late, "late_pct": pr["late_pct"], "deadline": dl.isoformat(), "newcomer": newcomer,
                    "late_total": q["total"] + pricing.late_fee(q["total"], pr["late_pct"])}
    from . import money
    manual = None
    if discount_id and not late:
        r = con.execute("SELECT * FROM discounts WHERE id=? AND active=1", (int(discount_id),)).fetchone()
        manual = dict(r) if r else None
    disc = None if late else money.best_discount([manual, *money.auto_discounts(con, sid, on)])  # скидки не суммируются, берём большую
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


def create_invoice(con, sid: int, ym: str, user, enrollment_id=None, discount_id=None, tariff=None,
                   excluded_dates=None, credit_requested=False, group_id=None, ofp_group_id=None, group_action="transfer") -> dict:
    from . import program, transfer, smart
    smart.require(con, sid, "sale")
    e = sale_enrollment(con, sid, enrollment_id, group_id)
    q = quote_for(con, sid, ym, enrollment_id, discount_id, tariff, excluded_dates, credit_requested, group_id, ofp_group_id)
    con.execute("SAVEPOINT sale_invoice")
    try:
        if e["id"] is None:
            if group_action not in ("transfer", "add"):
                raise ApiError("Выберите: перевести в новую группу или добавить вторую")
            if e.get("source_id") is None:
                if e.get("program_id"):
                    program.set_plan(con,sid,e["program_id"],q["tariff"],q["program"].get("ofp_group_id"),user)
                    eid=program.plan_of(con,sid,e["program_id"])["enrollment_id"]
                    con.execute("UPDATE enrollments SET group_id=?,start_date=? WHERE id=?",(e["group_id"],ym+"-01",eid))
                    con.execute("UPDATE enrollments SET start_date=? WHERE student_id=? AND active=1 AND group_id IN (SELECT id FROM groups WHERE program_id=?)",(ym+"-01",sid,e["program_id"]))
                else:
                    eid=add_enrollment(con,sid,e["group_id"],None,q["tariff"],user)
                    con.execute("UPDATE enrollments SET start_date=? WHERE id=?",(ym+"-01",eid))
            elif group_action == "transfer":
                if con.execute("SELECT 1 FROM subscriptions WHERE enrollment_id=? AND month>=? AND status!='cancelled'", (e["source_id"], ym)).fetchone():
                    raise ApiError("На выбранный или будущий месяц в прежней группе уже есть абонемент. Измените его через перевод группы")
                if e.get("program_id"):
                    raise ApiError("Перевод между группами сборной оформляется отдельно")
                eid = transfer.apply(con, e["source_id"], e["group_id"], ym + "-01", user)["enrollment_id"]
                con.execute("UPDATE enrollments SET tariff=? WHERE id=?", (q["tariff"], eid))
                sync_primary(con, sid)
            else:
                if e.get("program_id"):
                    raise ApiError("Вторая группа сборной оформляется отдельно")
                eid = add_enrollment(con, sid, e["group_id"], None, q["tariff"], user)
                con.execute("UPDATE enrollments SET start_date=? WHERE id=?", (ym + "-01", eid))
        else:
            eid = e["id"]
        if q.get("program") and tariff is not None:
            plan = program.plan_of(con, sid, e["program_id"])
            selected = {"tariff": q["tariff"], "ofp_group_id": q["program"].get("ofp_group_id")}
            effective = dict(plan)
            if plan.get("next") and ym >= plan["next"]["from"]:
                effective.update(plan["next"])
            if any(effective.get(k) != v for k, v in selected.items()):
                program.set_plan(con, sid, e["program_id"], selected["tariff"], selected["ofp_group_id"], user)
        q["enrollment_id"] = eid
        result = _create_invoice(con, sid, ym, user, eid, quote=q)
        con.execute("RELEASE sale_invoice")
        return result
    except BaseException:
        con.execute("ROLLBACK TO sale_invoice")
        con.execute("RELEASE sale_invoice")
        raise


def _create_invoice(con, sid: int, ym: str, user, enrollment_id=None, discount_id=None, tariff=None, excluded_dates=None, credit_requested=False, quote=None) -> dict:
    from . import smart
    smart.require(con, sid, "sale")  # почта родителя нужна для чека PayKeeper
    e = _pick_enrollment(con, sid, enrollment_id)
    from . import subscriptiondates
    subscriptiondates.guard_duplicate(con,sid,e["group_id"],ym)
    existing = con.execute("SELECT 1 FROM subscriptions WHERE enrollment_id=? AND month=? AND status != 'cancelled'",
                           (e["id"], ym)).fetchone()
    if existing:
        raise ApiError(f"На {logic.month_label(ym)} в группу {e['group_name']} уже есть абонемент")
    q = quote or quote_for(con, sid, ym, e["id"], discount_id, tariff, excluded_dates, credit_requested)
    d = q.get("discount") or {}
    from . import money
    available_before = max(0, money.balance(con,sid)['deposit'])
    sub_id = con.execute(
        """INSERT INTO subscriptions(student_id, enrollment_id, month, tariff, lessons, per_lesson, credit_lessons,
           late_fee, price, status, created_at, discount_name, discount_amount) VALUES (?,?,?,?,?,?,?,?,?,'invoiced',?,?,?)""",
        (sid, e["id"], ym, q["tariff"], q["lessons_paid"], q["per_lesson"], q["credit_lessons"], q["late_fee"],
         q["total"], now_iso(), d.get("name", ""), d.get("amount", 0))).lastrowid
    snapshot = {"tariff":q["tariff"],"ofp_group_id":q["program"].get("ofp_group_id"),
                "contract":{"base":q["base"],"on_time_total":q["program"]["on_time_total"],"late_total":q["program"]["late_total"],
                            "deadline":q["program"]["deadline"],"lessons":q["lessons_paid"],"components":q["components"]}} if q.get("program") else None
    con.execute("UPDATE subscriptions SET excluded_dates=?, program_selection=? WHERE id=?",
                (json.dumps(q.get("excluded_dates", [])), json.dumps(snapshot) if snapshot else "", sub_id))
    schedule_snapshot = q.get('components') or [{'group_id':e['group_id'],'rate':1,'dates':q['dates']}]
    con.execute('UPDATE subscriptions SET lesson_schedule=?,ledger_managed=1 WHERE id=?',(json.dumps(schedule_snapshot),sub_id))
    from . import money
    money.reconcile(con, sid)  # депозит сам закрывает новый счёт
    for aid in q["credit_absence_ids"]:
        con.execute("UPDATE attendance SET resolved='credit', resolved_month=? WHERE id=?", (ym, aid))
    extra = f", перерасчёт {q['credit_lessons']} пропуск(а) −{q['credit']} ₽" if q["credit_lessons"] else ""
    if q.get("excluded_dates"):
        extra += ", индивидуально исключены даты: " + ", ".join(q["excluded_dates"])
    if d:
        extra += f", скидка «{d['name']}» −{d['amount']} ₽"
    covered = max(0,money.balance(con,sid)['covered'].get(sub_id,0))
    from_balance = min(available_before,covered)
    details=f"{q['month_label']}, {e['group_name']}: {q['total']} ₽ ({q['lessons_paid']} занятий{extra})"
    if q['total']>0 and from_balance==q['total']:
        message=f"Абонемент оплачен средствами с баланса: {details}. Списано с баланса {from_balance} ₽"
    elif q['total']>0 and covered==q['total']:
        message=f"Абонемент оплачен ранее внесёнными средствами: {details}"
    elif from_balance:
        message=f"Оформлен абонемент: {details}. С баланса списано {from_balance} ₽, осталось доплатить {max(0,q['total']-covered)} ₽"
    else:
        message=f"Выставлен счёт на {details}"
    log(con,sid,message,user,'payment')
    from . import tasks
    tasks.schedule_renewal(con, sid)
    return {"id": sub_id, "quote": q}


def payment_quote(con, sub_id):
    """Сумма к оплате на сегодня; все временные изменения расчёта откатываются."""
    from . import program, pricing, money
    con.execute("SAVEPOINT payment_preview")
    try:
        program.reprice(con,sub_id)
        pricing.reprice(con,sub_id)
        row=con.execute("SELECT * FROM subscriptions WHERE id=? AND status!='cancelled'",(sub_id,)).fetchone()
        if not row:
            raise ApiError("Счёт не найден или отменён",404)
        paid=row["price"] if row["status"]=="paid" else max(0,money.balance(con,row["student_id"])["covered"].get(sub_id,0))
        return {"id":sub_id,"price":row["price"],"paid":paid,"total":max(0,row["price"]-paid),"late_fee":row["late_fee"]}
    finally:
        con.execute("ROLLBACK TO payment_preview")
        con.execute("RELEASE payment_preview")


def pay_subscription(con, sub_id: int, user, method: str = "cash", amount=None) -> None:
    from . import money
    try:
        money.pay_subscription(con, sub_id, method, user, int(amount) if amount else None)
    except ValueError as e:
        raise ApiError(str(e))


def cancel_subscription(con, sub_id: int, user, refund: bool = False, comment: str = "") -> None:
    """Внутренняя отмена с сохранением средств; возврат оформляется через subcancel."""
    sub = con.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
    if not sub:
        raise ApiError("Абонемент не найден", 404)
    from . import money
    if sub["status"] == "cancelled":
        raise ApiError("Абонемент уже отменён")
    if refund:
        raise ApiError("Возврат оформляется отдельно с согласованием Юлии и проверкой исходного способа оплаты")
    paid = money.balance(con, sub["student_id"])["covered"].get(sub_id, 0)
    con.execute("UPDATE subscriptions SET status='cancelled' WHERE id=?", (sub_id,))
    con.execute("""UPDATE attendance SET resolved=NULL, resolved_month=NULL
                   WHERE student_id=? AND resolved='credit' AND resolved_month=?
                     AND (? IS NULL OR group_id = (SELECT group_id FROM enrollments WHERE id = ?))""",
                (sub["student_id"], sub["month"], sub["enrollment_id"], sub["enrollment_id"]))
    money.reconcile(con, sub["student_id"])
    log(con, sub["student_id"], f"Счёт на {logic.month_label(sub['month'])} отменён, пропуски возвращены"
        + (" (оплата сохранена в учёте)" if paid else ""), user, "payment")


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
        "branch": "" if grp else _branch_name(con, s.get("branch_id")),
        "score": (__import__("crm.smart", fromlist=["lead_score"]).lead_score(con, s) or {}).get("level") if s["funnel"] == "sales" else None,
    }


def _branch_name(con, bid) -> str:
    if not bid:
        return ""
    r = con.execute("SELECT name FROM branches WHERE id=?", (bid,)).fetchone()
    return r[0] if r else ""


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
                if con.execute("SELECT 1 FROM tasks WHERE student_id=? AND kind='trial_confirm' AND status='done' AND auto_key LIKE ?",
                               (s["id"], f"%:{s['trial_at']}")).fetchone():
                    card["badge"] += " · подтвердили ✓"  # вместо этапа «Напоминание о пробном»
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
            {"key": "single", "title": "Разовые занятия", "cards": []},
            {"key": "unpaid_cur", "title": f"Не оплачен {logic.month_label(cur)}", "cards": []},
            {"key": "renew", "title": f"Нужно продлить на {logic.month_label(nxt)}", "cards": []},
            {"key": "invoiced", "title": f"Счёт на {logic.month_label(nxt)} выставлен. Ждём", "cards": []},
            # Юлия, 03.11: оплаченные не воронка, их видно только в списке действующих клиентов
            {"key": "paid", "title": "Оплачено", "cards": [], "board": False},
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
            from . import singles as _sg
            if s["stage"] == "left":
                key = "left"
            elif _sg.is_single(con, s["id"], cur):
                key = "single"  # разовые занятия: не должники (Юлия, 03.11)
                card["badge"] = "Разовые занятия"
            elif month_state(cur) != "paid":
                key = "unpaid_cur"
                card["color"] = "red"
                gs = unpaid_groups(con, s["id"], cur)
                if ens and len(gs) < len(ens):
                    card["badge"] = "Не оплачена группа " + ", ".join(gs)
            elif month_state(nxt) == "paid":
                key = "paid"
            elif month_state(nxt) != "invoiced" and today().day < RENEW_FROM_DAY:
                key = "paid"  # «Нужно продлить» показываем только с 20 числа (Юлия, 03.11)
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
            card["status"] = key
            card["groups"] = [dict(r) for r in con.execute(
                """SELECT g.id, g.name, b.name AS branch FROM enrollments e JOIN groups g ON g.id=e.group_id
                   JOIN branches b ON b.id=g.branch_id WHERE e.student_id=? AND e.active=1 ORDER BY b.name, g.name""", (s["id"],))] or [
                dict(r) for r in con.execute("""SELECT g.id, g.name, b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id
                                               WHERE g.id=?""", (s["group_id"] or 0,))]
            by[key]["cards"].append(card)
        return {"funnel": "members", "columns": cols, "draggable": False,
                "note": f"Этапы ставятся сами по оплатам. Продлить нужно до {deadline_day} числа."}

    if funnel == "skips":
        cols = [
            {"key": "none", "title": "Есть пропуски", "cards": []},
            {"key": "offered", "title": "Предложили отработку", "cards": []},
            {"key": "booked", "title": "Записан на отработку", "cards": []},
            {"key": "refused", "title": "Отказ от отработки", "cards": []},
        ]
        by = {c["key"]: c for c in cols}
        for s in _rows(con.execute("SELECT * FROM students WHERE funnel='members' AND stage != 'left' AND archived=0 ORDER BY name")):
            # отработки только на гибком абонементе: у кого без переносов, в воронку не берём
            if not con.execute("SELECT 1 FROM enrollments WHERE student_id=? AND active=1 AND tariff='flex'", (s["id"],)).fetchone():
                continue
            c = absence_counters(con, s["id"])
            if not c["available"] and not c["booked"]:
                continue
            card = card_summary(con, s, st)
            card["badge"] = f"Можно отработать: {c['available']}" + (f" · записан: {c['booked']}" if c["booked"] else "")
            key = "booked" if c["booked"] else s["skip_stage"]
            by.get(key, by["none"])["cards"].append(card)
        return {"funnel": "skips", "columns": cols, "draggable": True,
                "note": "Сюда попадают ученики на гибком абонементе с неотработанными пропусками за этот и прошлый месяц."}

    raise ApiError("Неизвестная воронка", 404)


def reinvite_pu(con, s: dict, due: str, comment: str, user) -> dict:
    """«Приглашение на ПУ повторно»: был на пробном, не сложилось. Админ сам выбирает, когда пригласить снова."""
    from . import tasks
    d = (due or "")[:10]
    try:
        day = date.fromisoformat(d)
    except ValueError:
        raise ApiError("Выберите дату, когда пригласить снова", 422, {"need_reinvite_date": True})
    if day < today():
        raise ApiError("Дата уже прошла, выберите сегодня или позже")
    hm = (due or "")[11:16] or "11:00"
    con.execute("""UPDATE tasks SET status='done', done_at=?, done_by=?, result='Перевели в «Приглашение на ПУ повторно»'
                   WHERE student_id=? AND status='open' AND kind IN ('how_was','why_not_bought','close_sale','reinvite_pu','trial_confirm','trial_today','not_marked','no_show')""",
                (now_iso(), (user or {}).get("name", ""), s["id"]))
    con.execute("UPDATE students SET stage='reinvite_pu', stage_changed_at=?, contacted_after_trial=1, priority=0 WHERE id=?", (now_iso(), s["id"]))
    tasks.add_task(con, "reinvite_pu", s["id"], f"{d}T{hm}", auto_key=f"life:reinvite_pu:{s['id']}:{d}", source="life",
                   note=(comment or "").strip())
    log(con, s["id"], f"Приглашение на ПУ повторно: пригласить {day.strftime('%d.%m.%Y')}" + (f". {comment.strip()}" if (comment or "").strip() else ""), user, "stage")
    return {"stage": "reinvite_pu", "due": f"{d}T{hm}"}


def move_card(con, sid: int, funnel: str, stage: str, user, reason: str = "", comment: str = "", due: str = "") -> dict | None:
    s = get_student_row(con, sid)
    if funnel == "sales" and stage in ("trial_booked", "trial_reminder"):
        from . import smart
        smart.require(con, sid, "trial")
    if funnel == "sales":
        if stage not in SALES_LABELS or stage in AUTO_SALES:
            raise ApiError("В этот этап карточка попадает сама")
        if stage == "refused":
            return _refuse(con, s, (reason or "").strip(), (comment or "").strip(), user)
        if stage == "reinvite_pu":
            return reinvite_pu(con, s, due, comment, user)
        if stage == "bought":
            # 01.11: «Купил» засчитывается при первой оплате (lifecycle.on_payment); до неё задача «Принять оплату»
            con.execute("""UPDATE students SET funnel='members', stage='active', stage_changed_at=?, priority=0,
                           sale_by=COALESCE(sale_by, ?) WHERE id=?""", (now_iso(), (user or {}).get("id"), sid))
            if s["group_id"] and not enrollments_of(con, sid):
                con.execute("INSERT INTO enrollments(student_id, group_id, weekdays, tariff, created_at) VALUES (?,?,?,?,?)",
                            (sid, s["group_id"], "", s["tariff"], now_iso()))
                from . import capacity
                capacity.ensure_roster(con, s["group_id"])
            log(con, sid, "Оформляет абонемент: переведён в ученики. Продажа засчитается при первой оплате", user, "stage")
            from . import lifecycle
            lifecycle.on_payment(con, sid)
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
        why = ""
        if stage == "left":  # 01.11: причина ухода обязательна, через 2 месяца задача пригласить вернуться
            from .lifecycle import LEFT_REASONS
            if (reason or "").strip() not in LEFT_REASONS:
                raise ApiError("Выберите, почему ушёл", 422, {"need_left_reason": LEFT_REASONS})
            if reason == "Другое" and not (comment or "").strip():
                raise ApiError("Напишите причину")
            why = reason + (f": {comment.strip()}" if (comment or "").strip() else "")
        if stage == "left":
            from .lifecycle import LEFT_DUE_BY_ADMIN
            if reason in LEFT_DUE_BY_ADMIN:  # болезнь, сезон: админ уточняет и сам выбирает дату (Юлия, 03.11)
                try:
                    if date.fromisoformat((due or "")[:10]) < today():
                        raise ApiError("Дата уже прошла")
                except ValueError:
                    raise ApiError("Выберите дату, когда пригласить вернуться", 422, {"need_left_due": True})
        con.execute("UPDATE students SET stage=?, stage_changed_at=?, refusal_reason=? WHERE id=?",
                    (stage, now_iso(), why if stage == "left" else "", sid))
        log(con, sid, f"Ушёл: {why}" if stage == "left" else "Вернулся", user, "stage")
        if stage == "left":
            from . import lifecycle
            return lifecycle.on_left(con, sid, reason, comment or "", due or "", user)
    else:
        raise ApiError("Неизвестная воронка")


def _refuse(con, s: dict, reason: str, comment: str, user) -> dict:
    """Отказ с причиной. «Думают» — перезвонить через 2 дня, «заболели или уезжают» — «Пригласим позже»."""
    from . import pu, tasks
    if reason not in TRIAL_REFUSALS:
        raise ApiError("Выберите, почему не купили")
    if reason == "Другое" and not comment:
        raise ApiError("Напишите причину")
    text = reason + (f": {comment}" if comment else "")
    sid, now = s["id"], datetime.now()
    who = pu.responsible(con, s["group_id"])
    if reason == REFUSAL_THINKING:
        due = pu.next_day_start(con, now, 2)
        con.execute("UPDATE students SET stage='how_was', contacted_after_trial=1, refusal_reason=?, stage_changed_at=?, priority=0 WHERE id=?",
                    (text, now_iso(), sid))
        tasks.add_task(con, "manual", sid, due, title="Перезвонить: думали после пробного", assignee=who, source="manual",
                       note=text, auto_key=f"think:{sid}:{due:%Y-%m-%d}")
        log(con, sid, f"Думают после пробного. Перезвоним {due:%d.%m}", user, "stage")
        return {"stage": "how_was", "due": due.isoformat(timespec="minutes")}
    if reason == REFUSAL_LATER:
        again = min(pu.next_day_start(con, now, 91), pu.window(now))
        con.execute("UPDATE students SET stage='later', refusal_reason=?, stage_changed_at=?, priority=0 WHERE id=?", (text, now_iso(), sid))
        pu.ensure_task(con, sid, again, now, title=f"Пригласить снова на ПУ (раньше: {text})")
        log(con, sid, f"Пока не могут: {text}. Пригласим снова {again:%d.%m.%Y}", user, "stage")
        return {"stage": "later", "due": again.isoformat(timespec="minutes")}
    con.execute("UPDATE students SET stage='refused', refusal_reason=?, stage_changed_at=?, contacted_after_trial=1, priority=0 WHERE id=?",
                (text, now_iso(), sid))
    log(con, sid, f"Отказ: {text}", user, "stage")
    if reason == "Дорого":  # Юлия, 04.11: дорого, значит ждём акцию, без сроков
        from . import nodeadline
        nodeadline.add(con, sid, "promo", "Отказ после пробного: дорого" + (f". {comment}" if comment else ""), user)
    return {"stage": "refused"}


def student_detail(con, sid: int) -> dict:
    s = get_student_row(con, sid)
    st = settings(con)
    fam = dict(con.execute("SELECT * FROM families WHERE id=?", (s["family_id"],)).fetchone())
    parents = _rows(con.execute("SELECT id, name, phone, messenger, email, relation FROM parents WHERE family_id=?", (s["family_id"],)))
    siblings = _rows(con.execute("SELECT id, name, birth FROM students WHERE family_id=? AND id != ?",
                                 (s["family_id"], sid)))
    grp = dict(con.execute("SELECT * FROM groups WHERE id=?", (s["group_id"],)).fetchone()) if s["group_id"] else None
    subs = _rows(con.execute("""SELECT x.*, COALESCE(g.name,sg.name) AS group_name,v.date AS single_date,COALESCE(e.group_id,v.group_id) AS group_id FROM subscriptions x
                                LEFT JOIN enrollments e ON e.id=x.enrollment_id LEFT JOIN groups g ON g.id=e.group_id
                                LEFT JOIN single_visits v ON v.subscription_id=x.id LEFT JOIN groups sg ON sg.id=v.group_id
                                WHERE x.student_id=? ORDER BY x.month DESC, x.id""", (sid,)))
    vacs = _rows(con.execute("SELECT * FROM vacations WHERE student_id=? ORDER BY start DESC", (sid,)))
    makeups = _rows(con.execute(
        """SELECT m.*, g.name AS group_name, a.date AS absence_date FROM makeups m
           JOIN groups g ON g.id = m.group_id JOIN attendance a ON a.id = m.absence_id
           WHERE m.student_id=? ORDER BY m.date DESC LIMIT 20""", (sid,)))
    hist = _rows(con.execute("SELECT ts, user_name, kind, text FROM history WHERE student_id=? AND NOT EXISTS (SELECT 1 FROM erp_full_history_corrections c WHERE c.history_id=history.id AND c.assigned_student_id IS NULL) ORDER BY id DESC LIMIT 100",
                             (sid,)))
    stage_label = SALES_LABELS.get(s["stage"], s["stage"])
    if s["funnel"] == "sales":
        eff, color, _ = effective_sales_stage(s, st, now())
        stage_label = SALES_LABELS.get(eff, eff)
    else:
        stage_label = "Ушёл" if s["stage"] == "left" else "Занимается"
    cur = logic.month_of(today())
    out = {
        "student": s, "family": fam, "parents": parents, "siblings": siblings, "group": grp,
        "subscriptions": subs, "subscription_refunds": __import__("crm.subcancel",fromlist=["listing"]).listing(con,sid), "subscription_cancellations": __import__("crm.subcancel",fromlist=["cancellations"]).cancellations(con,sid), "vacations": vacs,
        "enrollments": [e | {"age_warn": "" if e["program_id"] else logic.age_warning(s["birth"], e["group_age"], e["branch_name"])} for e in enrollments_of(con, sid)],
        "trial_age_warn": logic.age_warning(s["birth"], grp["age"], _branch_name(con, grp["branch_id"])) if grp and not grp["program_id"] and s["funnel"] == "sales" else "", "makeups": makeups, "history": hist,
        "counters": absence_counters(con, sid) if s["funnel"] == "members" else None,
        "balance": _balance_out(con, sid),
        "trial_hold": __import__("crm.capacity", fromlist=["active_holds"]).active_holds(con, student_id=sid)[:1],
        "tasks": __import__("crm.tasks", fromlist=["for_student"]).for_student(con, sid),
        "unpaid_cur": unpaid_groups(con, sid, cur) if s["funnel"] == "members" and s["stage"] != "left" else [],
        "ledger": __import__("crm.money", fromlist=["ledger"]).ledger(con, sid),
        "family_kids": _rows(con.execute("SELECT id, name FROM students WHERE family_id=? AND id!=? AND archived=0", (s["family_id"], sid))),
        "payments": __import__("crm.money",fromlist=["payments_for_student"]).payments_for_student(con,sid),
        "discounts": _rows(con.execute("SELECT id, name, percent, rule FROM discounts WHERE active=1 AND rule='manual' ORDER BY name")),
        "stage_label": stage_label,
        "next_month": logic.shift_month(cur, 1), "cur_month": cur,
        "program": _program_out(con, sid),
        "pu": __import__("crm.pu", fromlist=["card_info"]).card_info(con, sid),
        "waitlist": __import__("crm.waitlist", fromlist=["for_student"]).for_student(con, sid),
        "nodeadline": __import__("crm.nodeadline", fromlist=["of_student"]).of_student(con, sid),
        "singles": __import__("crm.singles", fromlist=["of_student"]).of_student(con, sid),
        "single_now": __import__("crm.singles", fromlist=["is_single"]).is_single(con, sid),
        # раздел «Посещения» (Юлия, 04.11): отметки за 3 месяца, новые сверху
        "visits": _rows(con.execute("""SELECT a.date, a.status, a.warned, a.note, a.resolved, g.name AS group_name, b.name AS branch
                                        FROM attendance a JOIN groups g ON g.id=a.group_id JOIN branches b ON b.id=g.branch_id
                                        WHERE a.student_id=? AND a.date >= ? ORDER BY a.date DESC LIMIT 60""",
                                     (sid, (today() - timedelta(days=92)).isoformat()))),
    }
    from . import smart
    out["lead_score"] = smart.lead_score(con, s)
    out["next_step"] = smart.next_step(con, s, out)
    out["stage_tip"] = smart.tips(con).get(s["stage"]) if s["funnel"] == "sales" else None
    from . import chains
    out["chains"] = chains.for_student(con, sid)
    return out


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
            raise ApiError("Семья с этим телефоном уже есть, добавьте ребёнка в неё", 409)
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


UPDATABLE = {"name", "birth", "group_id", "branch_id", "tariff", "trial_at", "source", "source_id", "refusal_reason"}


def update_student(con, sid: int, data: dict, user) -> None:
    s = get_student_row(con, sid)
    if data.get("trial_at") and data.get("trial_at") != s.get("trial_at") and s["funnel"] == "sales":
        from . import smart
        smart.require(con, sid, "trial")  # без имени, фамилии, даты рождения, имени и телефона родителя на пробное не записать
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
            v = None if k in ("group_id", "trial_at", "branch_id") else ""
        if s.get(k) != v:
            con.execute(f"UPDATE students SET {k} = ? WHERE id = ?", (v, sid))
            changes.append(k)
    if "source_id" in changes:
        from . import sources as _src
        con.execute("UPDATE students SET source=? WHERE id=?", (_src.label(con, data.get("source_id")), sid))
    if "trial_at" in changes:
        con.execute('UPDATE students SET historical_trial=0,trial_group_id=group_id WHERE id=?',(sid,))
        con.execute("UPDATE students SET contacted_after_trial = 0, trial_came = NULL WHERE id=?", (sid,))
        if data.get("trial_at"):
            con.execute("UPDATE students SET trial_booked_at = COALESCE(trial_booked_at, ?), trial_booked_by = COALESCE(trial_booked_by, ?) WHERE id=?",
                        (now_iso(), (user or {}).get("id"), sid))  # кто записал: 200 ₽ администратору (mypay.py)
        # Юлия, 01.11: при записи на пробное (в будущем) статус меняется сразу, с любого этапа: у записанного не может быть
        # «Отказ», «Не дошёл до ПУ», «Пригласим позже», «Купил», «Как прошло» и «не беспокоить»
        if data.get("trial_at") and str(data["trial_at"])[:10] >= today().isoformat():
            from . import nodeadline
            nodeadline.auto_exit(con, sid, "записан на пробное")
        if data.get("trial_at") and s["funnel"] == "sales" and str(data["trial_at"])[:10] >= today().isoformat() \
                and s["stage"] not in ("trial_booked", "trial_reminder", "on_trial"):
            con.execute("UPDATE students SET stage='trial_booked', stage_changed_at=?, refusal_reason='', dnd=0 WHERE id=?", (now_iso(), sid))
            if s["stage"] in ("missed_trial", "later", "refused"):
                log(con, sid, f"Записан на пробное: этап «{SALES_LABELS.get(s['stage'], s['stage'])}» снят", user, "stage")
                from . import pu as _pu
                _pu._close_family_task_if_empty(con, s["family_id"], "Записан на пробное")
    if ("trial_at" in changes or "group_id" in changes) and s["funnel"] == "sales":
        cur = get_student_row(con, sid)
        if cur["trial_at"] and not cur["group_id"]:  # Юлия, 01.11: при записи на ПУ группа обязательна
            raise ApiError("Выберите группу: без группы на пробное не записать")
        if cur["trial_at"] and cur["group_id"] and cur["trial_at"][:10] >= today().isoformat():
            from . import capacity
            capacity.check_trial_group(con, cur["group_id"])
            capacity.ensure(con, cur["group_id"], cur["trial_at"][:10], "записать на пробное")
            con.execute("DELETE FROM trial_holds WHERE student_id=?", (sid,))  # записали: место больше не держим
            if "trial_at" in changes:
                from . import tasks as _tasks
                _tasks.send_booked(con, sid)  # сразу скрипт родителю: дата, время, место, что взять
    names = {"name": "имя", "birth": "дата рождения", "group_id": "группа", "branch_id": "филиал", "tariff": "тариф",
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
    warn = ". Внимание: на этот месяц абонемент уже выставлен" if paid else ""
    log(con, sid, f"Отпуск {fmt_date(start)}–{fmt_date(end)}{warn}", user, "vacation")


# ---------- журнал ----------

def paid_for_month(con, sid: int, ym: str) -> bool:
    """Оплачено фактически: абонемент на месяц или разовое в этом месяце."""
    return bool(con.execute("""SELECT 1 FROM subscriptions WHERE student_id=? AND status='paid' AND (
                                 (month=? AND COALESCE(kind,'month')='month') OR (kind='single' AND (month=? OR substr(created_at,1,7)=?)))""",
                            (sid, ym, ym, ym)).fetchone())


def journal(con, group_id: int, day: str, paid_only: bool = False, include_unpaid_marks: bool = True) -> dict:
    g = con.execute("SELECT g.*, b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id WHERE g.id=?",
                    (group_id,)).fetchone()
    if not g:
        raise ApiError("Группа не найдена", 404)
    d = date.fromisoformat(day)
    from . import closures, schedule
    closed = closures.is_closed(con, group_id, d)
    has_lesson = d.weekday() in logic.parse_weekdays(schedule.on(con, g, d)["weekdays"]) and not closed  # с учётом версий расписания
    rows, hidden = [], []
    if g["is_makeup"]:
        for m in con.execute(
                """SELECT m.id AS makeup_id, m.status, s.id, s.name, a.date AS absence_date,
                          (SELECT g2.name FROM groups g2 WHERE g2.id = s.group_id) AS home_group
                   FROM makeups m JOIN students s ON s.id=m.student_id JOIN attendance a ON a.id=m.absence_id
                   WHERE m.group_id=? AND m.date=? AND m.status != 'cancelled' ORDER BY s.name""",
                (group_id, day)):
            rows.append(dict(m))
    else:
        # переведённые (transfer.py): в старой группе до даты перевода, в новой с неё
        for s in con.execute("""SELECT s.id, s.name, s.birth, e.weekdays FROM enrollments e JOIN students s ON s.id = e.student_id
                                WHERE e.group_id=? AND (e.active=1 OR e.end_date > ?) AND (e.start_date = '' OR e.start_date <= ?)
                                  AND s.funnel='members' AND s.stage != 'left' AND s.archived=0
                                ORDER BY s.name""", (group_id, day, day)).fetchall():
            if s["weekdays"] and d.weekday() not in logic.parse_weekdays(s["weekdays"]):
                continue
            a = con.execute("SELECT status, resolved, warned, note FROM attendance WHERE student_id=? AND date=? AND group_id=?",
                            (s["id"], day, group_id)).fetchone()
            from . import subscriptiondates
            if not a and (subscriptiondates.excluded(con, s["id"], group_id, day)
                          or subscriptiondates.outside_period(con, s["id"], group_id, day)):
                continue
            vac = logic.in_vacation(d, vacations_of(con, s["id"]))
            # в журнале только те, у кого оплачено фактически: абонемент на месяц или разовое (Юлия, 04.11).
            # Уже отмеченных не прячем, чтобы не потерять отметку
            paid_month = con.execute("""SELECT 1 FROM subscriptions WHERE student_id=? AND month=? AND status='paid'
                                        AND COALESCE(kind,'month')='month'""", (s["id"], day[:7])).fetchone()
            paid_single = not paid_month and con.execute(
                """SELECT 1 FROM subscriptions WHERE student_id=? AND kind='single' AND status='paid'
                   AND (month=? OR substr(created_at,1,7)=?)""", (s["id"], day[:7], day[:7])).fetchone()
            if paid_only and not paid_month and not paid_single and not a:
                hidden.append({"id": s["id"], "name": s["name"]})
                continue
            unpaid = not paid_month and not paid_single
            rows.append({"id": s["id"], "name": s["name"], "birth": s["birth"] or "", "unpaid": unpaid,
                         "tag": "paid" if paid_month else "single" if paid_single else "unpaid", "status": "vacation" if vac else (a["status"] if a else None),
                         "resolved": a["resolved"] if a else None, "warned": bool(a and a["warned"]) and not vac,
                         "note": a["note"] if a else ""})
        # разовые занятия на эту дату (Юлия, 03.11): в журнале с пометкой «разовое», отмечаются как все
        from . import singles as _sg
        have = {r["id"] for r in rows}
        for v in _sg.rows_for_day(con, group_id, day):
            if v["id"] in have:
                continue
            a = con.execute("SELECT status, resolved, warned, note FROM attendance WHERE student_id=? AND date=? AND group_id=?",
                            (v["id"], day, group_id)).fetchone()
            paid1 = con.execute("""SELECT 1 FROM subscriptions WHERE student_id=? AND kind='single' AND status='paid'
                                   AND (month=? OR substr(created_at,1,7)=?)""", (v["id"], day[:7], day[:7])).fetchone()
            rows.append({"id": v["id"], "name": v["name"], "birth": v["birth"] or "", "tag": "single", "single_id": v["single_id"],
                         "unpaid": not paid1, "status": a["status"] if a else None, "resolved": a["resolved"] if a else None,
                         "warned": bool(a and a["warned"]), "note": a["note"] if a else ""})
        # отработки в обычной группе (на место того, кто предупредил)
        for m in con.execute(
                """SELECT m.id AS makeup_id, m.status AS mk_status, s.id, s.name, a.date AS absence_date,
                          (SELECT g2.name FROM groups g2 WHERE g2.id = s.group_id) AS home_group
                   FROM makeups m JOIN students s ON s.id=m.student_id JOIN attendance a ON a.id=m.absence_id
                   WHERE m.group_id=? AND m.date=? AND m.status != 'cancelled' ORDER BY s.name""", (group_id, day)):
            rows.append(dict(m) | {"status": "makeup"})
        # пробные на этот день
        for s in con.execute("""SELECT id, name, birth, trial_at FROM students WHERE COALESCE(trial_group_id,group_id)=?
                                AND ((funnel='sales' AND archived=0) OR historical_trial=1) AND substr(trial_at,1,10)=?""", (group_id, day)):
            came = con.execute("SELECT trial_came FROM students WHERE id=?", (s["id"],)).fetchone()[0]
            rows=[r for r in rows if r['id']!=s['id']]
            rows.append({"id": s["id"], "name": s["name"], "birth": s["birth"] or "", "status": "trial", "trial_at": s["trial_at"], "trial_came": came})
    # Факт посещения принадлежит группе и дате, а не её сегодняшнему составу.
    marks = {a["student_id"]: dict(a) for a in con.execute(
        """SELECT a.*,s.name,s.birth FROM attendance a JOIN students s ON s.id=a.student_id
           WHERE a.group_id=? AND a.date=? ORDER BY s.name,a.id""", (group_id,day))}
    makeup_rows = {r["id"]:r for r in rows if r.get("makeup_id")}
    marked_rows, seen_marks = [], set()
    for row in rows:
        mark = marks.get(row["id"])
        if mark:
            if row["id"] in seen_marks:
                continue
            seen_marks.add(row["id"])
            row = row | makeup_rows.get(row["id"], {}) | {"status": mark["status"], "resolved": mark["resolved"], "warned": bool(mark["warned"]),
                         "note": mark["note"], "attendance_id": mark["id"], "group_id": group_id, "date": day}
        marked_rows.append(row)
    for sid, mark in marks.items():
        if sid in seen_marks:
            continue
        paid = paid_for_month(con,sid,day[:7])
        marked_rows.append({"id":sid,"name":mark["name"],"birth":mark["birth"] or "", "unpaid":not paid,
                            "tag":"paid" if paid else "unpaid", "status":mark["status"],"resolved":mark["resolved"],
                            "warned":bool(mark["warned"]),"note":mark["note"],"attendance_id":mark["id"],
                            "group_id":group_id,"date":day,"historical_attendance":True})
    historical_trials = con.execute("""SELECT v.id AS historical_trial_id,s.id,s.name,s.birth,v.visit_at AS trial_at,
                                      v.came AS trial_came FROM erp_trial_visits v JOIN students s ON s.id=v.student_id
                                      WHERE v.group_id=? AND v.visit_at>=? AND v.visit_at<?""",
                                   (group_id, day, (d+timedelta(days=1)).isoformat())).fetchall()
    trial_students = {r['id'] for r in historical_trials}
    marked_rows = [r for r in marked_rows if r['id'] not in trial_students
                   or r.get('attendance_id') or r.get('makeup_id') or r.get('single_id')]
    marked_rows.extend(dict(r) | {'status': 'trial'} for r in historical_trials)
    rows = marked_rows
    hidden = [r for r in hidden if r["id"] not in marks]
    # Дневной рабочий список — только оплаченные занятия; историю не удаляем.
    if paid_only and not include_unpaid_marks:
        excluded = [r for r in rows if r.get("unpaid") and not r.get("makeup_id") and r.get("status") != "trial"]
        excluded_ids = {r["id"] for r in excluded}
        rows = [r for r in rows if r["id"] not in excluded_ids or r.get("status") == "trial"]
        hidden = list({r["id"]: {"id": r["id"], "name": r["name"]} for r in hidden + excluded}.values())
    return {"group": dict(g), "date": day, "has_lesson": has_lesson, "closed": closed, "rows": rows, "hidden_unpaid": hidden}


def journal_month(con, group_id: int, ym: str) -> dict:
    """Журнал группы за месяц (Юлия, 04.11): сколько детей в каждую дату и сколько в группе на обычном абонементе."""
    start, end = logic.month_bounds(ym)
    marked_dates = {r[0] for r in con.execute(
        "SELECT DISTINCT date FROM attendance WHERE group_id=? AND date BETWEEN ? AND ?",
        (group_id,start.isoformat(),end.isoformat()))}
    marked_dates.update(r[0] for r in con.execute("SELECT substr(trial_at,1,10) FROM students WHERE historical_trial=1 AND trial_group_id=? AND substr(trial_at,1,10) BETWEEN ? AND ?",(group_id,start.isoformat(),end.isoformat())))
    marked_dates.update(r[0] for r in con.execute(
        "SELECT substr(visit_at,1,10) FROM erp_trial_visits WHERE group_id=? AND visit_at>=? AND visit_at<?",
        (group_id, start.isoformat(), (end+timedelta(days=1)).isoformat())))
    days, attendance, d = [], [], start
    while d <= end:
        j = journal(con, group_id, d.isoformat(), paid_only=True)
        if j["has_lesson"] or j["closed"] or d.isoformat() in marked_dates:
            rows = j["rows"]
            attendance.extend({"attendance_id":r["attendance_id"],"student_id":r["id"],"name":r["name"],
                               "group_id":group_id,"date":d.isoformat(),"status":r["status"],
                               "historical_attendance":bool(r.get("historical_attendance"))}
                              for r in rows if r.get("attendance_id"))
            kids = [r for r in rows if r.get("status") not in ("trial", "makeup") and "makeup_id" not in r]
            come = [r for r in kids if r.get("status") != "vacation" and not r.get("warned")]
            days.append({"date": d.isoformat(), "closed": j["closed"], "list": len(come),
                         "present": sum(r.get("status") == "present" for r in kids),
                         "absent": sum(r.get("status") == "absent" for r in kids),
                         "vacation": sum(r.get("status") == "vacation" for r in kids),
                         "trials": sum(r.get("status") == "trial" for r in rows),
                         "trials_came": sum(r.get("status") == "trial" and r.get("trial_came") == 1 for r in rows),
                         "makeups": sum(r.get("status") == "makeup" or "makeup_id" in r for r in rows)})
        d += timedelta(days=1)
    # кто в группе в этом месяце и на чём занимается
    kids = con.execute("""SELECT DISTINCT s.id FROM enrollments e JOIN students s ON s.id=e.student_id
                          WHERE e.group_id=? AND (e.active=1 OR e.end_date > ?) AND (e.start_date='' OR e.start_date <= ?)
                            AND s.funnel='members' AND s.stage != 'left' AND s.archived=0""",
                       (group_id, start.isoformat(), end.isoformat())).fetchall()
    subs = {"paid": 0, "invoiced": 0, "single": 0, "none": 0}
    for (sid,) in kids:
        r = con.execute("""SELECT status FROM subscriptions WHERE student_id=? AND month=? AND COALESCE(kind,'month')='month'
                           AND status!='cancelled' ORDER BY status='paid' DESC LIMIT 1""", (sid, ym)).fetchone()
        if r:
            subs["paid" if r[0] == "paid" else "invoiced"] += 1
        elif con.execute("""SELECT 1 FROM subscriptions WHERE student_id=? AND kind='single' AND status!='cancelled'
                            AND substr(created_at,1,7)=?""", (sid, ym)).fetchone():
            subs["single"] += 1
        else:
            subs["none"] += 1
    return {"month": ym, "days": days, "kids": len(kids), "subs": subs, "attendance": attendance}


def clear_trial_mark(con, sid: int, user) -> None:
    """Снять ошибочную отметку «пришёл на пробное» (только «пришёл»: «не пришёл» запускает задачи, его меняют на «пришёл»)."""
    s = get_student_row(con, sid)
    if s["trial_came"] == 1:
        con.execute("UPDATE students SET trial_came=NULL WHERE id=?", (sid,))
        log(con, sid, "Снята отметка «пришёл на пробное»", user, "attendance")


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
    from . import subscriptiondates
    if status != "clear" and subscriptiondates.excluded(con, sid, group_id, day):
        raise ApiError("Эта дата исключена из абонемента: сначала оформите занятие отдельно")
    if not a and status != "clear" and subscriptiondates.outside_period(con, sid, group_id, day):
        raise ApiError("Дата занятия вне срока действия абонемента")
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
    if status == "present":
        from . import lifecycle
        lifecycle.on_unpaid_visit(con, sid, group_id, day)
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
        "trial_refusals": TRIAL_REFUSALS,
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
