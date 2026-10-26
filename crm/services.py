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
    ("how_was", "Как прошло пробное?"),
    ("bought", "Купил абонемент"),
    ("refused", "Отказ"),
]
AUTO_SALES = {"on_trial", "trial_done"}
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


def unresolved_absences_in_month(con, sid: int, ym: str) -> list[dict]:
    start, end = logic.month_bounds(ym)
    return _rows(con.execute(
        """SELECT a.id, a.date FROM attendance a
           WHERE a.student_id = ? AND a.status = 'absent' AND a.resolved IS NULL
             AND a.date BETWEEN ? AND ?
             AND NOT EXISTS (SELECT 1 FROM makeups m WHERE m.absence_id = a.id AND m.status = 'booked')
           ORDER BY a.date""", (sid, start.isoformat(), end.isoformat())))


def book_makeup(con, sid: int, group_id: int, day: str, absence_id: int | None, user) -> dict:
    g = con.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
    if not g or not g["is_makeup"]:
        raise ApiError("Это не группа отработок")
    d = date.fromisoformat(day)
    if d < today():
        raise ApiError("Дата отработки уже прошла")
    if d.weekday() not in logic.parse_weekdays(g["weekdays"]):
        raise ApiError("В этот день у группы отработок нет занятия")
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
    if status == "done":
        con.execute("UPDATE attendance SET resolved = 'makeup' WHERE id = ?", (m["absence_id"],))
    elif m["status"] == "done":
        con.execute("UPDATE attendance SET resolved = NULL WHERE id = ? AND resolved = 'makeup'", (m["absence_id"],))
    labels = {"done": "Отработал", "missed": "Не пришёл на отработку", "cancelled": "Отработка отменена",
              "booked": "Отработка снова в записи"}
    log(con, m["student_id"], f"{labels[status]}: {fmt_date(m['date'])}", user, "makeup")


# ---------- абонементы ----------

def quote_for(con, sid: int, ym: str) -> dict:
    s = get_student_row(con, sid)
    if not s["group_id"]:
        raise ApiError("У ученика не выбрана группа")
    g = con.execute("SELECT * FROM groups WHERE id = ?", (s["group_id"],)).fetchone()
    st = settings(con)
    prices = {"fixed": st["price_fixed"], "flex": st["price_flex"]}
    prev_abs = unresolved_absences_in_month(con, sid, logic.shift_month(ym, -1))
    q = logic.quote(weekdays=logic.parse_weekdays(g["weekdays"]), ym=ym, tariff=s["tariff"], prices=prices,
                    vacations=vacations_of(con, sid), prev_unresolved_absences=len(prev_abs))
    q["credit_absence_ids"] = [a["id"] for a in prev_abs[:q["credit_lessons"]]]
    q["credit_absence_dates"] = [a["date"] for a in prev_abs[:q["credit_lessons"]]]
    total, fee = logic.apply_late_fee(q["total"], enabled=st["late_fee_enabled"] == "1",
                                      percent=float(st["late_fee_percent"]), today=today(), target_month=ym,
                                      deadline_day=int(st["renewal_deadline_day"]))
    q["late_fee"] = fee
    q["total"] = total
    q["tariff"] = s["tariff"]
    q["group"] = g["name"]
    return q


def create_invoice(con, sid: int, ym: str, user) -> dict:
    existing = con.execute("SELECT * FROM subscriptions WHERE student_id=? AND month=? AND status != 'cancelled'",
                           (sid, ym)).fetchone()
    if existing:
        raise ApiError(f"На {logic.month_label(ym)} уже есть абонемент")
    q = quote_for(con, sid, ym)
    con.execute("DELETE FROM subscriptions WHERE student_id=? AND month=? AND status='cancelled'", (sid, ym))
    sub_id = con.execute(
        """INSERT INTO subscriptions(student_id, month, tariff, lessons, per_lesson, credit_lessons, late_fee,
           price, status, created_at) VALUES (?,?,?,?,?,?,?,?,'invoiced',?)""",
        (sid, ym, q["tariff"], q["lessons_paid"], q["per_lesson"], q["credit_lessons"], q["late_fee"],
         q["total"], now_iso())).lastrowid
    for aid in q["credit_absence_ids"]:
        con.execute("UPDATE attendance SET resolved='credit', resolved_month=? WHERE id=?", (ym, aid))
    extra = f", перерасчёт {q['credit_lessons']} пропуск(а) −{q['credit']} ₽" if q["credit_lessons"] else ""
    log(con, sid, f"Выставлен счёт на {q['month_label']}: {q['total']} ₽ ({q['lessons_paid']} занятий{extra})",
        user, "payment")
    return {"id": sub_id, "quote": q}


def pay_subscription(con, sub_id: int, user) -> None:
    sub = con.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
    if not sub:
        raise ApiError("Абонемент не найден", 404)
    if sub["status"] != "invoiced":
        raise ApiError("Этот счёт уже оплачен или отменён")
    con.execute("UPDATE subscriptions SET status='paid', paid_at=? WHERE id=?", (now_iso(), sub_id))
    log(con, sub["student_id"], f"Оплачен абонемент на {logic.month_label(sub['month'])}: {sub['price']} ₽",
        user, "payment")


def cancel_subscription(con, sub_id: int, user) -> None:
    sub = con.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
    if not sub:
        raise ApiError("Абонемент не найден", 404)
    con.execute("UPDATE subscriptions SET status='cancelled' WHERE id=?", (sub_id,))
    con.execute("""UPDATE attendance SET resolved=NULL, resolved_month=NULL
                   WHERE student_id=? AND resolved='credit' AND resolved_month=?""",
                (sub["student_id"], sub["month"]))
    log(con, sub["student_id"], f"Счёт на {logic.month_label(sub['month'])} отменён, пропуски возвращены",
        user, "payment")


# ---------- карточки ----------

def fmt_date(iso: str) -> str:
    d = date.fromisoformat(iso[:10])
    return f"{d.day:02d}.{d.month:02d}"


def card_summary(con, s: dict, st: dict) -> dict:
    fam = con.execute("SELECT name FROM families WHERE id=?", (s["family_id"],)).fetchone()
    parent = con.execute("SELECT name, phone FROM parents WHERE family_id=? ORDER BY id LIMIT 1",
                         (s["family_id"],)).fetchone()
    grp = con.execute("SELECT name FROM groups WHERE id=?", (s["group_id"],)).fetchone() if s["group_id"] else None
    sib = con.execute("SELECT COUNT(*) FROM students WHERE family_id=? AND id != ?",
                      (s["family_id"], s["id"])).fetchone()[0]
    return {
        "id": s["id"], "name": s["name"], "family": fam["name"] if fam else "",
        "parent": parent["name"] if parent else "", "phone": parent["phone"] if parent else "",
        "group": grp["name"] if grp else "", "tariff": s["tariff"], "siblings": sib,
        "stage": s["stage"], "stage_changed_at": s["stage_changed_at"], "trial_at": s["trial_at"],
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
        cols = {k: {"key": k, "title": t, "cards": [], "auto": k in AUTO_SALES} for k, t in SALES_STAGES}
        for s in _rows(con.execute("SELECT * FROM students WHERE funnel='sales' ORDER BY stage_changed_at DESC")):
            stage, color, mins = effective_sales_stage(s, st, ref)
            card = card_summary(con, s, st)
            card["color"] = color
            card["minutes_since_end"] = mins
            if stage == "trial_done" and mins is not None:
                card["badge"] = f"{mins} мин после пробного"
            elif s["trial_at"] and stage in ("trial_booked", "trial_reminder", "on_trial"):
                card["badge"] = "Пробное " + datetime.fromisoformat(s["trial_at"]).strftime("%d.%m %H:%M")
            cols.get(stage, cols["lead"])["cards"].append(card)
        return {"funnel": "sales", "columns": list(cols.values()), "draggable": True}

    if funnel == "members":
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
        for s in _rows(con.execute("SELECT * FROM students WHERE funnel='members' ORDER BY name")):
            card = card_summary(con, s, st)
            subs = {r["month"]: r["status"] for r in con.execute(
                "SELECT month, status FROM subscriptions WHERE student_id=? AND status != 'cancelled'", (s["id"],))}
            if s["stage"] == "left":
                key = "left"
            elif subs.get(cur) != "paid":
                key = "unpaid_cur"
                card["color"] = "red"
            elif subs.get(nxt) == "paid":
                key = "paid_next"
            elif subs.get(nxt) == "invoiced":
                key = "invoiced"
                card["color"] = "yellow" if late else None
            else:
                key = "renew"
                card["color"] = "red" if late else None
                card["badge"] = f"Продлить до {deadline_day}.{today().month:02d}"
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
        for s in _rows(con.execute("SELECT * FROM students WHERE funnel='members' AND stage != 'left' ORDER BY name")):
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
            con.execute("UPDATE students SET funnel='members', stage='active', stage_changed_at=? WHERE id=?",
                        (now_iso(), sid))
            log(con, sid, "Купил абонемент — переведён в учеников", user, "stage")
            return
        contacted = 1 if stage in ("how_was", "refused") else s["contacted_after_trial"]
        con.execute("UPDATE students SET stage=?, stage_changed_at=?, contacted_after_trial=? WHERE id=?",
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
    subs = _rows(con.execute("SELECT * FROM subscriptions WHERE student_id=? ORDER BY month DESC", (sid,)))
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
        "subscriptions": subs, "vacations": vacs, "makeups": makeups, "history": hist,
        "counters": absence_counters(con, sid) if s["funnel"] == "members" else None,
        "stage_label": stage_label,
        "next_month": logic.shift_month(cur, 1), "cur_month": cur,
    }


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
    sid = con.execute(
        """INSERT INTO students(family_id, name, birth, group_id, tariff, funnel, stage, stage_changed_at,
           source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (family_id, name, data.get("birth") or "", data.get("group_id") or None, tariff, funnel, stage,
         now_iso(), data.get("source") or "", now_iso())).lastrowid
    log(con, sid, "Карточка создана", user, "system")
    audit(con, user, "student.create", str(sid))
    return sid


UPDATABLE = {"name", "birth", "group_id", "tariff", "trial_at", "source", "refusal_reason"}


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
    if "trial_at" in changes:
        con.execute("UPDATE students SET contacted_after_trial = 0 WHERE id=?", (sid,))
        if data.get("trial_at") and s["stage"] in ("lead", "no_answer"):
            con.execute("UPDATE students SET stage='trial_booked', stage_changed_at=? WHERE id=?", (now_iso(), sid))
    names = {"name": "имя", "birth": "дата рождения", "group_id": "группа", "tariff": "тариф",
             "trial_at": "пробное", "source": "источник", "refusal_reason": "причина отказа"}
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
    has_lesson = d.weekday() in logic.parse_weekdays(g["weekdays"])
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
        for s in con.execute("""SELECT id, name FROM students WHERE group_id=? AND funnel='members'
                                AND stage != 'left' ORDER BY name""", (group_id,)):
            a = con.execute("SELECT status, resolved FROM attendance WHERE student_id=? AND date=? AND group_id=?",
                            (s["id"], day, group_id)).fetchone()
            vac = logic.in_vacation(d, vacations_of(con, s["id"]))
            rows.append({"id": s["id"], "name": s["name"], "status": "vacation" if vac else (a["status"] if a else None),
                         "resolved": a["resolved"] if a else None})
        # пробные на этот день
        for s in con.execute("""SELECT id, name, trial_at FROM students WHERE funnel='sales' AND group_id=?
                                AND substr(trial_at,1,10)=?""", (group_id, day)):
            rows.append({"id": s["id"], "name": s["name"], "status": "trial", "trial_at": s["trial_at"]})
    return {"group": dict(g), "date": day, "has_lesson": has_lesson, "rows": rows}


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
        con.execute("UPDATE attendance SET status=? WHERE id=?", (status, a["id"]))
    else:
        con.execute("INSERT INTO attendance(student_id, group_id, date, status) VALUES (?,?,?,?)",
                    (sid, group_id, day, status))
    if status == "absent":
        if s["skip_stage"] == "refused":
            con.execute("UPDATE students SET skip_stage='none' WHERE id=?", (sid,))
        log(con, sid, f"Пропуск {fmt_date(day)}", user, "attendance")


def meta(con) -> dict:
    return {
        "branches": _rows(con.execute("SELECT * FROM branches ORDER BY id")),
        "groups": _rows(con.execute("""SELECT g.*, b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id
                                      WHERE g.active=1 ORDER BY g.is_makeup, b.id, g.name""")),
        "sales_stages": [{"key": k, "title": t, "auto": k in AUTO_SALES} for k, t in SALES_STAGES],
        "settings": settings(con),
        "today": today().isoformat(),
    }


def search_students(con, q: str) -> list[dict]:
    st = settings(con)
    like = f"%{q.strip().lower()}%"
    p = normalize_phone(q)
    rows = _rows(con.execute(
        """SELECT DISTINCT s.* FROM students s JOIN families f ON f.id=s.family_id
           LEFT JOIN parents p ON p.family_id=f.id
           WHERE ulower(s.name) LIKE ? OR ulower(f.name) LIKE ? OR ulower(p.name) LIKE ?
              OR (? != '' AND p.phone_norm LIKE ?)
           ORDER BY s.name LIMIT 100""", (like, like, like, p, f"%{p}%")))
    return [card_summary(con, s, st) | {"funnel": s["funnel"]} for s in rows]
