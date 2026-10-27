"""Выдуманные клиенты для проверки CRM на «живом» объёме. Все помечены fake=1 и удаляются одной кнопкой.

Раскладка: ~65% занимаются в реальных группах (с абонементами, оплатами, посещениями и пропусками),
остальные — лиды на разных этапах воронки продаж, включая записанных на пробное. Телефоны +7 999 0xx-xx-xx.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from . import logic

BOYS = ["Артём", "Миша", "Тимур", "Матвей", "Лев", "Марк", "Иван", "Максим", "Даниил", "Кирилл", "Егор", "Арсений", "Фёдор",
        "Савва", "Мирон", "Платон", "Глеб", "Тимофей", "Никита", "Захар", "Роман", "Ярослав", "Давид", "Елисей", "Серафим"]
GIRLS = ["Алиса", "Ева", "Вера", "Мия", "Варя", "Соня", "Полина", "Аня", "Маша", "Ксюша"]
SURN = ["Иванов", "Смирнов", "Кузнецов", "Попов", "Васильев", "Петров", "Соколов", "Михайлов", "Новиков", "Фёдоров",
        "Морозов", "Волков", "Алексеев", "Лебедев", "Семёнов", "Егоров", "Павлов", "Козлов", "Степанов", "Николаев",
        "Орлов", "Андреев", "Макаров", "Никитин", "Захаров", "Зайцев", "Соловьёв", "Борисов", "Яковлев", "Григорьев"]
MOMS = ["Анна", "Мария", "Елена", "Ольга", "Наталья", "Екатерина", "Ирина", "Татьяна", "Светлана", "Юлия", "Дарья", "Ксения"]
DADS = ["Алексей", "Дмитрий", "Сергей", "Андрей", "Павел", "Игорь", "Денис", "Евгений", "Олег", "Виктор"]
SOURCES = [("Сайт", "Заявка с сайта"), ("Промоутер", "Промоутер у ТЦ"), ("Вконтакте", "Таргет ВК"),
           ("Сарафан", "Посоветовали знакомые"), ("Филиал", "Увидели тренировку"), ("Паблики", "Районный паблик")]


def init(con):
    for t in ("students", "families"):
        if "fake" not in {r[1] for r in con.execute(f"PRAGMA table_info({t})")}:
            con.execute(f"ALTER TABLE {t} ADD COLUMN fake INTEGER NOT NULL DEFAULT 0")


def _surname(base: str, female: bool) -> str:
    if not female:
        return base
    return base[:-2] + "ая" if base.endswith("ий") else base + "а"


def _age_birth(age_txt: str, rnd: random.Random, today: date) -> str:
    nums = [int(x) for x in "".join(c if c.isdigit() else " " for c in age_txt or "").split()]
    lo, hi = (nums[0], nums[-1]) if nums else (4, 7)
    if hi > 14:
        hi = lo + 3
    age_days = rnd.randint(lo * 365 + 30, hi * 365 + 330)
    return (today - timedelta(days=age_days)).isoformat()


def generate(con, count: int, user: dict, seed: int | None = None) -> dict:
    from . import money, services as sv, sources
    init(con)
    rnd = random.Random(seed)
    today = date.today()
    ym = logic.month_of(today)
    groups = [dict(g) for g in con.execute("""SELECT g.*, b.name AS branch FROM groups g JOIN branches b ON b.id=g.branch_id
                                              WHERE g.active=1 AND b.active=1 AND g.is_makeup=0""")]
    if not groups:
        raise ValueError("Сначала нужны группы в расписании")
    kids_groups = [g for g in groups if "взросл" not in (g["age"] or "").lower()] or groups
    src_ids = [sources.ensure_source(con, ch, nm) for ch, nm in SOURCES]
    used = {r[0] for r in con.execute("SELECT phone_norm FROM parents")}
    made = {"members": 0, "leads": 0, "families": 0, "paid": 0, "skips": 0}
    fam_id = None
    n = 0
    while n < count:
        female_child = rnd.random() < 0.15
        child = rnd.choice(GIRLS if female_child else BOYS)
        base = rnd.choice(SURN)
        while True:
            phone = f"+7 999 0{rnd.randint(10, 99)}-{rnd.randint(10, 99)}-{rnd.randint(10, 99)}"
            norm = "7999" + "".join(c for c in phone[7:] if c.isdigit())
            if norm not in used:
                used.add(norm)
                break
        mom = rnd.random() < 0.8
        parent = f"{rnd.choice(MOMS if mom else DADS)} {_surname(base, mom)}"
        g = rnd.choice(kids_groups)
        member = rnd.random() < 0.65
        data = {"name": f"{child} {_surname(base, female_child)}", "parent_name": parent, "phone": phone,
                "family_name": _surname(base, False) + "ы", "funnel": "members" if member else "sales",
                "group_id": g["id"], "birth": _age_birth(g["age"], rnd, today), "tariff": "flex" if rnd.random() < 0.25 else "fixed",
                "source_id": rnd.choice(src_ids), "messenger": rnd.choice(["WhatsApp", "Telegram", "MAX", ""]), "force_new_family": True}
        sid = sv.create_student(con, data, user)
        fam_id = con.execute("SELECT family_id FROM students WHERE id=?", (sid,)).fetchone()[0]
        con.execute("UPDATE families SET fake=1 WHERE id=?", (fam_id,))
        con.execute("UPDATE students SET fake=1 WHERE id=?", (sid,))
        made["families"] += 1
        n += 1
        kids = [sid]
        if member and n < count and rnd.random() < 0.08:  # брат или сестра в той же семье
            g2 = rnd.choice(kids_groups)
            sid2 = sv.create_student(con, {"name": f"{rnd.choice(BOYS)} {base}", "family_id": fam_id, "funnel": "members",
                                           "group_id": g2["id"], "birth": _age_birth(g2["age"], rnd, today),
                                           "source_id": data["source_id"]}, user)
            con.execute("UPDATE students SET fake=1 WHERE id=?", (sid2,))
            kids.append(sid2)
            n += 1
        for k in kids:
            if member:
                made["members"] += 1
                _member_life(con, k, ym, today, user, rnd, made)
            else:
                made["leads"] += 1
                _lead_life(con, k, g, today, rnd)
    return made


def _member_life(con, sid, ym, today, user, rnd, made):
    from . import money, services as sv
    created = (today - timedelta(days=rnd.randint(20, 400))).isoformat() + "T10:00"
    con.execute("UPDATE students SET created_at=?, converted_at=? WHERE id=?", (created, created, sid))
    try:
        inv = sv.create_invoice(con, sid, ym, user)
        if rnd.random() < 0.78:
            money.pay_subscription(con, inv["id"], rnd.choice(["sbp", "card", "cash", "sbp", "online"]), user)
            made["paid"] += 1
    except Exception:  # noqa: BLE001 — для проверки не критично
        pass
    g = con.execute("SELECT g.weekdays FROM students s JOIN groups g ON g.id=s.group_id WHERE s.id=?", (sid,)).fetchone()
    days = logic.parse_weekdays(g[0]) if g else []
    d = today.replace(day=1)
    gid = con.execute("SELECT group_id FROM students WHERE id=?", (sid,)).fetchone()[0]
    absent_any = False
    while d < today:
        if d.weekday() in days:
            st = "absent" if rnd.random() < 0.12 else "present"
            try:
                sv.mark_attendance(con, sid, gid, d.isoformat(), st, user)
                absent_any |= st == "absent"
            except Exception:  # noqa: BLE001
                pass
        d += timedelta(days=1)
    made["skips"] += absent_any


def _lead_life(con, sid, g, today, rnd):
    stage = rnd.choices(["lead", "no_answer", "trial_booked", "trial_reminder", "how_was", "refused"],
                        weights=[24, 16, 22, 8, 12, 8])[0]
    created = datetime.combine(today - timedelta(days=rnd.randint(0, 25)), datetime.min.time()).replace(hour=rnd.randint(9, 21))
    trial = None
    days = logic.parse_weekdays(g["weekdays"])
    if stage in ("trial_booked", "trial_reminder") and days:
        d = today + timedelta(days=1)
        while d.weekday() not in days:
            d += timedelta(days=1)
        d += timedelta(days=7 * rnd.randint(0, 1))
        trial = f"{d.isoformat()}T{logic.group_time(g, d.weekday()) or '18:00'}"
    elif stage == "how_was" and days:
        d = today - timedelta(days=1)
        while d.weekday() not in days:
            d -= timedelta(days=1)
        trial = f"{d.isoformat()}T{logic.group_time(g, d.weekday()) or '18:00'}"
    con.execute("UPDATE students SET stage=?, trial_at=?, created_at=?, stage_changed_at=?, trial_came=? WHERE id=?",
                (stage, trial, created.isoformat(timespec="minutes"), created.isoformat(timespec="minutes"),
                 1 if stage == "how_was" else None, sid))
    if stage == "refused":
        con.execute("UPDATE students SET refusal_reason=? WHERE id=?",
                    (rnd.choice(["Далеко ездить", "Неудобное время", "Дорого", "Ребёнок не захотел"]), sid))


def remove(con) -> int:
    """Удаляет всех выдуманных клиентов вместе с их оплатами, посещениями и историей."""
    init(con)
    ids = [r[0] for r in con.execute("SELECT id FROM students WHERE fake=1")]
    if not ids:
        return 0
    q = ",".join("?" * len(ids))
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    for t in tables:
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
        if "student_id" in cols and t != "students":
            con.execute(f"DELETE FROM {t} WHERE student_id IN ({q})", ids)
    con.execute(f"DELETE FROM students WHERE id IN ({q})", ids)
    fams = [r[0] for r in con.execute("SELECT id FROM families WHERE fake=1")]
    if fams:
        fq = ",".join("?" * len(fams))
        for t in tables:
            cols = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
            if "family_id" in cols and t not in ("families", "students"):
                if t == "conversations":
                    con.execute(f"UPDATE {t} SET family_id=NULL WHERE family_id IN ({fq})", fams)
                else:
                    con.execute(f"DELETE FROM {t} WHERE family_id IN ({fq})", fams)
        con.execute(f"DELETE FROM families WHERE id IN ({fq})", fams)
    return len(ids)


def subscribe_all(con, user: dict, seed: int | None = None) -> dict:
    """Все выдуманные клиенты — абонементщики: лиды переводим в ученики, у всех оплачены сентябрь и октябрь."""
    from . import money, services as sv
    init(con)
    rnd = random.Random(seed)
    today = date.today()
    months = [logic.month_of(today.replace(day=1) - timedelta(days=1)), logic.month_of(today)]
    out = {"converted": 0, "invoices": 0, "paid": 0}
    for s in con.execute("SELECT * FROM students WHERE fake=1").fetchall():
        sid = s["id"]
        if s["funnel"] != "members":
            con.execute("UPDATE students SET trial_at=NULL, trial_came=NULL WHERE id=?", (sid,))
            sv.move_card(con, sid, "sales", "bought", user)
            out["converted"] += 1
        con.execute("UPDATE students SET stage='active', archived=0 WHERE id=?", (sid,))
        for ym in months:
            for e in con.execute("SELECT id FROM enrollments WHERE student_id=? AND active=1", (sid,)).fetchall():
                sub = con.execute("SELECT id, status FROM subscriptions WHERE enrollment_id=? AND month=? AND status!='cancelled'",
                                  (e["id"], ym)).fetchone()
                if not sub:
                    try:
                        sub_id = sv.create_invoice(con, sid, ym, user, e["id"])["id"]
                        out["invoices"] += 1
                    except Exception:  # noqa: BLE001
                        continue
                else:
                    sub_id = sub["id"]
                try:
                    money.pay_subscription(con, sub_id, rnd.choice(["sbp", "card", "cash", "sbp", "online"]), user)
                    out["paid"] += 1
                except ValueError:
                    pass  # уже оплачен
    return out
