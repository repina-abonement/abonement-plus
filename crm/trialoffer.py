"""Предложение ближайших пробных одним нажатием рядом с перепиской.

Подбираем группы по возрасту ребёнка (4 года → группы «3–4» и «4–5»), берём ближайшие тренировки, где есть места
(в филиалах с лимитом считаем лимит, иначе места в группе), без праздников и без сборной (туда ПУ нет).
Цель — чтобы ребёнок пришёл на пробное как можно раньше.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from . import logic

WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
DEFAULT = ("Здравствуйте! Вот ближайшие пробные занятия по футболу, на которые можно прийти ⚽\n\n{slots}\n\n"
           "Какой вариант вам удобнее? Запишем и пришлём напоминание 🙌")


def age_range(txt: str) -> tuple[int, int] | None:
    nums = [int(x) for x in re.findall(r"\d+", txt or "")]
    if not nums:
        return None
    if "+" in (txt or "") and len(nums) == 1:
        return nums[0], 99
    return nums[0], nums[-1]


def years(birth: str, on: date | None = None) -> int | None:
    try:
        b = date.fromisoformat(birth)
    except (TypeError, ValueError):
        return None
    on = on or date.today()
    return on.year - b.year - ((on.month, on.day) < (b.month, b.day))


def slots(con, age: int, branch_id: int | None = None, limit: int = 4, days: int = 14) -> list[dict]:
    from . import capacity, closures
    now = datetime.now()
    out = []
    for g in con.execute("""SELECT g.*, b.name AS branch, b.address, b.lesson_limit FROM groups g JOIN branches b ON b.id=g.branch_id
                            WHERE g.active=1 AND b.active=1 AND g.is_makeup=0 AND g.program_id IS NULL AND g.trial_ok=1"""
                         + (" AND g.branch_id=?" if branch_id else ""), (branch_id,) if branch_id else ()).fetchall():
        r = age_range(g["age"])
        if not r or not r[0] <= age <= r[1] or "взросл" in (g["age"] or "").lower() or "пап" in g["name"].lower() \
                or "ОФП" in g["name"]:  # пробное — на футбол, не на ОФП
            continue
        wd = logic.parse_weekdays(g["weekdays"])
        for i in range(days):
            d = now.date() + timedelta(days=i)
            if d.weekday() not in wd or closures.is_closed(con, g["id"], d):
                continue
            t = logic.group_time(dict(g), d.weekday())
            if not t or datetime.combine(d, datetime.strptime(t, "%H:%M").time()) < now + timedelta(hours=2):
                continue
            o = capacity.occupancy(con, g["id"], d.isoformat())
            cap = o["limit"] or g["capacity"]
            free = cap - o["count"]
            if free <= 0:
                continue
            out.append({"group_id": g["id"], "group": g["name"], "age": g["age"], "branch_id": g["branch_id"], "branch": g["branch"],
                        "address": g["address"] or "", "date": d.isoformat(), "time": t, "free": free,
                        "label": f"{WD[d.weekday()]} {d.strftime('%d.%m')} в {t}"})
    out.sort(key=lambda x: (x["date"], x["time"]))
    # не больше двух вариантов в один день, чтобы предложить разные дни
    res, per_day = [], {}
    for s in out:
        if per_day.get(s["date"], 0) >= 2:
            continue
        per_day[s["date"]] = per_day.get(s["date"], 0) + 1
        res.append(s)
        if len(res) >= limit:
            break
    return res


def offer(con, age: int | None, child: str = "", branch_id: int | None = None, n: int = 4) -> dict:
    from . import comms
    if age is None:
        return {"slots": [], "text": "", "error": "Укажите возраст ребёнка"}
    sl = slots(con, age, branch_id, n)
    many_branches = len({s["branch_id"] for s in sl}) > 1
    lines = []
    for s in sl:
        place = s["branch"] + (f", {s['address']}" if s["address"] and not many_branches else "")
        lines.append(f"📅 {s['label']}, {place} (группа {s['age']} лет)")
    tpl = comms.get(con, "msg_trial_offer") or DEFAULT
    child_txt = (child.split()[0] if child and "заявка" not in child.lower() and "ребёнок" not in child.lower() else "ребёнка")
    text = tpl.replace("{child}", child_txt).replace("{slots}", "\n".join(lines)).replace("{age}", str(age))
    return {"slots": sl, "text": text if sl else "", "age": age,
            "error": "" if sl else "Нет свободных мест для этого возраста на ближайшие 2 недели"}
