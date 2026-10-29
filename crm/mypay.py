"""«Моя зарплата»: сколько сотрудник заработал с 1 числа по сегодня, прогноз на весь месяц и цель.

Каждый видит только себя (руководитель может открыть любого). Части складываются, если у человека их несколько:
- тренер: по salary.payroll (доля выручки группы за каждую тренировку, отработки, сборная, штрафы);
  «на сегодня» - тренировки до сегодняшнего дня включительно, «весь месяц» - все тренировки месяца;
- администратор: смены по графику (pay_admin_shift за смену), запись на пробное (pay_admin_trial) и покупка
  абонемента после пробного (pay_admin_sale): засчитываются тому, кто записал и кто перевёл в «Купил»;
- промоутер: контакты за месяц × pay_promoter_contact. Контакты пока в Google-таблице, число вписывает сам промоутер.
Цель: сотрудник вводит, сколько хочет получить за месяц, и видит, сколько ещё нужно:
тренеру - детей в его группы (и где свободные места), промоутеру - контактов (и сколько в день), администратору - записей.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from . import logic, perms

SCHEMA = """
CREATE TABLE IF NOT EXISTS pay_goals (
    user_id INTEGER NOT NULL, month TEXT NOT NULL, amount INTEGER NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, month)
);
CREATE TABLE IF NOT EXISTS promoter_contacts (
    user_id INTEGER NOT NULL, month TEXT NOT NULL, contacts INTEGER NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, month)
);
"""
RATES = {"pay_admin_shift": "1000", "pay_admin_trial": "200", "pay_admin_sale": "200", "pay_promoter_contact": "300"}


def init(con):
    con.executescript(SCHEMA)
    for k, v in RATES.items():
        con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
    cols = {r[1] for r in con.execute("PRAGMA table_info(students)")}
    for col in ("trial_booked_by", "converted_by"):  # кто записал на пробное и кто продал абонемент
        if col not in cols:
            con.execute(f"ALTER TABLE students ADD COLUMN {col} INTEGER")


def _rate(con, key: str) -> int:
    r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return int(float(r[0])) if r and str(r[0]).replace(".", "", 1).isdigit() else int(RATES[key])


def _now() -> str:
    return datetime.now().isoformat(timespec="minutes")


def _days(ym: str):
    a, b = logic.month_bounds(ym)
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def coach_part(con, uid: int, ym: str, today: date) -> dict:
    from . import salary
    p = salary.payroll(con, ym, None, uid)
    me = next((x for x in p["coaches"] if x["id"] == uid), None)
    lines = [x for x in (me or {}).get("lines", []) if x["group"] != "Опоздание"]  # штрафы отдельно: только подтверждённые
    t = today.isoformat()
    a, b = logic.month_bounds(ym)
    share = min(1, max(0, ((today - a).days + 1) / ((b - a).days + 1)))  # сборная за месяц целиком: по дням
    now = sum(x["amount"] for x in lines if x["date"] and x["date"] <= t) + sum(x["amount"] * share for x in lines if not x["date"])
    fines = (me or {}).get("fines", 0)
    return {"now": round(now - fines), "month": round((me or {}).get("total", 0) - fines), "fines": fines,
            "lessons_done": sum(1 for x in lines if x["date"] and x["date"] <= t), "lessons": sum(1 for x in lines if x["date"])}


def admin_part(con, uid: int, ym: str, today: date) -> dict:
    shift_days = set()
    for s in con.execute("SELECT weekdays FROM shifts WHERE user_id=? AND active=1", (uid,)):
        wd = logic.parse_weekdays(s["weekdays"])
        shift_days |= {d for d in _days(ym) if d.weekday() in wd}
    done = sum(1 for d in shift_days if d <= today)
    a, b = logic.month_bounds(ym)
    rng = (a.isoformat(), b.isoformat() + "T23:59")
    trials = con.execute("SELECT COUNT(*) FROM students WHERE trial_booked_by=? AND trial_booked_at BETWEEN ? AND ?", (uid, *rng)).fetchone()[0]
    sales = con.execute("SELECT COUNT(*) FROM students WHERE converted_by=? AND converted_at BETWEEN ? AND ?", (uid, *rng)).fetchone()[0]
    rs, rt, rl = _rate(con, "pay_admin_shift"), _rate(con, "pay_admin_trial"), _rate(con, "pay_admin_sale")
    now = done * rs + trials * rt + sales * rl
    elapsed = max(1, min((today - a).days + 1, (b - a).days + 1))
    left = max(0, (b - today).days)
    pace = (trials * rt + sales * rl) / elapsed  # прогноз: остальные смены по графику, записи и продажи в том же темпе
    return {"now": now, "month": len(shift_days) * rs + trials * rt + sales * rl + round(pace * left),
            "shifts_done": done, "shifts": len(shift_days), "trials": trials, "sales": sales,
            "rates": {"shift": rs, "trial": rt, "sale": rl}}


def promoter_part(con, uid: int, ym: str) -> dict:
    r = con.execute("SELECT contacts FROM promoter_contacts WHERE user_id=? AND month=?", (uid, ym)).fetchone()
    n, rate = (r[0] if r else 0), _rate(con, "pay_promoter_contact")
    return {"now": n * rate, "month": n * rate, "contacts": n, "rate": rate}


def coach_room(con, uid: int, ym: str) -> dict:
    """Сколько приносит тренеру один ребёнок в месяц в каждой его группе и где есть свободные места."""
    from . import pricing, salary, staff, capacity
    price = pricing.prices_for(con, ym)["fixed"]
    pct = {k: staff.pay_share(con, *v) for k, v in {"main": (True, False), "assistant": (False, False), "solo": (True, True)}.items()}
    out = []
    for g in con.execute("""SELECT * FROM groups WHERE active=1 AND is_makeup=0 AND program_id IS NULL
                            AND (coach_id=? OR assistant_id=?)""", (uid, uid)):
        share = pct["assistant"] if g["assistant_id"] == uid else (pct["main"] if g["assistant_id"] else pct["solo"])
        lessons = len(salary._dates(con, g, ym))
        kids = con.execute("""SELECT COUNT(DISTINCT e.student_id) FROM enrollments e JOIN students s ON s.id=e.student_id
                              WHERE e.group_id=? AND e.active=1 AND s.funnel='members' AND s.stage!='left' AND s.archived=0""",
                           (g["id"],)).fetchone()[0]
        lim = capacity.limit_for(con, g["id"]) or g["capacity"] or 0
        out.append({"group": g["name"], "per_kid": round(price * lessons * share / 100), "kids": kids,
                    "free": max(0, lim - kids) if lim else None})
    return {"groups": out, "per_kid": round(sum(x["per_kid"] for x in out) / len(out)) if out else 0}


def view(con, user: dict, ym: str | None = None, today: date | None = None) -> dict:
    today = today or date.today()
    ym = ym or logic.month_of(today)
    if ym != logic.month_of(today):  # прошлый месяц целиком, будущий - только прогноз
        today = logic.month_bounds(ym)[1] if ym < logic.month_of(today) else logic.month_bounds(ym)[0] - timedelta(days=1)
    uid, rs = user["id"], perms.roles_of(user)
    parts = {}
    if "coach" in rs:
        parts["coach"] = coach_part(con, uid, ym, today)
    if rs & {"senior", "operator"}:
        parts["admin"] = admin_part(con, uid, ym, today)
    if "promoter" in rs:
        parts["promoter"] = promoter_part(con, uid, ym)
    now = sum(p["now"] for p in parts.values())
    month = sum(p["month"] for p in parts.values())
    a, b = logic.month_bounds(ym)
    g = con.execute("SELECT amount FROM pay_goals WHERE user_id=? AND month=?", (uid, ym)).fetchone()
    goal = g[0] if g else 0
    days_left = max(0, (b - today).days)
    out = {"month": ym, "month_label": logic.month_label(ym), "today": today.isoformat(), "days": (b - a).days + 1,
           "day": min((b - a).days + 1, max(0, (today - a).days + 1)), "now": now, "forecast": month, "parts": parts,
           "goal": goal, "days_left": days_left}
    if goal:
        gap = max(0, goal - month)
        need = {}
        if "coach" in parts:
            room = coach_room(con, uid, ym)
            need["kids"] = math.ceil(gap / room["per_kid"]) if gap and room["per_kid"] else 0
            need["groups"] = room["groups"]
        if "promoter" in parts:
            rate = parts["promoter"]["rate"]
            left = max(0, goal - now)  # промоутеру считаем от собранного: контакты не прогнозируем
            need["contacts"] = math.ceil(left / rate) if rate else 0
            need["per_day"] = math.ceil(need["contacts"] / days_left) if days_left and need["contacts"] else need["contacts"]
        if "admin" in parts:
            r = parts["admin"]["rates"]["trial"]
            need["trials"] = math.ceil(gap / r) if gap and r else 0
        out["need"] = need | {"gap": gap}
    return out


def set_goal(con, uid: int, ym: str, amount) -> None:
    amount = max(0, int(amount or 0))
    con.execute("""INSERT INTO pay_goals(user_id, month, amount, updated_at) VALUES (?,?,?,?)
                   ON CONFLICT(user_id, month) DO UPDATE SET amount=excluded.amount, updated_at=excluded.updated_at""",
                (uid, ym, amount, _now()))


def set_contacts(con, uid: int, ym: str, n) -> None:
    n = max(0, int(n or 0))
    con.execute("""INSERT INTO promoter_contacts(user_id, month, contacts, updated_at) VALUES (?,?,?,?)
                   ON CONFLICT(user_id, month) DO UPDATE SET contacts=excluded.contacts, updated_at=excluded.updated_at""",
                (uid, ym, n, _now()))
