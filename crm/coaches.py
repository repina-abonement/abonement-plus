"""Результаты тренеров: турнирная таблица и номинации месяца. Без денег — только дети, группы, пробные."""
from __future__ import annotations

from datetime import date

from . import logic

FILL_PHRASES = [
    (90, "Аншлаг! Зал трещит по швам 🔥"),
    (75, "Почти полный стадион, ещё чуть-чуть 💪"),
    (50, "Половина трибун занята, зовём друзей 📣"),
    (0, "Места есть, самое время звать новых игроков ⚽"),
]
TRIAL_PHRASES = [
    (70, "Пробные заходят как пенальти в пустые ворота 🎯"),
    (50, "Каждый второй остаётся, отличный пас 👟"),
    (30, "Есть голевые моменты, добиваем 💥"),
    (0, "Разминка закончилась, пора забивать 🥅"),
]
STAY_PHRASES = [
    (95, "Отсюда не уходят, это клуб мечты 🏆"),
    (85, "Крепкая защита, дети держатся за команду 🛡️"),
    (70, "Кто-то ушёл в запас, вернём 🔄"),
    (0, "Течь в обороне, срочно усиливаемся 🧱"),
]
ATT_PHRASES = [
    (90, "Ходят как на финал Лиги чемпионов 🏟️"),
    (75, "Хорошая явка, тренировки огонь 🔥"),
    (60, "Часть команды на скамейке, зовём на поле 📢"),
    (0, "Дети прогуливают, нужна мотивационная речь 🎤"),
]
TOP_PHRASES = {1: "Капитан лиги! Остальные догоняют 👑", 2: "Серебро, до золота один рывок 🥈",
               3: "Бронза, ты на пьедестале 🥉"}


def _phrase(table, value):
    if value is None:
        return "Пока мало данных, всё впереди ✨"
    for limit, text in table:
        if value >= limit:
            return text
    return table[-1][1]


def _pct(a, b):
    return round(100 * a / b) if b else None


def _of(a, b):
    """«6 из 10» — проценты тренерам не показываем, только «из скольких»."""
    if not b:
        return None
    scale = 10 if b >= 10 else b
    return {"num": round(a * scale / b), "den": scale, "raw": [a, b]}


def coach_stats(con, coach_id: int, ym: str) -> dict:
    groups = [dict(r) for r in con.execute(
        "SELECT id, name, capacity FROM groups WHERE coach_id=? AND is_makeup=0 AND active=1 ORDER BY name", (coach_id,))]
    gids = [g["id"] for g in groups]
    if not gids:
        return {"groups": 0, "group_names": [], "kids": 0, "kids_prev": 0, "seats": 0}
    ph = ",".join("?" * len(gids))
    prev = logic.shift_month(ym, -1)

    def kids(m):
        return {r[0] for r in con.execute(
            f"""SELECT DISTINCT x.student_id FROM subscriptions x JOIN enrollments e ON e.id = x.enrollment_id
                WHERE x.month=? AND x.status != 'cancelled' AND e.group_id IN ({ph})""", (m, *gids))}

    k_now, k_prev = kids(ym), kids(prev)
    stayed = len({r[0] for r in con.execute(
        f"SELECT DISTINCT student_id FROM subscriptions WHERE month=? AND status != 'cancelled' AND student_id IN "
        f"({','.join('?' * len(k_prev)) or 'NULL'})", (ym, *k_prev))}) if k_prev else 0
    start, end = logic.month_bounds(ym)
    s_iso, e_iso = start.isoformat(), end.isoformat() + "T23:59"
    tr = con.execute(f"""SELECT COUNT(*), SUM(trial_came = 1), SUM(trial_came = 1 AND converted_at IS NOT NULL)
                         FROM students WHERE group_id IN ({ph}) AND trial_at BETWEEN ? AND ?""",
                     (*gids, s_iso, e_iso)).fetchone()
    trials, came, bought = tr[0] or 0, tr[1] or 0, tr[2] or 0
    at = con.execute(f"""SELECT SUM(status='present'), COUNT(*) FROM attendance
                         WHERE group_id IN ({ph}) AND date BETWEEN ? AND ? AND status IN ('present','absent')""",
                     (*gids, start.isoformat(), end.isoformat())).fetchone()
    present, marked = at[0] or 0, at[1] or 0
    seats = sum(g["capacity"] or 0 for g in groups)
    return {
        "groups": len(groups), "group_names": [g["name"] for g in groups],
        "kids": len(k_now), "kids_prev": len(k_prev), "seats": seats,
        "fill": _pct(len(k_now), seats),
        "trials": trials, "came": came, "bought": bought, "trial_conv": _pct(bought, came),
        "trial_of": _of(bought, came),
        "stayed": stayed, "stay": _pct(stayed, len(k_prev)), "stay_of": _of(stayed, len(k_prev)),
        "attendance": _pct(present, marked), "att_of": _of(present, marked),
    }


NOMINATIONS = [
    ("kids", "Больше всех детей", "🧒", "детей"),
    ("trial_conv", "Король пробных", "🎯", "остались после пробного"),
    ("stay", "Железная защита", "🛡️", "детей продлились"),
    ("attendance", "Любимый тренер", "❤️", "лучшая посещаемость"),
    ("fill", "Полный зал", "🏟️", "заполненность групп"),
]


def leaderboard(con, ym: str | None = None, me: int | None = None) -> dict:
    ym = ym or logic.month_of(__import__("datetime").date.today())
    coaches = [dict(r) for r in con.execute(
        "SELECT id, name FROM users WHERE role='coach' AND active=1 ORDER BY name")]
    rows = []
    for c in coaches:
        st = coach_stats(con, c["id"], ym)
        if not st["groups"]:
            continue
        rows.append({"id": c["id"], "name": c["name"], **st,
                     "phrases": {"fill": _phrase(FILL_PHRASES, st["fill"]),
                                 "trial": _phrase(TRIAL_PHRASES, st["trial_conv"]) if st["came"] else
                                          "Пробных пока не было, ждём новичков 👀",
                                 "stay": _phrase(STAY_PHRASES, st["stay"]),
                                 "att": _phrase(ATT_PHRASES, st["attendance"])}})
    rows.sort(key=lambda r: (-r["kids"], -(r["fill"] or 0), r["name"]))
    place = 0
    for i, r in enumerate(rows):
        if i == 0 or r["kids"] != rows[i - 1]["kids"]:
            place = i + 1
        r["place"] = place
        r["delta"] = r["kids"] - r["kids_prev"]
        r["is_me"] = r["id"] == me
    noms = []
    for key, title, emoji, unit in NOMINATIONS:
        cand = [r for r in rows if r.get(key) is not None and (key != "trial_conv" or r["came"] >= 2)]
        if not cand:
            continue
        best = max(r[key] for r in cand)
        if not best:
            continue
        winners = [r["name"] for r in cand if r[key] == best]
        noms.append({"key": key, "title": title, "emoji": emoji, "unit": unit, "value": best, "winners": winners,
                     "me": any(r["is_me"] for r in cand if r[key] == best)})
    mine = next((r for r in rows if r["is_me"]), None)
    hero = None
    if mine:
        n = len(rows)
        top = TOP_PHRASES.get(mine["place"]) if n > 1 else "Ты один в таблице, значит ты лучший 😎"
        if not top:
            top = "Середина таблицы, отличный разгон для рывка 🚀" if mine["place"] < n else \
                "Последнее место это лучший старт для камбэка 💪"
        ahead = next((r for r in reversed(rows) if r["kids"] > mine["kids"]), None)
        hero = {"place": mine["place"], "of": n, "phrase": top,
                "gap": (ahead["kids"] - mine["kids"]) if ahead else 0, "gap_name": ahead["name"] if ahead else "",
                "badges": [x["title"] for x in noms if x["me"]]}
    return {"month": ym, "month_label": logic.month_label(ym), "rows": rows, "nominations": noms, "hero": hero,
            "me": me}


def seed_demo_coaches(con):
    """Тестовый режим: ещё два выдуманных тренера, чтобы было с кем соревноваться."""
    from .db import hash_password
    from . import perms
    if perms.real_staff(con) or con.execute("SELECT 1 FROM users WHERE login IN ('trener2','trener3')").fetchone():
        return  # уже есть настоящая команда или выдуманные тренеры заведены раньше: группы не трогаем
    free = [r[0] for r in con.execute("SELECT id FROM groups WHERE coach_id IS NULL AND is_makeup=0 ORDER BY id")]
    if not free:
        return
    for i, (login, name) in enumerate([("trener2", "Тренер Никита (тест)"), ("trener3", "Тренер Иван (тест)")]):
        if i >= len(free):
            break
        cur = con.execute("INSERT OR IGNORE INTO users(login, name, role, pw_hash) VALUES (?,?,?,?)",
                          (login, name, "coach", hash_password(login + "-test-1")))
        uid = cur.lastrowid if cur.rowcount else con.execute("SELECT id FROM users WHERE login=?", (login,)).fetchone()[0]
        con.execute("UPDATE groups SET coach_id=? WHERE id=?", (uid, free[i]))


def _kid_tag(con, sid: int, month: str, status) -> str:
    """Что тренеру важно знать про ребёнка на сегодня: по абонементу, без оплаты, в отпуске."""
    if status == "vacation":
        return "vacation"
    r = con.execute("""SELECT status FROM subscriptions WHERE student_id=? AND month=? AND kind='month' AND status!='cancelled'
                       ORDER BY status='paid' DESC LIMIT 1""", (sid, month)).fetchone()
    if r and r[0] == "paid":
        return "paid"
    if con.execute("""SELECT 1 FROM subscriptions WHERE student_id=? AND kind='single' AND status!='cancelled'
                      AND substr(created_at,1,7)=?""", (sid, month)).fetchone():
        return "single"
    return "unpaid"


def _lesson(con, g, day, today, sub=None, replaced=None) -> dict:
    from . import services as sv
    j = sv.journal(con, g["id"], day)
    rows = j["rows"]
    month = logic.month_of(today)
    kids, trials = [], []
    for r in rows:
        if g["is_makeup"]:
            kids.append({"id": r["id"], "name": r["name"], "tag": "makeup", "note": r.get("home_group") or "", "status": r.get("status")})
        elif r.get("status") == "makeup":
            kids.append({"id": r["id"], "name": r["name"], "tag": "makeup", "note": r.get("home_group") or "", "status": r.get("mk_status")})
        elif r.get("warned"):
            kids.append({"id": r["id"], "name": r["name"], "tag": "warned", "note": r.get("note") or "", "status": "absent"})
        elif r.get("status") == "trial":
            trials.append({"id": r["id"], "name": r["name"], "tag": "trial", "status": r.get("trial_came"),
                           "note": (r.get("trial_at") or "")[11:16]})
        else:
            kids.append({"id": r["id"], "name": r["name"], "tag": _kid_tag(con, r["id"], month, r.get("status")), "status": r.get("status")})
    bdays = []
    for r in kids + trials:
        b = con.execute("SELECT birth FROM students WHERE id=?", (r["id"],)).fetchone()
        info = sv.birthday_info(b[0], today) if b and b[0] else None
        if info:
            bdays.append({"name": r["name"], **info})
    regular = [k for k in kids if k["tag"] not in ("makeup", "warned")]
    come = [k for k in kids if k["tag"] not in ("vacation", "warned")]
    counts = {"total": len(come) + len(trials), "paid": sum(k["tag"] == "paid" for k in kids),
              "single": sum(k["tag"] == "single" for k in kids), "unpaid": sum(k["tag"] == "unpaid" for k in kids),
              "vacation": sum(k["tag"] == "vacation" for k in kids), "warned": sum(k["tag"] == "warned" for k in kids), "makeups": sum(k["tag"] == "makeup" for k in kids),
              "trials": len(trials)}
    marked = sum(1 for r in come if r.get("status") in ("present", "absent", "done", "missed"))
    out = {"id": g["id"], "name": g["name"], "time": logic.group_time(g, date.fromisoformat(day).weekday()), "branch": g["branch"], "age": g["age"],
           "is_makeup": g["is_makeup"], "kids": len(come), "marked": marked, "trials": len(trials), "capacity": g["capacity"],
           "trials_marked": sum(1 for r in trials if r.get("status") is not None), "counts": counts, "birthdays": bdays,
           "list": come + trials, "regular": len(regular)}
    if sub:
        out["sub_for"] = sub
    if replaced:
        out["replaced_by"] = replaced
    return out


def coach_today(con, coach_id: int, today=None) -> dict:
    """Рабочий стол тренера: тренировки сегодня с разбивкой по детям, замены, что не отмечено за неделю."""
    from datetime import date as _d, timedelta as _td
    from . import schedule, services as sv
    today = today or _d.today()
    day = today.isoformat()
    me = con.execute("SELECT id, name FROM users WHERE id=?", (coach_id,)).fetchone()
    own = [dict(r) for r in con.execute(
        """SELECT g.*, b.name AS branch, u.name AS coach_name, a.name AS assistant_name FROM groups g
           JOIN branches b ON b.id=g.branch_id LEFT JOIN users u ON u.id=g.coach_id LEFT JOIN users a ON a.id=g.assistant_id
           WHERE (g.coach_id=? OR g.assistant_id=?) AND g.active=1 ORDER BY g.time""", (coach_id, coach_id))]
    out_today, missing, replaced = [], [], []
    for g in own:
        role = "main" if g["coach_id"] == coach_id else "assistant"
        partner = g["assistant_name"] if role == "main" else g["coach_name"]
        days = logic.parse_weekdays(g["weekdays"])
        from . import closures
        if today.weekday() in days and not closures.is_closed(con, g["id"], today):
            sub = schedule.sub_on(con, g["id"], day, role)
            if sub:
                replaced.append({"group_id": g["id"], "name": g["name"], "time": logic.group_time(g, today.weekday()),
                                 "branch": g["branch"], "by": sub["coach_name"] or "никто, коллега ведёт один"})
            else:
                les = _lesson(con, g, day, today)
                les["role"] = role
                les["partner"] = partner or ""
                out_today.append(les)
        if role != "main":
            continue  # отмечает детей главный тренер
        for back in range(1, 8):
            d = today - _td(days=back)
            if d.weekday() not in days or g["is_makeup"]:
                continue
            s2 = schedule.sub_on(con, g["id"], d.isoformat(), "main")
            if s2 and s2["coach_id"] != coach_id:
                continue
            j = sv.journal(con, g["id"], d.isoformat())
            left = sum(1 for r in j["rows"] if r.get("status") is None or (r.get("status") == "trial" and r.get("trial_came") is None))
            if left:
                missing.append({"group_id": g["id"], "name": g["name"], "date": d.isoformat(), "left": left})
    for s in con.execute("""SELECT s.*, g.coach_id AS own_main, g.assistant_id AS own_asst FROM substitutions s
                            JOIN groups g ON g.id=s.group_id WHERE s.coach_id=? AND s.date BETWEEN ? AND ?""",
                         (coach_id, (today - _td(days=7)).isoformat(), day)):
        g = dict(con.execute("SELECT g.*, b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id WHERE g.id=?",
                             (s["group_id"],)).fetchone())
        own_id = s["own_main"] if s["role"] == "main" else s["own_asst"]
        own_name = (con.execute("SELECT name FROM users WHERE id=?", (own_id,)).fetchone() or [""])[0]
        if s["date"] == day:
            les = _lesson(con, g, day, today, sub={"instead": own_name, "note": s["note"], "role": s["role"]})
            les["role"] = s["role"]
            out_today.append(les)
        elif s["role"] == "main":
            j = sv.journal(con, g["id"], s["date"])
            left = sum(1 for r in j["rows"] if r.get("status") is None or (r.get("status") == "trial" and r.get("trial_came") is None))
            if left:
                missing.append({"group_id": g["id"], "name": g["name"] + " (замена)", "date": s["date"], "left": left})
    out_today.sort(key=lambda x: x["time"] or "")
    tot = {k: sum(l["counts"][k] for l in out_today) for k in ("total", "paid", "single", "unpaid", "vacation", "makeups", "trials")}
    week = schedule.coach_subs(con, coach_id, (today + _td(days=1)).isoformat(), (today + _td(days=14)).isoformat())
    away = schedule.absent_on(con, coach_id, day)
    return {"today": out_today, "missing": missing, "date": day, "replaced": replaced, "totals": tot, "upcoming": week,
            "away": dict(away) if away else None,
            "coach": {"id": me["id"], "name": me["name"]} if me else None}
