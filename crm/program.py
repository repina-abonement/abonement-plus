"""Направления со своими правилами продаж. Первое: «Сборная ПРО» (8+).

Группы направления: занятия в «Арктике» (lesson_kind='arena') и ОФП в «Небе» (lesson_kind='ofp', на выбор чт или сб).
Ребёнок ходит в группу Арктики (там его тариф и счёт) и, если тариф «Арктика + ОФП», ещё в одну группу ОФП
(эта запись не оплачивается отдельно: tariff='included').

Правила (Юлия, 28.10.2026):
- абонемент только на полный месяц, по реальным датам расписания (праздники исключены); разовых нет;
- новичок с середины месяца платит за занятия с ближайшего (после ПУ, если было), без надбавки;
- цена по прайсу оплачиваемого месяца; при оплате позже 25 числа предыдущего месяца +5% (с декабря 2026);
- скидка только «второму и следующему ребёнку» 20% и только при оплате до 25 числа; скидки не суммируются;
- переносов и перерасчётов нет; тариф и день ОФП меняются только со следующего месяца;
- в четверг отработок нет; на тарифе «Только Арктика» ОФП не засчитывается как отработка.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from . import logic
from .services import ApiError

SCHEMA = """
CREATE TABLE IF NOT EXISTS programs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    coach_pct REAL NOT NULL DEFAULT 20,
    royalty_pct REAL NOT NULL DEFAULT 6,
    acquiring_pct REAL NOT NULL DEFAULT 2,
    deadline_day INTEGER NOT NULL DEFAULT 25,
    arena_rent INTEGER NOT NULL DEFAULT 0,      -- аренда за одну тренировку в Арктике
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS program_prices (
    id INTEGER PRIMARY KEY,
    program_id INTEGER NOT NULL REFERENCES programs(id),
    valid_from TEXT NOT NULL,                   -- 'YYYY-MM': для абонементов с этого месяца
    arena INTEGER NOT NULL,                     -- Арктика в тарифе «Арктика + ОФП», за занятие
    ofp INTEGER NOT NULL,                       -- ОФП, за занятие
    arena_only INTEGER NOT NULL,                -- тариф «Только Арктика», за занятие
    late_pct REAL NOT NULL DEFAULT 0,           -- надбавка при оплате после срока
    created_at TEXT NOT NULL,
    UNIQUE(program_id, valid_from)
);
"""
TARIFFS = {"pkg": "Арктика + ОФП", "arena_only": "Только Арктика"}


def _now() -> str:
    return datetime.now().isoformat(timespec="minutes")


def init(con):
    con.executescript(SCHEMA)
    gcols = {r[1] for r in con.execute("PRAGMA table_info(groups)")}
    for col in ("program_id INTEGER", "lesson_kind TEXT NOT NULL DEFAULT ''", "shared_with INTEGER"):
        if col.split()[0] not in gcols:
            con.execute(f"ALTER TABLE groups ADD COLUMN {col}")
    ecols = {r[1] for r in con.execute("PRAGMA table_info(enrollments)")}
    if "next_plan" not in ecols:
        con.execute("ALTER TABLE enrollments ADD COLUMN next_plan TEXT NOT NULL DEFAULT ''")
    pid = ensure_sbornaya(con)
    setup_sbornaya_groups(con, pid)


def ensure_sbornaya(con) -> int:
    r = con.execute("SELECT id FROM programs WHERE slug='sbornaya'").fetchone()
    if r:
        return r[0]
    pid = con.execute("""INSERT INTO programs(name, slug, coach_pct, royalty_pct, acquiring_pct, deadline_day, arena_rent)
                         VALUES ('Сборная ПРО','sbornaya',20,6,2,25,8500)""").lastrowid
    for vf, a, o, ao, late in (("2026-11", 1500, 1200, 1700, 0), ("2026-12", 1600, 1200, 1800, 5)):
        con.execute("""INSERT INTO program_prices(program_id, valid_from, arena, ofp, arena_only, late_pct, created_at)
                       VALUES (?,?,?,?,?,?,?)""", (pid, vf, a, o, ao, late, _now()))
    return pid


def setup_sbornaya_groups(con, pid: int):
    """Один раз раскладывает текущее расписание сборной по правилам 28.10.2026 (если такие группы есть)."""
    if con.execute("SELECT 1 FROM settings WHERE key='sbornaya_setup_v1'").fetchone():
        return
    arena = con.execute("""SELECT g.* FROM groups g JOIN branches b ON b.id=g.branch_id
                           WHERE g.active=1 AND g.name LIKE '%Сборная%' AND b.name LIKE '%Арктик%' ORDER BY g.id LIMIT 1""").fetchone()
    nebo = con.execute("SELECT id FROM branches WHERE name LIKE '%Небо%' ORDER BY id LIMIT 1").fetchone()
    if not arena or not nebo:
        return
    con.execute("UPDATE branches SET name='СК «Западный» (Чоботовская)' WHERE name='Чоботовская'")
    con.execute("UPDATE groups SET program_id=?, lesson_kind='arena', name='Сборная Арктика Вт/Вс' WHERE id=?", (pid, arena["id"]))
    coach = arena["coach_id"]

    def mk(name, wd, t):
        r = con.execute("SELECT id FROM groups WHERE name=? AND branch_id=?", (name, nebo[0])).fetchone()
        if r:
            return r[0]
        return con.execute("""INSERT INTO groups(branch_id, name, weekdays, time, age, is_makeup, coach_id, capacity, program_id,
                              lesson_kind) VALUES (?,?,?,?,?,0,?,10,?,'ofp')""", (nebo[0], name, wd, t, "8+", coach, pid)).lastrowid
    thu = mk("Сборная ОФП Чт 20:30", "3", "20:30")
    sat = mk("Сборная ОФП Сб 17:00", "5", "17:00")
    pup = con.execute("""SELECT id FROM groups WHERE branch_id=? AND active=1 AND weekdays='5' AND time='17:00' AND id!=?
                         AND name LIKE '%ОФП%'""", (nebo[0], sat)).fetchone()
    if pup:
        con.execute("UPDATE groups SET shared_with=? WHERE id=?", (pup[0], sat))
    old18 = con.execute("SELECT id FROM groups WHERE name LIKE 'ОФП Сб 18:00%сборн%'").fetchone()
    moved = set()
    if old18:
        for e in con.execute("SELECT id, student_id FROM enrollments WHERE group_id=? AND active=1", (old18[0],)).fetchall():
            con.execute("UPDATE enrollments SET group_id=?, tariff='included', weekdays='' WHERE id=?", (sat, e[0]))
            moved.add(e[1])
            if not con.execute("SELECT 1 FROM enrollments WHERE student_id=? AND group_id=? AND active=1", (e[1], arena["id"])).fetchone():
                con.execute("INSERT INTO enrollments(student_id, group_id, weekdays, tariff, created_at) VALUES (?,?,?,?,?)",
                            (e[1], arena["id"], "", "pkg", _now()))
        con.execute("UPDATE groups SET active=0 WHERE id=?", (old18[0],))
    con.execute("UPDATE groups SET active=0 WHERE name LIKE '%Папы%' AND branch_id=(SELECT id FROM branches WHERE name LIKE '%Арктик%' LIMIT 1)")
    for e in con.execute("SELECT id, student_id FROM enrollments WHERE group_id=? AND active=1", (arena["id"],)).fetchall():
        con.execute("UPDATE enrollments SET tariff='pkg' WHERE id=?", (e[0],))
        if e[1] not in moved:
            con.execute("INSERT INTO enrollments(student_id, group_id, weekdays, tariff, created_at) VALUES (?,?,?,?,?)",
                        (e[1], thu, "", "included", _now()))
    con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('sbornaya_setup_v1','1')")


# ---------------- справочники ----------------

def program_of_group(con, gid: int) -> dict | None:
    r = con.execute("SELECT p.* FROM groups g JOIN programs p ON p.id=g.program_id WHERE g.id=?", (gid,)).fetchone()
    return dict(r) if r else None


def prices_for(con, pid: int, ym: str) -> dict:
    r = con.execute("""SELECT * FROM program_prices WHERE program_id=? AND valid_from<=? ORDER BY valid_from DESC LIMIT 1""",
                    (pid, ym)).fetchone() or con.execute(
        "SELECT * FROM program_prices WHERE program_id=? ORDER BY valid_from LIMIT 1", (pid,)).fetchone()
    if not r:
        raise ApiError("У направления не заведены цены")
    return dict(r)


def groups(con, pid: int, kind: str | None = None) -> list[dict]:
    q = "SELECT g.*, b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id WHERE g.program_id=? AND g.active=1"
    args = [pid]
    if kind:
        q += " AND g.lesson_kind=?"
        args.append(kind)
    return [dict(r) for r in con.execute(q + " ORDER BY g.lesson_kind, g.weekdays", args)]


def plan_of(con, sid: int, pid: int) -> dict | None:
    """Текущий тариф ребёнка в направлении: запись в Арктике, тариф, группа ОФП, отложенная смена."""
    a = con.execute("""SELECT e.* FROM enrollments e JOIN groups g ON g.id=e.group_id
                       WHERE e.student_id=? AND e.active=1 AND g.program_id=? AND g.lesson_kind='arena' ORDER BY e.id LIMIT 1""",
                    (sid, pid)).fetchone()
    if not a:
        return None
    o = con.execute("""SELECT e.group_id FROM enrollments e JOIN groups g ON g.id=e.group_id
                       WHERE e.student_id=? AND e.active=1 AND g.program_id=? AND g.lesson_kind='ofp' ORDER BY e.id LIMIT 1""",
                    (sid, pid)).fetchone()
    tariff = a["tariff"] if a["tariff"] in TARIFFS else "pkg"
    nxt = json.loads(a["next_plan"]) if a["next_plan"] else None
    return {"enrollment_id": a["id"], "group_id": a["group_id"], "tariff": tariff if o or tariff == "arena_only" else "arena_only",
            "ofp_group_id": o[0] if o else None, "next": nxt}


def _dates(con, gid: int, ym: str) -> list[date]:
    from . import closures
    g = con.execute("SELECT weekdays FROM groups WHERE id=?", (gid,)).fetchone()
    a, b = logic.month_bounds(ym)
    closed = closures.closed_dates(con, gid, a, b)
    return [d for d in logic.lesson_dates(logic.parse_weekdays(g[0]), ym) if d.isoformat() not in closed]


def _deadline(ym: str, day: int) -> date:
    y, m = logic.parse_month(logic.shift_month(ym, -1))
    return date(y, m, day)


def is_newcomer(con, sid: int, pid: int, ym: str) -> bool:
    return not con.execute("""SELECT 1 FROM subscriptions x JOIN enrollments e ON e.id=x.enrollment_id JOIN groups g ON g.id=e.group_id
                              WHERE x.student_id=? AND g.program_id=? AND x.status='paid' AND x.month<?""", (sid, pid, ym)).fetchone()


# ---------------- расчёт абонемента ----------------

def quote(con, sid: int, ym: str, e: dict, discount_id=None, on: date | None = None) -> dict:
    from . import money
    on = on or date.today()
    g = con.execute("SELECT * FROM groups WHERE id=?", (e["group_id"],)).fetchone()
    prog = program_of_group(con, g["id"])
    if g["lesson_kind"] == "ofp":
        raise ApiError("ОФП сборной оплачивается в общем абонементе: выставьте счёт по группе Арктики")
    plan = plan_of(con, sid, prog["id"])
    tariff, ofp_gid = plan["tariff"], plan["ofp_group_id"]
    nxt = plan.get("next")
    if nxt and ym >= nxt.get("from", "9999"):
        tariff, ofp_gid = nxt["tariff"], nxt.get("ofp_group_id")
    pr = prices_for(con, prog["id"], ym)
    arena_dates = _dates(con, g["id"], ym)
    ofp_dates = _dates(con, ofp_gid, ym) if tariff == "pkg" and ofp_gid else []
    cur_month = logic.month_of(on)
    newcomer = is_newcomer(con, sid, prog["id"], ym) and ym == cur_month
    if newcomer:  # с ближайшей тренировки (после ПУ, если оно было в этом месяце)
        s = con.execute("SELECT trial_at FROM students WHERE id=?", (sid,)).fetchone()
        start = on
        if s and s["trial_at"] and s["trial_at"][:10] >= on.isoformat():
            start = date.fromisoformat(s["trial_at"][:10]) + timedelta(days=1)
        arena_dates = [d for d in arena_dates if d >= start]
        ofp_dates = [d for d in ofp_dates if d >= start]
    deadline = _deadline(ym, prog["deadline_day"])
    late = on > deadline and not newcomer
    pa = pr["arena_only"] if tariff == "arena_only" else pr["arena"]
    po = pr["ofp"]

    def bump(p, pct):
        return round(p * (1 + pct / 100))
    la, lo = bump(pa, pr["late_pct"]), bump(po, pr["late_pct"])
    base = len(arena_dates) * pa + len(ofp_dates) * po          # при оплате до срока
    late_total = len(arena_dates) * la + len(ofp_dates) * lo     # при оплате после срока
    use_a, use_o = (la, lo) if late else (pa, po)
    gross = len(arena_dates) * use_a + len(ofp_dates) * use_o
    # скидки: только «второму ребёнку», только при оплате до срока, одна
    disc, off, disc_note = None, 0, ""
    if discount_id:
        r = con.execute("SELECT * FROM discounts WHERE id=? AND active=1", (int(discount_id),)).fetchone()
        if r and r["rule"] != "sibling":
            disc_note = "В сборной действует только скидка второму ребёнку"
    sib = next((d for d in money.auto_discounts(con, sid, on) if d["rule"] == "sibling"), None)
    if sib and not late:
        disc = sib
        off = round(base * int(sib["percent"]) / 100)
    elif sib and late:
        disc_note = "Оплата после срока: скидка второму ребёнку не действует"
    total = gross - off
    ofp_g = con.execute("SELECT name, weekdays FROM groups WHERE id=?", (ofp_gid,)).fetchone() if ofp_gid else None
    lines = [{"label": f"Арктика{' (только Арктика)' if tariff == 'arena_only' else ''}", "n": len(arena_dates), "price": use_a,
              "sum": len(arena_dates) * use_a}]
    if ofp_dates:
        lines.append({"label": f"ОФП, {logic.WEEKDAY_NAMES[int(ofp_g['weekdays'])].lower() if ofp_g and ofp_g['weekdays'].isdigit() else ''}".rstrip(", "),
                      "n": len(ofp_dates), "price": use_o, "sum": len(ofp_dates) * use_o})
    on_time_total = base - (round(base * int(sib["percent"]) / 100) if sib else 0)
    return {
        "month": ym, "month_label": logic.month_label(ym), "per_lesson": use_a, "lessons_total": len(arena_dates) + len(ofp_dates),
        "vacation_lessons": 0, "lessons_paid": len(arena_dates) + len(ofp_dates), "base": base, "credit_lessons": 0, "credit": 0,
        "credit_absence_ids": [], "credit_absence_dates": [], "total": total, "late_fee": (gross - base) if late else 0,
        "discount": {"id": disc["id"], "name": disc["name"], "percent": disc["percent"], "amount": off, "auto": True} if disc else None,
        "tariff": tariff, "group": e.get("group_name") or g["name"], "enrollment_id": e["id"], "days_label": "",
        "dates": [d.isoformat() for d in sorted(arena_dates + ofp_dates)], "vacation_dates": [],
        "program": {"id": prog["id"], "name": prog["name"], "tariff": tariff, "tariff_label": TARIFFS[tariff],
                    "ofp_group": ofp_g["name"] if ofp_g else "", "lines": lines, "late": late, "newcomer": newcomer,
                    "deadline": deadline.isoformat(), "on_time_total": on_time_total, "late_total": late_total,
                    "note": disc_note, "waitlist": waiting_count(con, g["id"])},
    }


def waiting_count(con, gid: int) -> int:
    try:
        return con.execute("SELECT COUNT(*) FROM waitlist WHERE group_id=? AND status IN ('queued','offered')", (gid,)).fetchone()[0]
    except Exception:  # noqa: BLE001 — лист ожидания ещё не создан
        return 0


def reprice(con, sub_id: int, on: date | None = None) -> bool:
    """Перед оплатой: цена сборной зависит от даты оплаты (+5% и без скидки после срока). Пересчитываем неоплаченный счёт."""
    from . import money, services as sv
    s = con.execute("""SELECT x.*, e.group_id FROM subscriptions x JOIN enrollments e ON e.id=x.enrollment_id
                       WHERE x.id=? AND x.status='invoiced' AND x.kind='month'""", (sub_id,)).fetchone()
    if not s or not program_of_group(con, s["group_id"]):
        return False
    if money.balance(con, s["student_id"])["covered"].get(sub_id, 0):
        return False  # уже частично оплачен — не трогаем
    e = next((x for x in sv.enrollments_of(con, s["student_id"], active_only=False) if x["id"] == s["enrollment_id"]), None)
    q = quote(con, s["student_id"], s["month"], e, on=on)
    d = q["discount"] or {}
    if q["total"] == s["price"] and q["late_fee"] == s["late_fee"]:
        return False
    con.execute("""UPDATE subscriptions SET price=?, late_fee=?, lessons=?, per_lesson=?, discount_name=?, discount_amount=?
                   WHERE id=?""", (q["total"], q["late_fee"], q["lessons_paid"], q["per_lesson"], d.get("name", ""),
                                   d.get("amount", 0), sub_id))
    sv.log(con, s["student_id"], f"Счёт сборной на {q['month_label']} пересчитан по дате оплаты: {q['total']} ₽"
           + (" (+5% и без скидки, оплата после срока)" if q["program"]["late"] else ""), None, "payment")
    return True


# ---------------- тариф и запись ----------------

def set_plan(con, sid: int, pid: int, tariff: str, ofp_gid: int | None, user: dict) -> dict:
    """Записать в сборную или сменить тариф. Новичку — сразу; тем, кто уже занимается, — со следующего месяца."""
    from . import capacity, services as sv
    if tariff not in TARIFFS:
        raise ApiError("Выберите тариф")
    if tariff == "pkg":
        if not ofp_gid or not con.execute("SELECT 1 FROM groups WHERE id=? AND program_id=? AND lesson_kind='ofp' AND active=1",
                                          (ofp_gid, pid)).fetchone():
            raise ApiError("Выберите день ОФП")
    else:
        ofp_gid = None
    plan = plan_of(con, sid, pid)
    cur = logic.month_of(date.today())
    has_paid = con.execute("""SELECT 1 FROM subscriptions x JOIN enrollments e ON e.id=x.enrollment_id JOIN groups g ON g.id=e.group_id
                              WHERE x.student_id=? AND g.program_id=? AND x.status='paid'""", (sid, pid)).fetchone()
    if plan and has_paid:
        nxt = {"from": logic.shift_month(cur, 1), "tariff": tariff, "ofp_group_id": ofp_gid}
        con.execute("UPDATE enrollments SET next_plan=? WHERE id=?", (json.dumps(nxt), plan["enrollment_id"]))
        sv.log(con, sid, f"Сборная: со следующего месяца тариф «{TARIFFS[tariff]}»", user, "system")
        return {"applied": "next", "from": nxt["from"]}
    _apply(con, sid, pid, tariff, ofp_gid, plan)
    if ofp_gid:
        capacity.ensure_roster(con, ofp_gid)
    sv.sync_primary(con, sid)
    sv.log(con, sid, f"Сборная: тариф «{TARIFFS[tariff]}»", user, "system")
    return {"applied": "now"}


def _apply(con, sid, pid, tariff, ofp_gid, plan):
    now = _now()
    if not plan:
        arena = groups(con, pid, "arena")
        if not arena:
            raise ApiError("У сборной нет группы в Арктике")
        con.execute("INSERT INTO enrollments(student_id, group_id, weekdays, tariff, created_at) VALUES (?,?,?,?,?)",
                    (sid, arena[0]["id"], "", tariff, now))
    else:
        con.execute("UPDATE enrollments SET tariff=?, next_plan='' WHERE id=?", (tariff, plan["enrollment_id"]))
    cur_ofp = plan["ofp_group_id"] if plan else None
    if cur_ofp != ofp_gid:
        if cur_ofp:
            con.execute("UPDATE enrollments SET active=0 WHERE student_id=? AND group_id=? AND active=1", (sid, cur_ofp))
        if ofp_gid:
            con.execute("INSERT INTO enrollments(student_id, group_id, weekdays, tariff, created_at) VALUES (?,?,?,?,?)",
                        (sid, ofp_gid, "", "included", now))


def apply_pending(con, today: date | None = None) -> int:
    """С 1 числа включаем отложенные смены тарифа."""
    today = today or date.today()
    cur = logic.month_of(today)
    n = 0
    for e in con.execute("""SELECT e.id, e.student_id, e.next_plan, g.program_id FROM enrollments e JOIN groups g ON g.id=e.group_id
                            WHERE e.active=1 AND e.next_plan!='' AND g.program_id IS NOT NULL""").fetchall():
        nxt = json.loads(e["next_plan"])
        if nxt.get("from", "9999") <= cur:
            _apply(con, e["student_id"], e["program_id"], nxt["tariff"], nxt.get("ofp_group_id"), plan_of(con, e["student_id"], e["program_id"]))
            n += 1
    return n


def student_program(con, sid: int) -> dict | None:
    for p in con.execute("SELECT * FROM programs WHERE active=1").fetchall():
        plan = plan_of(con, sid, p["id"])
        if plan:
            return {"program": dict(p), "plan": plan}
    return None


def check_makeup(con, sid: int, group_id: int, day: str):
    """Правила отработок для сборной."""
    sp = student_program(con, sid)
    if not sp:
        return
    d = date.fromisoformat(day)
    if d.weekday() == 3:
        raise ApiError("Сборная: в четверг отработок нет")
    g = con.execute("SELECT name, lesson_kind FROM groups WHERE id=?", (group_id,)).fetchone()
    if sp["plan"]["tariff"] == "arena_only" and g and (g["lesson_kind"] == "ofp" or "ОФП" in g["name"]):
        raise ApiError("На тарифе «Только Арктика» ОФП не засчитывается как отработка")


def only_program(con, sid: int) -> bool:
    rows = con.execute("""SELECT g.program_id FROM enrollments e JOIN groups g ON g.id=e.group_id WHERE e.student_id=? AND e.active=1""",
                       (sid,)).fetchall()
    return bool(rows) and all(r[0] for r in rows)


def price_notice(con, today: date | None = None) -> int:
    """С 15 по 20 число: если со следующего месяца меняются цены направления — задача администратору уведомить родителей."""
    from . import tasks
    today = today or date.today()
    if not 15 <= today.day <= 20:
        return 0
    cur, nxt = logic.month_of(today), logic.shift_month(logic.month_of(today), 1)
    n = 0
    for p in con.execute("SELECT * FROM programs WHERE active=1").fetchall():
        a, b = prices_for(con, p["id"], cur), prices_for(con, p["id"], nxt)
        if (a["arena"], a["ofp"], a["arena_only"], a["late_pct"]) != (b["arena"], b["ofp"], b["arena_only"], b["late_pct"]):
            due = datetime.combine(today.replace(day=20), datetime.min.time()).replace(hour=18)
            n += bool(tasks.add_task(con, "manual", None, due, auto_key=f"price_notice:{p['id']}:{nxt}",
                                     title=f"{p['name']}: уведомить родителей о ценах на {logic.month_label(nxt)} до 20 числа",
                                     note=f"Арктика {b['arena']} ₽, ОФП {b['ofp']} ₽, только Арктика {b['arena_only']} ₽; "
                                          f"после 25 числа +{b['late_pct']:g}%"))
    return n


def info(con) -> list[dict]:
    out = []
    for p in con.execute("SELECT * FROM programs ORDER BY id").fetchall():
        out.append(dict(p) | {"prices": [dict(r) for r in con.execute(
            "SELECT * FROM program_prices WHERE program_id=? ORDER BY valid_from", (p["id"],))],
            "groups": groups(con, p["id"])})
    return out


def save_price(con, pid: int, data: dict):
    vf = (data.get("valid_from") or "")[:7]
    try:
        logic.parse_month(vf)
    except Exception:
        raise ApiError("Укажите месяц, с которого действуют цены")
    vals = [int(data.get(k) or 0) for k in ("arena", "ofp", "arena_only")]
    if min(vals) <= 0:
        raise ApiError("Заполните все цены")
    con.execute("""INSERT INTO program_prices(program_id, valid_from, arena, ofp, arena_only, late_pct, created_at)
                   VALUES (?,?,?,?,?,?,?) ON CONFLICT(program_id, valid_from) DO UPDATE SET arena=excluded.arena, ofp=excluded.ofp,
                   arena_only=excluded.arena_only, late_pct=excluded.late_pct""",
                (pid, vf, *vals, float(data.get("late_pct") or 0), _now()))


def save_program(con, pid: int, data: dict):
    for k in ("coach_pct", "royalty_pct", "acquiring_pct", "deadline_day", "arena_rent"):
        if k in data and str(data[k]).strip() != "":
            con.execute(f"UPDATE programs SET {k}=? WHERE id=?", (float(data[k]), pid))
