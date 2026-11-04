"""Цепочки задач по всем этапам (разбор Юлии 01.11.2026: «где клиент теряется, там деньги»).

Каждый этап, на котором клиент может застрять, получает задачу администратору. Клиентам здесь ничего не пишется:
только задачи сотрудникам. Задачи помечены source='life', их снимает своя уборка, когда этап сменился.

1. Новый лид → «Позвонить новому лиду» за 15 рабочих минут. Недозвон → на следующий день, через 3 дня, через неделю,
   через месяц; дальше «Пригласим позже» с задачей пригласить в ближайший набор (август или январь).
2. «Как прошло пробное» больше суток без решения → «Дожать продажу» (до 3 раз, третья: принять решение).
3. «Купил» засчитывается при первой оплате (money.reconcile); до оплаты задача «Принять оплату за первый абонемент».
4. Отказ (кроме «выбрали другую секцию» и «не беспокоить») → через 3 месяца «Мягко пригласить снова».
5. Ученики: с 20 числа «Продлить на следующий месяц» по семье, после крайнего дня «Долг: связаться».
6. Ушёл → причина обязательна, через 2 месяца «Пригласить вернуться».
8. «Предложили отработку» 2 дня без записи → «Уточнить отработку».
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

KINDS = {
    "call_lead": ("Новый лид", "📞"),  # Юлия, 04.11: задача называется как этап
    "no_answer": ("Недозвон: позвонить ещё раз", "🔁"),
    "close_sale": ("Дожать продажу после пробного", "🎯"),
    "first_pay": ("Принять оплату за первый абонемент", "💳"),
    "reinvite": ("Мягко пригласить снова на пробное", "🌱"),
    "renew": ("Продлить абонемент", "🔄"),
    "debt": ("Долг за абонемент: связаться", "❗"),
    "win_back": ("Пригласить вернуться", "🤝"),
    "makeup_check": ("Уточнить отработку", "🩹"),
    "revive": ("Оживить старый лид", "🌱"),
    "unpaid_visit": ("Был на занятии без оплаты", "🔴"),  # Юлия, 04.11: отметили «был», а месяц не оплачен
}
# Юлия, 01.11: недозвон: завтра, через 3 дня, через неделю, через месяц, дальше ближайший набор (август или январь)
NO_ANSWER_STEPS = [(1, "на следующий день"), (3, "через 3 дня"), (7, "через неделю"), (30, "через месяц")]
REVIVE_PER_DAY = 10  # старых лидов в день на «оживление»: вся база обрабатывается постепенно, без завала
LEFT_REASONS = ["Дорого", "Неудобное расписание", "Далеко ездить", "Ребёнку не нравится", "Перешли в другую секцию",
                "Переезд", "Болезнь или травма", "Сезон, каникулы", "Другое"]
NO_COMEBACK = ("Выбрали другую секцию", "Переезд")
# «Ушёл»: когда пригласить вернуться, по причине (Юлия, 03.11). intake: ближайший набор (1 августа или 5 января)
LEFT_PLAN = {"Дорого": "promo", "Неудобное расписание": 14, "Далеко ездить": "intake", "Ребёнку не нравится": "intake",
             "Перешли в другую секцию": "intake", "Переезд": "archive", "Болезнь или травма": "admin", "Сезон, каникулы": "admin",
             "Другое": 60}
LEFT_DUE_BY_ADMIN = [k for k, v in LEFT_PLAN.items() if v == "admin"]


def next_intake(d: date) -> date:
    """Ближайший набор: 1 августа или 5 января, что раньше и ещё не прошло."""
    cands = [date(d.year, 1, 5), date(d.year, 8, 1), date(d.year + 1, 1, 5), date(d.year + 1, 8, 1)]
    return min(x for x in cands if x > d)


def on_left(con, sid: int, reason: str, comment: str, due: str, user) -> dict:
    """Ушёл: задача «Пригласить вернуться» по сроку из LEFT_PLAN, «Дорого» в «Ждут акцию», «Переезд» в архив."""
    from . import pu, services as sv
    s = con.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
    plan = LEFT_PLAN.get(reason, 60)
    today = sv.today()
    note = f"Ушёл: {reason}" + (f". {comment}" if comment else "")
    key = f"win_back:{sid}:{(s['stage_changed_at'] or '')[:10]}"
    if plan == "promo":
        from . import nodeadline
        nodeadline.add(con, sid, "promo", note, user)
        return {"plan": "promo"}
    if plan == "archive":
        from . import schedule
        schedule.set_student_archived(con, sid, True, user or {})
        sv.log(con, sid, f"В архив: переезд" + (f". {comment}" if comment else ""), user, "stage")
        return {"plan": "archive"}
    if plan == "intake":
        when = next_intake(today)
    elif plan == "admin":
        when = date.fromisoformat(due[:10])
    else:
        when = today + timedelta(days=int(plan))
    _add(con, "win_back", sid, f"{when.isoformat()}T11:00", key, note=note, group_id=s["group_id"])
    sv.log(con, sid, f"Пригласить вернуться {when:%d.%m.%Y}", user, "stage")
    return {"plan": "task", "due": when.isoformat()}


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="minutes")


def _dt(s: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((s or "")[:16]) if s else None
    except ValueError:
        return None


def _add(con, kind, sid, due, key, title="", note="", group_id=None):
    from . import pu, tasks
    return tasks.add_task(con, kind, sid, due, auto_key=f"life:{key}", title=title or KINDS[kind][0], source="life",
                          assignee=pu.responsible(con, group_id), note=note)


def _has_open(con, sid: int, fam: int | None = None) -> bool:
    return bool(con.execute("SELECT 1 FROM tasks WHERE status='open' AND (student_id=? OR (kind='rebook' AND family_id=?))",
                            (sid, fam or -1)).fetchone())


def _done_count(con, sid: int, kind: str, since: str) -> tuple[int, str | None]:
    r = con.execute("SELECT COUNT(*), MAX(done_at) FROM tasks WHERE student_id=? AND kind=? AND status='done' AND created_at >= ?",
                    (sid, kind, (since or "")[:19])).fetchone()
    return r[0], r[1]


def _people(con, where: str, args=()):
    fake = " AND COALESCE(f.fake,0)=0" if "fake" in {r[1] for r in con.execute("PRAGMA table_info(families)")} else ""
    return con.execute(f"""SELECT s.* FROM students s JOIN families f ON f.id=s.family_id
                           WHERE s.archived=0 AND COALESCE(s.dnd,0)=0{fake} AND {where}""", args).fetchall()


def tick(con, now: datetime | None = None) -> int:
    from . import logic, pu, services as sv
    now = (now or datetime.now()).replace(second=0, microsecond=0)
    cleanup(con, now)  # сначала снимаем задачи прошлых этапов, иначе новая не встанет
    made = 0
    # 1. новый лид и недозвон
    fresh = lambda days: _iso(now - timedelta(days=days))  # только свежие: старые лиды из ERP задачами не заваливаем
    for s in _people(con, "s.funnel='sales' AND s.stage='lead' AND COALESCE(s.stage_changed_at, s.created_at) >= ?", (fresh(7),)):
        if not _has_open(con, s["id"], s["family_id"]):
            start = max(_dt(s["stage_changed_at"]) or _dt(s["created_at"]) or now, now - timedelta(days=30))
            made += bool(_add(con, "call_lead", s["id"], pu.add_work(con, start, 15), f"call_lead:{s['id']}", group_id=s["group_id"]))
    for s in _people(con, "s.funnel='sales' AND s.stage='no_answer' AND s.stage_changed_at >= ?", (fresh(60),)):  # лесенка до 41 дня
        if _has_open(con, s["id"], s["family_id"]):
            continue
        n, last = _done_count(con, s["id"], "no_answer", s["stage_changed_at"])
        if n >= len(NO_ANSWER_STEPS):  # лесенка пройдена: в «Пригласим позже», пригласить в ближайший набор
            again = pu.window(now)
            con.execute("UPDATE students SET stage='later', refusal_reason='Не дозвонились', stage_changed_at=? WHERE id=?",
                        (_iso(now), s["id"]))
            pu.ensure_task(con, s["id"], again, now, title="Пригласить на ПУ в набор (раньше не дозвонились)")
            sv.log(con, s["id"], f"Не дозвонились {n + 1} раз: «Пригласим позже», пригласим в набор {again:%d.%m.%Y}", None, "stage")
            continue
        base = _dt(last) if last else (_dt(s["stage_changed_at"]) or now)
        days, label = NO_ANSWER_STEPS[n]
        due = pu.next_day_start(con, base, days)
        made += bool(_add(con, "no_answer", s["id"], due, f"no_answer:{s['id']}:{(s['stage_changed_at'] or '')[:16]}:{n}",
                          title=f"Недозвон: позвонить ещё раз ({label})", group_id=s["group_id"]))
    # 2. «Как прошло пробное» больше суток без решения
    for s in _people(con, "s.funnel='sales' AND s.stage='how_was' AND s.stage_changed_at <= ? AND s.stage_changed_at >= ?",
                     (_iso(now - timedelta(days=1)), fresh(10))):
        if _has_open(con, s["id"], s["family_id"]):
            continue
        n1, l1 = _done_count(con, s["id"], "why_not_bought", s["stage_changed_at"])
        n2, l2 = _done_count(con, s["id"], "close_sale", s["stage_changed_at"])
        n, last = n1 + n2, max(filter(None, (l1, l2)), default=None)
        if n >= 3 or (last and _dt(last) and _dt(last) > now - timedelta(days=1)):
            continue
        # как в ERP (Юлия, 03.11): сначала «Почему не купили», дальше раз в день «Думает», третья попытка: решение
        kind = "why_not_bought" if n == 0 else "close_sale"
        title = ("Принять решение: продаём или отказ (3-я попытка)" if n == 2 else
                 "Почему не купили" if n == 0 else "Думает: связаться ещё раз")
        made += bool(_add(con, kind, s["id"], now, f"close_sale:{s['id']}:{(s['stage_changed_at'] or '')[:16]}:{n}", title=title,
                          note="Пробное горячее 48 часов. Узнайте, что понравилось ребёнку, снимите возражение, предложите оплатить сегодня",
                          group_id=s["group_id"]))
    # 3. перевели в ученики, а первой оплаты нет
    for s in _people(con, "s.funnel='members' AND s.stage!='left' AND s.converted_at IS NULL AND s.sale_by IS NOT NULL AND s.stage_changed_at >= ?",
                     (fresh(30),)):
        made += bool(_add(con, "first_pay", s["id"], pu.add_work(con, _dt(s["stage_changed_at"]) or now, 60 * 24),
                          f"first_pay:{s['id']}", group_id=s["group_id"]))
    # 4. отказ: через 3 месяца мягко пригласить снова
    for s in _people(con, "s.funnel='sales' AND s.stage='refused' AND s.stage_changed_at <= ? AND s.stage_changed_at >= ?",
                     (_iso(now - timedelta(days=90)), fresh(120))):
        if any(x in (s["refusal_reason"] or "") for x in NO_COMEBACK) or _has_open(con, s["id"], s["family_id"]):
            continue
        made += bool(_add(con, "reinvite", s["id"], pu.next_day_start(con, now, 0) if now.hour < 11 else now,
                          f"reinvite:{s['id']}:{(s['stage_changed_at'] or '')[:10]}", note=f"Отказ был: {s['refusal_reason'] or 'без причины'}",
                          group_id=s["group_id"]))
    # 5. продления и долги учеников (одна задача на семью)
    dl = int((con.execute("SELECT value FROM settings WHERE key='renewal_deadline_day'").fetchone() or ["25"])[0] or 25)
    cur = logic.month_of(now.date())
    nxt = logic.shift_month(cur, 1)
    seen = set()
    for s in _people(con, "s.funnel='members' AND s.stage!='left'"):
        if s["family_id"] in seen:
            continue
        if now.day >= 20 and sv.unpaid_groups(con, s["id"], nxt):
            seen.add(s["family_id"])
            made += bool(_add(con, "renew", s["id"], now.replace(hour=max(now.hour, 11), minute=0) if now.day == 20 else now,
                              f"renew:{s['family_id']}:{nxt}", title=f"Продлить абонемент на {logic.month_label(nxt)} (до {dl} числа)",
                              group_id=s["group_id"]))
        if now.day > dl and sv.unpaid_groups(con, s["id"], cur) and not (s["sale_by"] and not s["converted_at"]):
            seen.add(s["family_id"])
            made += bool(_add(con, "debt", s["id"], now, f"debt:{s['family_id']}:{cur}",
                              title=f"Долг за {logic.month_label(cur)}: связаться", group_id=s["group_id"]))
    # 6. ушёл: через 2 месяца пригласить вернуться
    for s in _people(con, "s.funnel='members' AND s.stage='left' AND s.stage_changed_at <= ? AND s.stage_changed_at >= ?",
                     (_iso(now - timedelta(days=60)), fresh(90))):
        if any(x in (s["refusal_reason"] or "") for x in NO_COMEBACK) or _has_open(con, s["id"], s["family_id"]) \
                or (s["refusal_reason"] or "").startswith("Дорого") \
                or con.execute("SELECT 1 FROM tasks WHERE auto_key LIKE ?", (f"life:win_back:{s['id']}:%",)).fetchone():
            continue  # с 04.11 срок ставится сразу при уходе (on_left); здесь только старые уходы
        made += bool(_add(con, "win_back", s["id"], now, f"win_back:{s['id']}:{(s['stage_changed_at'] or '')[:10]}",
                          note=f"Ушёл: {s['refusal_reason'] or 'без причины'}", group_id=s["group_id"]))
    # 8. предложили отработку и 2 дня без записи
    for s in _people(con, "s.funnel='members' AND s.stage!='left' AND s.skip_stage='offered'"):
        h = con.execute("SELECT ts FROM history WHERE student_id=? AND text LIKE 'Отработки: Предложили%' ORDER BY id DESC LIMIT 1",
                        (s["id"],)).fetchone()
        if h and (_dt(h[0]) or now) <= now - timedelta(days=2):
            made += bool(_add(con, "makeup_check", s["id"], now, f"makeup:{s['id']}:{h[0][:10]}", group_id=s["group_id"]))
    made += revive(con, now)
    cleanup(con, now)
    return made


def revive(con, now: datetime) -> int:
    """Старые лиды из ERP не теряем: каждый день порция на «оживление», горячие первыми, потом тёплые, холодные.
    Берём тех, у кого нет открытой задачи и кого не «оживляли» последние 60 дней."""
    from . import pu, smart
    if not (9 <= now.hour < 21):
        return 0
    limit = int((con.execute("SELECT value FROM settings WHERE key='revive_per_day'").fetchone() or [REVIVE_PER_DAY])[0] or REVIVE_PER_DAY)
    today = now.date().isoformat()
    done_today = con.execute("SELECT COUNT(*) FROM tasks WHERE kind='revive' AND substr(created_at,1,10)=?", (today,)).fetchone()[0]
    if done_today >= limit:
        return 0
    rank = {"hot": 0, "warm": 1, "cold": 2}
    cands = []
    for s in _people(con, "s.funnel='sales' AND s.stage IN ('lead','no_answer','missed_trial','how_was','later','refused')"):
        if s["stage"] == "refused" and any(x in (s["refusal_reason"] or "") for x in NO_COMEBACK):
            continue
        if _has_open(con, s["id"], s["family_id"]) or con.execute(
                "SELECT 1 FROM tasks WHERE student_id=? AND kind IN ('revive','reinvite') AND created_at >= ?",
                (s["id"], _iso(now - timedelta(days=60)))).fetchone():
            continue
        sc = smart.lead_score(con, dict(s), now) or {"level": "cold", "label": "❄ Холодный", "points": -9}
        cands.append((rank[sc["level"]] + (1 if s["stage"] == "refused" else 0), -(sc["points"]), -(_dt(s["stage_changed_at"]) or now).timestamp(), s, sc))
    cands.sort(key=lambda x: x[:3])
    made = 0
    for *_, s, sc in cands[:limit - done_today]:
        from .services import SALES_LABELS
        made += bool(_add(con, "revive", s["id"], now, f"revive:{s['id']}:{today}",
                          title=f"Оживить старый лид ({sc['label']})",
                          note=f"Этап: {SALES_LABELS.get(s['stage'], s['stage'])}. " + ", ".join(sc.get("why") or []),
                          group_id=s["group_id"]))
    return made


def cleanup(con, now: datetime):
    """Этап сменился, значит задача больше не нужна: снимаем."""
    for t in con.execute("""SELECT t.id, t.kind, t.auto_key, s.funnel, s.stage, s.skip_stage, s.converted_at, s.family_id, s.id AS sid
                            FROM tasks t JOIN students s ON s.id=t.student_id WHERE t.status='open' AND t.source='life'""").fetchall():
        k = t["kind"]
        stale = (k == "call_lead" and t["stage"] != "lead") or (k == "no_answer" and t["stage"] != "no_answer") \
            or (k in ("close_sale", "why_not_bought") and t["stage"] != "how_was") \
            or (k == "reinvite_pu" and t["stage"] != "reinvite_pu") or (k == "first_pay" and t["converted_at"]) \
            or (k == "reinvite" and t["stage"] != "refused") or (k == "win_back" and t["stage"] != "left") \
            or (k == "makeup_check" and t["skip_stage"] != "offered") or (k in ("renew", "debt") and t["stage"] == "left") \
            or (k == "revive" and (t["funnel"] != "sales" or t["stage"] in ("trial_booked", "on_trial", "trial_done")))
        if k == "unpaid_visit":
            from . import services as sv
            stale = t["stage"] == "left" or sv.paid_for_month(con, t["sid"], t["auto_key"].rsplit(":", 1)[-1])
        if k in ("renew", "debt") and not stale:
            from . import services as sv
            ym = t["auto_key"].rsplit(":", 1)[-1]
            kids = [r[0] for r in con.execute("SELECT id FROM students WHERE family_id=? AND funnel='members' AND stage!='left' AND archived=0",
                                                (t["family_id"],))]
            stale = not any(sv.unpaid_groups(con, x, ym) for x in kids)  # оплатили
        if stale:
            con.execute("UPDATE tasks SET status='cancelled', done_at=?, result='Этап сменился' WHERE id=?", (_iso(now), t["id"]))


def on_payment(con, sid: int):
    """Первая оплата: продажа засчитана («Купил» по оплате, 200 ₽ тому, кто продал)."""
    s = con.execute("SELECT converted_at, sale_by, funnel FROM students WHERE id=?", (sid,)).fetchone()
    if not s or s["converted_at"] or s["funnel"] != "members":
        return
    if con.execute("SELECT 1 FROM subscriptions WHERE student_id=? AND status='paid'", (sid,)).fetchone():
        con.execute("UPDATE students SET converted_at=?, converted_by=COALESCE(converted_by, sale_by) WHERE id=?",
                    (datetime.now().isoformat(timespec="seconds"), sid))
        from . import services as sv
        sv.log(con, sid, "Первая оплата: продажа засчитана", None, "stage")


def init(con):
    cols = {r[1] for r in con.execute("PRAGMA table_info(students)")}
    if "sale_by" not in cols:
        con.execute("ALTER TABLE students ADD COLUMN sale_by INTEGER")
    # 01.11: перенос из ERP ставил всем дату этапа = день переноса. Если с тех пор этап не меняли, ставим настоящую:
    # дату пробного для «пробных» этапов, иначе дату обращения (иначе вся база выглядит свежей и «горячей»)
    if "erp_id" in cols and not con.execute("SELECT 1 FROM settings WHERE key='stage_dates_fixed_0111'").fetchone():
        con.execute("""UPDATE students SET stage_changed_at = CASE
                         WHEN stage IN ('trial_booked','missed_trial','how_was') AND trial_at IS NOT NULL AND trial_at < stage_changed_at THEN trial_at
                         WHEN created_at < stage_changed_at THEN created_at ELSE stage_changed_at END
                       WHERE COALESCE(erp_id,'') != '' AND NOT EXISTS (SELECT 1 FROM history h WHERE h.student_id=students.id AND h.kind='stage')""")
        con.execute("INSERT INTO settings(key, value) VALUES ('stage_dates_fixed_0111', '1')")
    # 7. лишний этап «Напоминание о пробном»: его дублирует задача, карточки возвращаем в «Записан на пробное»
    con.execute("UPDATE students SET stage='trial_booked' WHERE funnel='sales' AND stage='trial_reminder'")


def on_unpaid_visit(con, sid: int, group_id: int, day: str):
    """Ребёнка отметили «был», а за месяц не оплачено: красная задача администраторам (Юлия, 04.11). Снимается после оплаты."""
    from . import services as sv
    s = con.execute("SELECT funnel FROM students WHERE id=?", (sid,)).fetchone()
    if not s or s["funnel"] != "members" or sv.paid_for_month(con, sid, day[:7]):
        return
    g = (con.execute("SELECT name FROM groups WHERE id=?", (group_id,)).fetchone() or [""])[0]
    key = f"unpaid_visit:{sid}:{day[:7]}"
    note = f"{day[8:10]}.{day[5:7]} {g}"
    old = con.execute("SELECT id, note FROM tasks WHERE auto_key=? AND status='open'", ("life:" + key,)).fetchone()
    if old:  # уже есть: добавим дату, чтобы было видно все занятия без оплаты
        if note not in (old["note"] or ""):
            con.execute("UPDATE tasks SET note=? WHERE id=?", (((old["note"] or "") + ", " + note).strip(", "), old["id"]))
        return
    _add(con, "unpaid_visit", sid, _iso(datetime.now()), key, note=note, group_id=group_id)
