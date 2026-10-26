"""Результаты тренеров: турнирная таблица и номинации месяца. Без денег — только дети, группы, пробные."""
from __future__ import annotations

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
