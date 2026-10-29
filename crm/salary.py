"""Зарплата тренеров за месяц.

Правила (Юлия, 28.10.2026):
- обычная группа: берётся вся выручка группы за месяц (оплаченные абонементы и разовые на этот месяц),
  делится поровну на тренировки месяца. С каждой тренировки: вдвоём главный 15,5%, помощник 10%; один тренер 20%.
  Кто вёл тренировку — из отметки «Кто вёл тренировку», если её нет — по расписанию (с заменами и отсутствиями);
- группа отработок: фикс 1500 ₽ главному за тренировку, если пришёл хотя бы один ребёнок, иначе 0;
- направление (сборная): процент направления (20%) от выручки всех его групп — тренеру группы направления;
- штраф за опоздание (500 ₽) вычитается, когда проверяющий его подтвердил; неподтверждённые показываются отдельно.
Проценты и фикс меняются в Настройках (pay_main_pct, pay_assistant_pct, pay_solo_pct, pay_makeup_fixed).
"""
from __future__ import annotations

from datetime import date, timedelta

from . import logic


def _dates(con, g, ym: str) -> list[str]:
    from . import closures
    a, b = logic.month_bounds(ym)
    a, b = (date.fromisoformat(a) if isinstance(a, str) else a), (date.fromisoformat(b) if isinstance(b, str) else b)
    from . import schedule
    closed = closures.closed_dates(con, g["id"], a, b)
    out, d = [], a
    while d <= b:
        if d.weekday() in logic.parse_weekdays(schedule.on(con, g, d)["weekdays"]) and d.isoformat() not in closed:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _revenue(con, ym: str) -> dict[int, int]:
    rev = {}
    for r in con.execute("""SELECT COALESCE(e.group_id, s.group_id) AS gid, SUM(x.price) AS rub
                            FROM subscriptions x JOIN students s ON s.id=x.student_id
                            LEFT JOIN enrollments e ON e.id=x.enrollment_id
                            WHERE x.month=? AND x.status='paid' GROUP BY gid""", (ym,)):
        if r["gid"]:
            rev[r["gid"]] = int(r["rub"] or 0)
    return rev


def payroll(con, ym: str | None = None, branch_id: int | None = None, coach_id: int | None = None) -> dict:
    from . import staff
    ym = ym or logic.month_of(date.today())
    today = date.today().isoformat()
    pct = {k: staff.pay_share(con, *v) for k, v in {"main": (True, False), "assistant": (False, False), "solo": (True, True)}.items()}
    rev = _revenue(con, ym)
    names = {r["id"]: r["name"] for r in con.execute("SELECT id, name FROM users")}
    people: dict[int, dict] = {}

    def person(uid):
        return people.setdefault(uid, {"id": uid, "name": names.get(uid, "?"), "total": 0, "lessons": 0, "planned": 0,
                                       "fines": 0, "fines_pending": 0, "lines": []})

    groups_out = []
    sql = """SELECT g.*, b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id
             WHERE (g.active=1 OR g.id IN (SELECT group_id FROM lesson_staff WHERE substr(date,1,7)=?))"""
    args = [ym]
    if branch_id:
        sql += " AND g.branch_id=?"
        args.append(branch_id)
    groups = con.execute(sql + " ORDER BY g.branch_id, g.name", args).fetchall()
    programs: dict[int, list] = {}
    for g in groups:
        if g["program_id"]:
            programs.setdefault(g["program_id"], []).append(g)
            continue
        dates = _dates(con, g, ym)
        if not dates:
            continue
        r_total = 0 if g["is_makeup"] else rev.get(g["id"], 0)
        per = r_total / len(dates)
        gl = {"id": g["id"], "name": g["name"], "branch": g["branch"], "revenue": r_total, "lessons": len(dates),
              "per_lesson": round(per), "makeup": bool(g["is_makeup"]), "pay": 0}
        for d in dates:
            ls = staff.lesson_staff(con, g["id"], d)
            if not ls["main_id"]:
                continue
            plan = not ls["set"]
            if g["is_makeup"]:
                if d > today:
                    continue  # фикс только за прошедшие отработки, где кто-то был
                amt = staff.makeup_pay(con, g["id"], d)
                shares = [(ls["main_id"], amt, "отработки")]
            elif ls["solo"]:
                shares = [(ls["main_id"], per * pct["solo"] / 100, "один")]
            else:
                shares = [(ls["main_id"], per * pct["main"] / 100, "главный"),
                          (ls["assistant_id"], per * pct["assistant"] / 100, "помощник")]
            for uid, amt, role in shares:
                p = person(uid)
                p["lines"].append({"group": g["name"], "date": d, "role": role, "amount": round(amt), "planned": plan})
                p["total"] += amt
                p["lessons"] += 1
                p["planned"] += 1 if plan else 0
                gl["pay"] += amt
        gl["pay"] = round(gl["pay"])
        groups_out.append(gl)
    for pid, gs in programs.items():
        pr = con.execute("SELECT name, coach_pct FROM programs WHERE id=?", (pid,)).fetchone()
        r_total = sum(rev.get(g["id"], 0) for g in gs)
        main = next((g for g in gs if g["coach_id"] and g["lesson_kind"] != "ofp"), None) or next((g for g in gs if g["coach_id"]), None)
        amt = r_total * float(pr["coach_pct"] if pr else 20) / 100
        if main:
            p = person(main["coach_id"])
            p["lines"].append({"group": (pr["name"] if pr else "Направление") + " (все группы)", "date": "", "role": f"{pr['coach_pct'] if pr else 20:g}% выручки",
                               "amount": round(amt), "planned": False})
            p["total"] += amt
        groups_out.append({"id": None, "name": pr["name"] if pr else "Направление", "branch": "", "revenue": r_total,
                           "lessons": None, "per_lesson": None, "makeup": False, "pay": round(amt)})
    for f in staff.fines(con, ym):
        if f["user_id"] not in people and coach_id is None and branch_id:
            continue
        p = person(f["user_id"])
        if f["fine_status"] == "confirmed":
            p["fines"] += f["fine"]
        else:
            p["fines_pending"] += f["fine"]
        p["lines"].append({"group": "Опоздание", "date": f["date"], "role": f"{f['late_min']} мин" + ("" if f["fine_status"] == "confirmed" else ", ждёт проверки"),
                           "amount": -f["fine"], "planned": f["fine_status"] != "confirmed"})
    out = []
    for p in people.values():
        if coach_id and p["id"] != coach_id:
            continue
        p["total"] = round(p["total"])
        p["to_pay"] = p["total"] - p["fines"]
        p["lines"].sort(key=lambda x: (x["date"], x["group"]))
        out.append(p)
    out.sort(key=lambda x: -x["to_pay"])
    return {"month": ym, "month_label": logic.month_label(ym), "coaches": out, "groups": groups_out,
            "total": sum(p["to_pay"] for p in out), "revenue": sum(g["revenue"] for g in groups_out),
            "pct": pct, "makeup_fixed": int(staff.setting(con, "pay_makeup_fixed") or 0)}
