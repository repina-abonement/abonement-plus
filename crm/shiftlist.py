"""Списки детей на всю смену в филиале (Юлия, 04.11): скопировать текстом или распечатать лист тренерам.
Сверху вводники (пробные): фамилия имя, возраст, группа, имя родителя. Дальше группы по времени:
кто по абонементу, разовое, не оплачен, отработка, вводник; отдельно кто предупредил, что не придёт."""
from datetime import date

from . import coaches, logic, schedule, services as sv

TAGS = {"paid": "абонемент", "single": "разовое", "unpaid": "не оплачен", "makeup": "отработка", "trial": "вводник",
        "vacation": "отпуск", "warned": "не придёт"}


def years(birth: str, on: date) -> str:
    """Возраст с месяцами (Юлия, 04.11): «3 года 5 мес.»."""
    try:
        b = date.fromisoformat((birth or "")[:10])
    except ValueError:
        return ""
    months = (on.year - b.year) * 12 + on.month - b.month - (on.day < b.day)
    y, m = months // 12, months % 12
    word = "год" if y % 10 == 1 and y % 100 != 11 else "года" if y % 10 in (2, 3, 4) and y % 100 not in (12, 13, 14) else "лет"
    return f"{y} {word}" + (f" {m} мес." if m else "")


def shift(con, branch_id: int, day: str, group_ids: list[int] | None = None) -> dict:
    d = date.fromisoformat(day)
    br = con.execute("SELECT name FROM branches WHERE id=?", (branch_id,)).fetchone()
    if not br:
        raise sv.ApiError("Филиал не найден", 404)
    month = day[:7]
    groups, trials = [], []
    for g in con.execute("""SELECT g.*, u.name AS coach_name, a.name AS assistant_name FROM groups g
                            LEFT JOIN users u ON u.id=g.coach_id LEFT JOIN users a ON a.id=g.assistant_id
                            WHERE g.branch_id=? AND g.active=1""", (branch_id,)).fetchall():
        if group_ids is not None and g["id"] not in group_ids:
            continue
        j = sv.journal(con, g["id"], day, paid_only=True, include_unpaid_marks=False)
        if not j["has_lesson"]:
            continue
        gv = schedule.on(con, g, d)
        time = logic.group_time(gv, d.weekday())
        gname = g["name"]
        kids = []
        for r in j["rows"]:
            birth = r.get("birth")
            if birth is None:
                row = con.execute("SELECT birth FROM students WHERE id=?", (r["id"],)).fetchone()
                birth = row[0] if row else ""
            age = years(birth, d)
            if g["is_makeup"] or r.get("status") == "makeup":
                tag, note = "makeup", ("из группы " + r["home_group"]) if r.get("home_group") else ""
            elif r.get("status") == "trial":
                tag, note = "trial", ""
                p = con.execute("""SELECT p.name FROM parents p JOIN students s ON s.family_id=p.family_id
                                   WHERE s.id=? ORDER BY p.id LIMIT 1""", (r["id"],)).fetchone()
                trials.append({"name": r["name"], "age": age, "group": gname, "time": time, "parent": p[0] if p else ""})
            elif r.get("warned"):
                tag, note = "warned", r.get("note") or ""
            else:
                tag, note = r.get("tag") or coaches._kid_tag(con, r["id"], month, r.get("status")), ""
            kids.append({"name": r["name"], "age": age, "tag": tag, "label": TAGS.get(tag, tag), "note": note})
        order = {"paid": 0, "single": 1, "unpaid": 2, "makeup": 3, "trial": 4, "vacation": 5, "warned": 6}
        kids.sort(key=lambda k: (order.get(k["tag"], 9), k["name"]))
        coach = g["coach_name"] or ""
        if g["assistant_name"]:
            coach += (", " if coach else "") + g["assistant_name"]
        come = [k for k in kids if k["tag"] not in ("vacation", "warned")]
        groups.append({"id": g["id"], "name": gname, "time": time, "age": g["age"], "coach": coach,
                       "is_makeup": bool(g["is_makeup"]), "kids": kids, "come": len(come)})
    groups.sort(key=lambda x: (x["time"], x["name"]))
    trials.sort(key=lambda x: (x["time"], x["name"]))
    return {"branch": br[0], "date": day, "groups": groups, "trials": trials}


def day_all(con, day: str, branch_id: int | None = None, only: list[int] | None = None) -> dict:
    """Все занятия дня: по всем филиалам или по одному (Юлия, 04.11). Филиалы без занятий не показываем."""
    ids = [branch_id] if branch_id else [r[0] for r in con.execute(
        "SELECT DISTINCT b.id FROM branches b JOIN groups g ON g.branch_id=b.id AND g.active=1 ORDER BY b.id")]
    out = [shift(con, b, day, only) for b in ids]
    return {"date": day, "branches": [x for x in out if x["groups"]]}
