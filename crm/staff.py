"""Тренеры на филиале: отметка прихода (опоздание = штраф) и кто вёл каждую тренировку (главный, помощник или один).

Приход: тренер должен быть на филиале за 30 минут до первой тренировки своей смены. Нажимает «Я на филиале»,
CRM запоминает время и, если телефон разрешил, место. Опоздал — штраф, его подтверждает или снимает проверяющий.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import comms, logic

SCHEMA = """
CREATE TABLE IF NOT EXISTS checkins (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    branch_id INTEGER NOT NULL REFERENCES branches(id),
    date TEXT NOT NULL,
    at TEXT NOT NULL,                     -- когда отметился, ЧЧ:ММ
    due TEXT NOT NULL,                    -- к которому часу надо было прийти
    late_min INTEGER NOT NULL DEFAULT 0,
    fine INTEGER NOT NULL DEFAULT 0,
    fine_status TEXT NOT NULL DEFAULT '', -- '' нет штрафа | pending | confirmed | cancelled
    lat REAL, lng REAL, accuracy REAL,
    reviewed_by TEXT NOT NULL DEFAULT '',
    review_note TEXT NOT NULL DEFAULT '',
    UNIQUE(user_id, branch_id, date)
);
CREATE TABLE IF NOT EXISTS lesson_staff (
    id INTEGER PRIMARY KEY,
    group_id INTEGER NOT NULL REFERENCES groups(id),
    date TEXT NOT NULL,
    main_id INTEGER NOT NULL REFERENCES users(id),
    assistant_id INTEGER REFERENCES users(id),   -- пусто = тренер был один
    set_by TEXT NOT NULL DEFAULT '',
    set_at TEXT NOT NULL,
    UNIQUE(group_id, date)
);
"""

DEFAULTS = {"checkin_before_min": "30", "late_fine": "500", "checkin_reviewer": "",
            "pay_main_pct": "15.5", "pay_assistant_pct": "10", "pay_solo_pct": "20", "pay_makeup_fixed": "1500"}


class StaffError(ValueError):
    pass


def init(con):
    con.executescript(SCHEMA)


def setting(con, key: str) -> str:
    return comms.get(con, key) or DEFAULTS[key]


def _now() -> datetime:
    return datetime.now()


def _hm(t: str) -> int:
    return int(t[:2]) * 60 + int(t[3:5]) if t and len(t) >= 5 else 0


def _fmt(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def planned(con, d: date) -> list[dict]:
    """Кто сегодня должен вести каждую тренировку: штатные с учётом замен и отсутствий."""
    from . import schedule
    out = []
    ds = d.isoformat()
    for g in con.execute("""SELECT g.*, b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id
                            WHERE g.active=1 AND b.active=1"""):
        from . import closures, schedule
        g = schedule.on(con, g, d)  # расписание, которое действует в этот день
        if d.weekday() not in logic.parse_weekdays(g["weekdays"]):
            continue
        if closures.is_closed(con, g["id"], d):
            continue
        people = {}
        for role in ("main", "assistant"):
            sub = schedule.sub_on(con, g["id"], ds, role)
            own = g["coach_id"] if role == "main" else g["assistant_id"]
            if sub:
                people[role] = sub["coach_id"]
            elif own and not schedule.absent_on(con, own, ds):
                people[role] = own
        out.append({"group_id": g["id"], "group": g["name"], "branch_id": g["branch_id"], "branch": g["branch"],
                    "time": logic.group_time(g, d.weekday()), "main": people.get("main"), "assistant": people.get("assistant")})
    return out


def expected(con, d: date) -> list[dict]:
    """Смены на день: тренер + филиал + к которому часу прийти (первая тренировка минус 30 минут)."""
    before = int(setting(con, "checkin_before_min") or 30)
    shifts = {}
    for p in planned(con, d):
        for role in ("main", "assistant"):
            uid = p[role]
            if not uid or not p["time"]:
                continue
            k = (uid, p["branch_id"])
            if k not in shifts or p["time"] < shifts[k]["first"]:
                shifts[k] = {"user_id": uid, "branch_id": p["branch_id"], "branch": p["branch"], "first": p["time"]}
    for s in shifts.values():
        s["due"] = _fmt(max(0, _hm(s["first"]) - before))
    return sorted(shifts.values(), key=lambda x: (x["due"], x["branch"]))


def checkin(con, user: dict, branch_id: int, lat=None, lng=None, accuracy=None, now: datetime | None = None) -> dict:
    now = now or _now()
    d = now.date()
    mine = [s for s in expected(con, d) if s["user_id"] == user["id"]]
    s = next((x for x in mine if x["branch_id"] == branch_id), None)
    if not s:
        raise StaffError("Сегодня у вас нет тренировок в этом филиале")
    if con.execute("SELECT 1 FROM checkins WHERE user_id=? AND branch_id=? AND date=?", (user["id"], branch_id, d.isoformat())).fetchone():
        raise StaffError("Приход уже отмечен")
    if _hm(now.strftime("%H:%M")) < _hm(s["due"]) - 180:
        raise StaffError("Отметить приход можно не раньше чем за 3 часа до смены")
    at = now.strftime("%H:%M")
    late = max(0, _hm(at) - _hm(s["due"]))
    fine = int(setting(con, "late_fine") or 0) if late else 0
    con.execute("""INSERT INTO checkins(user_id, branch_id, date, at, due, late_min, fine, fine_status, lat, lng, accuracy)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (user["id"], branch_id, d.isoformat(), at, s["due"], late, fine, "pending" if fine else "",
                 lat, lng, accuracy))
    return {"at": at, "due": s["due"], "late_min": late, "fine": fine}


def my_day(con, user_id: int, d: date | None = None) -> list[dict]:
    d = d or _now().date()
    out = []
    for s in expected(con, d):
        if s["user_id"] != user_id:
            continue
        c = con.execute("SELECT * FROM checkins WHERE user_id=? AND branch_id=? AND date=?",
                        (user_id, s["branch_id"], d.isoformat())).fetchone()
        out.append(s | {"checkin": dict(c) if c else None})
    return out


def day_report(con, d: date | None = None, now: datetime | None = None) -> dict:
    """Для проверяющего: кто пришёл вовремя, кто опоздал, кого ещё нет."""
    now = now or _now()
    d = d or now.date()
    names = {r["id"]: r["name"] for r in con.execute("SELECT id, name FROM users")}
    rows = []
    for s in expected(con, d):
        c = con.execute("SELECT * FROM checkins WHERE user_id=? AND branch_id=? AND date=?",
                        (s["user_id"], s["branch_id"], d.isoformat())).fetchone()
        if c:
            state = "late" if c["late_min"] else "ok"
        else:
            state = "absent" if d < now.date() or (d == now.date() and now.strftime("%H:%M") > s["due"]) else "wait"
        rows.append(s | {"name": names.get(s["user_id"], ""), "checkin": dict(c) if c else None, "state": state})
    pending = [dict(r) | {"name": names.get(r["user_id"], ""),
                          "branch": (con.execute("SELECT name FROM branches WHERE id=?", (r["branch_id"],)).fetchone() or [""])[0]}
               for r in con.execute("SELECT * FROM checkins WHERE fine_status='pending' AND date<? ORDER BY date", (d.isoformat(),))]
    return {"date": d.isoformat(), "rows": rows, "pending_old": pending,
            "reviewer": setting(con, "checkin_reviewer"), "fine": int(setting(con, "late_fine") or 0)}


def review(con, cid: int, action: str, user: dict, note: str = ""):
    if action not in ("confirm", "cancel"):
        raise StaffError("Неизвестное действие")
    r = con.execute("SELECT fine FROM checkins WHERE id=?", (cid,)).fetchone()
    if not r or not r["fine"]:
        raise StaffError("Штрафа нет")
    con.execute("UPDATE checkins SET fine_status=?, reviewed_by=?, review_note=? WHERE id=?",
                ("confirmed" if action == "confirm" else "cancelled", user.get("name", ""), note.strip()[:200], cid))


def fines(con, month: str) -> list[dict]:
    """Подтверждённые и ожидающие штрафы за месяц — для будущего расчёта зарплаты."""
    return [dict(r) for r in con.execute(
        """SELECT c.*, u.name FROM checkins c JOIN users u ON u.id=c.user_id
           WHERE substr(c.date,1,7)=? AND c.fine>0 AND c.fine_status!='cancelled' ORDER BY c.date""", (month,))]


# ---------------- кто вёл тренировку ----------------

def lesson_staff(con, group_id: int, d: str) -> dict:
    """Что записано про тренировку, а если ещё не записано — кто по плану."""
    r = con.execute("SELECT * FROM lesson_staff WHERE group_id=? AND date=?", (group_id, d)).fetchone()
    names = lambda i: (con.execute("SELECT name FROM users WHERE id=?", (i,)).fetchone() or [""])[0] if i else ""
    if r:
        return {"set": True, "main_id": r["main_id"], "assistant_id": r["assistant_id"], "solo": r["assistant_id"] is None,
                "main": names(r["main_id"]), "assistant": names(r["assistant_id"]), "set_by": r["set_by"]}
    p = next((x for x in planned(con, date.fromisoformat(d)) if x["group_id"] == group_id), None)
    if not p:
        return {"set": False, "main_id": None, "assistant_id": None, "solo": False, "main": "", "assistant": ""}
    return {"set": False, "main_id": p["main"], "assistant_id": p["assistant"], "solo": not p["assistant"],
            "main": names(p["main"]), "assistant": names(p["assistant"])}


def set_lesson_staff(con, group_id: int, d: str, main_id: int, assistant_id: int | None, user: dict):
    try:
        day = date.fromisoformat(d)
    except ValueError:
        raise StaffError("Неверная дата")
    if day > _now().date():
        raise StaffError("Тренировка ещё не прошла")
    g = con.execute("SELECT weekdays FROM groups WHERE id=?", (group_id,)).fetchone()
    if not g or day.weekday() not in logic.parse_weekdays(g["weekdays"]):
        raise StaffError("В этот день у группы нет тренировки")
    ids = [main_id] + ([assistant_id] if assistant_id else [])
    for i in ids:
        if not con.execute("SELECT 1 FROM users WHERE id=? AND role='coach'", (i,)).fetchone():
            raise StaffError("Выберите тренера")
    if assistant_id and assistant_id == main_id:
        raise StaffError("Главный и помощник — один и тот же человек")
    if user["role"] == "coach" and user["id"] not in ids:
        raise StaffError("Вы можете отметить только тренировку, которую вели сами")
    con.execute("""INSERT INTO lesson_staff(group_id, date, main_id, assistant_id, set_by, set_at) VALUES (?,?,?,?,?,?)
                   ON CONFLICT(group_id, date) DO UPDATE SET main_id=excluded.main_id, assistant_id=excluded.assistant_id,
                   set_by=excluded.set_by, set_at=excluded.set_at""",
                (group_id, d, main_id, assistant_id or None, user.get("name", ""), _now().isoformat(timespec="minutes")))


def pay_share(con, main: bool, solo: bool) -> float:
    """Процент тренера от тренировки: один — 20%, вдвоём главный 15,5%, помощник 10%."""
    key = "pay_solo_pct" if solo else ("pay_main_pct" if main else "pay_assistant_pct")
    return float(setting(con, key).replace(",", "."))


def makeup_pay(con, group_id: int, d: str) -> int:
    """Отработки: фикс за тренировку, но только если пришёл хотя бы один ребёнок."""
    came = con.execute("SELECT COUNT(*) FROM makeups WHERE group_id=? AND date=? AND status='done'", (group_id, d)).fetchone()[0]
    return int(setting(con, "pay_makeup_fixed") or 0) if came else 0
